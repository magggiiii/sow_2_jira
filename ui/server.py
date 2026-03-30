import json
import os
import shutil
import asyncio
import threading
import base64
import secrets
from pathlib import Path
from typing import List, Optional
from datetime import datetime
import uuid

from fastapi import FastAPI, HTTPException, UploadFile, File, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import logging
import httpx
import time

# Add project root to path for imports
import sys
UI_DIR = Path(__file__).parent
sys.path.insert(0, str(UI_DIR.parent))

# Load environment variables
load_dotenv()

from models.schemas import RunConfig, LLMMode, JiraHierarchy, ManagedTask, TaskStatus
from pipeline.orchestrator import PipelineOrchestrator
from audit.logger import AuditLogger
from config.settings import SettingsManager, PROVIDER_REGISTRY, build_litellm_model, resolve_provider_base

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pipeline.observability import trace_span, sync_telemetry, logger

app = FastAPI(title="SOW to Jira Pipeline")

DATA_DIR = Path("data")
settings_manager = SettingsManager(str(DATA_DIR))

@app.on_event("startup")
def startup_event():
    # Legacy Migration: Move old pipeline_output.json to a session folder
    legacy_file = Path("data/pipeline_output.json")
    if legacy_file.exists():
        legacy_id = "legacy-migration-" + datetime.now().strftime("%Y%m%d")
        legacy_dir = Path(f"data/sessions/{legacy_id}")
        legacy_dir.mkdir(parents=True, exist_ok=True)
        
        # Move output file
        shutil.move(str(legacy_file), str(legacy_dir / "pipeline_output.json"))
        
        # Create metadata
        with open(legacy_dir / "metadata.json", "w") as f:
            json.dump({
                "run_id": legacy_id,
                "filename": "Legacy Export",
                "llm_mode": "api",
                "created_at": datetime.utcnow().isoformat()
            }, f)
        logger.info(f"Migrated legacy data to session: {legacy_id}")

    sync_telemetry()
    try:
        settings = settings_manager.load()
        if settings:
            _apply_settings_to_env_legacy(settings)
    except Exception as e:
        logger.error(f"Failed to load settings on startup: {e}")
    
    # Filter out frequent status polling from logs
    class PollingFilter(logging.Filter):
        def filter(self, record):
            return "/api/status" not in record.getMessage()

    logging.getLogger("uvicorn.access").addFilter(PollingFilter())

def _apply_settings_to_env_legacy(settings: dict) -> None:
    provider = settings.get("provider")
    providers = settings.get("providers", {})
    provider_settings = providers.get(provider, {}) if provider else {}
    model = provider_settings.get("model")
    api_key_enc = provider_settings.get("api_key")
    base_url = provider_settings.get("base_url")
    azure_deployment = provider_settings.get("azure_deployment_name")
    azure_api_version = provider_settings.get("azure_api_version")

    if api_key_enc:
        try:
            os.environ["LITELLM_API_KEY"] = settings_manager.decrypt_secret(api_key_enc)
        except Exception:
            pass
    if provider:
        os.environ["LITELLM_PROVIDER"] = provider
    if base_url:
        os.environ["LITELLM_API_BASE"] = base_url
    if model and provider:
        os.environ["LITELLM_MODEL"] = build_litellm_model(provider, model, azure_deployment)
    if azure_api_version:
        os.environ["AZURE_API_VERSION"] = azure_api_version
    if azure_deployment:
        os.environ["AZURE_DEPLOYMENT_NAME"] = azure_deployment
    if settings.get("jira_server_url"):
        os.environ["JIRA_SERVER"] = settings["jira_server_url"]
    jira_token_enc = settings.get("jira_api_token")
    if jira_token_enc:
        try:
            os.environ["JIRA_API_TOKEN"] = settings_manager.decrypt_secret(jira_token_enc)
        except Exception:
            pass

# Add Secure CORS Middleware (Trusted Origins)
# Default to localhost for local deploys, can be overridden by env
trusted_origins = os.environ.get("BETTER_AUTH_TRUSTED_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000")
origins = [o.strip() for o in trusted_origins.split(",") if o.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Instrument FastAPI
FastAPIInstrumentor.instrument_app(app)

UPLOAD_DIR = Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Mount static files
app.mount("/static", StaticFiles(directory=UI_DIR), name="static")

# Global status tracking (Concurrent state dictionary)
class ProcessingStatus(BaseModel):
    is_running: bool = False
    current_step: int = 0
    message: str = "Idle"
    progress: float = 0.0
    error: Optional[str] = None
    run_id: Optional[str] = None
    kind: str = "pipeline"
    logs: List[str] = []

class ModelDiscoveryRequest(BaseModel):
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    azure_deployment_name: Optional[str] = None
    azure_api_version: Optional[str] = None

class SettingsConfig(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    azure_deployment_name: Optional[str] = None
    azure_api_version: Optional[str] = None
    jira_server_url: Optional[str] = None
    jira_api_token: Optional[str] = None

active_runs: dict[str, ProcessingStatus] = {}
active_orchestrators: dict[str, PipelineOrchestrator] = {}

def get_session_path(session_id: str) -> Path:
    if not session_id or '..' in session_id or '/' in session_id or '\\' in session_id:
        return Path("data/pipeline_output.json")  # fallback for legacy
    return Path(f"data/sessions/{session_id}/pipeline_output.json")

@app.get("/")
def read_root():
    return FileResponse(UI_DIR / "index.html")

def load_data(session_id: str = None):
    path = get_session_path(session_id)
    if not path.exists():
        return {"tasks": [], "config": {}}
    try:
        if path.stat().st_size == 0:
            return {"tasks": [], "config": {}}
        with open(path, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError):
        logger.warning(f"Corrupted results file found at {path}. Returning empty data.")
        return {"tasks": [], "config": {}}

def save_data(data, session_id: str = None):
    path = get_session_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)

@app.get("/api/sessions")
def get_sessions():
    sessions_dir = Path("data/sessions")
    if not sessions_dir.exists():
        return []
        
    sessions = []
    for d in sessions_dir.iterdir():
        if d.is_dir():
            meta_path = d / "metadata.json"
            if meta_path.exists():
                try:
                    with open(meta_path, "r") as f:
                        sessions.append(json.load(f))
                except:
                    pass
    # Sort by created_at descending
    sessions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
    return sessions

@app.get("/api/tasks")
def get_tasks(session_id: Optional[str] = None):
    data = load_data(session_id)
    # Add current environment defaults to help UI
    data["env_defaults"] = {
        "jira_project_key": os.environ.get("JIRA_PROJECT_KEY", "PROJ"),
        "jira_server": os.environ.get("JIRA_SERVER"),
    }
    return data

@app.get("/api/status")
def get_status(session_id: Optional[str] = None):
    if not session_id:
        return ProcessingStatus().model_dump()
    return active_runs.get(session_id, ProcessingStatus()).model_dump()

class ProcessRequest(BaseModel):
    pdf_filename: str
    llm_mode: str  # "api" | "local" | "custom"
    jira_hierarchy: str # "flat" | "epic_task" | "story_subtask"
    jira_project_key: str
    skip_indexing: bool = False
    max_nodes: int = 200

def run_pipeline_task(req: ProcessRequest, run_id: str):
    status = ProcessingStatus()
    status.is_running = True
    status.run_id = run_id
    active_runs[run_id] = status
    
    # Save session metadata safely
    session_dir = Path(f"data/sessions/{run_id}")
    session_dir.mkdir(parents=True, exist_ok=True)
    with open(session_dir / "metadata.json", "w") as f:
        json.dump({
            "run_id": run_id,
            "filename": req.pdf_filename,
            "llm_mode": req.llm_mode,
            "created_at": datetime.utcnow().isoformat()
        }, f)
        
    from pipeline.observability import run_logger
    
    with run_logger(run_id):
        try:
            # Load app config
            config_path = Path("config/sow_config.json")
            with open(config_path) as f:
                app_config = json.load(f)
                
            # Build RunConfig
            run_cfg = RunConfig(
                sow_pdf_path=str(UPLOAD_DIR / req.pdf_filename),
                llm_mode=LLMMode(req.llm_mode),
                jira_hierarchy=JiraHierarchy(req.jira_hierarchy),
                jira_project_key=req.jira_project_key,
                skip_indexing=req.skip_indexing,
                max_nodes=req.max_nodes,
                run_id=run_id
            )
            
            audit = AuditLogger()
            
            def status_cb(step, msg, progress):
                status.current_step = step
                status.message = msg
                status.progress = progress
                status.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
                if len(status.logs) > 50:
                    status.logs.pop(0)
                
            orchestrator = PipelineOrchestrator(run_cfg, app_config, audit, status_callback=status_cb)
            active_orchestrators[run_id] = orchestrator
            orchestrator.run()
            
            status.message = "Pipeline Complete"
            status.progress = 1.0
        except Exception as e:
            import traceback
            traceback.print_exc()
            status.error = str(e)
            status.message = f"Error: {str(e)}"
        finally:
            status.is_running = False
            if run_id in active_orchestrators:
                del active_orchestrators[run_id]

@app.post("/api/cancel/{run_id}")
async def cancel_run(run_id: str):
    if run_id in active_orchestrators:
        active_orchestrators[run_id].stop_event.set()
        if run_id in active_runs:
            active_runs[run_id].message = "Cancelling..."
        return {"message": "Cancellation signal sent"}
    raise HTTPException(status_code=404, detail="Active run not found")

@app.delete("/api/sessions/{run_id}")
async def delete_session(run_id: str):
    session_dir = Path(f"data/sessions/{run_id}")
    if session_dir.exists():
        # Stop if running
        if run_id in active_orchestrators:
            active_orchestrators[run_id].stop_event.set()
        
        shutil.rmtree(session_dir)
        if run_id in active_runs:
            del active_runs[run_id]
        return {"message": f"Session {run_id} deleted"}
    raise HTTPException(status_code=404, detail="Session not found")

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files allowed")
    
    file_path = UPLOAD_DIR / file.filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    return {"filename": file.filename}

@app.get("/api/providers")
def get_providers():
    return {"providers": PROVIDER_REGISTRY}

MODEL_CACHE = {}  # provider_id: (timestamp, models_list)

def _extract_models_from_response(provider_id: str, data: dict) -> list[str]:
    if provider_id == "ollama":
        return [m.get("name") for m in data.get("models", []) if m.get("name")]
    if provider_id == "azure":
        return [m.get("id") or m.get("model") for m in data.get("data", []) if m.get("id") or m.get("model")]
    if provider_id in {"google"}:
        models = []
        for m in data.get("models", []):
            name = m.get("name")
            if name and name.startswith("models/"):
                name = name.split("/", 1)[1]
            if name:
                models.append(name)
        return models
    if provider_id == "cohere":
        return [m.get("name") for m in data.get("models", []) if m.get("name")]
    items = data.get("data", [])
    return [m.get("id") for m in items if m.get("id")]

@app.post("/api/providers/{provider_id}/models")
async def get_provider_models(provider_id: str, req: ModelDiscoveryRequest):
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail="Unknown provider")

    settings = settings_manager.load()
    provider_settings = settings.get("providers", {}).get(provider_id, {})
    base_url = resolve_provider_base(provider_id, req.base_url or provider_settings.get("base_url"))
    if not base_url:
        raise HTTPException(status_code=400, detail="Base URL is required for this provider")
    
    stored_key = None
    if provider_settings.get("api_key"):
        try:
            stored_key = settings_manager.decrypt_secret(provider_settings.get("api_key"))
        except Exception:
            stored_key = None

    api_key = req.api_key if req.api_key and req.api_key != "***" else stored_key

    # Cache Check
    cache_key = f"{provider_id}:{base_url}:{api_key}"
    if cache_key in MODEL_CACHE:
        ts, models = MODEL_CACHE[cache_key]
        if time.time() - ts < 300:  # 5 minute cache
            return {"success": True, "models": models}

    if provider_id == "azure" and not req.azure_api_version:
        raise HTTPException(status_code=400, detail="Azure API version is required")
        
    if provider_id in {"openai", "openrouter", "groq", "mistral", "together", "zai"}:
        url = f"{base_url.rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    elif provider_id == "anthropic":
        url = f"{base_url.rstrip('/')}/v1/models"
        headers = {
            "x-api-key": api_key or "",
            "anthropic-version": "2023-06-01",
        }
    elif provider_id == "google":
        url = f"{base_url.rstrip('/')}/models"
        headers = {"x-goog-api-key": api_key or ""}
    elif provider_id == "ollama":
        url = f"{base_url.rstrip('/')}/api/tags"
        headers = {}
    elif provider_id == "cohere":
        url = f"{base_url.rstrip('/')}/models"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    elif provider_id == "azure":
        url = f"{base_url.rstrip('/')}/openai/deployments?api-version={req.azure_api_version}"
        headers = {"api-key": api_key or ""}
    else:
        raise HTTPException(status_code=400, detail="Model discovery not supported for provider")

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=headers, timeout=8)
            if resp.status_code >= 400:
                raise HTTPException(status_code=resp.status_code, detail=resp.text[:200])
            data = resp.json()
            models = sorted({m for m in _extract_models_from_response(provider_id, data) if m})
            MODEL_CACHE[cache_key] = (time.time(), models)
            return {"success": True, "models": models}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Model discovery failed: {e}")

@app.get("/api/settings")
def get_settings():
    try:
        settings = settings_manager.load()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    provider = settings.get("provider", "openai")
    providers = settings.get("providers", {})
    return {
        "provider": provider,
        "providers": {
            k: {
                "model": v.get("model", ""),
                "api_key": "***" if v.get("api_key") else "",
                "base_url": v.get("base_url", ""),
                "azure_deployment_name": v.get("azure_deployment_name", ""),
                "azure_api_version": v.get("azure_api_version", ""),
            }
            for k, v in providers.items()
        },
        "jira_server_url": settings.get("jira_server_url", ""),
        "jira_api_token": "***" if settings.get("jira_api_token") else "",
    }

@app.post("/api/settings")
def save_settings(req: SettingsConfig):
    try:
        settings = settings_manager.load()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    provider = req.provider or settings.get("provider") or "openai"
    prev_provider = settings.get("provider")
    base_url = resolve_provider_base(provider, req.base_url)

    settings["provider"] = provider
    settings.setdefault("providers", {})
    provider_settings = settings["providers"].get(provider, {})
    prev_model = provider_settings.get("model")
    provider_settings["model"] = req.model or provider_settings.get("model", "")
    provider_settings["base_url"] = base_url
    provider_settings["azure_deployment_name"] = req.azure_deployment_name or provider_settings.get("azure_deployment_name", "")
    provider_settings["azure_api_version"] = req.azure_api_version or provider_settings.get("azure_api_version", "")

    if req.api_key and req.api_key != "***":
        provider_settings["api_key"] = settings_manager.encrypt_secret(req.api_key)
    elif "api_key" not in provider_settings:
        provider_settings["api_key"] = ""

    if req.jira_server_url:
        settings["jira_server_url"] = req.jira_server_url
        os.environ["JIRA_SERVER"] = req.jira_server_url

    if req.jira_api_token and req.jira_api_token != "***":
        settings["jira_api_token"] = settings_manager.encrypt_secret(req.jira_api_token)
        os.environ["JIRA_API_TOKEN"] = req.jira_api_token
    elif "jira_api_token" not in settings:
        settings["jira_api_token"] = ""

    settings["providers"][provider] = provider_settings
    settings_manager.save(settings)
    
    # Update env for current process (LiteLLM usually reads these once, but we'll try)
    if req.api_key and req.api_key != "***":
        os.environ["LITELLM_API_KEY"] = req.api_key
    os.environ["LITELLM_PROVIDER"] = provider
    if base_url:
        os.environ["LITELLM_API_BASE"] = base_url
    if provider_settings.get("model"):
        os.environ["LITELLM_MODEL"] = build_litellm_model(provider, provider_settings["model"], provider_settings["azure_deployment_name"])

    if prev_provider != provider:
        logger.info(f"LLM provider switched: {prev_provider or 'unset'} → {provider}")
    if prev_model != provider_settings.get("model"):
        logger.info(f"LLM model switched ({provider}): {prev_model or 'unset'} → {provider_settings.get('model') or 'unset'}")
    
    # Invalidate Cache on setting change
    MODEL_CACHE.clear()
    
    return {"message": "Settings saved successfully"}

@app.post("/api/process")
async def start_processing(req: ProcessRequest, background_tasks: BackgroundTasks):
    # Readable Run ID: YYYYMMDD-HHMMSS-filename
    clean_name = "".join(c if c.isalnum() else "-" for c in req.pdf_filename.split(".")[0]).strip("-")
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"{timestamp}-{clean_name}"
    
    background_tasks.add_task(run_pipeline_task, req, run_id)
    return {"message": "Processing started", "run_id": run_id}

class TaskUpdate(BaseModel):
    id: str
    title: str
    short_description: Optional[str] = None
    use_case: Optional[str] = None
    acceptance_criteria: Optional[List[str]] = None
    considerations_constraints: Optional[List[str]] = None
    deliverables: Optional[List[str]] = None
    mockup_prototype: Optional[str] = None
    status: str

@app.post("/api/tasks")
def update_task(task_update: TaskUpdate, session_id: Optional[str] = None):
    data = load_data(session_id)
    tasks = data.get("tasks", [])
    
    task_found = False
    for i, t in enumerate(tasks):
        if str(t.get("id")) == task_update.id:
            tasks[i].update(task_update.model_dump(exclude_unset=True))
            task_found = True
            break
            
    if not task_found:
        raise HTTPException(status_code=404, detail="Task not found")
        
    save_data(data, session_id)
    return {"message": "Task updated successfully", "task": task_update}

class AddTaskRequest(BaseModel):
    title: str
    short_description: str

@app.post("/api/tasks/add")
def add_task(req: AddTaskRequest, session_id: Optional[str] = None):
    data = load_data(session_id)
    if "tasks" not in data:
        data["tasks"] = []
    
    import uuid
    import datetime
    
    new_task = {
        "id": str(uuid.uuid4()),
        "title": req.title,
        "short_description": req.short_description,
        "status": "APPROVED",
        "confidence": 1.0,
        "created_at": datetime.datetime.utcnow().isoformat(),
        "updated_at": datetime.datetime.utcnow().isoformat(),
        "flags": [],
        "source_refs": []
    }
    
    data["tasks"].append(new_task)
    save_data(data, session_id)
    return {"message": "Task added successfully", "task": new_task}

@app.post("/api/tasks/approve_all")
def approve_all(session_id: Optional[str] = None):
    data = load_data(session_id)
    tasks = data.get("tasks", [])
    count = 0
    for i, t in enumerate(tasks):
        if t.get("status") == "CLOSED":
            tasks[i]["status"] = "APPROVED"
            count += 1
    
    save_data(data, session_id)
    return {"message": f"Approved {count} tasks successfully", "count": count}

class PushRequest(BaseModel):
    jira_hierarchy: Optional[str] = None
    jira_project_key: Optional[str] = None

def _append_status_log(status: ProcessingStatus, msg: str) -> None:
    status.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    if len(status.logs) > 50:
        status.logs.pop(0)

def run_push_task(req: Optional[PushRequest], session_id: Optional[str], run_id: str):
    status = ProcessingStatus(
        is_running=True,
        current_step=1,
        message="Pushing approved tasks to Jira...",
        progress=0.05,
        run_id=run_id,
        kind="jira_push",
        logs=[]
    )
    active_runs[run_id] = status
    _append_status_log(status, "Initializing Jira push")

    from pipeline.observability import run_logger

    with run_logger(run_id):
        try:
            data = load_data(session_id)
            run_config = data.get("config", {}) if isinstance(data.get("config"), dict) else {}

            project_key = (req.jira_project_key if (req and req.jira_project_key)
                           else os.environ.get("JIRA_PROJECT_KEY", run_config.get("jira_project_key", "PROJ")))

            if run_config.get("jira_project_key") != project_key:
                run_config["jira_project_key"] = project_key
                data["config"] = run_config

            hierarchy_val = (req.jira_hierarchy if req and req.jira_hierarchy
                             else run_config.get("jira_hierarchy", "flat"))

            os.environ["JIRA_PROJECT_KEY"] = project_key
            hierarchy = JiraHierarchy(hierarchy_val)

            tasks_data = data.get("tasks", [])
            managed_tasks = [ManagedTask(**t) for t in tasks_data]
            approved_tasks = [t for t in managed_tasks if t.status == TaskStatus.APPROVED]

            if not approved_tasks:
                status.message = "No approved tasks to push"
                status.progress = 1.0
                status.is_running = False
                _append_status_log(status, "No approved tasks found")
                return

            audit = AuditLogger()
            jira = JiraClient(hierarchy, audit, run_config.get("run_id", session_id or "ui"), project_key=project_key)

            status.progress = 0.2
            _append_status_log(status, f"Pushing {len(approved_tasks)} tasks")
            results = jira.push_tasks(approved_tasks)

            result_map = {str(r.task_id): r for r in results}
            for i, t in enumerate(tasks_data):
                if t.get("status") == "APPROVED":
                    res = result_map.get(str(t.get("id")))
                    if res and res.success:
                        tasks_data[i]["status"] = "PUSHED"

            save_data(data, session_id)

            total_passed = sum(1 for r in results if r.success)
            total_failed = len(results) - total_passed
            overall_success = total_failed == 0

            first_error = next((r.error for r in results if not r.success and r.error), None)
            message = f"Push complete. {total_passed} passed, {total_failed} failed."
            if first_error:
                message += f" First error: {first_error[:100]}..."

            status.progress = 1.0
            status.message = message
            _append_status_log(status, message)
            status.is_running = False
            if not overall_success:
                status.error = first_error or "Push completed with failures"
        except Exception as e:
            status.error = str(e)
            status.message = f"Error: {str(e)}"
            _append_status_log(status, f"Error: {str(e)}")
            status.is_running = False

@app.post("/api/push")
def push_to_jira(req: Optional[PushRequest] = None, session_id: Optional[str] = None):
    if session_id and session_id in active_runs and active_runs[session_id].is_running:
        raise HTTPException(status_code=409, detail="A task is already running for this session")

    data = load_data(session_id)
    tasks_data = data.get("tasks", [])
    managed_tasks = [ManagedTask(**t) for t in tasks_data]
    approved_tasks = [t for t in managed_tasks if t.status == TaskStatus.APPROVED]
    if not approved_tasks:
        return {"success": False, "message": "No approved tasks to push", "results": []}

    run_id = session_id or f"push-{uuid.uuid4()}"
    thread = threading.Thread(target=run_push_task, args=(req, session_id, run_id), daemon=True)
    thread.start()
    return {"success": True, "started": True, "run_id": run_id, "message": "Push started"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui.server:app", host="127.0.0.1", port=8000, reload=True)

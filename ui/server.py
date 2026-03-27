import json
import os
import shutil
import asyncio
import threading
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

# Add project root to path for imports
import sys
UI_DIR = Path(__file__).parent
sys.path.insert(0, str(UI_DIR.parent))

# Load environment variables
load_dotenv()

from models.schemas import RunConfig, LLMMode, JiraHierarchy, ManagedTask, TaskStatus
from pipeline.orchestrator import PipelineOrchestrator
from audit.logger import AuditLogger

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pipeline.observability import trace_span, sync_telemetry

app = FastAPI(title="SOW to Jira Pipeline")

@app.on_event("startup")
def startup_event():
    sync_telemetry()

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
    logs: List[str] = []

active_runs: dict[str, ProcessingStatus] = {}

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
        
    # Initialize run-level file logger
    from pipeline.observability import add_run_file_logger
    logger_id = add_run_file_logger(run_id)
    
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

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files allowed")
    
    file_path = UPLOAD_DIR / file.filename
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    return {"filename": file.filename}

class SettingsConfig(BaseModel):
    universal_api_key: Optional[str] = None
    universal_model: Optional[str] = None
    universal_api_base: Optional[str] = None
    jira_server_url: Optional[str] = None
    jira_api_token: Optional[str] = None

@app.get("/api/settings")
def get_settings():
    return {
        "universal_api_key": "***" if os.environ.get("LITELLM_API_KEY") else "",
        "universal_model": os.environ.get("LITELLM_MODEL", ""),
        "universal_api_base": os.environ.get("LITELLM_API_BASE", ""),
        "jira_server_url": os.environ.get("JIRA_SERVER", ""),
        "jira_api_token": "***" if os.environ.get("JIRA_API_TOKEN") else ""
    }

@app.post("/api/settings")
def save_settings(req: SettingsConfig):
    env_path = Path(".env")
    env_vars = {}
    if env_path.exists():
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line: continue
                k, v = line.split("=", 1)
                env_vars[k.strip()] = v.strip()
                
    if req.universal_api_key and req.universal_api_key != "***":
        env_vars["LITELLM_API_KEY"] = req.universal_api_key
        os.environ["LITELLM_API_KEY"] = req.universal_api_key
        
    if req.universal_model:
        env_vars["LITELLM_MODEL"] = req.universal_model
        os.environ["LITELLM_MODEL"] = req.universal_model
        
    if req.universal_api_base:
        env_vars["LITELLM_API_BASE"] = req.universal_api_base
        os.environ["LITELLM_API_BASE"] = req.universal_api_base
        
    if req.jira_server_url:
        env_vars["JIRA_SERVER"] = req.jira_server_url
        os.environ["JIRA_SERVER"] = req.jira_server_url
        
    if req.jira_api_token and req.jira_api_token != "***":
        env_vars["JIRA_API_TOKEN"] = req.jira_api_token
        os.environ["JIRA_API_TOKEN"] = req.jira_api_token
        
    with open(env_path, "w") as f:
        for k, v in env_vars.items():
            f.write(f"{k}={v}\n")
            
    return {"message": "Settings saved successfully to .env"}

@app.post("/api/process")
async def start_processing(req: ProcessRequest, background_tasks: BackgroundTasks):
    run_id_tmp = str(uuid.uuid4())[:8]
    background_tasks.add_task(run_pipeline_task, req, run_id_tmp)
    return {"message": "Processing started", "run_id": run_id_tmp}

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

@app.post("/api/push")
def push_to_jira(req: Optional[PushRequest] = None, session_id: Optional[str] = None):
    from integrations.jira_client import JiraClient
    from audit.logger import AuditLogger
    
    data = load_data(session_id)
    run_config = data.get("config", {}) if isinstance(data.get("config"), dict) else {}
    
    # Use request values if provided, otherwise fallback to environment, then saved config
    project_key = (req.jira_project_key if (req and req.jira_project_key) 
                   else os.environ.get("JIRA_PROJECT_KEY", run_config.get("jira_project_key", "PROJ")))
    
    # Save the used project key back to the session if it's different (persistence)
    if run_config.get("jira_project_key") != project_key:
        run_config["jira_project_key"] = project_key
        data["config"] = run_config
        # We will save entire data at the end after results are updated
    
    hierarchy_val = (req.jira_hierarchy if req and req.jira_hierarchy 
                     else run_config.get("jira_hierarchy", "flat"))
    
    os.environ["JIRA_PROJECT_KEY"] = project_key
    hierarchy = JiraHierarchy(hierarchy_val)
    
    tasks_data = data.get("tasks", [])
    managed_tasks = [ManagedTask(**t) for t in tasks_data]
    
    approved_tasks = [t for t in managed_tasks if t.status == TaskStatus.APPROVED]
    
    if not approved_tasks:
        return {"success": False, "message": "No approved tasks to push", "results": []}
        
    audit = AuditLogger()
    jira = JiraClient(hierarchy, audit, run_config.get("run_id", session_id or "ui"), project_key=project_key)
    
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
    
    return {
        "success": overall_success,
        "message": message,
        "results": [
            {
                "task_id": str(r.task_id),
                "success": r.success,
                "jira_key": r.jira_issue_key,
                "url": r.jira_issue_url,
                "error": r.error,
                "warning": r.warning
            }
            for r in results
        ]
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("ui.server:app", host="127.0.0.1", port=8000, reload=True)

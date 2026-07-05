import json
import os
import shutil
import asyncio
import threading
import base64
import secrets
from pathlib import Path
from typing import List, Optional
from datetime import datetime, timezone
import uuid

from fastapi import (
    FastAPI,
    HTTPException,
    UploadFile,
    File,
    BackgroundTasks,
    Depends,
    Request,
    Cookie,
    Header,
    Response,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv
import logging
import httpx
import time
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

# Add project root to path for imports
import sys
UI_DIR = Path(__file__).parent
sys.path.insert(0, str(UI_DIR.parent))

# Load .env BEFORE importing pipeline.observability — that module reads
# Langfuse/Argus env vars at module-import time.
load_dotenv(UI_DIR.parent / ".env")

from models.schemas import RunConfig, LLMMode, JiraHierarchy, ManagedTask, TaskStatus, JiraPushResult
from core.errors import ErrorClass, classify_exception
from core.domain.ids import make_run_id
from core.guardrails import PushGate, PushBlocked
from pipeline.orchestrator import PipelineOrchestrator
from audit.logger import AuditLogger
from config.settings import SettingsManager, PROVIDER_REGISTRY, build_litellm_model, resolve_provider_base, _ensure_docker_host
from integrations.jira_client import JiraClient
from jira import JIRA

from auth.deps import SESSION_COOKIE_NAME, current_user, get_session_store

from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pipeline.observability import trace_span, logger

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
                "created_at": datetime.now(timezone.utc).isoformat()
            }, f)
        logger.info(f"Migrated legacy data to session: {legacy_id}")

    try:
        settings = settings_manager.load()
        if settings:
            _apply_settings_to_env_legacy(settings)
    except Exception as e:
        logger.error(f"Failed to load settings on startup: {e}")
    
    # Filter out frequent status polling and liveness probes from logs.
    # /healthz (4.2d) is hit by container/Render health checks on a tight
    # interval, so keep it out of the access log alongside /api/status.
    class PollingFilter(logging.Filter):
        def filter(self, record):
            msg = record.getMessage()
            return "/api/status" not in msg and "/healthz" not in msg

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

# Mount static files. UI.2a: serve the Vite build output (ui/dist) when it exists
# (prod / after `npm run build`), else the raw ui/ source (only usable via the
# Vite dev server, `make ui-dev`). The built index.html references hashed assets
# under /static/, matching this mount.
UI_DIST = UI_DIR / "dist"
FRONTEND_DIR = UI_DIST if (UI_DIST / "index.html").exists() else UI_DIR
app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

# Global status tracking (Concurrent state dictionary)
class ProcessingStatus(BaseModel):
    is_running: bool = False
    current_step: int = 0
    message: str = "Idle"
    progress: float = 0.0
    error: Optional[str] = None
    # How the UI should react to `error`: transient (retry), user_fixable
    # (fix credentials/config), or terminal. Set at the pipeline/push boundary.
    error_class: Optional[ErrorClass] = None
    run_id: Optional[str] = None
    # SERVER-B 2.6a: creator's user id, stamped at run creation so data routes
    # can resolve ownership. In single-user mode this is the default user's id.
    owner_id: Optional[str] = None
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


# ─────────────────────────────────────────────────────────────────────────────
# SERVER-B multi-tenant hardening (2.5c / 2.6a / 2.6b / 2.6c)
#
# THE OVERRIDING CONSTRAINT: hardening ACTIVATES ONLY WHEN AUTH IS CONFIGURED.
# The 780 existing tests hit routes WITHOUT auth cookies and MUST stay green, so
# every check below is gated on ``auth_enabled()``. When OFF (the default):
#   - current_user resolves to a fixed DEFAULT/local user
#   - IDOR ownership never 404s (everything is owned by the default user)
#   - CSRF is not enforced
# When ON (a SessionStore wired via app.state.session_store or a dependency
# override): real per-user auth + IDOR + CSRF enforcement.
# ─────────────────────────────────────────────────────────────────────────────

# Stable identity for single-user / local mode. Every run created while auth is
# OFF is owned by this id, so ownership checks are transparently satisfied.
DEFAULT_USER_ID = "local-default-user"
DEFAULT_USER_EMAIL = "local@localhost"


class _DefaultUser:
    """Fixed principal used when auth is not configured (single-user mode)."""

    id = DEFAULT_USER_ID
    email = DEFAULT_USER_EMAIL
    is_active = True


_DEFAULT_USER = _DefaultUser()

# CSRF double-submit cookie/header names (2.6c). A state-changing request in
# hardened mode must send an ``X-CSRF-Token`` header matching the CSRF cookie.
CSRF_COOKIE_NAME = "sow_csrf"
CSRF_HEADER_NAME = "x-csrf-token"

# Upload hardening (2.6b): max upload size, overridable via env.
SOW_MAX_UPLOAD_MB = int(os.environ.get("SOW_MAX_UPLOAD_MB", "50"))
_PDF_MAGIC = b"%PDF-"


def auth_enabled() -> bool:
    """Return True iff per-user auth is configured for this app.

    Auth is considered ON when a ``SessionStore`` has been wired onto
    ``app.state.session_store`` OR the ``get_session_store`` dependency has been
    overridden (the path tests use). Default is OFF so existing single-user
    behavior — and the 780 existing tests — are byte-for-byte unchanged.
    """
    if getattr(app.state, "session_store", None) is not None:
        return True
    if get_session_store in app.dependency_overrides:
        return True
    return False


def get_current_user(
    request: Request,
    sow_session: Optional[str] = Cookie(default=None, alias=SESSION_COOKIE_NAME),
):
    """Resolve the acting principal for a request.

    - Auth OFF  → the fixed default local user (no 401, no cookie needed).
    - Auth ON   → delegate to ``auth.deps.current_user`` (401 on missing/forged/
      expired cookie).
    """
    if not auth_enabled():
        return _DEFAULT_USER

    # Resolve the store the same way auth_enabled() detected it: prefer an
    # explicit dependency override (tests), else the app-state store.
    store_provider = app.dependency_overrides.get(get_session_store)
    store = store_provider() if store_provider else app.state.session_store
    return current_user(sow_session=sow_session, store=store)


def enforce_csrf(
    request: Request,
    sow_csrf: Optional[str] = Cookie(default=None, alias=CSRF_COOKIE_NAME),
    x_csrf_token: Optional[str] = Header(default=None, alias=CSRF_HEADER_NAME),
):
    """CSRF double-submit check for state-changing routes (2.6c).

    ENFORCED ONLY in hardened mode, so existing POST tests (which send no token)
    still pass. In hardened mode the ``X-CSRF-Token`` header must be present and
    equal to the ``sow_csrf`` cookie; otherwise HTTP 403.
    """
    if not auth_enabled():
        return
    if not sow_csrf or not x_csrf_token or not secrets.compare_digest(
        str(sow_csrf), str(x_csrf_token)
    ):
        raise HTTPException(status_code=403, detail="CSRF token missing or invalid")


# ── run/session ownership metadata (2.6a) ────────────────────────────────────
#
# Ownership lives in two places kept in sync: the in-memory ``active_runs`` entry
# (ProcessingStatus.owner_id) AND the persisted metadata.json. These helpers wrap
# the on-disk metadata so tests can inject an in-memory store via monkeypatch.


def _run_meta_path(run_id: str) -> Path:
    return Path(f"data/sessions/{run_id}/metadata.json")


def _read_run_meta(run_id: str) -> Optional[dict]:
    if not run_id or ".." in run_id or "/" in run_id or "\\" in run_id:
        return None
    p = _run_meta_path(run_id)
    if not p.exists():
        return None
    try:
        with open(p, "r") as f:
            return json.load(f)
    except (json.JSONDecodeError, ValueError, OSError):
        return None


def _write_run_meta(run_id: str, meta: dict) -> None:
    p = _run_meta_path(run_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f:
        json.dump(meta, f)


def _list_run_meta() -> list[dict]:
    sessions_dir = Path("data/sessions")
    if not sessions_dir.exists():
        return []
    out = []
    for d in sessions_dir.iterdir():
        if d.is_dir():
            meta = _read_run_meta(d.name)
            if meta is not None:
                out.append(meta)
    return out


def _run_owner_id(run_id: str) -> Optional[str]:
    """Return the owner id for ``run_id`` from active_runs or persisted metadata.

    Returns None when the run does not exist at all.
    """
    st = active_runs.get(run_id)
    if st is not None and getattr(st, "owner_id", None):
        return st.owner_id
    meta = _read_run_meta(run_id)
    if meta is not None:
        # Legacy runs (pre-hardening) have no owner_id — treat as default-owned so
        # single-user data stays reachable.
        return meta.get("owner_id", DEFAULT_USER_ID)
    if st is not None:
        # Live run with no persisted meta yet and no stamped owner → default.
        return getattr(st, "owner_id", None) or DEFAULT_USER_ID
    return None


def _assert_owned_or_404(run_id: Optional[str], user) -> None:
    """Ownership guard for data routes (2.6a).

    Only enforces when auth is ON. Raises 404 (NOT 403 — do not leak existence)
    when the run exists but is owned by someone else, or when it does not exist.
    A falsy ``run_id`` (no session selected) is left to the route's own handling.
    """
    if not auth_enabled():
        return
    if not run_id:
        return
    owner = _run_owner_id(run_id)
    if owner is None or owner != getattr(user, "id", None):
        raise HTTPException(status_code=404, detail="Not found")

@app.get("/")
def read_root():
    return FileResponse(FRONTEND_DIR / "index.html")

@app.get("/api/csrf")
def get_csrf_token(response: Response, user=Depends(get_current_user)):
    """2.6c: mint a CSRF token and set it as a readable double-submit cookie.

    The browser echoes this value back in the ``X-CSRF-Token`` header on
    state-changing requests. In single-user (auth OFF) mode a token is still
    issued for symmetry, but CSRF is not enforced so it is a no-op.
    """
    token = secrets.token_urlsafe(32)
    # Not HttpOnly on purpose: JS must read it to echo it in the header
    # (double-submit pattern). Secure/SameSite left to the deployment/proxy.
    response.set_cookie(
        CSRF_COOKIE_NAME, token, samesite="strict", httponly=False
    )
    return {"csrf_token": token}


@app.get("/healthz")
def healthz():
    """4.2d: lightweight liveness probe for containers / Render.

    Deliberately does NO DB / settings / network work — it must stay fast and
    dependency-free so an unhealthy backing store never fails the liveness check.
    """
    return {"status": "ok"}

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
def get_sessions(user=Depends(get_current_user)):
    if auth_enabled():
        # 2.6a: only surface runs owned by the caller (legacy runs w/o owner_id
        # belong to the default user, which is not a real principal when auth is
        # ON, so they are excluded here).
        sessions = [
            m for m in _list_run_meta()
            if m.get("owner_id") == getattr(user, "id", None)
        ]
        sessions.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return sessions

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
def get_tasks(session_id: Optional[str] = None, user=Depends(get_current_user)):
    _assert_owned_or_404(session_id, user)
    data = load_data(session_id)
    # Add current environment defaults to help UI
    data["env_defaults"] = {
        "jira_project_key": os.environ.get("JIRA_PROJECT_KEY", "PROJ"),
        "jira_server": os.environ.get("JIRA_SERVER"),
    }
    return data

@app.get("/api/status")
def get_status(session_id: Optional[str] = None, user=Depends(get_current_user)):
    if not session_id:
        return ProcessingStatus().model_dump()
    _assert_owned_or_404(session_id, user)
    return active_runs.get(session_id, ProcessingStatus()).model_dump()

class ProcessRequest(BaseModel):
    pdf_filename: str
    llm_mode: str  # "api" | "local" | "custom"
    jira_hierarchy: str # "flat" | "epic_task" | "story_subtask"
    jira_project_key: str
    skip_indexing: bool = False
    max_nodes: int = 200

def run_pipeline_task(req: ProcessRequest, run_id: str, owner_id: str = DEFAULT_USER_ID):
    status = ProcessingStatus()
    status.is_running = True
    status.run_id = run_id
    # 2.6a: stamp the creator so data routes can resolve ownership.
    status.owner_id = owner_id
    active_runs[run_id] = status

    # Save session metadata safely
    session_dir = Path(f"data/sessions/{run_id}")
    session_dir.mkdir(parents=True, exist_ok=True)
    with open(session_dir / "metadata.json", "w") as f:
        json.dump({
            "run_id": run_id,
            "filename": req.pdf_filename,
            "llm_mode": req.llm_mode,
            "owner_id": owner_id,
            "created_at": datetime.now(timezone.utc).isoformat()
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
            status.error_class = classify_exception(e)
            status.message = f"Error: {str(e)}"
        finally:
            status.is_running = False
            if run_id in active_orchestrators:
                del active_orchestrators[run_id]

@app.post("/api/cancel/{run_id}")
async def cancel_run(
    run_id: str,
    user=Depends(get_current_user),
    _csrf=Depends(enforce_csrf),
):
    _assert_owned_or_404(run_id, user)
    if run_id in active_orchestrators:
        active_orchestrators[run_id].stop_event.set()
        if run_id in active_runs:
            active_runs[run_id].message = "Cancelling..."
        return {"message": "Cancellation signal sent"}
    raise HTTPException(status_code=404, detail="Active run not found")

@app.delete("/api/sessions/{run_id}")
async def delete_session(
    run_id: str,
    user=Depends(get_current_user),
    _csrf=Depends(enforce_csrf),
):
    _assert_owned_or_404(run_id, user)
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

def _safe_upload_basename(raw_name: Optional[str]) -> str:
    """2.6b: sanitize an uploaded filename to a safe basename.

    Strips any directory components (both / and \\) and rejects traversal so a
    crafted name like ``../../etc/evil.pdf`` cannot escape UPLOAD_DIR. Returns
    just the final path segment (e.g. ``evil.pdf``).
    """
    name = (raw_name or "").replace("\\", "/")
    # Take the final path segment only — drops any leading dirs / traversal.
    base = os.path.basename(name).strip()
    # Defensive: after basename there should be no separators or traversal left.
    base = base.replace("/", "").replace("\\", "")
    if not base or base in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    return base


@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    user=Depends(get_current_user),
    _csrf=Depends(enforce_csrf),
):
    # 2.6b upload hardening (applies in BOTH modes — safe for valid PDFs):
    #   1. sanitize the filename to a safe basename (no path traversal)
    #   2. reject by extension AND content sniff (must be a real PDF) -> 400
    #   3. reject oversize uploads (> SOW_MAX_UPLOAD_MB) -> 413
    safe_name = _safe_upload_basename(file.filename)
    if not safe_name.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files allowed")

    contents = await file.read()

    max_bytes = SOW_MAX_UPLOAD_MB * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File too large (max {SOW_MAX_UPLOAD_MB} MB)",
        )

    # Content sniff: a genuine PDF starts with the %PDF- magic marker.
    if not contents.startswith(_PDF_MAGIC):
        raise HTTPException(status_code=400, detail="File is not a valid PDF")

    file_path = UPLOAD_DIR / safe_name
    with open(file_path, "wb") as buffer:
        buffer.write(contents)

    return {"filename": safe_name}

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
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((httpx.ConnectError, httpx.TimeoutException))
)
async def get_provider_models(
    provider_id: str,
    req: ModelDiscoveryRequest,
    user=Depends(get_current_user),
    _csrf=Depends(enforce_csrf),
):
    if provider_id not in PROVIDER_REGISTRY:
        raise HTTPException(status_code=404, detail="Unknown provider")

    settings = settings_manager.load()
    provider_settings = settings.get("providers", {}).get(provider_id, {})
    
    # Resolve the base URL (uses provided if present, else stored, else registry default)
    raw_base = resolve_provider_base(provider_id, req.base_url or provider_settings.get("base_url"))
    
    # Ensure Docker Host resolution for the FINAL string
    base_url = _ensure_docker_host(raw_base)
    
    if not base_url:
        raise HTTPException(status_code=400, detail="Base URL is required for this provider")
    
    logger.info(f"› Attempting model discovery for {provider_id} at {base_url}")
    
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
            resp = await client.get(url, headers=headers, timeout=15.0)
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
def save_settings(
    req: SettingsConfig,
    user=Depends(get_current_user),
    _csrf=Depends(enforce_csrf),
):
    try:
        settings = settings_manager.load()
    except RuntimeError as e:
        raise HTTPException(status_code=500, detail=str(e))
        
    provider = req.provider or settings.get("provider") or "openai"
    prev_provider = settings.get("provider")
    
    # Resolve and translate the base URL
    raw_base = resolve_provider_base(provider, req.base_url)
    base_url = _ensure_docker_host(raw_base)

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
async def start_processing(
    req: ProcessRequest,
    background_tasks: BackgroundTasks,
    user=Depends(get_current_user),
    _csrf=Depends(enforce_csrf),
):
    # Full-UUID run id (W1 1.5). The friendly filename is preserved separately in
    # the session metadata (see run_pipeline_task), so the UI still shows a
    # readable name; old timestamp-slug session dirs remain readable by id.
    run_id = make_run_id()

    # 2.6a: the created run is owned by the caller (default user in single-user
    # mode), stamped into both active_runs and the persisted metadata.
    owner_id = getattr(user, "id", DEFAULT_USER_ID)
    background_tasks.add_task(run_pipeline_task, req, run_id, owner_id)
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
def update_task(
    task_update: TaskUpdate,
    session_id: Optional[str] = None,
    user=Depends(get_current_user),
    _csrf=Depends(enforce_csrf),
):
    _assert_owned_or_404(session_id, user)
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
def add_task(
    req: AddTaskRequest,
    session_id: Optional[str] = None,
    user=Depends(get_current_user),
    _csrf=Depends(enforce_csrf),
):
    _assert_owned_or_404(session_id, user)
    data = load_data(session_id)
    if "tasks" not in data:
        data["tasks"] = []
    
    new_task = {
        "id": str(uuid.uuid4()),
        "title": req.title,
        "short_description": req.short_description,
        "status": "APPROVED",
        "confidence": 1.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "flags": [],
        "source_refs": []
    }
    
    data["tasks"].append(new_task)
    save_data(data, session_id)
    return {"message": "Task added successfully", "task": new_task}

@app.post("/api/tasks/approve_all")
def approve_all(
    session_id: Optional[str] = None,
    user=Depends(get_current_user),
    _csrf=Depends(enforce_csrf),
):
    _assert_owned_or_404(session_id, user)
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
    # STEP 5.3: force-push past the PushGate (flagged / DEGRADED-run tasks).
    override: bool = False

def _append_status_log(status: ProcessingStatus, msg: str) -> None:
    status.logs.append(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")
    if len(status.logs) > 50:
        status.logs.pop(0)


def _gate_push(approved_tasks, run_status, override: bool = False):
    """STEP 5.3: run approved tasks through PushGate before any Jira create.

    Returns ``(pushable_tasks, gated_results)``. ``pushable_tasks`` are cleared to
    push; ``gated_results`` are synthesized ``JiraPushResult``s for tasks the gate
    skipped (already pushed → idempotent success) or blocked (a blocking flag, or
    ANY task on a DEGRADED run → failed result carrying the reason). Never raises —
    a blocked task is surfaced as a failure, not a crash, so one bad task can't abort
    the whole push. ``override=True`` force-pushes flagged/DEGRADED tasks (skips of
    already-pushed tasks still hold — idempotency is not a quality gate).
    """
    by_id = {str(t.id): t for t in approved_tasks}
    try:
        result = PushGate().assert_pushable(approved_tasks, run_status, override=override)
    except PushBlocked as exc:
        result = exc.result

    gated: list[JiraPushResult] = []
    for decision in result.blocked:
        task = by_id.get(decision.task_id)
        if task is not None:
            gated.append(JiraPushResult(
                task_id=task.id, success=False,
                error=f"Blocked by PushGate: {decision.reason}",
            ))
    for decision in result.skipped:
        task = by_id.get(decision.task_id)
        if task is not None:
            gated.append(JiraPushResult(
                task_id=task.id, success=True,
                jira_issue_key=task.jira_issue_key,
                warning="Already pushed; skipped (idempotent)",
            ))
    return result.pushable, gated

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

            # STEP 5.3: gate approved tasks through PushGate before any Jira create —
            # skip already-pushed (idempotent), block flagged / DEGRADED-run tasks
            # (surfaced as failed results, never a crash) unless override.
            run_status = data.get("health")
            override = bool(getattr(req, "override", False)) if req else False
            pushable, gated_results = _gate_push(approved_tasks, run_status, override)

            status.progress = 0.2
            _append_status_log(
                status,
                f"PushGate: {len(pushable)} cleared, {len(gated_results)} gated"
                f"{' [override]' if override else ''}",
            )
            push_results = jira.push_tasks(pushable) if pushable else []
            results = push_results + gated_results

            # BE-1: persist the per-task JiraPushResult onto its task so the
            # outcome (issue key/url on success, error_class/message on failure)
            # survives a reload — previously `push_results` was built then
            # discarded, losing all per-task push detail on the next load.
            result_map = {str(r.task_id): r for r in results}
            for i, t in enumerate(tasks_data):
                res = result_map.get(str(t.get("id")))
                if res is not None:
                    tasks_data[i]["push_result"] = res.model_dump(mode="json")
                    if res.success:
                        tasks_data[i]["status"] = "PUSHED"

            save_data(data, session_id)

            total_passed = sum(1 for r in results if r.success)
            total_failed = len(results) - total_passed
            overall_success = total_failed == 0

            first_error = next((r.error for r in results if not r.success and r.error), None)
            first_error_class = next(
                (r.error_class for r in results if not r.success and r.error_class), None
            )
            message = f"Push complete. {total_passed} passed, {total_failed} failed."
            if first_error:
                message += f" First error: {first_error[:100]}..."

            status.progress = 1.0
            status.message = message
            _append_status_log(status, message)
            status.is_running = False
            if not overall_success:
                status.error = first_error or "Push completed with failures"
                status.error_class = first_error_class
        except Exception as e:
            status.error = str(e)
            status.error_class = classify_exception(e)
            status.message = f"Error: {str(e)}"
            _append_status_log(status, f"Error: {str(e)}")
            status.is_running = False

@app.post("/api/push")
def push_to_jira(
    req: Optional[PushRequest] = None,
    session_id: Optional[str] = None,
    user=Depends(get_current_user),
    _csrf=Depends(enforce_csrf),
):
    _assert_owned_or_404(session_id, user)
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

@app.post("/api/jira/test")
def test_jira_connection(
    user=Depends(get_current_user),
    _csrf=Depends(enforce_csrf),
):
    """BE-3: read-only Jira connection test.

    Validates credentials and reaches the configured server WITHOUT creating any
    issue — it only calls the SDK's ``myself()`` (whoami). Failures are mapped to
    the app ErrorClass taxonomy via ``classify_exception`` (401/403 →
    user_fixable, 429/5xx/timeout → transient) so the UI can react appropriately.
    Never raises: the outcome is returned as a classified JSON payload.
    """
    server = os.environ.get("JIRA_SERVER")
    email = os.environ.get("JIRA_EMAIL")
    token = os.environ.get("JIRA_API_TOKEN")

    if not (server and email and token):
        return {
            "success": False,
            "error": "Jira credentials are not configured (JIRA_SERVER / JIRA_EMAIL / JIRA_API_TOKEN).",
            "error_class": ErrorClass.USER_FIXABLE.value,
        }

    try:
        client = JIRA(server=server, basic_auth=(email, token))
        me = client.myself()  # read-only whoami — validates creds, no writes
        display = None
        if isinstance(me, dict):
            display = me.get("displayName") or me.get("emailAddress")
        return {"success": True, "user": display, "server": server}
    except Exception as e:
        error_class = classify_exception(e)
        logger.warning(f"Jira connection test failed ({error_class.value}): {e}")
        return {
            "success": False,
            "error": str(e),
            "error_class": error_class.value,
        }

if __name__ == "__main__":
    import uvicorn
    # 4.2b: bind all interfaces + honor $PORT so the container / Render can route
    # to the app. reload is disabled here (dev reload uses `make ui` /
    # `uvicorn ... --reload`); this __main__ path is the container entrypoint.
    uvicorn.run("ui.server:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))

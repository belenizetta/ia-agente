from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from core.orchestrator import Orchestrator
from typing import Dict, Any, Optional
from dotenv import load_dotenv
import uuid
import os
import tempfile
import json
import time

load_dotenv()

import logging
import traceback

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("main")

app = FastAPI(title="AI Code Orchestrator")
orchestrator = Orchestrator()


class ProcessRequest(BaseModel):
    prompt: str
    repos: Dict[str, str]
    tokens: Dict[str, str] = {}
    base_branch: str = "main"


class ConfirmRequest(BaseModel):
    job_id: str
    approved: bool
    feedback: Optional[str] = None


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _job_dir(job_id: str) -> str:
    return os.path.join(tempfile.gettempdir(), "ai-jobs", job_id)


def _record_error(job_id: str, error: str):
    """Escribe el error en el audit file para que el cliente pueda verlo."""
    try:
        out_dir = _job_dir(job_id)
        os.makedirs(out_dir, exist_ok=True)
        audit_file = os.path.join(out_dir, f"audit_{job_id}.jsonl")
        entry = {
            "timestamp": __import__("datetime").datetime.utcnow().isoformat() + "Z",
            "event": "job_end",
            "data": {"status": "error", "message": error},
        }
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def _read_audit(job_id: str):
    audit_file = os.path.join(_job_dir(job_id), f"audit_{job_id}.jsonl")
    if not os.path.exists(audit_file):
        return None, []
    events = []
    with open(audit_file, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except Exception:
                    pass
    return audit_file, events


def _run_process(job_id: str, prompt: str, repos: dict, tokens: dict, base_branch: str):
    try:
        orchestrator.process(
            prompt=prompt,
            repos=repos,
            tokens=tokens,
            base_branch=base_branch,
            dry_run=True,
            job_id=job_id,
        )
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error(f"[JOB {job_id}] Error en background task:\n{error_msg}")
        _record_error(job_id, error_msg)


def _run_execute(job_id: str, plan: dict, repos: dict, tokens: dict,
                 local_paths: dict, base_branch: str, project_info: dict):
    try:
        orchestrator.execute_plan(
            job_id=job_id,
            plan=plan,
            repos=repos,
            tokens=tokens,
            local_paths=local_paths,
            base_branch=base_branch,
            project_info=project_info,
        )
    except Exception as e:
        error_msg = f"{type(e).__name__}: {e}\n{traceback.format_exc()}"
        logger.error(f"[JOB {job_id}] Error en ejecución:\n{error_msg}")
        _record_error(job_id, error_msg)


# ------------------------------------------------------------------
# POST /process — analizar y planificar (siempre dry_run=True)
# ------------------------------------------------------------------

@app.post("/process")
def process(req: ProcessRequest, background_tasks: BackgroundTasks):
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    background_tasks.add_task(
        _run_process,
        job_id=job_id,
        prompt=req.prompt,
        repos=req.repos,
        tokens=req.tokens,
        base_branch=req.base_branch,
    )
    return {
        "job_id": job_id,
        "status": "processing",
        "message": f"Job iniciado. Consultá /jobs/{job_id}/status para ver el progreso.",
    }


# ------------------------------------------------------------------
# POST /confirm — aprobar o rechazar el plan
# ------------------------------------------------------------------

@app.post("/confirm")
def confirm(req: ConfirmRequest, background_tasks: BackgroundTasks):
    state = orchestrator.load_job_state(req.job_id)
    if not state:
        raise HTTPException(status_code=404, detail="Job no encontrado o plan no disponible aún.")

    if not req.approved:
        return {
            "job_id": req.job_id,
            "status": "rejected",
            "message": "Plan rechazado. No se realizaron cambios.",
        }

    plan = state["plan"]
    local_paths = state["local_paths"]
    repos = state["repos"]
    tokens = state["tokens"]
    base_branch = state["base_branch"]
    project_info = state["project_info"]

    background_tasks.add_task(
        _run_execute,
        job_id=req.job_id,
        plan=plan,
        repos=repos,
        tokens=tokens,
        local_paths=local_paths,
        base_branch=base_branch,
        project_info=project_info,
    )

    return {
        "job_id": req.job_id,
        "status": "executing",
        "message": f"Ejecutando plan. Consultá /jobs/{req.job_id}/status para el resultado.",
    }


# ------------------------------------------------------------------
# GET /jobs/{job_id}/plan — obtener el plan legible
# ------------------------------------------------------------------

@app.get("/jobs/{job_id}/plan")
def get_plan(job_id: str):
    state = orchestrator.load_job_state(job_id)
    if not state:
        raise HTTPException(status_code=404, detail="Job no encontrado o plan no disponible aún.")

    plan = state["plan"]
    tasks_summary = []
    for task in plan.get("tasks", []):
        tasks_summary.append({
            "servicio": task.get("service"),
            "accion": task.get("action"),
            "entidad": task.get("entity"),
            "archivos": task.get("files", []),
            "pasos": task.get("steps", []),
        })

    return {
        "job_id": job_id,
        "status": state.get("status"),
        "resumen": plan.get("summary", "Sin resumen disponible."),
        "tareas": tasks_summary,
    }


# ------------------------------------------------------------------
# GET /jobs/{job_id}/status — estado y eventos del job
# ------------------------------------------------------------------

@app.get("/jobs/{job_id}/status")
def get_status(job_id: str):
    audit_file, events = _read_audit(job_id)
    if audit_file is None:
        raise HTTPException(status_code=404, detail="Job no encontrado.")

    if not events:
        return {"job_id": job_id, "status": "processing", "events": []}

    last = events[-1]
    status = "processing"
    if last.get("event") == "job_end":
        status = last.get("data", {}).get("status", "done")

    return {
        "job_id": job_id,
        "status": status,
        "latest_event": last.get("event"),
        "all_events": events,
    }


# ------------------------------------------------------------------
# GET /jobs/{job_id}/stream — SSE para seguir el progreso en tiempo real
# ------------------------------------------------------------------

@app.get("/jobs/{job_id}/stream")
def stream_job(job_id: str):
    audit_file = os.path.join(_job_dir(job_id), f"audit_{job_id}.jsonl")

    def event_generator():
        sent = 0
        timeout = 300
        elapsed = 0
        while elapsed < timeout:
            if os.path.exists(audit_file):
                with open(audit_file, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                for line in lines[sent:]:
                    line = line.strip()
                    if line:
                        yield f"data: {line}\n\n"
                        sent = len(lines)
                        try:
                            event = json.loads(line)
                            if event.get("event") == "job_end":
                                return
                        except Exception:
                            pass
            time.sleep(1)
            elapsed += 1

    return StreamingResponse(event_generator(), media_type="text/event-stream")

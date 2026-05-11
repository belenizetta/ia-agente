from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
from core.orchestrator import Orchestrator
from typing import Dict, Any
from dotenv import load_dotenv
import uuid
import os
import tempfile
import json

load_dotenv()

class ProcessRequest(BaseModel):
    prompt: str
    repos: Dict[str, str]   # {"user-service": "...", "order-service": "..."}
    tokens: Dict[str, str] = {} # {"user-service": "TOKEN1", "order-service": "TOKEN2"}
    base_branch: str = "main"
    dry_run: bool = False   # Si es True, solo devuelve el plan sin aplicar cambios

class ConfirmRequest(BaseModel):
    job_id: str
    plan: Dict
    repos: Dict[str, str] = {}
    tokens: Dict[str, str] = {}
    local_paths: Dict[str, str]
    base_branch: str = "main"
    project_info: Dict = None


app = FastAPI()
orchestrator = Orchestrator()

@app.post("/process")
def process(req: ProcessRequest, background_tasks: BackgroundTasks):
    job_id = f"job_{uuid.uuid4().hex[:8]}"
    
    background_tasks.add_task(
        orchestrator.process,
        prompt=req.prompt,
        repos=req.repos,
        tokens=req.tokens,
        base_branch=req.base_branch,
        dry_run=req.dry_run,
        job_id=job_id
    )
    
    return {"job_id": job_id, "status": "processing", "message": "Job started in background. Poll /jobs/{job_id}/status for updates."}

@app.post("/confirm")
def confirm(req: ConfirmRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(
        orchestrator.execute_plan,
        job_id=req.job_id,
        plan=req.plan,
        repos=req.repos,
        tokens=req.tokens,
        local_paths=req.local_paths,
        base_branch=req.base_branch,
        project_info=req.project_info
    )
    return {"job_id": req.job_id, "status": "processing", "message": "Execution started in background. Poll /jobs/{job_id}/status for updates."}

@app.get("/jobs/{job_id}/status")
def get_job_status(job_id: str):
    base_tmp = tempfile.gettempdir()
    audit_file = os.path.join(base_tmp, "ai-jobs", job_id, f"audit_{job_id}.jsonl")
    
    if not os.path.exists(audit_file):
        raise HTTPException(status_code=404, detail="Job not found")
        
    events = []
    try:
        with open(audit_file, "r", encoding="utf-8") as f:
            for line in f:
                events.append(json.loads(line.strip()))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error reading audit file: {str(e)}")
        
    if not events:
        return {"job_id": job_id, "status": "processing", "events": []}
        
    last_event = events[-1]
    
    status = "processing"
    if last_event.get("event") == "job_end":
        status = last_event.get("data", {}).get("status", "done")
        
    return {
        "job_id": job_id,
        "status": status,
        "latest_event": last_event.get("event"),
        "all_events": events
    }

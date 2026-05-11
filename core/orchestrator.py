# core/orchestrator.py

import os
import shutil
import stat
import json
import uuid
import tempfile
from typing import Dict, Any
from git import Repo
from dotenv import load_dotenv
from llm.interpreter import PromptInterpreter
from planning.planner_multi import MultiServicePlanner
from llm.generator import CodeGenerator
from codebase.detect_multi import ProjectDetector
from core.audit import Auditor
from ci.runner import TestRunner
from git.exc import GitCommandError
from core.router import SemanticRouter
from pr.manager import PullRequestManager



class Orchestrator:

    def __init__(self):
        load_dotenv()
        self.generator = CodeGenerator()
        self.interpreter = PromptInterpreter(self.generator.llama)
        self.planner = MultiServicePlanner()
        self.detector = ProjectDetector()
        self.runner = TestRunner()
        self.pr_manager = PullRequestManager()
        # Auditor no se crea acá → se crea POR JOB
        self.auditor = None
        self.router = SemanticRouter(self.generator.llama)


    def _get_authenticated_url(self, url: str, token: str = None) -> str:
        """
        Inyecta tokens de GitHub o GitLab en la URL para clonado/push privado.
        Si se pasa un token específico, se usa ese en lugar de los globales de .env.
        """
        github_token = token if token else os.getenv("GITHUB_TOKEN")
        gitlab_token = token if token else os.getenv("GITLAB_TOKEN")
        
        # DEBUG: Verificar si los tokens están presentes (sin mostrarlos)
        if not github_token: print("[ORCHESTRATOR] WARN: GITHUB_TOKEN is not set in environment or request.")
        if not gitlab_token: print("[ORCHESTRATOR] WARN: GITLAB_TOKEN is not set in environment or request.")
        
        url_clean = url.strip().strip('`').strip()
        
        if "github.com" in url_clean and github_token:
            # https://token@github.com/...
            for proto in ["https://", "http://"]:
                if proto in url_clean and "@github.com" not in url_clean:
                    auth_url = url_clean.replace(f"{proto}github.com", f"{proto}{github_token}@github.com")
                    print(f"[ORCHESTRATOR] Auth URL (GitHub): {proto}***@github.com/{auth_url.split('github.com/')[-1]}")
                    return auth_url
        
        elif ("gitlab.com" in url_clean or "gitlab" in url_clean) and gitlab_token:
            # https://oauth2:token@gitlab.com/...
            for proto in ["https://", "http://"]:
                if proto in url_clean and "@" not in url_clean:
                    if "gitlab.com" in url_clean:
                        auth_url = url_clean.replace(f"{proto}gitlab.com", f"{proto}oauth2:{gitlab_token}@gitlab.com")
                        print(f"[ORCHESTRATOR] Auth URL (GitLab): {proto}oauth2:***@gitlab.com/{auth_url.split('gitlab.com/')[-1]}")
                        return auth_url
                    else:
                        # Caso self-hosted
                        parts = url_clean.split("://")
                        if len(parts) == 2:
                            auth_url = f"{parts[0]}://oauth2:{gitlab_token}@{parts[1]}"
                            print(f"[ORCHESTRATOR] Auth URL (GitLab Self-hosted): {parts[0]}://oauth2:***@{parts[1]}")
                            return auth_url
        
        print(f"[ORCHESTRATOR] No token applied for URL: {url_clean}")
        return url_clean


    # =====================================================================
    #                           MÉTODO PRINCIPAL
    # =====================================================================
    def process(self, prompt: str, repos: Dict[str, str], tokens: Dict[str, str] = None, base_branch: str = "main", dry_run: bool = False, job_id: str = None) -> Dict[str, Any]:
        # ============================================================
        # 0) Crear auditor por job
        # ============================================================
        job_id = job_id or f"job_{uuid.uuid4().hex[:8]}"
        base_tmp = tempfile.gettempdir()
        out_dir = os.path.join(base_tmp, "ai-jobs", job_id)

        os.makedirs(out_dir, exist_ok=True)
        self.auditor = Auditor(job_id=job_id, out_dir=out_dir)
        audit = self.auditor

        audit.record("job_start", {
            "job_id": job_id,
            "prompt": prompt,
            "repos": repos
        })

        # ============================================================
        # 1) Clonar TODOS los repositorios
        # ============================================================
        local_paths: Dict[str, str] = {}

        def safe_rmtree(path: str):
            if not os.path.exists(path):
                return
            def _onerror(func, p, exc):
                try:
                    os.chmod(p, stat.S_IWRITE)
                except Exception:
                    pass
                try:
                    func(p)
                except Exception:
                    pass
            shutil.rmtree(path, onerror=_onerror)

        for service, url in repos.items():
            # Obtener token específico si existe
            svc_token = (tokens or {}).get(service)
            
            # Sanitizar URL e inyectar tokens si existen
            auth_url = self._get_authenticated_url(url, svc_token)
            
            path = os.path.join(base_tmp, f"ai-repo-{service}")

            if os.path.exists(path):
                # Intentar reutilizar el repo existente para ganar velocidad
                try:
                    repo = Repo(path)
                    # Actualizar remote URL si el token cambió o no estaba
                    if repo.remotes.origin.url != auth_url:
                        repo.remotes.origin.set_url(auth_url)
                    
                    repo.remotes.origin.fetch()
                    # Reset duro a main/master o la rama base
                    # Ojo: si base_branch es dinámica, esto puede requerir ajuste.
                    # Asumimos que queremos limpiar el estado anterior.
                    repo.git.reset('--hard', 'HEAD') 
                    repo.git.clean('-fdx')
                    
                    # Checkout robusto
                    try:
                        repo.git.checkout(base_branch)
                    except GitCommandError:
                        try:
                            # Intentar detectar rama por defecto si la pedida falla
                            remote_head = repo.git.symbolic_ref('refs/remotes/origin/HEAD').split('/')[-1]
                            print(f"[ORCHESTRATOR] Fallback Reuse: Using detected branch '{remote_head}'")
                            repo.git.checkout(remote_head)
                        except:
                            print(f"[ORCHESTRATOR] WARN - Could not checkout '{base_branch}' or default. Staying on current.")

                    repo.git.pull("origin", repo.active_branch.name)
                    local_paths[service] = path
                    audit.record("repo_reuse", {"service": service, "path": path})
                    continue
                except Exception as e:
                    print(f"[ORCHESTRATOR] WARN - Reuse failed: {e}. Re-cloning...")
                    safe_rmtree(path)

            try:
                Repo.clone_from(auth_url, path)
            except GitCommandError as e:
                print(f"[ORCHESTRATOR] ERROR - Clone failed for {service}: {e.stderr}")
                return {"status": "error", "message": f"Clone failed for {service}: {e.stderr}"}
            except Exception as e:
                print(f"[ORCHESTRATOR] ERROR - Clone failed for {service}: {str(e)}")
                return {"status": "error", "message": f"Clone failed for {service}: {str(e)}"}
            
            local_paths[service] = path

            # LOG de depuración: ver qué archivos hay después de clonar
            cloned_files = os.listdir(path)
            print(f"[ORCHESTRATOR] Cloned {service} to {path}. Root files: {cloned_files}")

            audit.record("repo_clone", {
                "service": service,
                "repo_url": url,
                "local_path": path
            })

        # ============================================================
        # 1.1) MAPEO SEMÁNTICO (NUEVO)
        # ============================================================
        # Primero generamos un manifiesto de qué archivos hay en cada repo
        manifest = {}
        for svc, path in local_paths.items():
            manifest[svc] = []
            for root, _, filenames in os.walk(path):
                if ".git" in root or "__pycache__" in root: continue
                for f in filenames:
                    rel = os.path.relpath(os.path.join(root, f), path)
                    manifest[svc].append(rel)

        # Preguntamos al Router qué servicios son los 'afectados'
        affected_services = self.router.filter_services(prompt, manifest)
        print(f"[ORCHESTRATOR] Semantic Routing: Selected services {affected_services}")

        # Aseguramos que affected_services sea una lista, nunca None
        if not affected_services or not isinstance(affected_services, list):
            print("[ORCHESTRATOR] WARN - Router failed or returned None. Using all services as fallback.")
            affected_services = list(local_paths.keys())

        filtered_local_paths = {s: p for s, p in local_paths.items() if s in affected_services}

        audit.record("routing_info", {"affected_services": affected_services})

        # ============================================================
        # 2) ANALIZAR MULTIPROYECTO (lenguajes, frameworks, endpoints…)
        # ============================================================
        project_info = self.detector.analyze_repos(local_paths)

        audit.record("project_info", project_info)

        # ============================================================
        # 3) INTERPRETAR PROMPT COMPLETO (generalista + arquitectura)
        # ============================================================
        intent_info = self.interpreter.interpret(
            prompt=prompt,
            project_info=project_info
        )

        audit.record("intent_info", intent_info)

        # ============================================================
        # 4) PLANIFICACIÓN MULTISERVICIO (MultiServicePlanner)
        # ============================================================
        plan = self.planner.plan(
            prompt=prompt,
            project_info=project_info,
            intent_info=intent_info,
            involved_services=affected_services
        )

        audit.record("plan", plan)

        # ============================================================
        # 4.1) MAPEO DE CONTEXTO GLOBAL (NUEVO - EVITA ALUCINACIONES)
        # ============================================================
        # Leemos el contenido de TODOS los archivos mencionados en el plan
        # para tener una visión completa antes de empezar a modificar.
        global_context = {}
        for task in plan.get("tasks", []):
            svc = task["service"]
            svc_path = local_paths.get(svc)
            if not svc_path: continue
            
            for fp in task.get("files", []):
                abs_p = os.path.join(svc_path, fp)
                if os.path.exists(abs_p):
                    try:
                        with open(abs_p, "r", encoding="utf-8", errors="ignore") as f:
                            # Guardamos una versión resumida o completa para el LLM
                            content = f.read()
                            global_context[f"{svc}:{fp}"] = content
                    except: pass
        
        audit.record("global_context_mapped", {"files_count": len(global_context)})

        if dry_run:
            audit.record("job_end", {
                "status": "pending_approval",
                "summary": plan.get("summary"),
                "plan": plan,
                "project_info": project_info,
                "local_paths": local_paths
            })
            return {
                "job_id": job_id,
                "status": "pending_approval",
                "summary": plan.get("summary"),
                "plan": plan,
                "repos": repos,
                "tokens": tokens,
                "project_info": project_info,
                "local_paths": local_paths,
                "base_branch": base_branch,
                "global_context": global_context # Incluir contexto en el dry_run
            }

        return self.execute_plan(job_id, plan, repos, tokens, local_paths, base_branch, project_info, global_context)

    def execute_plan(self, job_id: str, plan: Dict, repos: Dict[str, str], tokens: Dict[str, str] = None, local_paths: Dict[str, str] = None, base_branch: str = "main", project_info: Dict = None, global_context: Dict = None) -> Dict[str, Any]:
        results = []
        audit = self.auditor or Auditor(job_id=job_id, out_dir=os.path.join(tempfile.gettempdir(), "ai-jobs", job_id))
        
        # Mapa para trackear cambios ya realizados y pasarlos como contexto
        accumulated_changes = {} 

        # Si no vienen los datos, intentar recuperarlos de los parámetros o del plan
        if not local_paths:
             return {"status": "error", "message": "Missing local_paths for execution"}
        
        prompt = plan.get("prompt_origin", "")
        global_context = global_context or {}

        # ============================================================
        # 5) EJECUTAR TODAS LAS TAREAS DEL PLAN
        # ============================================================
        # Generar UN ÚNICO nombre de rama para todo el job (consistencia multirepo)
        job_branch_name = f"ai/update-{uuid.uuid4().hex[:8]}"
        
        for task in plan["tasks"]:

            svc = task["service"]
            action = task["action"]
            files = task["files"]
            entity = task.get("entity")
            steps = task.get("steps", [])

            audit.record("task_start", {
                "service": svc,
                "action": action,
                "entity": entity,
                "files": files,
                "steps": steps
            })

            repo_path = local_paths[svc]
            repo = Repo(repo_path)

            # --- MEJORA: Checkout robusto de la rama base ---
            try:
                repo.git.checkout(base_branch)
            except GitCommandError:
                try:
                    remote_head = repo.git.symbolic_ref('refs/remotes/origin/HEAD').split('/')[-1]
                    print(f"[ORCHESTRATOR] Fallback: Using detected default branch '{remote_head}' instead of '{base_branch}'")
                    repo.git.checkout(remote_head)
                    base_branch = remote_head 
                except Exception as e:
                    print(f"[ORCHESTRATOR] WARN - Could not detect default branch: {e}. Staying on current branch.")

            # Crear o cambiar a la rama única del job en este repo
            try:
                # Si la rama ya existe en este repo (porque hubo otra tarea previa del mismo svc)
                repo.git.checkout(job_branch_name)
            except GitCommandError:
                # Si no existe, crearla
                repo.git.checkout("-b", job_branch_name)

            # ============================================================
            # 5A) Leer sólo los archivos relevantes para este servicio
            # ============================================================
            # --- CAMBIO CLAVE AQUÍ: PROCESAR ARCHIVO POR ARCHIVO ---
            all_service_changes = []
            
            # LÓGICA DE SOLO ANÁLISIS (SIN CAMBIOS)
            if action == "analyze_code":
                analysis_results = []
                for fp in files:
                    abs_path = os.path.join(repo_path, fp)
                    try:
                        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                            # Tomar solo los primeros 1000 caracteres para análisis rápido si son muchos archivos
                            analysis_results.append(f"FILE: {fp}\nCONTENT PREVIEW:\n{content[:1000]}")
                    except: continue
                
                # Generar el resumen final con el LLM
                summary_prompt = (
                    f"Analyze the following code from service '{svc}' and summarize its main objective and functionality.\n"
                    f"Code context:\n" + "\n---\n".join(analysis_results) + "\n\n"
                    f"User Request: {prompt}\n"
                    "Provide a concise summary in Spanish."
                )
                
                try:
                    res = self.generator.llama.create_chat_completion(
                        messages=[{"role": "user", "content": summary_prompt}],
                        temperature=0
                    )
                    final_summary = res["choices"][0]["message"]["content"]
                    
                    results.append({
                        "service": svc,
                        "status": "analyzed",
                        "analysis_summary": final_summary
                    })
                    audit.record("task_end", {"service": svc, "status": "analyzed", "summary": final_summary})
                    continue # No seguir con la lógica de archivos/commits
                except Exception as e:
                    print(f"[ORCHESTRATOR] Error during analysis: {e}")
                    results.append({"service": svc, "status": "error", "message": "Analysis failed"})
                    continue

            for fp in files:
                # 5A) Leer UN solo archivo
                abs_path = os.path.join(repo_path, fp)
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        single_file_content = f.read()
                except Exception as e:
                    audit.record("missing_file", {"file": fp, "error": str(e)})
                    continue

                # 5B) GENERAR CAMBIOS PARA ESTE ARCHIVO ESPECÍFICO
                # Al enviarle contexto global y cambios acumulados, evitamos alucinaciones.
                file_changes = self.generator.generate(
                    repo_dir=repo_path,
                    intent=action,
                    files={fp: single_file_content}, 
                    prompt=prompt,
                    context={
                        "frameworks": project_info.get("frameworks", []),
                        "languages": project_info.get("languages", []),
                        "plan_steps": steps,
                        "current_file": fp
                    },
                    global_context=global_context,
                    accumulated_changes=accumulated_changes,
                    service=svc
                )
                
                if file_changes:
                    all_service_changes.extend(file_changes)
                    # Guardar en cambios acumulados para el siguiente archivo
                    for ch in file_changes:
                        accumulated_changes[f"{svc}:{ch.path}"] = ch.content

            changes = all_service_changes

            audit.record("generated_changes", {
                "service": svc, 
                "changed": [c.path for c in all_service_changes]
            })

            # ============================================================
            # 5C) NO HACER NADA SI NO HAY CAMBIOS
            # ============================================================
            if not changes:
                audit.record("task_end", {
                    "service": svc,
                    "status": "no_changes"
                })
                results.append({
                    "service": svc,
                    "status": "no_changes"
                })
                continue

            # ============================================================
            # 5D) APLICAR CAMBIOS AL REPO
            # ============================================================
            written_files = []

            for ch in changes:
                abs_path = os.path.join(repo_path, ch.path)
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(ch.content)
                written_files.append(ch.path)

            # ============================================================
            # 5D-2) EJECUTAR TESTS (CI) - Solo para el servicio afectado
            # ============================================================
            ci_res = self.runner.run_all(repo_path, auditor=audit)
            ci_status_str = "PASSED" if ci_res["passed"] else "FAILED"
            print(f"[ORCHESTRATOR] Service: {svc} | Tests: {ci_status_str}")

            # ============================================================
            # 5E) COMMIT Y PUSH
            # ============================================================
            repo.git.add(".")
            
            commit_msg = f"AI Action: {action} (entity={entity})"
            if not ci_res["passed"]:
                commit_msg += " [CI FAILED]"

            repo.index.commit(commit_msg)
            try:
                repo.git.push("origin", job_branch_name)
            except GitCommandError as e:
                print(f"[ORCHESTRATOR] WARN - Push failed, branch already exists:")
                print(e.stderr)

            # ============================================================
            # 5F) CREAR PR/MR (NUEVO)
            # ============================================================
            repo_url = repos.get(svc)
            pr_res = {}
            if repo_url:
                pr_title = f"AI Update: {action} {entity or ''}"
                pr_body = f"Generated by AI Orchestrator.\nAction: {action}\nSteps:\n" + "\n".join(task.get("steps", []))
                
                # Obtener token específico para este servicio
                svc_token = (tokens or {}).get(svc)

                pr_res = self.pr_manager.create_pr(
                    repo_url=repo_url,
                    branch=job_branch_name,
                    base=base_branch,
                    title=pr_title,
                    body=pr_body,
                    token=svc_token
                )
                print(f"[ORCHESTRATOR] PR/MR Result: {pr_res}")

            audit.record("task_end", {
                "service": svc,
                "status": "done",
                "written_files": written_files,
                "branch": job_branch_name,
                "ci_status": ci_status_str,
                "pr_info": pr_res
            })

            results.append({
                "service": svc,
                "status": "done",
                "branch": job_branch_name,
                "changed_files": written_files,
                "pr_info": pr_res
            })

        # ============================================================
        # 6) FINAL DEL JOB — sin audit.finalize()
        # ============================================================

        audit.record("job_end", {"status": "done", "results": results})

        # Intentamos obtener la ruta del archivo de auditoría, si existe
        audit_path = None
        try:
            audit_path = audit.path()  # cambiar por audit.out_dir o la propiedad correcta si aplica
        except Exception:
            audit_path = None

        return {
            "status": "ok",
            "plan": plan,
            "results": results,
            "audit": audit_path
        }

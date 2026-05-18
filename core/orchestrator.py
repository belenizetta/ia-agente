import os
import logging
import shutil
import stat
import json
import uuid
import tempfile
from typing import Dict, Any, Optional, List
from git import Repo
from dotenv import load_dotenv

logger = logging.getLogger("orchestrator")
from llm.interpreter import PromptInterpreter
from llm.generator import CodeGenerator
from llm.claude_client import get_client
from planning.planner_multi import MultiServicePlanner
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
        self.interpreter = PromptInterpreter()
        self.planner = MultiServicePlanner()
        self.detector = ProjectDetector()
        self.runner = TestRunner()
        self.pr_manager = PullRequestManager()
        self.router = SemanticRouter()
        self.auditor = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_authenticated_url(self, url: str, token: str = None) -> str:
        github_token = token if token else os.getenv("GITHUB_TOKEN")
        gitlab_token = token if token else os.getenv("GITLAB_TOKEN")

        url_clean = url.strip().strip('`').strip()

        if "github.com" in url_clean and github_token:
            for proto in ["https://", "http://"]:
                if proto in url_clean and "@github.com" not in url_clean:
                    return url_clean.replace(f"{proto}github.com", f"{proto}{github_token}@github.com")

        elif ("gitlab.com" in url_clean or "gitlab" in url_clean) and gitlab_token:
            for proto in ["https://", "http://"]:
                if proto in url_clean and "@" not in url_clean:
                    if "gitlab.com" in url_clean:
                        return url_clean.replace(f"{proto}gitlab.com", f"{proto}oauth2:{gitlab_token}@gitlab.com")
                    parts = url_clean.split("://")
                    if len(parts) == 2:
                        return f"{parts[0]}://oauth2:{gitlab_token}@{parts[1]}"

        return url_clean

    def _job_dir(self, job_id: str) -> str:
        path = os.path.join(tempfile.gettempdir(), "ai-jobs", job_id)
        os.makedirs(path, exist_ok=True)
        return path

    def _find_files_in_repo(self, repo_path: str, class_names: List[str],
                            extensions: set) -> List[str]:
        """
        Busca archivos en el repo por nombre de clase o por contenido.
        Estrategia:
          1) Match exacto por nombre de archivo (EventListener.java)
          2) Nombre de clase contenido en el basename (CreditEventListener.java contiene EventListener)
          3) Fallback: buscar declaración de clase dentro del archivo (class EventListener {)
        """
        skip_dirs = {".git", "target", "build", "node_modules", "__pycache__", ".gradle"}
        # Solo nombres significativos (>5 chars) para evitar falsos positivos
        significant = [cn for cn in class_names if len(cn) > 5]

        by_ext: dict = {}
        name_matches = []

        for root, dirs, files in os.walk(repo_path):
            dirs[:] = [d for d in dirs if d not in skip_dirs]
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext not in extensions:
                    continue
                rel = os.path.relpath(os.path.join(root, f), repo_path)
                by_ext.setdefault(ext, []).append(rel)

                basename_lower = os.path.splitext(f)[0].lower()
                for cn in significant:
                    cn_lower = cn.lower()
                    # Solo: exacto O clase contenida en el nombre del archivo
                    # NO al revés (evita "Loan" matchear "LoanEventListener")
                    if cn_lower == basename_lower or cn_lower in basename_lower:
                        if rel not in name_matches:
                            name_matches.append(rel)
                        break

        # Log todos los .java para diagnóstico
        java_files = by_ext.get(".java", [])
        logger.info(f"[REPO SCAN] .java ({len(java_files)} total): {[os.path.basename(p) for p in java_files]}")

        if name_matches:
            logger.info(f"[REPO SEARCH] match por nombre: {name_matches}")
            return name_matches

        # Fallback: buscar declaración de clase DENTRO del contenido del archivo
        logger.info("[REPO SEARCH] Sin match por nombre. Buscando por contenido (class declaration)...")
        content_matches = []
        for rel in java_files:
            abs_path = os.path.join(repo_path, rel)
            try:
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                    head = fh.read(3000)  # Solo primeras 3000 chars
                for cn in significant:
                    # Busca "class EventListener" o "class EventListener " o "class EventListener{"
                    if f"class {cn}" in head:
                        logger.info(f"[REPO SEARCH] match por contenido: {rel} (contiene 'class {cn}')")
                        if rel not in content_matches:
                            content_matches.append(rel)
                        break
            except Exception:
                pass

        logger.info(f"[REPO SEARCH] content matches: {content_matches}")
        return content_matches

    def _select_files_for_task(self, task_description: str, candidate_files: List[str],
                                service: str) -> List[str]:
        """Selecciona archivos por nombre de clase extraído del texto — sin LLM."""
        import re as _re
        # Extraer nombres de clases Java/Python del texto (PascalCase)
        class_names = list(dict.fromkeys(
            _re.findall(r'\b([A-Z][a-zA-Z0-9]+)\b', task_description)
        ))

        selected = []
        for candidate in candidate_files:
            basename = os.path.splitext(os.path.basename(candidate.replace("\\", "/")))[0].lower()
            if any(cn.lower() == basename for cn in class_names):
                selected.append(candidate)

        if selected:
            logger.info(f"[FILE SELECTOR] {service}: seleccionados {selected}")
            return selected

        # Fallback: devolver los primeros 3 candidatos
        logger.info(f"[FILE SELECTOR] {service}: sin match exacto, usando primeros 3 de {len(candidate_files)} candidatos")
        return candidate_files[:3]

    def _save_job_state(self, job_id: str, state: Dict):
        """Persiste el estado del job (plan + paths) para que /confirm pueda leerlo."""
        state_file = os.path.join(self._job_dir(job_id), f"state_{job_id}.json")
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

    def load_job_state(self, job_id: str) -> Optional[Dict]:
        state_file = os.path.join(self._job_dir(job_id), f"state_{job_id}.json")
        if not os.path.exists(state_file):
            return None
        with open(state_file, "r", encoding="utf-8") as f:
            return json.load(f)

    def _build_global_context(self, plan: Dict, local_paths: Dict[str, str]) -> Dict[str, str]:
        global_context = {}
        for task in plan.get("tasks", []):
            svc = task["service"]
            svc_path = local_paths.get(svc)
            if not svc_path:
                continue
            for fp in task.get("files", []):
                abs_p = os.path.join(svc_path, fp)
                if os.path.exists(abs_p):
                    try:
                        with open(abs_p, "r", encoding="utf-8", errors="ignore") as f:
                            global_context[f"{svc}:{fp}"] = f.read()
                    except Exception:
                        pass
        return global_context

    # ------------------------------------------------------------------
    # PROCESO PRINCIPAL
    # ------------------------------------------------------------------

    def process(self, prompt: str, repos: Dict[str, str], tokens: Dict[str, str] = None,
                base_branch: str = "main", dry_run: bool = False, job_id: str = None) -> Dict[str, Any]:

        job_id = job_id or f"job_{uuid.uuid4().hex[:8]}"
        out_dir = self._job_dir(job_id)
        self.auditor = Auditor(job_id=job_id, out_dir=out_dir)
        audit = self.auditor

        audit.record("job_start", {"job_id": job_id, "prompt": prompt, "repos": repos})

        # ------------------------------------------------------------------
        # 1) Clonar repositorios
        # ------------------------------------------------------------------
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

        # Env para que git nunca pregunte credenciales — falla rápido en vez de colgar
        git_env = os.environ.copy()
        git_env["GIT_TERMINAL_PROMPT"] = "0"
        git_env["GIT_ASKPASS"] = "echo"

        for service, url in repos.items():
            svc_token = (tokens or {}).get(service)
            auth_url = self._get_authenticated_url(url, svc_token)
            path = os.path.join(tempfile.gettempdir(), f"ai-repo-{service}")

            audit.record("repo_cloning", {"service": service, "repo_url": url})

            if os.path.exists(path):
                try:
                    repo = Repo(path)
                    if repo.remotes.origin.url != auth_url:
                        repo.remotes.origin.set_url(auth_url)
                    with repo.git.custom_environment(**{"GIT_TERMINAL_PROMPT": "0", "GIT_ASKPASS": "echo"}):
                        repo.remotes.origin.fetch()
                        repo.git.reset("--hard", "HEAD")
                        repo.git.clean("-fdx")
                        try:
                            repo.git.checkout(base_branch)
                        except GitCommandError:
                            try:
                                remote_head = repo.git.symbolic_ref("refs/remotes/origin/HEAD").split("/")[-1]
                                repo.git.checkout(remote_head)
                            except Exception:
                                pass
                        repo.git.pull("origin", repo.active_branch.name)
                    local_paths[service] = path
                    audit.record("repo_reuse", {"service": service, "path": path})
                    continue
                except Exception as e:
                    print(f"[ORCHESTRATOR] Reuse failed: {e}. Re-clonando...")
                    safe_rmtree(path)

            try:
                Repo.clone_from(auth_url, path, env=git_env)
            except GitCommandError as e:
                error_msg = f"Clone fallido para '{service}': {e.stderr or str(e)}"
                audit.record("job_end", {"status": "error", "message": error_msg})
                raise RuntimeError(error_msg)
            except Exception as e:
                error_msg = f"Clone fallido para '{service}': {str(e)}"
                audit.record("job_end", {"status": "error", "message": error_msg})
                raise RuntimeError(error_msg)

            local_paths[service] = path
            audit.record("repo_clone", {"service": service, "repo_url": url, "local_path": path})

        # ------------------------------------------------------------------
        # 2) Routing semántico
        # ------------------------------------------------------------------
        manifest = {}
        for svc, path in local_paths.items():
            manifest[svc] = []
            for root, _, filenames in os.walk(path):
                if ".git" in root or "__pycache__" in root:
                    continue
                for fn in filenames:
                    manifest[svc].append(os.path.relpath(os.path.join(root, fn), path))

        affected_services = self.router.filter_services(prompt, manifest)
        if not affected_services or not isinstance(affected_services, list):
            affected_services = list(local_paths.keys())

        print(f"[ORCHESTRATOR] Servicios afectados: {affected_services}")
        audit.record("routing_info", {"affected_services": affected_services})

        # ------------------------------------------------------------------
        # 3) Detectar proyectos
        # ------------------------------------------------------------------
        project_info = self.detector.analyze_repos(local_paths)
        audit.record("project_info", project_info)

        # ------------------------------------------------------------------
        # 4) Interpretar intent
        # ------------------------------------------------------------------
        intent_info = self.interpreter.interpret(prompt=prompt, project_info=project_info)
        audit.record("intent_info", intent_info)

        # ------------------------------------------------------------------
        # 5) Planificar
        # ------------------------------------------------------------------
        plan = self.planner.plan(
            prompt=prompt,
            project_info=project_info,
            intent_info=intent_info,
            involved_services=affected_services,
        )
        audit.record("plan", plan)

        # ------------------------------------------------------------------
        # 6) Contexto global (contenido de archivos del plan)
        # ------------------------------------------------------------------
        global_context = self._build_global_context(plan, local_paths)
        audit.record("global_context_mapped", {"files_count": len(global_context)})

        if dry_run:
            state = {
                "job_id": job_id,
                "status": "pending_approval",
                "plan": plan,
                "local_paths": local_paths,
                "repos": repos,
                "tokens": tokens or {},
                "base_branch": base_branch,
                "project_info": project_info,
            }
            self._save_job_state(job_id, state)

            audit.record("job_end", {
                "status": "pending_approval",
                "summary": plan.get("summary"),
                "plan": plan,
            })
            return {
                "job_id": job_id,
                "status": "pending_approval",
                "summary": plan.get("summary"),
                "plan": plan,
            }

        return self.execute_plan(job_id, plan, repos, tokens, local_paths, base_branch, project_info, global_context)

    # ------------------------------------------------------------------
    # EJECUCIÓN DEL PLAN
    # ------------------------------------------------------------------

    def execute_plan(self, job_id: str, plan: Dict, repos: Dict[str, str],
                     tokens: Dict[str, str] = None, local_paths: Dict[str, str] = None,
                     base_branch: str = "main", project_info: Dict = None,
                     global_context: Dict = None) -> Dict[str, Any]:

        audit = self.auditor or Auditor(job_id=job_id, out_dir=self._job_dir(job_id))

        if not local_paths:
            return {"status": "error", "message": "Faltan local_paths para la ejecución"}

        if global_context is None:
            global_context = self._build_global_context(plan, local_paths)

        prompt = plan.get("prompt_origin", "")
        accumulated_changes: Dict[str, str] = {}
        results = []
        job_branch_name = f"ai/update-{uuid.uuid4().hex[:8]}"
        claude = get_client()

        for task in plan["tasks"]:
            svc = task["service"]
            action = task["action"]
            files = task["files"]
            entity = task.get("entity")
            steps = task.get("steps", [])

            audit.record("task_start", {"service": svc, "action": action, "entity": entity, "files": files})

            repo_path = local_paths.get(svc)
            if not repo_path:
                results.append({"service": svc, "status": "error", "message": "local_path no encontrado"})
                continue

            repo = Repo(repo_path)

            try:
                repo.git.checkout(base_branch)
            except GitCommandError:
                try:
                    remote_head = repo.git.symbolic_ref("refs/remotes/origin/HEAD").split("/")[-1]
                    repo.git.checkout(remote_head)
                    base_branch = remote_head
                except Exception:
                    pass

            try:
                repo.git.checkout(job_branch_name)
            except GitCommandError:
                repo.git.checkout("-b", job_branch_name)

            # ------------------------------------------------------------------
            # Acción: analizar código (sin commits)
            # ------------------------------------------------------------------
            if action == "analyze_code":
                previews = []
                for fp in files:
                    abs_path = os.path.join(repo_path, fp)
                    try:
                        with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                            previews.append(f"FILE: {fp}\n{f.read()[:1000]}")
                    except Exception:
                        continue

                analysis_prompt = (
                    f"Analyze the following code from service '{svc}' and summarize its purpose and functionality.\n"
                    f"Code:\n" + "\n---\n".join(previews) + f"\n\nUser Request: {prompt}\nRespond in Spanish."
                )
                summary = claude.complete(analysis_prompt)
                results.append({"service": svc, "status": "analyzed", "analysis_summary": summary})
                audit.record("task_end", {"service": svc, "status": "analyzed"})
                continue

            # ------------------------------------------------------------------
            # 1) Buscar directamente en el repo los archivos con nombres de clase
            #    mencionados en el prompt (EventListener.java, etc.)
            # ------------------------------------------------------------------
            CODE_EXTENSIONS = {
                ".java", ".py", ".ts", ".js", ".go", ".cs", ".kt", ".rb",
                ".php", ".rs", ".cpp", ".c", ".h", ".scala", ".swift",
                ".html", ".css", ".scss", ".xml", ".yaml", ".yml",
                ".json", ".properties", ".gradle", ".toml", ".sql",
            }

            import re as _re
            class_names_in_prompt = list(dict.fromkeys(
                _re.findall(r'\b([A-Z][a-zA-Z0-9]+)\b', prompt)
            ))

            # Buscar en el repo físico (más confiable que las listas del planner)
            direct_matches = self._find_files_in_repo(repo_path, class_names_in_prompt, CODE_EXTENSIONS)

            if direct_matches:
                logger.info(f"[REPO SEARCH] {svc}: encontrados {direct_matches}")
                existing_code_files = direct_matches
            else:
                # Fallback: usar la lista del planner
                existing_code_files = [
                    fp for fp in files
                    if os.path.splitext(fp)[1].lower() in CODE_EXTENSIONS
                    and os.path.exists(os.path.join(repo_path, fp))
                ]

            # 2) Selección por keyword (sin LLM)
            selected_files = self._select_files_for_task(
                task_description=f"{action}: {prompt}",
                candidate_files=existing_code_files,
                service=svc,
            )
            audit.record("files_selected", {"service": svc, "files": selected_files})

            # 3) Leer solo los archivos seleccionados
            batch_files: Dict[str, str] = {}
            for fp in selected_files:
                abs_path = os.path.join(repo_path, fp)
                try:
                    with open(abs_path, "r", encoding="utf-8") as f:
                        content = f.read()
                    if not content.strip():
                        logger.info(f"Skipping empty: {fp}")
                        continue
                    batch_files[fp] = content
                except Exception as e:
                    audit.record("missing_file", {"file": fp, "error": str(e)})

            if not batch_files:
                audit.record("task_end", {"service": svc, "status": "no_files"})
                results.append({"service": svc, "status": "no_files"})
                continue

            # ------------------------------------------------------------------
            # UNA SOLA llamada al LLM con todos los archivos del task
            # ------------------------------------------------------------------
            all_service_changes = self.generator.generate(
                repo_dir=repo_path,
                intent=action,
                files=batch_files,
                prompt=prompt,
                context={
                    "frameworks": project_info.get("frameworks", []),
                    "languages": project_info.get("languages", []),
                    "plan_steps": steps,
                },
                global_context=global_context,
                service=svc,
            )

            for ch in all_service_changes:
                accumulated_changes[f"{svc}:{ch.path}"] = ch.content

            audit.record("generated_changes", {"service": svc, "changed": [c.path for c in all_service_changes]})

            if not all_service_changes:
                audit.record("task_end", {"service": svc, "status": "no_changes"})
                results.append({"service": svc, "status": "no_changes"})
                continue

            # ------------------------------------------------------------------
            # Escribir archivos
            # ------------------------------------------------------------------
            written_files = []
            changed_paths = {ch.path for ch in all_service_changes}

            for ch in all_service_changes:
                abs_path = os.path.join(repo_path, ch.path)
                os.makedirs(os.path.dirname(abs_path), exist_ok=True)
                with open(abs_path, "w", encoding="utf-8") as f:
                    f.write(ch.content)
                written_files.append(ch.path)

            # Eliminar archivos originales renombrados (cuando el path cambió)
            for original_fp in files:
                if original_fp not in changed_paths:
                    original_abs = os.path.join(repo_path, original_fp)
                    # Si existe un cambio cuyo basename coincide con el original pero con otro nombre,
                    # es un rename → eliminar el archivo viejo
                    original_base = os.path.basename(original_fp)
                    for new_path in changed_paths:
                        if (os.path.dirname(new_path) == os.path.dirname(original_fp)
                                and os.path.basename(new_path) != original_base
                                and os.path.exists(original_abs)):
                            os.remove(original_abs)
                            audit.record("file_deleted", {"file": original_fp, "reason": f"renamed to {new_path}"})
                            print(f"[ORCHESTRATOR] Archivo original eliminado tras rename: {original_fp}")

            # ------------------------------------------------------------------
            # CI
            # ------------------------------------------------------------------
            ci_res = self.runner.run_all(repo_path, auditor=audit)
            ci_status = "PASSED" if ci_res["passed"] else "FAILED"
            print(f"[ORCHESTRATOR] {svc} | CI: {ci_status}")

            # ------------------------------------------------------------------
            # Commit y push
            # ------------------------------------------------------------------
            repo.git.add(".")
            commit_msg = f"AI Action: {action} (entity={entity})"
            if not ci_res["passed"]:
                commit_msg += " [CI FAILED]"
            repo.index.commit(commit_msg)

            try:
                repo.git.push("origin", job_branch_name)
            except GitCommandError as e:
                print(f"[ORCHESTRATOR] Push warn: {e.stderr}")

            # ------------------------------------------------------------------
            # Crear PR/MR
            # ------------------------------------------------------------------
            pr_res = {}
            repo_url = repos.get(svc)
            if repo_url:
                svc_token = (tokens or {}).get(svc)
                pr_res = self.pr_manager.create_pr(
                    repo_url=repo_url,
                    branch=job_branch_name,
                    base=base_branch,
                    title=f"AI Update: {action} {entity or ''}",
                    body=f"Generated by AI Orchestrator.\nAction: {action}\nSteps:\n" + "\n".join(steps),
                    token=svc_token,
                )
                print(f"[ORCHESTRATOR] PR: {pr_res}")

            audit.record("task_end", {
                "service": svc,
                "status": "done",
                "written_files": written_files,
                "branch": job_branch_name,
                "ci_status": ci_status,
                "pr_info": pr_res,
            })

            results.append({
                "service": svc,
                "status": "done",
                "branch": job_branch_name,
                "changed_files": written_files,
                "pr_info": pr_res,
            })

        audit.record("job_end", {"status": "done", "results": results})

        return {
            "status": "ok",
            "plan": plan,
            "results": results,
            "audit": audit.path(),
        }

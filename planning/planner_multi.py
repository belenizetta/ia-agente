# planning/planner_multi.py

import re
from typing import Dict, Any, List, Optional


class MultiServicePlanner:
    """
    Planner inteligente para arquitecturas multirepo / microservicios / monolito.

    Entrada principal:
      plan(prompt, project_info, intent_info)

    - project_info: salida de ProjectDetector
    - intent_info: salida de PromptInterpreter
    """

    def __init__(self):
        self.basic_actions = [
            "fix_bug",
            "modify_code",
            "create_feature",
            "create_crud",
            "update_dto",
            "update_contract",
            "add_tests",
            "refactor",
            "update_docs",
            "upgrade_version",
            "analyze_code",
        ]
        self.infra_keywords = ["db", "database", "docker", "config", "env", "setup", "connection", "postgres", "sql"]

    # ============================================================
    # Detectar servicios involucrados
    # ============================================================
    def detect_involved_services(
        self,
        prompt: str,
        project_info: dict,
        intent_info: dict
    ) -> List[str]:

        services = list(project_info.get("services", {}).keys())
        found: List[str] = []

        # 1) Target service explícito desde el interpreter
        tgt = intent_info.get("target_service")
        if tgt and tgt in services:
            found.append(tgt)

        # 2) Rutas mencionadas → fuzzy match con endpoints
        paths = intent_info.get("paths", []) or []

        for p in paths:
            p_norm = (
                p.replace("{id}", "")
                 .replace("{user_id}", "")
                 .replace("{order_id}", "")
                 .replace("users", "user")
                 .replace("orders", "order")
                 .strip("/")
            )

            for svc, info in project_info.get("services", {}).items():
                for ep in info.get("endpoints", []):
                    ep_path = ep.get("path", "") or ""
                    ep_norm = (
                        ep_path.replace("{id}", "")
                               .replace("{user_id}", "")
                               .replace("{order_id}", "")
                               .replace("users", "user")
                               .replace("orders", "order")
                               .strip("/")
                    )

                    if not ep_norm or not p_norm:
                        continue

                    if ep_norm in p_norm or p_norm in ep_norm:
                        if svc not in found:
                            found.append(svc)

        # 3) Entidades, fields y file_hints → heurística por archivos
        entities = intent_info.get("entities", []) or []
        fields = intent_info.get("fields", []) or []
        file_hints = intent_info.get("file_hints", []) or []
        hints = set(entities + fields + file_hints)

        if hints:
            for svc, info in project_info.get("services", {}).items():
                for f in info.get("files", []):
                    lname = f.lower()
                    if any(h.lower() in lname for h in hints):
                        if svc not in found:
                            found.append(svc)
                            break

        # 4) Keywords → nombre de servicio
        keywords = intent_info.get("keywords", []) or []
        for kw in keywords:
            for svc in services:
                if kw.lower().replace("_", "-") in svc.lower():
                    if svc not in found:
                        found.append(svc)

        # 4.5) Cambios de infraestructura/Config GLOBAL → aplicar a TODOS los servicios
        prompt_l = (prompt or "").lower()
        change_types = intent_info.get("change_type", []) or []
        
        # "db" NO debe disparar todos los servicios automáticamente (ej: cambiar un modelo es local)
        # Solo "infra" (docker) o "config" (variables globales) justifican un barrido total.
        global_triggers = ["config", "infra"] 
        
        infra_triggers = [
            "docker", "compose", "kubernetes", "k8s", "pipeline", "ci/cd"
        ]
        
        should_trigger_all = False
        
        # Si el tipo de cambio es explícitamente infra/config
        if any(ct in global_triggers for ct in change_types):
            should_trigger_all = True
            
        # O si hay palabras clave fuertes de infraestructura global
        if any(tok in prompt_l for tok in infra_triggers):
            should_trigger_all = True

        if should_trigger_all:
             return services

        # 5) Fallback
        if not found and len(services) == 1:
            found.append(services[0])

        return self._apply_primary_owner_filter(found, prompt, intent_info, project_info)

    def _apply_primary_owner_filter(self, candidates: List[str], prompt: str, intent_info: dict, project_info: dict) -> List[str]:
        # =========================================================
        # REGLA DE "SERVICIO DUEÑO" (PRIMARY OWNER)
        # =========================================================
        prompt_lower = prompt.lower()
        intents = intent_info.get("intents", []) or []
        change_types = intent_info.get("change_type", []) or []
        is_global_intent = any(ct in ["infra", "config"] for ct in change_types)
        
        # 1. FILTRO POR FRAMEWORK (MANDATORIO EN UPGRADES)
        # Si es un upgrade de un framework específico (ej: Angular), SOLO incluir servicios que lo usen
        if "upgrade_version" in intents or "version" in change_types:
            frameworks_in_prompt = []
            for fw in ["angular", "spring", "fastapi", "django", "react", "nestjs"]:
                if fw in prompt_lower:
                    frameworks_in_prompt.append(fw)
            
            if frameworks_in_prompt:
                filtered_by_fw = []
                all_services = project_info.get("services", {})
                for s_name, s_info in all_services.items():
                    svc_fws = [f.lower() for f in s_info.get("frameworks", [])]
                    # Log para depuración
                    print(f"[PLANNER] Checking service '{s_name}' frameworks: {svc_fws}")
                    if any(fw in svc_fws for fw in frameworks_in_prompt):
                        filtered_by_fw.append(s_name)
                
                if filtered_by_fw:
                    print(f"[PLANNER] Strict framework filter applied: {filtered_by_fw} (Prompt mentioned: {frameworks_in_prompt})")
                    return filtered_by_fw
                else:
                    print(f"[PLANNER] WARN: Prompt mentioned {frameworks_in_prompt} but no service seems to use them.")

        # 2. FILTRO POR ENTIDAD (DUEÑO DEL DATO)
        entities = intent_info.get("entities", []) or []
        entities_lower = [e.lower() for e in entities]
        
        # Buscar dueños en TODOS los servicios del proyecto
        all_services_list = list(project_info.get("services", {}).keys())
        owners = []
        
        for svc in all_services_list:
            svc_base = svc.lower().replace("-service", "").replace("_service", "")
            # Chequeo exacto o muy fuerte
            if svc_base in entities_lower:
                owners.append(svc)
        
        # Si no es global ni cross-service, y tenemos dueños claros, RESTRINGIMOS
        is_cross_service = any(k in prompt_lower for k in ["extraer servicio", "split", "comunicación", "eventos", "mensajería", "communication", "dependen", "integración", "consumer", "client"])
        
        if not is_global_intent and not is_cross_service:
            if owners:
                return owners

        # 3. FALLBACK: Si no hay filtros fuertes, usamos los candidatos originales
        return candidates
    
    # ============================================================
    # Buscar archivos (REEMPLAZAR COMPLETO)
    # ============================================================
    def find_relevant_files(
        self,
        svc: str,
        project_info: dict,
        intent_info: dict,
        prompt: str = "",
        limit: int = 10
    ) -> List[str]:
        # Aumentar límite para migraciones de versión
        ptxt = (prompt or "").lower()
        is_upgrade = any(k in ptxt for k in ["migrar", "upgrade", "actualizar versión", "subir versión"])
        if is_upgrade:
            limit = max(limit, 30)

        prioritized: List[str] = []
        
        # Keywords separadas para DB y DEPLOYMENT
        db_keywords = ["model", "entity", "schema", "migration", "alembic", "versions", "sql", "db"]
        deploy_keywords = ["docker", "compose", "k8s", "kubernetes", "helm", "pipeline", "ci", "cd"]
        config_keywords = [".env", "settings", "config", "yaml", "json"]

        def add_if(path: str):
            if not path:
                return
            
            p_lower = path.lower().replace("\\", "/")
            ptxt_l = (prompt or "").lower()
            
            # --- MEJORA: Excluir archivos de bloqueo y assets pesados ---
            # Estos archivos NUNCA deben ser modificados por la IA en una migración
            if any(lock in p_lower for lock in ["package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "gradle-wrapper.jar"]):
                return
            
            # Excluir assets estáticos y archivos binarios
            if any(p_lower.endswith(ext) for ext in [".ico", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ogg", ".mp3", ".wav", ".eot", ".ttf", ".woff", ".woff2"]):
                return
            
            # Excluir archivos de datos JSON (a menos que sean de config conocidos)
            if p_lower.endswith(".json") and not any(conf in p_lower for conf in ["package.json", "tsconfig", "angular.json", "composer.json", "project.json"]):
                # Si el archivo JSON está en una carpeta de assets o data, ignorar
                if "assets/" in p_lower or "data/" in p_lower or "mock/" in p_lower:
                    return

            # FILTROS DE RUIDO: Excluir __init__.py y alembic.ini por defecto
            # salvo que el prompt lo pida explícitamente.
            
            if p_lower.endswith("__init__.py"):
                # Solo incluir si se pide "export", "init", "expose" o explícitamente "__init__"
                if not any(k in ptxt_l for k in ["export", "init", "expose", "configur", "setup"]):
                    return
            
            if "alembic.ini" in p_lower:
                # Solo incluir si se pide config de alembic
                if not any(k in ptxt_l for k in ["config", "ini", "setup", "alembic"]):
                    return

            if "migrations" in p_lower and not is_db_change:
                 # Solo si el prompt pide explícitamente migraciones
                 if not any(k in ptxt_l for k in ["migration", "migracion", "upgrade", "downgrade"]):
                     return

            if "requirements.txt" in p_lower or "package.json" in p_lower or "poetry.lock" in p_lower or "pyproject.toml" in p_lower or "setup.cfg" in p_lower or "pom.xml" in p_lower or "build.gradle" in p_lower or "go.mod" in p_lower:
                 # Solo si el prompt pide dependencias
                 keywords = ["dependency", "dependencia", "install", "instalar", "paquete", "libreria", "library", "build", "packaging", "poetry", "versionado", "version", "maven", "gradle", "upgrade", "migrar", "actualizar"]
                 if not any(k in ptxt_l for k in keywords):
                     return
            
            if p_lower.endswith(".md"):
                 # Solo si el prompt pide documentación
                 if not any(k in ptxt_l for k in ["doc", "readme", "leeme", "instruccion"]):
                     return

            # Excluir entrypoints y configuración a menos que se solicite explícitamente
            if p_lower.endswith("main.py") or p_lower.endswith("app.py") or p_lower.endswith("config.py") or p_lower.endswith("settings.py") or "application.yml" in p_lower or "application.properties" in p_lower or "angular.json" in p_lower:
                if not is_infra_change and not is_upgrade and not any(k in ptxt_l for k in ["config", "settings", "entrypoint", "bootstrap", "main", "app", "configuracion", "ajuste", "properties", "yaml"]):
                    return

            if path not in prioritized:
                prioritized.append(path)

        info = project_info.get("services", {}).get(svc, {})
        files = info.get("files", []) or []
        
        # d) Patrones por lenguaje/framework (Java / Angular)
        svc_langs = info.get("languages", [])
        svc_frameworks = info.get("frameworks", [])
        
        is_java = "java" in svc_langs
        is_angular = "Angular" in (info.get("frameworks", {})) or "angular" in (info.get("frameworks", {}))

        # --- LÓGICA ESPECÍFICA PARA ANGULAR UPGRADE ---
        if is_angular and is_upgrade:
            candidates: List[str] = []
            # 1. Manifests y config (Mandatorios)
            angular_configs = ["package.json", "angular.json", "tsconfig.json", "tsconfig.app.json"]
            for f in files:
                if any(conf in f for conf in angular_configs):
                    candidates.append(f)
            
            # 2. Entrypoints y Root
            for f in files:
                fl = f.lower()
                if "main.ts" in fl or "app.config.ts" in fl or "app.module.ts" in fl:
                    if f not in candidates: candidates.append(f)
            
            # 3. Componentes clave (limitado para no exceder tokens)
            for f in files:
                if f.endswith(".ts") or f.endswith(".html"):
                    if "component" in f.lower():
                        if f not in candidates: candidates.append(f)
                if len(candidates) >= limit: break
            
            return candidates[:limit]

        # Extracción de validación de email → limitar a router y excluir migraciones
        ptxt = (prompt or "").lower()
        is_email_extract = any(tok in ptxt for tok in ["validación de correo", "email", "validator", "validators"]) and any(tok in ptxt for tok in ["extraer", "extract"])
        if is_email_extract:
            candidates: List[str] = []
            for f in files:
                fl = f.lower().replace("\\", "/")
                if "migrations" in fl or "alembic.ini" in fl:
                    continue
                if fl.endswith("__init__.py"):
                    continue
                if "api/router.py" in fl:
                    candidates.append(f)
            return candidates[:max(1, min(limit, len(candidates)))]

        # Renombrado de campo de Usuario → restringir a user-service
        ptxt = (prompt or "").lower()
        is_rename_user = (("renombrar el campo" in ptxt) or ("rename field" in ptxt)) and any(tok in ptxt for tok in ["usuario", "user"])
        if is_rename_user and "user-service" not in (svc or "").lower():
            return []

        # 1) Detectar intención de DB vs INFRA
        is_db_change = any(k in ptxt for k in ["database", "postgres", "sql", "db", "persistir", "campo", "tabla"])
        is_infra_change = any(k in ptxt for k in ["docker", "container", "deploy", "infra"])
        is_api_docs = any(k in ptxt for k in ["openapi", "swagger", "docs", "documentacion"])
        is_upgrade = any(k in ptxt for k in ["migrar", "upgrade", "actualizar versión", "subir versión"])

        # a) Si es cambio de DB, buscar modelos y migraciones
        if is_db_change:
            for f in files:
                path_norm = f.lower().replace("\\", "/")
                # Incluir modelos y migraciones
                if any(k in path_norm for k in db_keywords):
                    # EXCLUIR Dockerfile y requirements.txt a menos que sea explícito
                    if "docker" in path_norm or "requirements" in path_norm:
                        continue
                    add_if(f)

        # b) Si es cambio de Infra, buscar docker/k8s
        if is_infra_change:
            for f in files:
                path_norm = f.lower().replace("\\", "/")
                if any(k in path_norm for k in deploy_keywords):
                    add_if(f)
        
        # c) Si es Docs
        if is_api_docs:
            for f in files:
                if "openapi" in f.lower() or "swagger" in f.lower():
                    add_if(f)

        # d) Si es Upgrade de Versión, incluir TODOS los config relevantes y archivos de código fuente
        if is_upgrade:
            config_extensions = [".json", ".xml", ".yaml", ".yml", ".toml", ".gradle", ".properties", ".config", ".ts", ".html", ".scss"]
            # Priorizar archivos de configuración y manifiestos primero
            for f in files:
                f_l = f.lower()
                if any(cf in f_l for cf in ["package.json", "pom.xml", "angular.json", "tsconfig", "requirements.txt"]):
                    add_if(f)
            
            # Luego archivos de lógica que podrían tener breaking changes (limitado para evitar ruido)
            for f in files:
                f_l = f.lower()
                # En Angular, priorizar componentes y servicios, evitar assets estáticos
                if is_angular:
                    if any(ext in f_l for ext in [".component.ts", ".service.ts", ".module.ts"]):
                        add_if(f)
                else:
                    if any(ext in f_l for ext in [".py", ".java", ".ts"]):
                        add_if(f)

        # Keywords para capas arquitectónicas
        layer_keywords = []
        if is_java:
            layer_keywords = ["controller", "service", "repository", "entity", "dto", "model"]
        if is_angular:
            layer_keywords = ["component", "service", "module", "html", "css", "scss", "ts"]

        # 2) File hints, entities y keywords del intérprete
        hints = set(
            (intent_info.get("file_hints", []) or []) + 
            (intent_info.get("entities", []) or []) + 
            (intent_info.get("keywords", []) or [])
        )
        
        for f in files:
            # Filtrar archivos "peligrosos" si no fueron pedidos explícitamente por tipo de tarea
            f_lower = f.lower()
            if "dockerfile" in f_lower and not is_infra_change:
                continue
            if "openapi.json" in f_lower and not is_api_docs:
                continue
            if "alembic.ini" in f_lower and not is_db_change:
                continue
                
            if any(h.lower() in f_lower for h in hints):
                add_if(f)

        # 3) Candidatos estándar de código (si no es infra pura)
        if not is_infra_change:
            include_router = any(tok in ptxt for tok in ["router", "ruta", "api"])
            standard_patterns = ["model", "schema", "controller", "service"]
            
            # AGREGADO: Integrar keywords de Java/Angular
            if layer_keywords:
                standard_patterns.extend(layer_keywords)

            if include_router:
                standard_patterns += ["route", "api"]
            for f in files:
                f_lower = f.lower()
                if "dockerfile" in f_lower: continue 
                if "openapi.json" in f_lower and not is_api_docs: continue
                if f_lower.endswith("main.py") or f_lower.endswith("app.py") or f_lower.endswith("config.py") or f_lower.endswith("settings.py"): 
                    continue
                
                if any(sp in f_lower for sp in standard_patterns):
                    add_if(f)

        # 4) Completar hasta el límite con archivos del servicio
        for f in files:
            if len(prioritized) >= limit:
                break
            # Último filtro de seguridad
            if "dockerfile" in f.lower() and not is_infra_change: continue
            if "openapi.json" in f.lower() and not is_api_docs: continue
            fl = f.lower()
            if ("pyproject.toml" in fl or "setup.cfg" in fl) and not any(k in ptxt for k in ["dependency", "dependencia", "install", "instalar", "paquete", "libreria", "library", "build", "packaging", "poetry", "versionado", "version"]): 
                continue
            if (fl.endswith("main.py") or fl.endswith("app.py") or fl.endswith("config.py") or fl.endswith("settings.py")) and not is_infra_change:
                if not any(k in ptxt for k in ["config", "settings", "entrypoint", "bootstrap", "main", "app", "configuracion", "ajuste"]):
                    continue
            
            add_if(f)

        # Evitar tocar __init__.py en renombrados de Usuario
        if is_rename_user:
            prioritized = [f for f in prioritized if not f.lower().endswith("__init__.py")]

        return prioritized[:limit]

    # ============================================================
    # Construir pasos por acción
    # ============================================================
    def _build_steps_for_task(
        self,
        action: str,
        entity: Optional[str],
        change_type: Optional[list],
        arch_intent: Optional[list],
        semantic: dict,
        svc_info: dict = None,
        prompt: str = ""
    ) -> List[str]:

        ent = entity or "system"
        steps = []
        
        # Extraer versiones si es un upgrade
        current_ver = "unknown"
        target_ver = "latest"
        if svc_info and "frameworks" in svc_info:
            fws = svc_info["frameworks"]
            if "Angular" in fws: current_ver = fws["Angular"]
            elif "Spring Boot" in fws: current_ver = fws["Spring Boot"]
        
        # Detectar target version del prompt (ej: "Angular 21")
        ver_match = re.search(r"(?:angular|version|v)\s*(\d+)", prompt.lower())
        if ver_match:
            target_ver = ver_match.group(1)

        # --- LÓGICA ESPECÍFICA PARA BASE DE DATOS (PostgreSQL) ---
        # Si detectamos que el cambio es de base de datos, inyectamos pasos técnicos
        is_db_migration = any(kw in (ent.lower()) for kw in ["database", "db", "postgres"])
        
        if is_db_migration:
            steps = [
                "IDENTIFY the database engine configuration (SQLAlchemy/Tortoise/etc).",
                "CHANGE the connection string to PostgreSQL format: 'postgresql://user:pass@host:port/db'.",
                "ENSURE the 'psycopg2-binary' or 'asyncpg' driver is used in the engine creation.",
                "KEEP all existing business logic, routes, and models intact.",
                "DO NOT DELETE any existing function bodies or logic."
            ]
        elif action == "fix_bug":
            steps = [f"Identify root cause in {ent}", "Apply minimal fix", "Verify logic"]
        elif action == "upgrade_version":
            steps = [
                f"DETECTED CURRENT VERSION: {current_ver}",
                f"TARGET VERSION: {target_ver}",
                f"IDENTIFY specific breaking changes from v{current_ver} to v{target_ver}.",
                f"UPDATE dependencies in manifest files to version {target_ver}."
            ]
            # Pasos específicos para Angular
            if "angular" in (ent.lower()) or "frontend" in (ent.lower()):
                try:
                    curr_v = int(current_ver.split('.')[0]) if current_ver != "unknown" else 16
                    tgt_v = int(target_ver) if target_ver.isdigit() else 21
                    
                    if curr_v < 17 and tgt_v >= 17:
                        steps.append("MIGRATE to Standalone Components (standalone: true) and remove NgModules where possible.")
                        steps.append("UPDATE Control Flow syntax to new @if, @for block syntax.")
                    
                    if tgt_v >= 18:
                        steps.append("ADAPT to new hydration and zoneless detection patterns if applicable.")
                    
                    if tgt_v >= 19:
                        steps.append("CONVERT @Input/@Output to signal-based input()/output().")
                        steps.append("REPLACE ViewChild/ContentChild with signal-based viewChild()/contentChild().")
                    
                    if tgt_v >= 21:
                        steps.append("OPTIMIZE signals for Zoneless Change Detection.")
                        steps.append("USE new Router features for advanced data loading if applicable.")
                        steps.append("ENSURE full compatibility with latest TypeScript version.")
                except:
                    steps.append("APPLY standard Angular migration patterns for the requested version.")
                
                steps.append("ENSURE all project configurations (angular.json, tsconfig.json) match the new version standards.")
            else:
                steps.extend([
                    "REFACTOR code to maintain compatibility with the new version.",
                    "ENSURE all configuration files are updated to the new standard."
                ])
        elif action == "analyze_code":
            steps = [
                "READ the project structure and main entry points.",
                "IDENTIFY core business logic and key services.",
                "SUMMARIZE the main objective and functionality of the code.",
                "NO CODE CHANGES REQUIRED: Just provide a descriptive summary."
            ]
        else:
            # Default para otros casos
            steps = [
                f"Analyze current implementation of {ent}",
                "Apply requested changes following existing patterns",
                "Ensure no breaking changes in existing functionality"
            ]

        # Agregar contexto extra si es alta complejidad
        if semantic.get("complexity_hint") == "high":
            steps.append("Double-check all imports and dependency injections")

        # --- AUTO-GENERACIÓN DE PRUEBAS UNITARIAS ---
        # Si la acción implica cambios en la lógica de negocio, forzamos la creación de tests
        logic_actions = ["modify_code", "create_feature", "refactor", "create_crud"]
        if action in logic_actions and action != "analyze_code":
            steps.append("GENERATE unit tests to verify the new logic or refactor.")
            steps.append("ENSURE tests cover edge cases and main business rules.")
            steps.append("PLACE tests in the appropriate directory (e.g., tests/, src/test/) following project conventions.")

        return steps

    # ============================================================
    # Método principal
    # ============================================================
    def plan(
        self,
        prompt: str,
        project_info: dict,
        intent_info: dict,
        involved_services: List[str] = None  # <--- Nuevo parámetro
    ) -> Dict[str, Any]:

        # 1) Garantizar que existan intents. Si no hay, asumimos modify_code
        intents = intent_info.get("intents") or ["modify_code"]
        if isinstance(intents, str): 
            intents = [intents]
        intents = [i for i in intents if i]

        # Priorizar analyze_code si está presente para evitar ruidos de otros intents
        if "analyze_code" in intents:
            intents = ["analyze_code"]

        architecture = (
            intent_info.get("architecture")
            or project_info.get("architecture")
            or {"type": "microservices"}
        )

        # 2) Combinar detección heurística con la del Router para mayor robustez
        heuristic_services = self.detect_involved_services(prompt, project_info, intent_info)
        
        involved = []
        if involved_services:
            involved.extend(involved_services)
        if heuristic_services:
            involved.extend(heuristic_services)
        
        # Eliminar duplicados
        involved = list(set(involved))
        
        # Si no hay nada, fallback a todo (si es muy radical, el filtro de owner lo arreglará)
        if not involved:
             involved = list(project_info.get("services", {}).keys())

        # 3) Aplicar filtro de seguridad (Primary Owner) SIEMPRE
        # Le pasamos project_info para que pueda buscar el dueño globalmente si falta
        involved = self._apply_primary_owner_filter(involved, prompt, intent_info, project_info)

        tasks: List[Dict[str, Any]] = []

        # 4) Generar tareas para cada servicio involucrado
        for action in intents:
            action_norm = action if action in self.basic_actions else "modify_code"

            for svc in involved:
                # Buscar archivos relevantes (ahora incluye lógica de infraestructura)
                files = self.find_relevant_files(svc, project_info, intent_info, prompt)
                
                svc_info = project_info.get("services", {}).get(svc, {})

                # Intentar detectar la entidad principal para los pasos
                entity = None
                if intent_info.get("entities"):
                    entity = intent_info["entities"][0]
                elif any(k in prompt.lower() for k in ["database", "postgres", "sql"]):
                    entity = "Database Configuration"
                elif action_norm == "upgrade_version":
                    # Si es upgrade, la entidad es el framework
                    fws = svc_info.get("frameworks", {})
                    entity = list(fws.keys())[0] if fws else "Framework"

                steps = self._build_steps_for_task(
                    action_norm, 
                    entity, 
                    intent_info.get("change_type"), 
                    intent_info.get("architectural_intent"), 
                    intent_info.get("semantic_context") or {},
                    svc_info=svc_info,
                    prompt=prompt
                )

                tasks.append({
                    "service": svc,
                    "action": action_norm,
                    "entity": entity,
                    "files": files,
                    "steps": steps,
                    "change_type": intent_info.get("change_type"),
                    "architectural_intent": intent_info.get("architectural_intent"),
                    "frameworks": svc_info.get("frameworks", {}), # Inyectar versiones detectadas
                    "notes": {
                        "keywords": intent_info.get("keywords", []),
                        "prompt_origin": prompt
                    }
                })

        return {
            "tasks": tasks,
            "cross_dependencies": [],
            "architecture": architecture,
            "prompt_origin": prompt,
            "summary": self.generate_summary(tasks, prompt)
        }

    def generate_summary(self, tasks: List[Dict], prompt: str) -> str:
        """
        Genera un resumen humano y sintético de lo que la IA planea hacer.
        """
        if not tasks:
            return "No se detectaron tareas necesarias para este pedido."

        summary = [f"### 📋 Plan de Ejecución para: \"{prompt}\"\n"]
        
        # Agrupar por servicio para claridad
        services_map = {}
        for t in tasks:
            svc = t["service"]
            if svc not in services_map:
                services_map[svc] = []
            services_map[svc].append(t)

        for svc, svc_tasks in services_map.items():
            summary.append(f"**Servicio: `{svc}`**")
            for t in svc_tasks:
                action_raw = t["action"]
                action_clean = action_raw.replace("_", " ").capitalize()
                entity = t.get("entity") or "sistema"
                
                if action_raw == "analyze_code":
                    summary.append(f"- **Acción**: Análisis de Código")
                    summary.append(f"  - **Objetivo**: Generar un resumen del propósito y funcionamiento del servicio.")
                    summary.append(f"  - **Archivos a leer**: Se analizarán los archivos principales de negocio y configuración.")
                else:
                    summary.append(f"- **Acción**: {action_clean} sobre {entity}")
                    
                    if t["files"]:
                        files_str = ", ".join([f"`{f}`" for f in t["files"][:5]])
                        if len(t["files"]) > 5:
                            files_str += f" (y {len(t['files']) - 5} más)"
                        summary.append(f"  - **Archivos a modificar**: {files_str}")
                    else:
                        summary.append(f"  - ⚠️ **Atención**: No se encontraron archivos relevantes para esta acción.")
                
                if t["steps"]:
                    steps_preview = t["steps"][:3]
                    summary.append(f"  - **Pasos clave**: {'; '.join(steps_preview)}...")
            summary.append("")

        summary.append("---")
        summary.append("*¿Deseas proceder con estos cambios? Envía la confirmación para ejecutar.*")
        
        return "\n".join(summary)

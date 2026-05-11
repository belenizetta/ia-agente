
import os
import re
from typing import Dict, Any, List


class ProjectDetector:
    """
    Analiza repositorios (uno o varios) para detectar:

    - Lenguajes usados
    - Frameworks
    - Endpoints / rutas
    - Entrypoints
    - Archivos de configuración
    - Módulos significativos
    - Servicios internos (si existen múltiples módulos con lógica propia)
    - Arquitectura general (monolito / multimódulo / microservicios)
    """

    # ================================================================
    #           CONFIGURACIONES CLAVE
    # ================================================================
    ENTRYPOINT_PATTERNS = {
        "python": [r"if\s+__name__\s*==\s*['\"]__main__['\"]"],
        "javascript": [r"app\.listen", r"server\.listen", r"express\("],
        "typescript": [r"NestFactory", r"express\("],
        "java": [r"public\s+static\s+void\s+main"],
        "go": [r"package\s+main", r"func\s+main\("],
        "php": [r"<\?php"],
        "ruby": [r"class\s+\w+\s*<\s*ApplicationRecord"],
        "csharp": [r"static\s+void\s+Main"],
    }

    CONFIG_FILES = [
        "requirements.txt", "pyproject.toml",
        "package.json",
        "pom.xml", "build.gradle", "build.gradle.kts",
        "go.mod",
        "composer.json",
        "Gemfile",
        "Dockerfile", "docker-compose.yml",
        ".env"
    ]

    EXCLUDED_MODULE_DIRS = {
        "tests", "__pycache__", "node_modules",
        ".git", "env", "venv", ".idea", ".vscode"
    }

    CODE_EXTENSIONS = {
        ".py", ".js", ".ts", ".java", ".go",
        ".rb", ".php", ".cs"
    }


    # ================================================================
    #           MÉTODO PRINCIPAL
    # ================================================================
    def analyze_repos(self, repos: Dict[str, str]) -> Dict[str, Any]:
        """
        repos = {
            "user-service": "/tmp/user",
            "order-service": "/tmp/order"
        }
        """
        project_info = {
            "services": {},
            "languages": set(),
            "frameworks": set(),
            "config_files": {},
            "architecture": None,
            "relationships": []
        }

        # analizar cada repo por separado
        for name, path in repos.items():
            svc_info = self._analyze_single_repo(path)
            project_info["services"][name] = svc_info

            # acumular globales
            project_info["languages"].update(svc_info["languages"])
            project_info["frameworks"].update(svc_info["frameworks"])

        project_info["languages"] = list(project_info["languages"])
        project_info["frameworks"] = list(project_info["frameworks"])

        # detectar arquitectura global
        project_info["architecture"] = self._detect_architecture_type(project_info)
        
        # detectar relaciones entre servicios
        project_info["relationships"] = self._detect_service_relationships(project_info)

        return project_info

    # ================================================================
    #           ANÁLISIS DE UN REPO
    # ================================================================
    def _analyze_single_repo(self, root: str) -> Dict[str, Any]:
        languages = set()
        frameworks = {} # Cambiado de set() a dict para guardar versiones {name: version}
        endpoints = []
        entrypoints = []
        files_list = []
        
        # Recorrer archivos
        for dirpath, _, filenames in os.walk(root):
            # Obtener el path relativo al root para evitar colisiones con el path absoluto del sistema
            rel_dir = os.path.relpath(dirpath, root)
            if rel_dir == ".":
                parts = []
            else:
                # Normalizar separadores y dividir
                parts = rel_dir.replace("\\", "/").split("/")
            
            # Solo excluir si alguna parte del path RELATIVO está en la lista negra
            if any(p in self.EXCLUDED_MODULE_DIRS for p in parts):
                continue

            for f in filenames:
                ext = os.path.splitext(f)[1]
                full_path = os.path.join(dirpath, f)
                rel_path = os.path.relpath(full_path, root)
                files_list.append(rel_path)

                # Detección de lenguaje
                if ext in self.CODE_EXTENSIONS:
                    lang = self._map_extension_to_lang(ext)
                    if lang:
                        languages.add(lang)

                # Detección de frameworks / config
                if f in self.CONFIG_FILES:
                    try:
                        content = ""
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as fc:
                            content = fc.read().lower()
                            
                        if f == "requirements.txt" or f == "pyproject.toml":
                            if "fastapi" in content:
                                ver = re.search(r"fastapi[>=~\s]*([\d\.]+)", content)
                                frameworks["FastAPI"] = ver.group(1) if ver else "unknown"
                            if "flask" in content:
                                ver = re.search(r"flask[>=~\s]*([\d\.]+)", content)
                                frameworks["Flask"] = ver.group(1) if ver else "unknown"
                            if "django" in content:
                                ver = re.search(r"django[>=~\s]*([\d\.]+)", content)
                                frameworks["Django"] = ver.group(1) if ver else "unknown"
                            
                        if f == "package.json":
                            # Buscar Angular Core y otras dependencias clave
                            if "angular/core" in content or "angular" in content:
                                core_ver = re.search(r"\"@angular/core\"\s*:\s*\"[^\d]*([\d\.]+)\"", content)
                                frameworks["Angular"] = core_ver.group(1) if core_ver else "unknown"
                                
                                # Detectar si usa RxJS (común en Angular)
                                if "rxjs" in content:
                                    rxjs_ver = re.search(r"\"rxjs\"\s*:\s*\"[^\d]*([\d\.]+)\"", content)
                                    frameworks["RxJS"] = rxjs_ver.group(1) if rxjs_ver else "unknown"

                            if "react" in content:
                                ver = re.search(r"\"react\"\s*:\s*\"[^\d]*([\d\.]+)\"", content)
                                frameworks["React"] = ver.group(1) if ver else "unknown"
                            if "nestjs/core" in content or "nestjs" in content:
                                ver = re.search(r"\"@nestjs/core\"\s*:\s*\"[^\d]*([\d\.]+)\"", content)
                                frameworks["NestJS"] = ver.group(1) if ver else "unknown"
                            if "express" in content:
                                ver = re.search(r"\"express\"\s*:\s*\"[^\d]*([\d\.]+)\"", content)
                                frameworks["Express"] = ver.group(1) if ver else "unknown"
                            
                        if f == "pom.xml" or f == "build.gradle":
                            if "spring-boot" in content:
                                # Simplificado para XML/Gradle
                                ver = re.search(r"<version>([\d\.]+)</version>", content) if f == "pom.xml" else re.search(r"id\s+'org\.springframework\.boot'\s+version\s+'([\d\.]+)'", content)
                                frameworks["Spring Boot"] = ver.group(1) if ver else "unknown"
                            
                    except Exception:
                        pass

                # Detección de patrones específicos de Angular (Standalone vs Modules)
                if ext == ".ts" and ("Angular" in frameworks or "angular" in frameworks):
                    try:
                        with open(full_path, "r", encoding="utf-8", errors="ignore") as fa:
                            file_content = fa.read()
                            if "bootstrapApplication" in file_content:
                                frameworks["Angular Features"] = frameworks.get("Angular Features", []) + ["Standalone"]
                            if "@NgModule" in file_content:
                                frameworks["Angular Features"] = frameworks.get("Angular Features", []) + ["Modules"]
                            if "inject(" in file_content:
                                frameworks["Angular Features"] = frameworks.get("Angular Features", []) + ["Inject Function"]
                            if "signal(" in file_content:
                                frameworks["Angular Features"] = frameworks.get("Angular Features", []) + ["Signals"]
                    except:
                        pass

                # Detección heurística de entrypoints
                if f in ["main.py", "app.py", "index.js", "main.ts", "index.html", "app.component.ts"]:
                    entrypoints.append(rel_path)

        # Limpiar duplicados de Angular Features
        if "Angular Features" in frameworks:
            frameworks["Angular Features"] = list(set(frameworks["Angular Features"]))

        return {
            "root_path": root,
            "languages": list(languages),
            "frameworks": frameworks,       # Diccionario {nombre: version}
            "endpoints": endpoints,
            "entrypoints": entrypoints,
            "files": files_list
        }

    def _map_extension_to_lang(self, ext: str) -> str:
        map_ext = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".java": "java",
            ".go": "go",
            ".rb": "ruby",
            ".php": "php",
            ".cs": "csharp"
        }
        return map_ext.get(ext, "")

    def _detect_architecture_type(self, project_info: Dict[str, Any]) -> Dict[str, Any]:
        """
        Determina si es Monolito o Microservicios
        """
        services = project_info.get("services", {})
        count = len(services)
        
        if count > 1:
            return {
                "type": "microservices",
                "reason": f"{count} servicios detectados: {list(services.keys())}"
            }
        elif count == 1:
            # Podría ser un repo con muchos módulos (monorepo) o un monolito simple
            # Por ahora simplificamos: 1 repo = monolito (salvo que tenga docker-compose con varios services)
            return {
                "type": "monolith", 
                "reason": "Un solo repositorio detectado"
            }
        else:
            return {
                "type": "unknown",
                "reason": "No se detectaron servicios"
            }

    def _detect_service_relationships(self, project_info: Dict[str, Any]) -> List[Dict[str, str]]:
        """
        Detecta relaciones de comunicación entre servicios (A llama a B).
        Busca patrones de URLs, nombres de host o variables de entorno que apunten a otros servicios.
        """
        relationships = []
        services = project_info.get("services", {})
        service_names = list(services.keys())
        
        for source_name, source_data in services.items():
            files = source_data.get("files", [])
            root_path = source_data.get("root_path", "")
            
            if not root_path or not os.path.exists(root_path):
                continue

            # Solo buscar en código fuente y config
            relevant_files = [f for f in files if any(f.endswith(ext) for ext in self.CODE_EXTENSIONS) or f in self.CONFIG_FILES]
            
            found_targets = set()
            
            for rel_file in relevant_files:
                full_path = os.path.join(root_path, rel_file)
                try:
                    content = ""
                    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                        content = f.read().lower()
                    
                    # Buscar referencias a otros servicios
                    for target_name in service_names:
                        if target_name == source_name:
                            continue
                        
                        # Patrones simples:
                        # 1. "http://target-service"
                        # 2. "TARGET_SERVICE_URL"
                        # 3. "target-service" (en strings)
                        
                        # Normalizar nombre para búsqueda (ej: user-service -> user_service)
                        target_clean = target_name.replace("-", "_")
                        
                        patterns = [
                            f"http://{target_name}",
                            f"https://{target_name}",
                            f"{target_clean}_url",
                            f"{target_clean}_host",
                            f'"{target_name}"',
                            f"'{target_name}'"
                        ]
                        
                        if any(p in content for p in patterns):
                            found_targets.add(target_name)
                            
                except Exception:
                    pass
            
            for target in found_targets:
                relationships.append({
                    "source": source_name,
                    "target": target,
                    "type": "http_call" # Por defecto asumimos llamada HTTP
                })

        return relationships

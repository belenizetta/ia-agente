import os
import re
from typing import Dict, List, Any


class ProjectDetector:
    def __init__(self):
        pass

    # -------------------------------------------------------------
    # 1. DETECCIÓN DE LENGUAJES
    # -------------------------------------------------------------
    def detect_languages(self, repo_dir: str) -> List[str]:
        extensions = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".go": "go",
            ".rb": "ruby",
            ".java": "java",
            ".cs": "csharp",
        }

        found = set()

        for root, dirs, files in os.walk(repo_dir):
            for f in files:
                for ext, lang in extensions.items():
                    if f.endswith(ext):
                        found.add(lang)

        return list(found)

    # -------------------------------------------------------------
    # 2. DETECCIÓN DE FRAMEWORKS
    # -------------------------------------------------------------
    def detect_frameworks(self, repo_dir: str) -> List[str]:
        frameworks = []

        for root, dirs, files in os.walk(repo_dir):
            for f in files:
                path = os.path.join(root, f)

                if not os.path.isfile(path):
                    continue

                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as fp:
                        txt = fp.read()
                except Exception:
                    continue

                # Python
                if "from fastapi import" in txt or "import fastapi" in txt:
                    frameworks.append("fastapi")

                if "from flask import" in txt or "import flask" in txt:
                    frameworks.append("flask")

                if "django." in txt or "from django" in txt:
                    frameworks.append("django")

                # JavaScript / TypeScript
                if "express()" in txt or "require('express')" in txt:
                    frameworks.append("express")

                if "react" in txt.lower() or "from 'react'" in txt:
                    frameworks.append("react")

                if "next/router" in txt or "getServerSideProps" in txt:
                    frameworks.append("nextjs")

                if "<template" in txt and "<script" in txt:
                    frameworks.append("vue")

                # Go
                if 'github.com/gin-gonic/gin' in txt:
                    frameworks.append("gin")

                if 'github.com/gofiber/fiber' in txt:
                    frameworks.append("fiber")

                # Ruby
                if "Rails.application" in txt or "ActiveRecord" in txt:
                    frameworks.append("rails")

        return list(dict.fromkeys(frameworks))

    # -------------------------------------------------------------
    # 3. DETECCIÓN DE ARQUITECTURA
    # -------------------------------------------------------------
    def detect_architecture(self, repo_dir: str, frameworks: List[str]) -> str:

        # BACKEND + FRONTEND
        if "react" in frameworks or "nextjs" in frameworks or "vue" in frameworks:
            if any(f in frameworks for f in ["fastapi", "django", "flask", "express"]):
                return "backend_frontend_split"

        # FASTAPI → REST API Standard
        if "fastapi" in frameworks:
            return "rest_api"

        # Django → MVC
        if "django" in frameworks:
            return "mvc"

        # Flask → Microservicio o API simple
        if "flask" in frameworks:
            return "microservice_or_rest"

        # Express + Vue/React
        if "express" in frameworks:
            return "node_rest_api"

        return "unknown"

    # -------------------------------------------------------------
    # 4. DETECTAR ENDPOINTS API
    # -------------------------------------------------------------
    def detect_endpoints(self, repo_dir: str) -> List[str]:
        endpoints = []

        for root, dirs, files in os.walk(repo_dir):
            for f in files:
                if not f.endswith(".py") and not f.endswith(".js") and not f.endswith(".ts"):
                    continue

                try:
                    txt = open(os.path.join(root, f), "r", encoding="utf-8", errors="ignore").read()
                except:
                    continue

                # FastAPI
                matches = re.findall(r'@(app|router)\.(get|post|put|delete)\(["\']([^"\']+)["\']\)', txt)
                for m in matches:
                    endpoints.append(f"{m[1].upper()} {m[2]}")

                # Express
                matches = re.findall(r'app\.(get|post|put|delete)\(["\']([^"\']+)["\']', txt)
                for m in matches:
                    endpoints.append(f"{m[0].upper()} {m[1]}")

        return list(dict.fromkeys(endpoints))

    # -------------------------------------------------------------
    # 5. DETECTAR SI EL PROYECTO TIENE CRUDs
    # -------------------------------------------------------------
    def detect_crud(self, endpoints: List[str]) -> bool:
        patterns = ["POST", "GET", "PUT", "DELETE"]
        count = sum(1 for ep in endpoints if any(p in ep for p in patterns))
        return count >= 3  # simple criterio

    # -------------------------------------------------------------
    # 6. DETECTAR MICROSERVICIOS
    # -------------------------------------------------------------
    def detect_microservices(self, repo_dir: str) -> bool:
        # criterio: múltiples carpetas con APIs separadas
        microservice_markers = ["service", "services", "ms-", "micro", "gateway"]
        dirs = next(os.walk(repo_dir))[1]
        return any(d.lower() in microservice_markers for d in dirs)

    # -------------------------------------------------------------
    # SALIDA COMPLETA
    # -------------------------------------------------------------
    def analyze(self, repo_dir: str) -> Dict[str, Any]:
        languages = self.detect_languages(repo_dir)
        frameworks = self.detect_frameworks(repo_dir)
        arc = self.detect_architecture(repo_dir, frameworks)
        endpoints = self.detect_endpoints(repo_dir)
        crud = self.detect_crud(endpoints)
        ms = self.detect_microservices(repo_dir)

        return {
            "languages": languages,
            "frameworks": frameworks,
            "architecture": arc,
            "endpoints": endpoints,
            "has_crud": crud,
            "is_microservice": ms
        }

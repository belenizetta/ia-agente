import re
from typing import Dict, List

# Palabras clave que indican que el cambio es solo de backend (Java/Spring/Python/etc.)
_BACKEND_SIGNALS = {
    "spring", "java", "maven", "gradle", "hibernate", "jpa", "bean", "autowired",
    "component", "service", "repository", "controller", "eventlistener",
    "nullpointerexception", "javamail", "mailsender", "async", "slf4j",
    "creditapprovedevent", "loaneventlistener", "emailnotificationservice",
    "fastapi", "django", "flask", "sqlalchemy", "pytest", "pydantic",
    "nestjs", "express", "nodejs", "node.js",
    ".java", ".py", ".go", ".rs", ".rb", ".php",
}

# Palabras clave que indican que el cambio es solo de frontend (Angular/React/etc.)
_FRONTEND_SIGNALS = {
    "angular", "react", "vue", "typescript", "ngmodule", "component.ts",
    "rxjs", "observable", "subscribe", "ngonit", "ngoninit",
    "template", "html", "scss", "css", "npm", "webpack", "vite",
    ".component.ts", ".module.ts", ".service.ts",
}


_BACKEND_SVC_KEYS = ("backend", "back", "api", "server", "service", "core")
_FRONTEND_SVC_KEYS = ("frontend", "front", "web", "ui", "client", "app")


class SemanticRouter:
    def __init__(self):
        pass  # Sin LLM — routing por keywords

    def filter_services(self, prompt: str, project_manifest: Dict[str, List]) -> List[str]:
        prompt_lower = prompt.lower()
        all_services = list(project_manifest.keys())

        # Separar servicios en categorías por su nombre (matching parcial)
        backend_svcs = [s for s in all_services if any(k in s.lower() for k in _BACKEND_SVC_KEYS)]
        frontend_svcs = [s for s in all_services if any(k in s.lower() for k in _FRONTEND_SVC_KEYS)]
        other_svcs = [s for s in all_services if s not in backend_svcs and s not in frontend_svcs]

        backend_score = sum(1 for kw in _BACKEND_SIGNALS if kw in prompt_lower)
        frontend_score = sum(1 for kw in _FRONTEND_SIGNALS if kw in prompt_lower)

        # Bonus: si el prompt menciona explícitamente el nombre de un servicio
        for svc in frontend_svcs:
            if svc.lower() in prompt_lower:
                frontend_score += 2
        for svc in backend_svcs:
            if svc.lower() in prompt_lower:
                backend_score += 2

        print(f"[ROUTER] backend_score={backend_score}, frontend_score={frontend_score}")

        # Decisión clara: solo backend
        if backend_score > 0 and frontend_score == 0:
            result = backend_svcs or other_svcs or all_services
            print(f"[ROUTER] → Solo backend: {result}")
            return result

        # Decisión clara: solo frontend
        if frontend_score > 0 and backend_score == 0:
            result = frontend_svcs or other_svcs or all_services
            print(f"[ROUTER] → Solo frontend: {result}")
            return result

        # Ambos con señales: el que tenga mayor score
        if frontend_score > backend_score:
            result = frontend_svcs or other_svcs or all_services
            print(f"[ROUTER] → Frontend (score mayor): {result}")
            return result
        if backend_score > frontend_score:
            result = backend_svcs or other_svcs or all_services
            print(f"[ROUTER] → Backend (score mayor): {result}")
            return result

        # Empate o sin señales: usar todos
        print(f"[ROUTER] → Todos los servicios: {all_services}")
        return all_services

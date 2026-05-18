# planning/planner.py

import re
from typing import Dict, Any, List


class Planner:
    def __init__(self):
        self.zs = None
        try:
            from transformers import pipeline
            self.zs = pipeline(
                "zero-shot-classification",
                model="joeddav/xlm-roberta-large-xnli"
            )
        except Exception:
            self.zs = None

        # Etiquetas para clasificación de intención
        self.labels = [
            "fix_bug",
            "modify_code",
            "create_feature",
            "create_crud",
            "add_tests",
            "optimize",
            "refactor",
            "update_docs"
        ]

    # =============================================================
    # NUEVO: DETECCIÓN DE MICROSERVICIO OBJETIVO
    # =============================================================
    def detect_service_from_prompt(self, prompt: str, repos: Dict[str, str]) -> str | None:
        """
        Intenta determinar el microservicio mencionado en el prompt.

        Estrategias:
        1. Coincidencia exacta del nombre del repo
        2. Coincidencia parcial (order, billing, payments, users, auth, etc)
        3. Detección de entidades tipo: "en user-service", "del servicio pago"
        4. Detección de rutas: "/users", "/orders"
        """

        lower = prompt.lower()

        # 1. Coincidencia exacta
        for name in repos.keys():
            if name.lower() in lower:
                return name

        # 2. Coincidencia parcial por palabras comunes
        tokens = re.findall(r"[a-zA-Z]+", lower)
        for token in tokens:
            for name in repos.keys():
                if token in name.lower():
                    return name

        # 3. Frases naturales tipo “el servicio de usuarios”
        svc_map = {
            "usuario": "user-service",
            "usuarios": "user-service",
            "auth": "auth-service",
            "orden": "order-service",
            "ordenes": "order-service",
            "pedido": "order-service",
            "factura": "billing-service",
            "pago": "payment-service",
        }

        for key, svc in svc_map.items():
            if key in lower and svc in repos:
                return svc

        # 4. Rutas REST → "/users" → user-service
        path_match = re.search(r"/([a-z0-9_-]+)", lower)
        if path_match:
            entity = path_match.group(1)
            for name in repos:
                if entity in name.lower():
                    return name

        # No lo encontró
        return None

    # =============================================================
    # PLANIFICACIÓN PRINCIPAL
    # =============================================================
    def plan(self, prompt: str, intent_info: Dict[str, Any], files: List[str]) -> Dict[str, Any]:
        lower = prompt.lower()

        # 1. Clasificación por Zero-Shot
        action = None
        if self.zs:
            try:
                res = self.zs(lower, candidate_labels=self.labels, multi_label=False)
                if res and "labels" in res:
                    action = res["labels"][0]
            except Exception:
                action = None

        # 2. Reglas para CRUD
        resource = None
        if "crud" in lower:
            action = "create_crud"
            m = re.search(r"crud\s+(?:de|para)\s+([a-zA-Z_][a-zA-Z0-9_]*)", lower)
            resource = m.group(1) if m else None

        # 3. Si el zero-shot no resolvió
        if action is None:
            action = intent_info.get("intent", "unknown")

        # 4. Flags para commit y PR
        do_commit = any(k in lower for k in [
            "commit", "commitear", "hacer el commit"
        ])

        create_pr = any(k in lower for k in [
            "pull request", "pr", "crear pr", "abrí un pr"
        ])

        return {
            "action": action,
            "resource": resource,
            "do_commit": do_commit,
            "create_pr": create_pr
        }

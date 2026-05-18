import re
from typing import Dict, Any, List

# Mapeo de palabras clave a intents — sin LLM, sin tokens
_INTENT_RULES = [
    ("fix_bug",        ["corregir", "fix", "bug", "error", "exception", "nullpointer",
                        "falla", "crash", "problema", "issue", "arreglar", "solucionar",
                        "no funciona", "lanza", "throws"]),
    ("refactor",       ["refactor", "renombrar", "rename", "reorganizar", "restructur",
                        "mover", "extraer", "extract", "separar"]),
    ("modify_code",    ["reemplazar", "replace", "agregar", "add", "añadir", "implementar",
                        "implement", "modificar", "modify", "cambiar", "change", "actualizar"]),
    ("upgrade_version",["migrar versión", "upgrade", "actualizar versión", "subir versión",
                        "migration", "migrate"]),
    ("create_feature", ["crear", "create", "nueva funcionalidad", "new feature", "nuevo endpoint"]),
    ("add_tests",      ["test", "prueba", "unittest", "junit", "pytest"]),
    ("analyze_code",   ["analizar", "analyze", "explicar", "explain", "describir", "describe",
                        "qué hace", "what does", "resumen", "summary"]),
]

_CHANGE_TYPE_RULES = [
    ("db",      ["database", "sql", "table", "migration", "entity", "schema", "postgres"]),
    ("api",     ["endpoint", "route", "controller", "rest", "api", "http"]),
    ("logic",   ["service", "business", "logic", "inject", "bean", "spring", "event"]),
    ("config",  ["config", "properties", "yaml", "docker", "environment"]),
    ("version", ["upgrade", "version", "migrate", "migration"]),
]

_JAVA_CLASS_RE = re.compile(
    r'\b([A-Z][a-zA-Z0-9]+(?:Service|Controller|Repository|Listener|Event|Handler|'
    r'Config|Factory|Manager|Component|Entity|DTO|Dto|Mapper|Util|Helper))\b'
)


def _score_rules(text: str, rules: list) -> str:
    """Retorna la categoría con más coincidencias de keywords."""
    best, best_score = rules[0][0], 0
    for category, keywords in rules:
        score = sum(1 for kw in keywords if kw in text)
        if score > best_score:
            best, best_score = category, score
    return best


class PromptInterpreter:
    def __init__(self):
        pass  # Sin LLM

    def interpret(self, prompt: str, project_info: Dict[str, Any]) -> Dict[str, Any]:
        text = prompt.lower()

        # Intent principal
        primary_intent = _score_rules(text, _INTENT_RULES)

        # Intents secundarios (todos los que tienen al menos 1 match)
        intents = list({primary_intent} | {
            cat for cat, kws in _INTENT_RULES if any(kw in text for kw in kws)
        })
        # analyze_code solo si no hay otro intent más concreto
        if primary_intent != "analyze_code" and "analyze_code" in intents:
            intents.remove("analyze_code")

        # Tipo de cambio
        change_type = [cat for cat, kws in _CHANGE_TYPE_RULES if any(kw in text for kw in kws)]
        if not change_type:
            change_type = ["logic"]

        # Entidades: clases Java mencionadas en el prompt
        entities = list(dict.fromkeys(_JAVA_CLASS_RE.findall(prompt)))

        # File hints: entidades que terminen en nombres de archivo comunes
        file_hints = [e for e in entities if any(
            e.endswith(s) for s in ("Listener", "Service", "Controller", "Repository",
                                    "Event", "Handler", "Config", "Entity")
        )]

        # Target service: detectar si el prompt menciona el nombre de algún servicio
        target_service = None
        for svc in project_info.get("services", {}).keys():
            if svc.lower() in text or svc.lower().replace("-", "") in text:
                target_service = svc
                break

        # Complejidad heurística: alta si menciona 3+ clases o 4+ cambios
        change_count = sum(1 for kw in ["1)", "2)", "3)", "4)", "5)"] if kw in prompt)
        complexity = "high" if (len(entities) >= 3 or change_count >= 3) else "medium"

        result = {
            "intents": intents,
            "primary_intent": primary_intent,
            "entities": entities,
            "fields": [],
            "paths": [],
            "file_hints": file_hints,
            "target_service": target_service,
            "crud_action": None,
            "change_type": change_type,
            "architectural_intent": ["none"],
            "keywords": entities,
            "architecture": project_info.get("architecture", {"type": "unknown"}),
            "semantic_context": {
                "implicit_reference": False,
                "quality_related": "quality" in text or "clean" in text,
                "bug_suggested": primary_intent == "fix_bug",
                "complexity_hint": complexity,
            },
        }

        print(f"[INTERPRETER] intent={primary_intent}, entities={entities}, file_hints={file_hints}")
        return result

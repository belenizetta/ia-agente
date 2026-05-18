# llm/generator_fallback.py
from core.models import FileChange
from typing import Optional, Dict, List

def fallback_generate(intent: str, context: Optional[Dict], target: str) -> List[FileChange]:
    frameworks = context.get("frameworks", []) if context else []
    resource = context.get("resource") or context.get("resource_name") or "item"
    resource = str(resource).lower()

    # CRUD
    if intent == "create_crud":
        if "fastapi" in frameworks:
            content = (
                "from fastapi import APIRouter\n"
                f"router = APIRouter(prefix='/{resource}', tags=['{resource}'])\n\n"
                "@router.post('/')\n"
                f"def create_{resource}(data: dict):\n    return data\n"
            )
            return [FileChange(path=f"routers/{resource}.py", content=content, mode="add")]
        # fallback generic
        content = (
            f"class {resource.capitalize()}Service:\n"
            "    def create(self, data):\n        return data\n"
        )
        return [FileChange(path=f"crud_{resource}.py", content=content, mode="add")]

    # FIX / MODIFY -> try to read target and do simple replacement if possible
    if intent in ("fix_bug", "modify_code"):
        try:
            import os, re
            if os.path.exists(target):
                with open(target, "r", encoding="utf-8", errors="ignore") as f:
                    src = f.read()
                fixed = re.sub(r"return\s+str\(a\)\s*\+\s*str\(b\)", "return a + b", src)
                if fixed != src:
                    return [FileChange(path=target, content=fixed, mode="update")]
        except Exception:
            pass

    # default
    return [FileChange(path=target, content="def generated_function():\n    return 'ok'\n", mode="add")]

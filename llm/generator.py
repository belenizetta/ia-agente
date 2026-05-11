import os
import re
import json
import logging
from typing import List, Optional, Dict
from core.models import FileChange

logger = logging.getLogger("generator")

class CodeGenerator:
    def __init__(self):
        self.llama = self._init_llama()

    def _init_llama(self):
        try:
            from llama_cpp import Llama
            model_path = os.getenv("LLM_MODEL_PATH")
            if model_path and os.path.exists(model_path):
                return Llama(model_path=model_path, n_ctx=8192, verbose=False)
        except Exception as e:
            logger.error(f"Error cargando LLM: {e}")
        return None

    def _parse(self, text: str, fallback_path: str = "") -> List[FileChange]:
        """Extrae bloques de código parseando el JSON retornado por el LLM."""
        changes = []
        try:
            # Buscar array JSON en la respuesta
            match = re.search(r"\[.*\]", text, re.DOTALL)
            if match:
                parsed = json.loads(match.group(0))
                for item in parsed:
                    if "path" in item and "content" in item:
                        changes.append(FileChange(path=item["path"].strip(), content=item["content"].strip(), mode="update"))
                return changes
        except Exception as e:
            logger.warning(f"Failed to parse JSON from LLM: {e}")
            
        # Fallback a bloques markdown si el LLM falla en seguir el formato
        code_blocks = re.findall(r"```(?:\w+)?\s*(.*?)\s*```", text, re.DOTALL)
        for content in code_blocks:
            changes.append(FileChange(path=fallback_path, content=content.strip(), mode="update"))
            
        return changes

    def generate(self, repo_dir: str, intent: str, files: Dict[str, str], prompt: str, **kwargs) -> List[FileChange]:
        all_changes = []

        try:
            if prompt.strip().startswith("{"):
                data = json.loads(prompt)
                if "prompt" in data:
                    prompt = data["prompt"]
        except Exception:
            pass
        
        system_role = (
            "Act as a Senior Polyglot Developer and Software Architect.\n"
            "Your goal is to apply changes following Clean Code and SOLID principles.\n"
            "YOU MUST ALWAYS RESPOND WITH A VALID JSON ARRAY OF OBJECTS.\n"
            "DO NOT use Markdown code blocks. ONLY raw JSON.\n"
            "Example Format:\n"
            "[\n"
            "  {\n"
            "    \"path\": \"src/file.py\",\n"
            "    \"content\": \"<entire updated code here>\"\n"
            "  }\n"
            "]\n"
        )

        universal_rules = (
            "CONSTRAINTS:\n"
            "- DO NOT move core logic to entry-point/index/manifest files unless strictly necessary.\n"
            "- ONLY modify what is necessary to fulfill the task.\n"
            "- Ensure the response is ONLY valid JSON, nothing else.\n"
            "- PREFER service layer: extract business validations/functions into existing modules.\n"
            "- NO TRIVIAL CHANGES: If there are no technical or functional changes, return the ORIGINAL code exactly.\n"
            "- BUSINESS LOGIC INTEGRITY: DO NOT change method names or logic that is not affected by the request.\n"
            "- UNIT TESTING: When modifying or creating business logic, you MUST generate corresponding unit tests if requested.\n"
        )

        for path, content in files.items():
            MAX_FILE_SIZE = 25000 
            if len(content) > MAX_FILE_SIZE:
                logger.warning(f"Archivo {path} demasiado grande ({len(content)} chars). Se procesará con contexto reducido.")
                content = content[:MAX_FILE_SIZE] + "\n\n// [ALERTA: Archivo truncado por tamaño para procesado de IA.]"

            ctx = kwargs.get("context", {})
            langs = ctx.get("languages", [])
            fw = ctx.get("frameworks", [])
            steps = ctx.get("plan_steps", [])
            
            global_ctx = kwargs.get("global_context", {})
            acc_changes = kwargs.get("accumulated_changes", {})
            
            global_ctx_text = ""
            if global_ctx:
                global_ctx_text = "CROSS-FILE CONTEXT:\n"
                for key, val in global_ctx.items():
                    if key != f"{kwargs.get('service')}:{path}":
                        summary = val[:2000] + "..." if len(val) > 2000 else val
                        global_ctx_text += f"File '{key}':\n```\n{summary}\n```\n"

            acc_changes_text = ""
            if acc_changes:
                acc_changes_text = "PREVIOUS CHANGES IN THIS JOB:\n"
                for f_path, f_content in acc_changes.items():
                    acc_changes_text += f"Updated File '{f_path}':\n```\n{f_content[:2000]}...\n```\n"

            ext = os.path.splitext(path)[1].lower()
            expected_lang = "source code"
            if ext == ".html": expected_lang = "HTML/Angular Template"
            elif ext in [".css", ".scss"]: expected_lang = "CSS/SCSS"
            elif ext == ".ts": expected_lang = "TypeScript"
            elif ext == ".py": expected_lang = "Python"
            elif ext == ".json": expected_lang = "JSON"

            steps_text = "\n".join([f"- {s}" for s in steps]) if steps else "No specific steps provided."
            
            file_prompt = (
                f"{system_role}\n"
                f"PROJECT CONTEXT:\n"
                f"- Languages: {', '.join(langs)}\n"
                f"- Frameworks: {', '.join(fw)}\n\n"
                f"{global_ctx_text}\n"
                f"{acc_changes_text}\n"
                f"{universal_rules}\n\n"
                f"TASK: {prompt}\n"
                f"PLAN STEPS:\n{steps_text}\n\n"
                f"CURRENT FILE: {path}\n"
                f"EXPECTED FORMAT: {expected_lang}\n"
                f"CONTENT:\n{content}\n"
            )
            
            raw_output = self._call_llm(file_prompt)
            parsed_changes = self._parse(raw_output, fallback_path=path)
            
            for change in parsed_changes:
                if len(change.content) < len(content) * 0.3 and "delete" not in intent.lower():
                    logger.warning(f"Protección de integridad: Cambio rechazado en {path} (contenido demasiado corto)")
                    continue
                
                if ext in [".html", ".css", ".scss"] and ("import " in change.content or "export class" in change.content):
                    logger.warning(f"Protección de Formato: Se detectó código TS/JS en un archivo {ext} ({path}). Cambio rechazado.")
                    continue

                def get_substance(c, ext):
                    c = re.sub(r"\s+", "", c)
                    if ext in [".py"]:
                        c = re.sub(r"#.*", "", c)
                    elif ext in [".js", ".ts", ".java", ".go", ".cs"]:
                        c = re.sub(r"//.*|/\*.*?\*/", "", c, flags=re.DOTALL)
                    elif ext in [".html"]:
                        c = re.sub(r"<!--.*?-->", "", c, flags=re.DOTALL)
                    return c

                content_substance = get_substance(content, ext)
                change_substance = get_substance(change.content, ext)
                
                if content_substance == change_substance:
                    logger.info(f"Protección Trivial: Ignorando cambios de solo formato/comentarios en {path}")
                    continue

                if change.content.strip() != content.strip():
                    all_changes.append(change)

        return all_changes

    def _call_llm(self, prompt: str) -> str:
        if not self.llama: return ""
        try:
            res = self.llama.create_chat_completion(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1
            )
            return res["choices"][0]["message"]["content"]
        except Exception:
            return ""

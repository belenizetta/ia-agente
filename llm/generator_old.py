# llm/generator.py
"""
CodeGenerator conservador multi-lenguaje (Python / PHP / Java / JS).

Mejoras clave:
- Acepta cambios estructurales pequeños (ej: add field) sin exigir alto overlap
- Validación por lenguaje NO bloqueante
- Python sigue validándose, otros lenguajes pasan por heurísticas seguras
- Evita falsos no_changes en modelos/schemas
"""

import os
import re
import subprocess
import tempfile
import difflib
from typing import List, Optional, Dict, Tuple

from core.models import FileChange
from llm.templates import system_code_generator, user_code_prompt
from policy.checker import PolicyChecker
from unidiff import PatchSet


# -------------------------
# Configuración
# -------------------------

SAFE_EXTS = (".py", ".js", ".ts", ".php", ".java", ".json", ".yaml", ".yml", ".md")

MIN_LINES_FOR_CHANGE = int(os.getenv("MIN_LINES_FOR_CHANGE", "3"))
MIN_OVERLAP_RATIO = float(os.getenv("MIN_OVERLAP_RATIO", "0.12"))

ALLOW_MINIMAL_CHANGES_INTENTS = {
    "modify_code",
    "add_field",
    "extend_model",
    "fix_bug",
}

STRUCTURAL_PATH_HINTS = (
    "model",
    "models",
    "schema",
    "schemas",
    "entity",
    "entities",
    "dto",
)

PROTECTED_FILES = {
    "generic": ["Dockerfile", "docker-compose.yml", ".github/workflows"],
}


FILE_BLOCK_RE = re.compile(
    r"^FILE\s+(.+?)\s*\n(.*?)(?=(?:\n^FILE\s+)|\Z)",
    re.DOTALL | re.MULTILINE,
)


# -------------------------
# Generator
# -------------------------

class CodeGenerator:
    def __init__(self):
        self.policy = PolicyChecker()
        self.llama = None
        self.hf = None

        try:
            from llama_cpp import Llama

            model_path = os.getenv("LLM_MODEL_PATH")
            if model_path and os.path.exists(model_path):
                self.llama = Llama(
                    model_path=model_path,
                    n_ctx=int(os.getenv("LLAMA_CTX", "2048")),
                    n_threads=int(os.getenv("LLAMA_THREADS", "4")),
                )
        except Exception:
            pass

        try:
            from transformers import pipeline

            hf_model = os.getenv("HF_MODEL")
            if hf_model:
                self.hf = pipeline("text-generation", model=hf_model)
        except Exception:
            pass

    # -------------------------
    # Utils
    # -------------------------

    def _is_protected(self, path: str) -> bool:
        low = path.lower()
        return any(p in low for p in PROTECTED_FILES["generic"])

    def _overlap_ratio(self, old: str, new: str) -> float:
        try:
            return difflib.SequenceMatcher(a=old, b=new).quick_ratio()
        except Exception:
            return 0.0

    def _looks_structural(self, path: str) -> bool:
        low = path.lower()
        return any(h in low for h in STRUCTURAL_PATH_HINTS)

    def _validate_python(self, code: str) -> bool:
        try:
            compile(code, "<string>", "exec")
            return True
        except Exception:
            return False

    def _language_ok(self, path: str, content: str) -> bool:
        """
        Validación liviana y NO bloqueante por lenguaje
        """
        if path.endswith(".py"):
            return self._validate_python(content)

        # PHP / Java / JS: solo chequeos triviales
        if len(content.strip()) < 10:
            return False

        return True

    # -------------------------
    # Prompt
    # -------------------------

    def _build_prompt(self, prompt: str, files: Dict[str, str]) -> str:
        files_txt = []
        for p, c in files.items():
            files_txt.append(f"--- FILE: {p} ---\n{c}")

        return f"""
You are a conservative code editor.

RULES:
- Modify ONLY the files listed
- Return ONLY full FILE blocks
- Do NOT add explanations

USER REQUEST:
{prompt}

FILES:
{chr(10).join(files_txt)}
"""

    # -------------------------
    # LLM Call
    # -------------------------

    def _call_llm(self, prompt: str) -> str:
        if self.llama:
            out = self.llama(prompt, max_tokens=1024)
            return out["choices"][0]["text"]

        if self.hf:
            out = self.hf(prompt, max_new_tokens=512, do_sample=False)
            return out[0]["generated_text"]

        return ""

    # -------------------------
    # Parse output
    # -------------------------

    def _parse(self, text: str) -> List[FileChange]:
        changes = []
        for path, content in FILE_BLOCK_RE.findall(text):
            changes.append(
                FileChange(
                    path=path.strip(),
                    content=content.strip(),
                    mode="update",
                )
            )
        return changes

    # -------------------------
    # Main
    # -------------------------

    def generate(
        self,
        repo_dir: str,
        intent: str,
        files: Dict[str, str],
        prompt: str,
        context: Optional[Dict] = None,
    ) -> List[FileChange]:

        prompt_text = self._build_prompt(prompt, files)
        llm_out = self._call_llm(prompt_text)
        changes = self._parse(llm_out)

        if not changes:
            return []

        final_changes = []

        for ch in changes:
            if not ch.path or ".." in ch.path:
                continue

            if self._is_protected(ch.path):
                continue

            abs_path = os.path.join(repo_dir, ch.path)
            old = ""

            if os.path.exists(abs_path):
                with open(abs_path, "r", encoding="utf-8", errors="ignore") as f:
                    old = f.read()

            if old:
                ratio = self._overlap_ratio(old, ch.content)

                if ratio < MIN_OVERLAP_RATIO:
                    if (
                        intent in ALLOW_MINIMAL_CHANGES_INTENTS
                        and self._looks_structural(ch.path)
                        and self._language_ok(ch.path, ch.content)
                    ):
                        pass
                    else:
                        continue

            if not self._language_ok(ch.path, ch.content):
                continue

            final_changes.append(ch)

        if not final_changes:
            return []

        policy = self.policy.check(final_changes)
        if not policy.get("passed", False):
            return []

        return final_changes

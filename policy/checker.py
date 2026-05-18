# policy/checker.py
import re
import os
from typing import List, Dict, Any
from core.models import FileChange

SECRET_PATTERNS = [
    r"(?i)api[_-]?key\s*[:=]\s*['\"][A-Za-z0-9\-_]{16,}['\"]",
    r"(?i)secret[_-]?(key|token)?\s*[:=]\s*['\"][A-Za-z0-9\-_]{8,}['\"]",
    r"(?i)aws_access_key_id\s*[:=]\s*['\"][A-Z0-9]{16,}['\"]",
    r"(?i)aws_secret_access_key\s*[:=]\s*['\"][A-Za-z0-9\/+]{40,}['\"]",
    r"(?i)ssh-rsa\s+[A-Za-z0-9+/=]+",
    r"-----BEGIN PRIVATE KEY-----"
]

DANGEROUS_PATTERNS = [
    r"\beval\(", r"\bexec\(", r"os\.system\(", r"subprocess\.Popen\(", r"pickle\.loads\(", r"input\(",
]

BANNED_PATHS = [
    ".env",
    "secrets.json",
    "id_rsa",
    "id_rsa.pub",
]

MAX_TOTAL_LINES = int(os.getenv("POLICY_MAX_LINES", "500"))  # límite de líneas por patch


class PolicyChecker:
    def __init__(self):
        # podés cargar reglas desde archivo si querés
        self.secret_regexes = [re.compile(p) for p in SECRET_PATTERNS]
        self.danger_regexes = [re.compile(p) for p in DANGEROUS_PATTERNS]
        self.banned_paths = set(BANNED_PATHS)

    def _check_file_for_secrets(self, content: str) -> List[str]:
        issues = []
        for rx in self.secret_regexes:
            for m in rx.finditer(content):
                snippet = content[max(0, m.start() - 20):m.end() + 20]
                issues.append(f"Possible secret match: {m.group(0)[:80]}... snippet: {snippet[:120]}")
        return issues

    def _check_file_for_danger(self, content: str) -> List[str]:
        issues = []
        for rx in self.danger_regexes:
            for m in rx.finditer(content):
                context = content[max(0, m.start() - 20):m.end() + 50]
                issues.append(f"Potential dangerous pattern: '{m.group(0)}' context: {context[:100]}")
        return issues

    def check(self, changes: List[FileChange]) -> Dict[str, Any]:
        """
        Revisa una lista de FileChange.
        Retorna:
          {
            "passed": bool,
            "issues": [ ... ],
            "summary": { "files_checked": N, "total_lines": M }
          }
        """
        issues: List[str] = []
        total_lines = 0
        files_checked = 0

        for ch in changes:
            # chequeo paths prohibidos
            for banned in self.banned_paths:
                if os.path.basename(ch.path).lower() == banned.lower():
                    issues.append(f"Banned file modified/added: {ch.path}")

            # contenido
            content = ch.content or ""
            files_checked += 1
            total_lines += content.count("\n") + 1

            # secrets
            issues.extend(self._check_file_for_secrets(content))

            # dangerous patterns
            issues.extend(self._check_file_for_danger(content))

            # heurística: grandes borrados/adiciones (simple)
            if content.count("\n") > MAX_TOTAL_LINES:
                issues.append(f"Large change in {ch.path}: {content.count('\\n')} lines (limit {MAX_TOTAL_LINES})")

        passed = len(issues) == 0
        summary = {"files_checked": files_checked, "total_lines": total_lines}
        return {"passed": passed, "issues": issues, "summary": summary}

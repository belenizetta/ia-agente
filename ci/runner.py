import os
import subprocess
from typing import List, Dict, Any, Optional
from core.audit import Auditor


class TestRunner:
    """
    Ejecuta los tests del repositorio usando comandos del usuario, del entorno
    o fallback por defecto. Maneja múltiples comandos, logs y timeout.
    """

    def __init__(self, timeout: int = 120):
        """
        :param timeout: Timeout global para cada comando, en segundos.
        """
        self.timeout = timeout

    def _parse_commands(self, manual_cmds: Optional[List[str]]) -> List[str]:
        """
        Determina qué comandos deben ejecutarse:
        1. Comandos del usuario (manual_cmds)
        2. Desde la variable de entorno CI_COMMANDS
        3. Fallback por defecto
        """
        if manual_cmds:
            return manual_cmds

        env_cmds = os.getenv("CI_COMMANDS")
        if env_cmds:
            return [c.strip() for c in env_cmds.split("&&") if c.strip()]

        # --- Fallback por defecto ---
        # Si el proyecto detecta FastAPI, suele usar pytest.
        return ["pytest -q"]

    def _run_command(self, cmd: str, cwd: str) -> Dict[str, Any]:
        """
        Ejecuta un solo comando y captura toda la información.
        """
        try:
            out = subprocess.run(
                cmd,
                cwd=cwd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            return {
                "command": cmd,
                "return_code": out.returncode,
                "stdout": out.stdout,
                "stderr": out.stderr,
                "success": out.returncode == 0,
            }

        except subprocess.TimeoutExpired as e:
            return {
                "command": cmd,
                "return_code": -1,
                "stdout": e.stdout or "",
                "stderr": f"TIMEOUT: {str(e)}",
                "success": False,
            }

        except Exception as e:
            return {
                "command": cmd,
                "return_code": -1,
                "stdout": "",
                "stderr": f"ERROR: {str(e)}",
                "success": False,
            }

    def run_all(self, repo_dir: str, commands: Optional[List[str]] = None, auditor: Optional[Auditor] = None) -> Dict[str, Any]:
        """
        Ejecuta todos los comandos y devuelve:
        {
            "passed": bool,
            "results": [ ... ]
        }
        """
        cmds = self._parse_commands(commands)

        if auditor:
            auditor.record("ci_commands_selected", {"commands": cmds})

        results = []
        all_passed = True

        for cmd in cmds:
            if auditor:
                auditor.record("ci_command_start", {"cmd": cmd})

            result = self._run_command(cmd, repo_dir)
            results.append(result)

            if auditor:
                auditor.record("ci_command_end", result)

            if not result["success"]:
                all_passed = False

        return {"passed": all_passed, "results": results}

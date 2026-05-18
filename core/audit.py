import os
import json
from datetime import datetime


class Auditor:
    def __init__(self, job_id: str, out_dir: str):
        # Convertir a ruta ABSOLUTA SIEMPRE
        base = os.path.abspath(out_dir)

        # Crear la carpeta si no existe
        os.makedirs(base, exist_ok=True)

        # Ruta final del archivo
        self.file = os.path.join(base, f"audit_{job_id}.jsonl")

        # DEBUG opcional
        print("[AUDIT FILE]", self.file)

    def record(self, event_type: str, data: dict):
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "event": event_type,
            "data": data,
        }
        with open(self.file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")

    def path(self):
        return self.file

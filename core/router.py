# core/router.py
import re
import json
from typing import Dict, List

class SemanticRouter:
    def __init__(self, llama_instance):
        self.llama = llama_instance

    def filter_services(self, prompt: str, project_manifest: Dict[str, List[str]]) -> List[str]:
        # Sanitizar prompt si viene como JSON
        try:
            if prompt.strip().startswith("{"):
                data = json.loads(prompt)
                if "prompt" in data:
                    prompt = data["prompt"]
        except Exception:
            pass

        # --- OPTIMIZACIÓN DE CONTEXTO ---
        # Si el manifiesto es muy grande, enviar solo los nombres de servicios y frameworks
        # en lugar de la lista completa de archivos para evitar el desborde de tokens.
        summarized_manifest = {}
        for svc, info in project_manifest.items():
            # info puede ser una lista de archivos o un dict con frameworks/files
            if isinstance(info, dict):
                summarized_manifest[svc] = {
                    "frameworks": info.get("frameworks", []),
                    "file_count": len(info.get("files", []))
                }
            else:
                summarized_manifest[svc] = {"file_count": len(info)}

        system_prompt = (
            "You are a Senior Architect. Identify affected services for the request.\n"
            f"Services Summary: {json.dumps(summarized_manifest)}\n"
            f"User Request: {prompt}\n"
            "Output ONLY a JSON list: [\"svc1\", \"svc2\"]"
        )

        try:
            res = self.llama.create_chat_completion(
                messages=[{"role": "user", "content": system_prompt}],
                temperature=0
            )
            raw_output = res["choices"][0]["message"]["content"]
            
            # --- NUEVA LÓGICA DE EXTRACCIÓN ROBUSTA ---
            # Buscamos algo que empiece con [ y termine con ]
            match = re.search(r"\[.*\]", raw_output, re.DOTALL)
            if match:
                clean_json = match.group(0)
                services = json.loads(clean_json)
                # Aseguramos que sea una lista y que los servicios existan en el manifiesto
                if isinstance(services, list):
                    return [s for s in services if s in project_manifest]
            
            print(f"[ROUTER] WARN - Could not parse JSON from: {raw_output}")
            return list(project_manifest.keys()) # Fallback: devolver todos si no entendimos
            
        except Exception as e:
            print(f"[ROUTER] ERROR: {e}")
            return list(project_manifest.keys()) # Fallback de seguridad
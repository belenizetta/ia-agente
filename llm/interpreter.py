import json
import re
from typing import Dict, Any, List, Optional

class PromptInterpreter:
    """
    LLM-based Interpreter.
    - Replaces massive regexes with Structured JSON Output from the LLM.
    """

    def __init__(self, llama_instance=None):
        self.llama = llama_instance

    def interpret(self, prompt: str, project_info: Dict[str, Any]) -> Dict[str, Any]:
        text = prompt.lower()
        
        # Extract a simplified manifest to avoid token limits
        summarized_manifest = {}
        for svc, info in project_info.get("services", {}).items():
            summarized_manifest[svc] = {
                "frameworks": info.get("frameworks", []),
                "files_sample": info.get("files", [])[:20]  # Just top 20 files
            }

        system_prompt = f"""You are a Senior Software Architect and intent analyzer.
Analyze the user request and extract key information in valid JSON format.
DO NOT wrap the response in markdown blocks like ```json. Return ONLY raw JSON.

Project Context: {json.dumps(summarized_manifest)}

Required JSON Output Structure:
{{
  "intents": ["modify_code" | "create_feature" | "fix_bug" | "refactor" | "add_tests" | "update_docs" | "create_crud" | "upgrade_version" | "analyze_code"],
  "primary_intent": "one of the above",
  "entities": ["list", "of", "domain", "entities", "mentioned"],
  "fields": ["list", "of", "specific", "fields", "or", "properties", "mentioned"],
  "paths": ["list", "of", "api", "paths", "or", "endpoints"],
  "file_hints": ["list", "of", "specific", "files", "mentioned", "like", "models.py"],
  "target_service": "name of the specific service if mentioned, or null",
  "crud_action": "create" | "read" | "update" | "delete" | "list" | null,
  "change_type": ["db" | "api" | "scaffold" | "infra" | "config" | "logic" | "version" | "style"],
  "architectural_intent": ["ddd" | "hexagonal" | "clean_architecture" | "mvc" | "event_driven" | "microservices" | "none"],
  "semantic_context": {{
    "implicit_reference": boolean,
    "quality_related": boolean,
    "bug_suggested": boolean,
    "complexity_hint": "low" | "medium" | "high"
  }}
}}

User Request: {prompt}
"""

        # Default fallback structure in case of LLM failure or no LLM available
        result = {
            "intents": ["modify_code"],
            "primary_intent": "modify_code",
            "fields": [],
            "entities": [],
            "paths": [],
            "file_hints": [],
            "multilang_files": [],
            "relevant_folders": [],
            "target_service": None,
            "keywords": [],
            "architecture": project_info.get("architecture", {"type": "unknown"}),
            "crud_action": None,
            "inferred_route": None,
            "change_type": ["logic"],
            "architectural_intent": ["none"],
            "semantic_context": {
                "implicit_reference": False,
                "quality_related": False,
                "bug_suggested": False,
                "complexity_hint": "low"
            }
        }

        if self.llama:
            try:
                res = self.llama.create_chat_completion(
                    messages=[{"role": "user", "content": system_prompt}],
                    temperature=0.1
                )
                raw_output = res["choices"][0]["message"]["content"]
                
                # Try to parse JSON robustly
                match = re.search(r"\{.*\}", raw_output, re.DOTALL)
                if match:
                    parsed = json.loads(match.group(0))
                    # Merge parsed data into result
                    for k, v in parsed.items():
                        if k in result:
                            result[k] = v
                            
            except Exception as e:
                print(f"[INTERPRETER] LLM parsing failed, falling back to basic heuristics: {e}")
                
        # Basic regex fallbacks if LLM is missing or failed (for target_service specifically)
        if not result.get("target_service"):
            services = list(project_info.get("services", {}).keys())
            for svc in services:
                if svc.lower() in text or svc.lower().replace("-", "") in text:
                    result["target_service"] = svc
                    break

        return result

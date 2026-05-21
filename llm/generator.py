import os
import re
import json
import logging
from typing import List, Dict, Optional, Tuple
from core.models import FileChange
from llm.claude_client import get_client

logger = logging.getLogger("generator")

# Umbral: archivos más grandes se procesan por sección
# Con qwen2.5-coder:7b en GPU (8192 tokens ctx) podemos manejar ~12000 chars completos
LARGE_FILE_THRESHOLD = 12000

SYSTEM_ROLE = (
    "You are a code editor. Return ONLY a JSON array. No explanation, no markdown.\n"
    'Format: [{"path": "path/to/File.java", "content": "<code here>"}]\n'
    "If a file needs no changes, return []."
)

UNIVERSAL_RULES = (
    "Rules:\n"
    "- Return ONLY the JSON array, nothing else.\n"
    "- Only modify what the task requires. Keep everything else identical.\n"
    "- If a file needs no changes, return [].\n"
)


class CodeGenerator:
    def __init__(self):
        self.claude = get_client()

    # ------------------------------------------------------------------
    # Extracción de sección relevante para archivos grandes
    # ------------------------------------------------------------------

    def _find_method_bounds(self, lines: List[str], method_name: str) -> Tuple[Optional[int], Optional[int]]:
        """Encuentra el inicio y fin de un método por nombre usando conteo de llaves."""
        start = None
        for i, line in enumerate(lines):
            if re.search(rf'\b{re.escape(method_name)}\s*\(', line):
                if any(kw in line for kw in ['void ', 'public ', 'private ', 'protected ',
                                              'List<', 'Optional<', 'ResponseEntity', 'String ', 'int ', 'boolean ']):
                    start = i
                    break

        if start is None:
            return None, None

        depth, opened = 0, False
        for i in range(start, min(start + 300, len(lines))):
            depth += lines[i].count('{') - lines[i].count('}')
            if depth > 0:
                opened = True
            if opened and depth <= 0:
                return start, i

        return start, min(start + 80, len(lines) - 1)

    def _extract_section(self, content: str, prompt: str) -> Tuple[str, Optional[int], Optional[int]]:
        """
        Para archivos grandes: extrae el header de clase + el método relevante.
        Retorna (sección_reducida, línea_inicio_método, línea_fin_método).
        Si el archivo es pequeño retorna (content, None, None).
        """
        if len(content) <= LARGE_FILE_THRESHOLD:
            return content, None, None

        lines = content.splitlines()
        n = len(lines)

        # Header: primeras 25 líneas (package, imports, class declaration)
        header_lines = min(25, n)
        header = lines[:header_lines]

        # 1) Rango de líneas explícito en el prompt (ej: "líneas 144–165")
        m = re.search(r'l[íi]neas?\s+(\d+)\s*[–\-]\s*(\d+)', prompt, re.IGNORECASE)
        if m:
            sl = max(header_lines, int(m.group(1)) - 10)
            el = min(n - 1, int(m.group(2)) + 10)
            section = lines[sl:el + 1]
            focused = '\n'.join(header) + '\n// ...\n' + '\n'.join(section) + '\n// ...'
            logger.info(f"[EXTRACT] Por rango de líneas {sl}-{el}")
            return focused, sl, el

        # 2) Nombre de método camelCase mencionado en el prompt
        methods = re.findall(r'\b([a-z][a-zA-Z0-9]+)\s*\(', prompt)
        for method in methods:
            ms, me = self._find_method_bounds(lines, method)
            if ms is not None:
                section = lines[ms:me + 1]
                focused = '\n'.join(header) + '\n// ...\n' + '\n'.join(section)
                logger.info(f"[EXTRACT] Método '{method}' en líneas {ms}-{me}")
                return focused, ms, me

        # 3) Fallback: primeros LARGE_FILE_THRESHOLD chars
        logger.info("[EXTRACT] Sin método identificado, enviando inicio del archivo")
        return content[:LARGE_FILE_THRESHOLD], None, None

    def _patch_section(self, full_content: str, modified_section: str,
                       start_line: int, end_line: int) -> str:
        """Reemplaza las líneas [start_line, end_line] con el contenido modificado."""
        lines = full_content.splitlines()
        new_lines = (
            lines[:start_line]
            + modified_section.splitlines()
            + lines[end_line + 1:]
        )
        return '\n'.join(new_lines)

    # ------------------------------------------------------------------
    # Parser de respuesta LLM
    # ------------------------------------------------------------------

    @staticmethod
    def _fix_json_control_chars(text: str) -> str:
        """Escapa tabs y newlines literales dentro de strings JSON.
        El LLM a veces genera JSON con código fuente indentado con tabs reales
        en lugar de \\t y \\n, lo que rompe json.loads()."""
        result = []
        in_string = False
        skip_next = False
        for c in text:
            if skip_next:
                result.append(c)
                skip_next = False
            elif c == '\\' and in_string:
                result.append(c)
                skip_next = True
            elif c == '"':
                in_string = not in_string
                result.append(c)
            elif in_string:
                if c == '\n':
                    result.append('\\n')
                elif c == '\r':
                    result.append('\\r')
                elif c == '\t':
                    result.append('\\t')
                else:
                    result.append(c)
            else:
                result.append(c)
        return ''.join(result)

    def _parse(self, text: str, paths: List[str] = None) -> List[FileChange]:
        changes = []
        clean = re.sub(r'^```(?:json)?\s*', '', text.strip(), flags=re.IGNORECASE)
        clean = re.sub(r'\s*```$', '', clean)
        match = re.search(r'\[.*\]', clean, re.DOTALL)

        if match:
            json_text = match.group(0)
            # Intentar parsear directo, y si falla reparar los control chars
            for attempt in (json_text, self._fix_json_control_chars(json_text)):
                try:
                    parsed = json.loads(attempt)
                    for item in parsed:
                        if 'path' in item and 'content' in item:
                            changes.append(FileChange(
                                path=item['path'].strip(),
                                content=item['content'].strip(),
                                mode='update',
                            ))
                    return changes
                except Exception as e:
                    logger.warning(f"JSON parse failed: {e}")

        # Fallback: extraer bloque de código si solo hay un archivo
        if paths and len(paths) == 1:
            code_blocks = re.findall(r'```(?:\w+)?\s*(.*?)\s*```', text, re.DOTALL)
            if code_blocks:
                changes.append(FileChange(path=paths[0], content=code_blocks[0].strip(), mode='update'))

        return changes

    # ------------------------------------------------------------------
    # Validación de integridad
    # ------------------------------------------------------------------

    def _validate_change(self, change: FileChange, original_content: str,
                         original_path: str, intent: str) -> bool:
        is_delete = 'delete' in intent.lower() or 'remove' in intent.lower()
        orig_chars = len(original_content)
        new_chars = len(change.content)
        orig_lines = original_content.splitlines()
        new_lines = change.content.splitlines()

        if not is_delete and orig_chars > 100:
            ratio = new_chars / orig_chars
            if ratio < 0.50:
                logger.warning(f"Integridad RECHAZADA '{change.path}': {ratio:.0%} del original.")
                return False

        if not is_delete and len(orig_lines) > 10:
            line_ratio = len(new_lines) / len(orig_lines)
            if line_ratio < 0.50:
                logger.warning(f"Integridad RECHAZADA '{change.path}': {line_ratio:.0%} líneas.")
                return False

        if change.content.strip() == original_content.strip():
            logger.info(f"Sin cambios reales en {change.path} — ignorado.")
            return False

        return True

    # ------------------------------------------------------------------
    # Generación principal
    # ------------------------------------------------------------------

    def generate(self, repo_dir: str, intent: str, files: Dict[str, str],
                 prompt: str, **kwargs) -> List[FileChange]:
        if not files:
            return []

        try:
            if prompt.strip().startswith('{'):
                data = json.loads(prompt)
                if 'prompt' in data:
                    prompt = data['prompt']
        except Exception:
            pass

        ctx = kwargs.get('context', {})
        langs = ctx.get('languages', [])
        fw = ctx.get('frameworks', [])
        steps = ctx.get('plan_steps', [])
        service = kwargs.get('service', '')
        paths_list = list(files.keys())

        # Guardar contenido original antes de cualquier truncación (para patchear después)
        original_files: Dict[str, str] = dict(files)

        # Truncar solo para análisis de sección (la extracción usa el contenido truncado
        # pero el patch final usa el original completo)
        MAX_INPUT_CHARS = 16000
        files_for_section = {
            path: (content[:MAX_INPUT_CHARS] if len(content) > MAX_INPUT_CHARS else content)
            for path, content in files.items()
        }

        # Extraer secciones relevantes
        section_meta: Dict[str, Tuple[str, Optional[int], Optional[int]]] = {}
        uses_section = False

        for path, content in files_for_section.items():
            focused, sec_start, sec_end = self._extract_section(content, prompt)
            section_meta[path] = (focused, sec_start, sec_end)
            if sec_start is not None:
                uses_section = True

        steps_text = '; '.join(steps[:3]) if steps else ''

        # ----------------------------------------------------------------
        # Estrategia A: archivo grande con sección identificada
        # → el modelo recibe SOLO la sección y devuelve SOLO la sección corregida
        # → se parchea de vuelta en el archivo original completo
        # ----------------------------------------------------------------
        if uses_section and len(files) == 1:
            path = paths_list[0]
            focused, sec_start, sec_end = section_meta[path]
            original_full = original_files[path]

            section_system = (
                "You are a precise code editor. "
                "Return ONLY a JSON array with ONE element. "
                "The 'content' field must contain ONLY the fixed method/section shown — "
                "NOT the entire file. No duplicate methods. No extra code.\n"
                'Format: [{"path": "path/to/File.java", "content": "fixed method here"}]'
            )

            section_prompt = (
                f"TASK: {prompt}\n\n"
                f"Fix ONLY the following code section from {path}:\n"
                f"```java\n{focused}\n```\n\n"
                f"Return a JSON array with the corrected section. "
                f"Do NOT return the full file — only the fixed method/section."
            )

            raw_output = self.claude.complete(section_prompt, system=section_system, max_tokens=2048)
            logger.info(f'[LLM-SECTION] Response (primeros 400 chars): {raw_output[:400]}')

            parsed = self._parse(raw_output, paths=[path])
            if not parsed:
                logger.warning(f'[LLM-SECTION] Sin cambios parseables.\n{raw_output}')
                return []

            fixed_section = parsed[0].content
            # Pegar la sección corregida en el archivo ORIGINAL completo
            full_fixed = self._patch_section(original_full, fixed_section, sec_start, sec_end)
            change = FileChange(path=path, content=full_fixed, mode='update')

            if self._validate_change(change, original_full, path, intent):
                return [change]
            return []

        # ----------------------------------------------------------------
        # Estrategia B: archivo(s) pequeño(s) → devolver archivo completo
        # ----------------------------------------------------------------
        SYSTEM_ROLE_FULL = (
            "You are a code editor. Return ONLY a JSON array. No explanation, no markdown.\n"
            'Format: [{"path": "path/to/File.java", "content": "COMPLETE file here"}]\n'
            "Return the FULL file content. If no changes needed, return []."
        )

        files_section = ''
        for path, content in files.items():
            files_section += f'\nFILE: {path}\n```\n{content}\n```\n'

        batch_prompt = (
            f'{UNIVERSAL_RULES}\n'
            f'TASK: {prompt}\n'
            + (f'Steps: {steps_text}\n' if steps_text else '')
            + f'\nFiles:\n{files_section}\n'
            f'Return ONLY the JSON array with the complete modified files.'
        )

        raw_output = self.claude.complete(batch_prompt, system=SYSTEM_ROLE_FULL, max_tokens=4096)

        if not raw_output.strip():
            logger.error(f'[LLM] Respuesta vacía para {service}')
            return []

        logger.info(f'[LLM] Response para {service} (primeros 400 chars): {raw_output[:400]}')

        parsed_changes = self._parse(raw_output, paths=paths_list)

        if not parsed_changes:
            logger.warning(f'[LLM] No se pudieron parsear cambios.\n{raw_output}')
            return []

        all_changes = []
        for change in parsed_changes:
            original_path = next(
                (p for p in paths_list if os.path.basename(p) == os.path.basename(change.path)),
                None
            )
            if original_path and os.path.dirname(original_path) and not os.path.dirname(change.path):
                corrected = os.path.join(
                    os.path.dirname(original_path),
                    os.path.basename(change.path)
                ).replace('\\', '/')
                change = FileChange(path=corrected, content=change.content, mode=change.mode)
                original_path = corrected

            lookup_path = original_path or change.path
            original_content = original_files.get(lookup_path, '')

            if not original_content:
                all_changes.append(change)
                continue

            if self._validate_change(change, original_content, lookup_path, intent):
                all_changes.append(change)

        return all_changes

    def call_llm(self, prompt: str, system: str = None) -> str:
        return self.claude.complete(prompt, system=system)

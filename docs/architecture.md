# Arquitectura del asistente

## Módulos

- API Layer: `app/main.py`
- Orchestrator: `core/orchestrator.py`
- Interpreter: `llm/interpreter.py`
- Code Analyzer: `code/analyzer.py`
- Semantic Diff: `code/diff.py`
- Code Generator: `llm/generator.py`
- Policy Checker: `policy/checker.py`
- VCS Layer: `vcs/repo.py`
- CI Runner: `ci/runner.py`
- PR Manager: `pr/manager.py`
- Audit Logger: `core/audit.py`
- Planner: `planning/planner.py`
- Detector de proyecto: `code/detect.py`

## Flujo

1. API recibe `POST /process`.
2. Orchestrator interpreta intención y crea plan.
3. Analizador selecciona archivos relevantes.
4. Generador produce cambios (modelo GGUF/Transformers/fallback determinístico).
5. Se calculan diffs unificados y se registran.
6. VCS crea rama, aplica cambios, commit y push.
7. CI ejecuta pruebas y análisis.
8. Si se solicita, se crea PR incluyendo diffs.
9. Auditoría registra eventos en `.audit/`.

## Configuración

- `LLM_MODEL_DIR` o `LLM_MODEL_PATH` para GGUF.
- `LLAMA_CTX`, `LLAMA_THREADS`, `LLAMA_MAX_TOKENS`, `LLAMA_TEMP`.
- `CI_COMMANDS` para comandos de CI personalizados.
- `GITHUB_TOKEN` para PRs.

## Extensibilidad

- El Planner permite nuevas acciones.
- El Generator acepta `context` para adaptar la salida (ej. FastAPI).
- El Analyzer puede incorporar embeddings.
- El Runner soporta override de comandos.

## Seguridad

- Policy Checker detecta secretos y cambios excesivos.
- Auditoría completa de cada paso.

## Trazabilidad

- Diffs unificados generados en `core/orchestrator.py` y agregados al cuerpo del PR.


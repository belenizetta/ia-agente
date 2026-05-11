# Guía de entrenamiento y ajuste de modelos

## Objetivo

Entrenar/ajustar modelos open‑source para generación de código e interpretación de intención.

## Modelos recomendados

- Generación de código: Qwen2.5‑Coder 1.5B (GGUF para inferencia local). Para fine‑tuning, usar versión HF.
- Intención: XLM‑RoBERTa para zero‑shot; entrenable en clasificación.
- Embeddings: e5‑multilingual para indexación semántica.

## Fine‑tuning con QLoRA (resumen)

1. Preparar dataset de instrucciones:
   - Extraer issues/PRs de tu repositorio.
   - Generar pares `instrucción → patch` de commits.
   - Normalizar a formato JSONL con campos `prompt`, `code_change`.
2. Configurar entorno GPU (recomendado):
   - Instalar `torch` con CUDA.
   - Instalar `transformers`, `datasets`, `peft`, `bitsandbytes`.
3. QLoRA:
   - Cargar modelo base (e.g., `Qwen2.5-Coder-1.5B` HF).
   - Aplicar LoRA con `peft` y 8‑bit/4‑bit con `bitsandbytes`.
   - Entrenar con máximo contexto y plantillas de sistema orientadas a parches.
4. Evaluación:
   - Validar en repos de prueba: precisión del patch y compilación.
5. Exportar y servir:
   - Guardar adaptadores PEFT.
   - Para inferencia local sin GPU, convertir a GGUF con `llama.cpp` herramientas y cuantizar.

## Generación de dataset desde repos

- Recorrer commits y diffs.
- Construir `prompt` a partir de mensaje de commit y archivos afectados.
- Incluir contexto de archivos y diffs como entrada y salida.

## Entrenamiento de intención

- Dataset con etiquetas: `modify_code`, `fix_bug`, `add_tests`, `create_feature`.
- Entrenar clasificación con XLM‑RoBERTa.

## Mini‑Copilot especializado

- Indizar repo con embeddings.
- Recuperar contexto relevante por prompt.
- Generar parches con plantilla de sistema para diffs.

## Consideraciones

- En CPU el entrenamiento no es práctico; usar GPU local o cloud.
- Para inferencia CPU, usar GGUF cuantizado (`Q4_K_M`).


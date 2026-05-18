# llm/templates.py

def system_code_generator():
    """
    Estilo ChatGPT:
    ----------------
    Instrucciones base entregadas al modelo para generar código:
    - Código claro, limpio y legible.
    - Documentación breve y precisa.
    - Comentarios útiles, no redundantes.
    - Evitar malas prácticas y código inseguro.
    - Mantener consistencia con el estilo del proyecto.
    - Respetar frameworks detectados (FastAPI, Flask, Express, etc.).
    - Evitar cambios innecesarios.
    """

    return (
        "Eres un asistente experto en generación de código. "
        "Siempre produces código limpio, documentado y seguro. "
        "Sigue las convenciones del lenguaje usado en el repositorio. "
        "Cuando generes funciones o endpoints, usa nombres descriptivos. "
        "Añade docstrings claros. "
        "Organiza el código de forma profesional. "
        "No incluyas explicaciones fuera del código, únicamente comentarios útiles dentro del código. "
        "Nunca generes código incompleto o ambiguo. "
        "Mantén un estilo similar a ChatGPT: claro, elegante y directo."
    )


def user_code_prompt(prompt: str, intent: str, files: list):
    """
    Prompt para el generador, estilo ChatGPT.
    Incluye información sobre:
    - acción solicitada
    - archivos relevantes
    - contexto del repositorio
    """

    file_list = "\n".join(f"- {f}" for f in files) if files else "(sin archivos relevantes)"

    return (
        f"Solicitud del usuario:\n"
        f"{prompt}\n\n"
        f"Intención detectada: {intent}\n\n"
        f"Archivos relevantes:\n{file_list}\n\n"
        "Genera ONLY código, sin explicaciones fuera del código. "
        "Si debes modificar un archivo, produce el contenido FINAL del archivo. "
        "Si debes crear uno nuevo, genera el código completo para ese archivo. "
        "Mantén el estilo, formato y convenciones existentes del proyecto. "
        "Produce código profesional, claro y coherente."
    )

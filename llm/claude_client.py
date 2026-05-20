import os
import time
import logging
from openai import OpenAI

logger = logging.getLogger("llm_client")

_instance = None


def get_client() -> "LLMClient":
    global _instance
    if _instance is None:
        _instance = LLMClient()
    return _instance


class LLMClient:
    """
    Cliente LLM unificado. Soporta:
      - Ollama local/remoto (OLLAMA_BASE_URL)
      - OpenRouter (OPENROUTER_API_KEY)
    Prioridad: Ollama > OpenRouter
    """

    def __init__(self):
        ollama_url = os.getenv("OLLAMA_BASE_URL")
        openrouter_key = os.getenv("OPENROUTER_API_KEY")

        # LLM_TIMEOUT: segundos para esperar la respuesta completa (streaming mantiene la
        # conexión viva, pero igual necesitamos un techo para generaciones muy largas).
        timeout = float(os.getenv("LLM_TIMEOUT", "300"))

        if ollama_url:
            self._client = OpenAI(base_url=ollama_url, api_key="ollama", timeout=timeout)
            self.model = os.getenv("LLM_MODEL", "qwen2.5-coder:7b")
            self._use_stream = True  # Streaming evita que ngrok corte la conexión por idle
            logger.info(f"LLM: Ollama en {ollama_url} | modelo: {self.model} | streaming=True")
        elif openrouter_key:
            self._client = OpenAI(
                base_url="https://openrouter.ai/api/v1",
                api_key=openrouter_key,
                timeout=timeout,
            )
            self.model = os.getenv("LLM_MODEL", "qwen/qwen-2.5-coder-32b-instruct:free")
            self._use_stream = False
            logger.info(f"LLM: OpenRouter | modelo: {self.model}")
        else:
            raise ValueError(
                "Configurá OLLAMA_BASE_URL (recomendado en Colab) "
                "o OPENROUTER_API_KEY en el .env"
            )

        self.max_retries = 3

    def complete(self, prompt: str, system: str = None,
                 temperature: float = 0.1, max_tokens: int = 4096) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        wait = 10
        for attempt in range(self.max_retries):
            try:
                if self._use_stream:
                    return self._complete_stream(messages, temperature, max_tokens)

                response = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return response.choices[0].message.content or ""

            except Exception as e:
                msg = str(e)
                is_rate = "429" in msg or "rate" in msg.lower() or "quota" in msg.lower()
                if is_rate and attempt < self.max_retries - 1:
                    logger.warning(f"Rate limit — esperando {wait}s (intento {attempt+1})")
                    time.sleep(wait)
                    wait *= 2
                else:
                    logger.error(f"LLM error (intento {attempt+1}): {e}")
                    if attempt < self.max_retries - 1:
                        time.sleep(wait)
                        wait *= 2
                    else:
                        return ""
        return ""

    def _complete_stream(self, messages: list, temperature: float, max_tokens: int) -> str:
        """
        Modo streaming: recibe tokens a medida que el modelo los genera.
        Mantiene la conexión HTTP viva cuando el modelo es lento (ej: ngrok free → Kaggle T4).
        """
        stream = self._client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
        )
        chunks = []
        for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                chunks.append(delta)
        result = "".join(chunks)
        logger.info(f"[LLM-STREAM] Recibidos {len(result)} chars")
        return result

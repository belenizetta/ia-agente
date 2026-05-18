from typing import Optional


class RefactorRule:
    """
    Regla genérica de refactor.
    """

    name: str = "base"

    def detect_new_abstraction(self, new: str) -> bool:
        """
        Devuelve True si detecta que apareció una abstracción nueva.
        """
        return False

    def detect_old_pattern(self, content: str) -> bool:
        """
        Devuelve True si todavía existe el patrón viejo.
        """
        return False

    def apply(self, content: str) -> str:
        """
        Aplica el refactor automáticamente.
        """
        return content

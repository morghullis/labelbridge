"""Tipos comunes a todos los proveedores de IA (Claude, OpenAI, Gemini y las API compatibles con OpenAI).

La conversación (agent.py) solo conoce estos tipos. Cada adaptador traduce el historial al formato de su API y
la respuesta de vuelta, así los límites, los permisos y el filtro de secretos son los mismos con cualquier modelo.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterator, Protocol, Union

MAX_TOKENS = 16000  # salida máxima por llamada, incluido el razonamiento del modelo


@dataclass
class ToolCall:
    id: str
    name: str
    args: Any  # dict; si el modelo mandó JSON inválido, el texto tal cual (se le devuelve como error)
    native_id: bool = True  # False si la API no dio un id y lo puso la app


@dataclass
class Usage:
    input: int = 0  # entrada sin caché
    output: int = 0  # salida, incluido el razonamiento
    cache_read: int = 0
    cache_write: int = 0
    cost: float | None = None  # USD, cuando el proveedor informa el costo exacto

    def plus(self, other: "Usage | None") -> "Usage":
        if other is None:
            return self
        cost = None if self.cost is None and other.cost is None else (self.cost or 0.0) + (other.cost or 0.0)
        return Usage(self.input + other.input, self.output + other.output, self.cache_read + other.cache_read,
                     self.cache_write + other.cache_write, cost)


@dataclass
class Reply:
    """Un turno del modelo. `raw` es su contenido en el formato de la API, para reenviarlo tal cual."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop: str = "end"  # end | tool_use | max_tokens | refusal | pause
    usage: Usage | None = None
    raw: Any = None


@dataclass
class UserText:
    text: str


@dataclass
class ToolResult:
    call_id: str
    name: str
    output: str
    is_error: bool = False
    native_id: bool = True


Entry = Union[UserText, Reply, ToolResult]


class ProviderError(Exception):
    """Fallo al hablar con el proveedor.

    kind: auth | permission | not_found | quota | rate_limit | overloaded | bad_request | too_large | no_tools |
    network | timeout | redirect | policy | invalid_response
    """

    def __init__(self, kind: str, detail: str = "", status: int = 0, retry_after: float = 0.0) -> None:
        super().__init__(detail or kind)
        self.kind = kind
        self.detail = detail
        self.status = status
        self.retry_after = retry_after


class ModelClient(Protocol):
    def complete(self, history: list[Entry]) -> Reply:
        """Una llamada al modelo con todo el historial (el prefijo ya enviado se repite idéntico)."""
        ...

    def check(self) -> str:
        """Comprueba la clave y el modelo sin gastar tokens. Devuelve el nombre del modelo."""
        ...

    def models(self) -> list[str]:
        """Modelos disponibles para la clave (para elegir en Ajustes)."""
        ...


def groups(history: list[Entry]) -> Iterator[tuple[str, Any]]:
    """Agrupa el historial en turnos: ("assistant", Reply) o ("user", [ToolResult…, UserText…]).

    Las entradas seguidas del lado del usuario (resultados de herramientas y mensajes que quedaron sin
    respuesta) van en un solo turno: así los roles siempre se alternan, como piden todas las API.
    """
    pending: list[Entry] = []
    for entry in history:
        if isinstance(entry, Reply):
            if pending:
                yield "user", pending
                pending = []
            yield "assistant", entry
        else:
            pending.append(entry)
    if pending:
        yield "user", pending


# Cómo dicen las API que no admiten un parámetro. Que el error solo lo mencione no basta: un fallo de otro tipo
# (p. ej. una firma de razonamiento inválida) no debe apagar una opción para el resto de la sesión.
UNSUPPORTED_HINTS = ("unsupported", "not supported", "does not support", "is not allowed", "not permitted",
                     "extra inputs", "extra_forbidden", "unknown parameter", "unknown name", "unknown field",
                     "unrecognized", "unexpected", "input should be", "valid range", "instead")


def rejects(error_text: str, words: tuple[str, ...]) -> bool:
    """¿El error dice que la API no admite el parámetro (nombrado por `words`)?"""
    text = error_text.lower()
    return any(word in text for word in words) and any(hint in text for hint in UNSUPPORTED_HINTS)


def parse_args(raw: Any) -> Any:
    """Argumentos de una llamada a herramienta: dict, o el texto tal cual si no es JSON válido."""
    if isinstance(raw, dict):
        return raw
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return {}
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return str(raw)
    return value if isinstance(value, dict) else str(raw)


def error_output(result: ToolResult) -> str:
    """Resultado para las API que no tienen marca de error propia."""
    return f"ERROR: {result.output}" if result.is_error else result.output


def plain_schema(schema: dict) -> bool:
    """¿La herramienta no tiene parámetros?"""
    return not schema.get("properties")

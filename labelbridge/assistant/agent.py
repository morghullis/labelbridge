"""Conversación con Claude: el modelo pide herramientas, la app las ejecuta y le devuelve el resultado.

Se ejecuta en un hilo de trabajo (nunca en el de la interfaz). El historial solo crece al final:
el prompt del sistema y las herramientas no cambian, y lo ya enviado no se modifica, así el prefijo
se reutiliza de la caché y los bloques de razonamiento del modelo siguen siendo válidos.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Callable

import anthropic
from anthropic.types.beta import BetaTextBlock

from ..secret_store import ANTHROPIC_API_KEY
from .prompt import SYSTEM_PROMPT
from .tools import Toolbox

log = logging.getLogger(__name__)

MODEL = "claude-opus-5-5"
MODEL_NAME = "Claude Opus 5.5"
MAX_TOKENS = 16000  # incluye el razonamiento del modelo
EFFORT = "medium"
MAX_STEPS = 24  # llamadas al modelo por cada mensaje del usuario
# Si un filtro de seguridad rechaza una petición legítima, la API la repite con otro modelo recomendado.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
API_KEYS_URL = "https://platform.claude.com/settings/keys"
BILLING_URL = "https://platform.claude.com/settings/billing"

Emit = Callable[[str, dict], None]


class AssistantError(Exception):
    """Problema que se muestra tal cual en el chat."""


def friendly_error(exc: BaseException) -> str:
    if isinstance(exc, AssistantError):
        return str(exc)
    if isinstance(exc, anthropic.AuthenticationError):
        return "La clave de API del asistente no es válida o fue revocada. Cámbiala con el botón «Clave de API»."
    if isinstance(exc, anthropic.PermissionDeniedError):
        return f"Tu clave de API no tiene permiso para usar {MODEL_NAME}. Revisa tu cuenta en platform.claude.com."
    if isinstance(exc, anthropic.NotFoundError):
        return f"{MODEL_NAME} no está disponible para tu cuenta de Anthropic."
    if isinstance(exc, anthropic.RateLimitError):
        return "Tu cuenta de Anthropic llegó a su límite de uso por ahora. Espera un minuto y vuelve a intentarlo."
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code == 402:
            return "Tu cuenta de Anthropic no tiene saldo. Agrega créditos en platform.claude.com → Billing."
        if exc.status_code >= 500:
            return "El servicio de Claude no responde ahora mismo (está saturado). Inténtalo de nuevo en un minuto."
        if exc.status_code == 400:
            return f"La solicitud no fue aceptada ({exc.message}). Empieza una conversación nueva."
        return f"Error de la API de Claude ({exc.status_code}): {exc.message}"
    if isinstance(exc, anthropic.APIConnectionError):
        return "No hay conexión con el servicio de Claude (api.anthropic.com). Revisa internet o el firewall."
    return f"Error inesperado: {exc}"


def default_client(api_key: str) -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=api_key, max_retries=2, timeout=anthropic.Timeout(600.0, connect=15.0))


def validate_key(api_key: str, client_factory: Callable[[str], Any] = default_client) -> str:
    """Comprueba la clave sin gastar tokens. Devuelve el nombre del modelo."""
    model = client_factory(api_key).models.retrieve(MODEL)
    return getattr(model, "display_name", "") or MODEL_NAME


def _kept_after_fallback(content: list) -> list:
    """Tras un cambio de modelo a mitad de respuesta, lo anterior al último bloque «fallback» no se reenvía."""
    last = max((i for i, block in enumerate(content) if block.type == "fallback"), default=-1)
    if last < 0:
        return list(content)
    dropped = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}
    return [block for i, block in enumerate(content) if i > last or block.type not in dropped]


class Conversation:
    def __init__(self, toolbox: Toolbox, secrets, client_factory: Callable[[str], Any] = default_client) -> None:
        self.toolbox = toolbox
        self.secrets = secrets
        self.client_factory = client_factory
        self.messages: list[dict] = []
        self.carry: list[dict] = []  # bloques de un turno interrumpido que acompañan al siguiente mensaje
        self.use_fallbacks = True
        self.system = [{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}]
        self.tools = toolbox.definitions()
        self._client = None
        self._client_key = ""

    # --- estado ------------------------------------------------------------------------------
    def reset(self) -> None:
        self.messages = []
        self.carry = []

    def has_key(self) -> bool:
        return bool(self.secrets.get(ANTHROPIC_API_KEY))

    def client(self):
        key = self.secrets.get(ANTHROPIC_API_KEY)
        if not key:
            raise AssistantError("Falta la clave de API del asistente. Agrégala con el botón «Clave de API».")
        if self._client is None or key != self._client_key:
            self._client = self.client_factory(key)
            self._client_key = key
        return self._client

    # --- turno -------------------------------------------------------------------------------
    def send(self, text: str, emit: Emit, cancel: threading.Event) -> None:
        """Procesa un mensaje del usuario (ya sin secretos). Bloquea hasta terminar."""
        self.toolbox.cancel = cancel
        self.messages.append({"role": "user", "content": [*self.carry, {"type": "text", "text": text}]})
        self.carry = []
        refused = False
        try:
            refused = self._loop(emit, cancel)
        except Exception as exc:
            if not isinstance(exc, (AssistantError, anthropic.APIError)):
                log.exception("Asistente: error inesperado")
            else:
                log.warning("Asistente: %s", exc)
            emit("error", {"message": friendly_error(exc)})
        finally:
            self._settle(keep_text=not refused)

    def _loop(self, emit: Emit, cancel: threading.Event) -> bool:
        """Devuelve True si el modelo rechazó la petición."""
        for _ in range(MAX_STEPS):
            response = self._create_cancellable(cancel)
            if response is None:
                emit("stopped", {})
                return False
            if response.stop_reason == "refusal":
                emit(
                    "refusal",
                    {"message": "No puedo ayudar con eso. Reformula la petición o configura esa parte desde las pantallas."},
                )
                return True
            content = _kept_after_fallback(response.content) or [BetaTextBlock(type="text", text="Listo.")]
            tool_uses = [block for block in content if block.type == "tool_use"]
            if response.stop_reason == "max_tokens" and tool_uses:
                raise AssistantError("La respuesta quedó incompleta. Vuelve a intentarlo con una petición más corta.")
            self.messages.append({"role": "assistant", "content": content})
            text = "\n\n".join(block.text.strip() for block in content if block.type == "text" and block.text.strip())
            if not tool_uses:
                if response.stop_reason == "pause_turn":
                    continue
                emit("reply", {"text": text or "Listo."})
                return False
            if text:
                emit("note", {"text": text})
            results = []
            for block in tool_uses:
                if cancel.is_set():
                    output, is_error = "El usuario detuvo el asistente antes de ejecutar esta herramienta.", True
                else:
                    emit("tool", {"id": block.id, "name": block.name, "activity": self.toolbox.activity(block.name)})
                    output, is_error = self.toolbox.run(block.name, block.input)
                    emit("tool_done", {"id": block.id, "name": block.name, "ok": not is_error, "detail": output if is_error else ""})
                result = {"type": "tool_result", "tool_use_id": block.id, "content": output}
                if is_error:
                    result["is_error"] = True
                results.append(result)
            self.messages.append({"role": "user", "content": results})
            if cancel.is_set():
                emit("stopped", {})
                return False
        raise AssistantError("El asistente dio demasiados pasos seguidos. Dime cómo quieres seguir.")

    def _settle(self, keep_text: bool) -> None:
        """El historial siempre termina en una respuesta del asistente.

        Si el turno se cortó (error, botón Detener, rechazo), el último mensaje del usuario no tuvo respuesta:
        se quita del historial y sus bloques acompañan al próximo mensaje (los resultados de herramientas
        son obligatorios después de una llamada a herramienta).
        """
        if not self.messages:
            return
        if self.messages[-1]["role"] == "assistant":
            # Cortado justo después de pedir herramientas: cada llamada necesita su resultado.
            self.carry = [
                {"type": "tool_result", "tool_use_id": block.id, "content": "Interrumpido.", "is_error": True}
                for block in self.messages[-1]["content"]
                if block.type == "tool_use"
            ]
            return
        pending = self.messages.pop()["content"]
        for block in pending:
            if block.get("type") == "tool_result":
                self.carry.append(block)
            elif block.get("type") == "text" and keep_text:
                text = block["text"]
                if not text.startswith("(Mensaje anterior sin respuesta)"):
                    text = f"(Mensaje anterior sin respuesta) {text}"
                self.carry.append({"type": "text", "text": text})

    # --- API ---------------------------------------------------------------------------------
    def _create_cancellable(self, cancel: threading.Event):
        """La llamada corre en su propio hilo: «Detener» responde al instante y la respuesta tardía se descarta."""
        messages = list(self.messages)
        outcome: dict[str, Any] = {}

        def call() -> None:
            try:
                outcome["response"] = self._create(messages)
            except BaseException as exc:  # se relanza en el hilo del turno
                outcome["error"] = exc

        worker = threading.Thread(target=call, name="asistente-api", daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(0.1)
            if cancel.is_set():
                return None
        if cancel.is_set():
            return None
        if "error" in outcome:
            raise outcome["error"]
        return outcome["response"]

    def _create(self, messages: list[dict]):
        client = self.client()
        params = dict(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=self.system,
            tools=self.tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": EFFORT},
            cache_control={"type": "ephemeral"},
        )
        if self.use_fallbacks:
            try:
                return self._log_usage(client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params))
            except anthropic.BadRequestError as exc:
                if "fallback" not in str(exc).lower() and "anthropic-beta" not in str(exc).lower():
                    raise
                log.warning("Asistente: la cuenta no admite fallbacks del servidor; se sigue sin ellos (%s)", exc)
                self.use_fallbacks = False
        return self._log_usage(client.beta.messages.create(**params))

    @staticmethod
    def _log_usage(response):
        usage = getattr(response, "usage", None)
        if usage is not None:
            log.debug(
                "Asistente: %s · entrada %s (caché leída %s, escrita %s) · salida %s",
                response.stop_reason,
                usage.input_tokens,
                usage.cache_read_input_tokens,
                usage.cache_creation_input_tokens,
                usage.output_tokens,
            )
        return response

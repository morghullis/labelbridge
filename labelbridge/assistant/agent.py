"""Conversación con el modelo de IA elegido: el modelo pide herramientas, la app las ejecuta y le devuelve el resultado.

Funciona igual con cualquier proveedor (Claude, OpenAI, Gemini o una API compatible con OpenAI): los límites, los
permisos y el filtro de secretos están aquí, no en los adaptadores. Se ejecuta en un hilo de trabajo (nunca en el
de la interfaz). El historial solo crece al final: el prompt del sistema y las herramientas no cambian y lo ya
enviado no se modifica, así el prefijo se reutiliza de la caché del proveedor y el razonamiento sigue siendo válido.
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Callable

from ..config import AssistantSettings
from .permissions import ToolPermissions
from .pricing import cost_of, price_for
from .prompt import SYSTEM_PROMPT
from .providers import Endpoint, Entry, ProviderError, Reply, ToolResult, UserText, connect, resolve
from .tools import Toolbox
from .usage import UsageMeter

log = logging.getLogger(__name__)

DEFAULTS = AssistantSettings()
MAX_STEPS = 24  # llamadas al modelo por cada mensaje del usuario
UNANSWERED = "(Mensaje anterior sin respuesta) "

Emit = Callable[[str, dict], None]
Connector = Callable[..., Any]


class AssistantError(Exception):
    """Problema que se muestra tal cual en el chat."""


def friendly_error(exc: BaseException, endpoint: Endpoint | None = None) -> str:
    if isinstance(exc, AssistantError):
        return str(exc)
    if not isinstance(exc, ProviderError):
        return f"Error inesperado: {exc}"
    name = endpoint.provider.short if endpoint else "el proveedor de IA"
    model = endpoint.provider.model_label(endpoint.model) if endpoint else "el modelo"
    where = "Asistente → Conectar IA"
    detail = " ".join(exc.detail.split())[:240]
    local = bool(endpoint and endpoint.provider.local)
    messages = {
        "auth": f"La clave de API de {name} no es válida o fue revocada. Cámbiala en {where}.",
        "permission": f"Tu clave de {name} no tiene permiso para usar {model}. Revisa tu cuenta o elige otro modelo.",
        "not_found": f"{model} no existe o no está disponible en tu cuenta de {name}. Elige otro modelo en Ajustes → "
        "Asistente (o revisa la dirección del servidor).",
        "quota": f"Tu cuenta de {name} no tiene saldo o agotó su cuota. Revisa la facturación de tu cuenta.",
        "rate_limit": f"Tu cuenta de {name} llegó a su límite de uso por ahora. Espera un minuto y vuelve a intentarlo.",
        "overloaded": f"El servicio de {name} no responde ahora mismo (está saturado). Inténtalo de nuevo en un minuto.",
        "too_large": "La conversación es demasiado larga para este modelo. Pulsa «Nueva conversación».",
        "no_tools": f"{model} no puede usar herramientas (function calling) y el asistente las necesita. Elige otro "
        "modelo en Ajustes → Asistente.",
        "network": (
            f"No se pudo conectar con {name} en este PC. ¿Está abierto y con el servidor activo?"
            if local
            else f"No hay conexión con {name}. Revisa internet o el firewall."
        ),
        "timeout": f"{name} tardó demasiado en responder. Inténtalo de nuevo.",
        "redirect": f"La dirección de {name} redirige a otro servidor. Revisa la dirección en Ajustes → Asistente.",
        "policy": f"La dirección del servidor no está permitida: {detail}",
        "invalid_response": f"{name} respondió algo que no se entiende ({detail or 'sin detalles'}).",
    }
    if exc.kind == "bad_request":
        return f"{name} no aceptó la solicitud ({detail or 'sin detalles'}). Empieza una conversación nueva."
    return messages.get(exc.kind, f"Error de {name}: {detail or exc.kind}")


class Conversation:
    """Una conversación. El proveedor, el modelo y el nivel de razonamiento se fijan con el primer mensaje
    (cambiarlos a mitad invalidaría la caché del prompt); los límites se leen de Ajustes en cada mensaje."""

    def __init__(self, toolbox: Toolbox, secrets, connector: Connector = connect) -> None:
        self.toolbox = toolbox
        self.secrets = secrets
        self.connector = connector
        self.history: list[Entry] = []
        self.turns = 0
        self.tools = toolbox.definitions()
        self.permissions = ToolPermissions(toolbox.ctx, toolbox.ui)
        self.meter = UsageMeter(toolbox.ctx.store)
        self.session = f"lb-{uuid.uuid4().hex[:16]}"  # agrupa las llamadas en la caché del proveedor
        self._endpoint: Endpoint | None = None
        self._client = None
        self._client_id: tuple | None = None

    @property
    def settings(self) -> AssistantSettings:
        return self.toolbox.ctx.cfg.data.assistant

    @property
    def started(self) -> bool:
        """¿El modelo ya respondió? Desde entonces el historial lleva datos propios de su API (razonamiento,
        firmas) y la conversación sigue con el mismo proveedor y modelo."""
        return any(isinstance(entry, Reply) for entry in self.history)

    @property
    def endpoint(self) -> Endpoint:
        """El de Ajustes hasta que el modelo responde; después, el de la conversación."""
        if self._endpoint is None or not self.started:
            return resolve(self.settings)
        return self._endpoint

    @property
    def model(self) -> str:
        return self.endpoint.model

    def admit(self, text: str) -> str:
        """Middleware de entrada: "" si el mensaje puede enviarse; si no, el motivo (no se gasta nada)."""
        s = self.settings
        if not s.enabled:
            return "El asistente está desactivado en Ajustes → Asistente."
        if len(text) > max(200, s.max_message_chars):
            return f"El mensaje es muy largo (máximo {s.max_message_chars} caracteres). Resúmelo un poco."
        if self.turns >= max(1, s.max_turns):
            return "Esta conversación ya es larga. Pulsa «Nueva conversación» para seguir (gasta menos tokens)."
        return self.meter.admit_message(s)

    # --- estado ------------------------------------------------------------------------------
    def reset(self) -> None:
        self.history = []

    def has_key(self) -> bool:
        endpoint = self.endpoint
        return not endpoint.needs_key or bool(self.secrets.get(endpoint.key_name))

    def client(self):
        endpoint = self._endpoint or self.endpoint
        if not endpoint.model:
            raise AssistantError("Falta elegir el modelo de IA en Ajustes → Asistente.")
        key = self.secrets.get(endpoint.key_name)
        if endpoint.needs_key and not key:
            raise AssistantError(f"Falta la clave de API de {endpoint.provider.short}. Agrégala en Asistente → Conectar IA.")
        identity = (endpoint, key)
        if self._client is None or identity != self._client_id:
            self._client = self.connector(endpoint, key, system=SYSTEM_PROMPT, tools=self.tools, session=self.session)
            self._client_id = identity
        return self._client

    # --- turno -------------------------------------------------------------------------------
    def send(self, text: str, emit: Emit, cancel: threading.Event) -> None:
        """Procesa un mensaje del usuario. Bloquea hasta terminar.

        Los secretos se apartan siempre aquí (aunque quien llame ya lo haya hecho): nada que parezca una clave
        sale de este PC, venga el mensaje de donde venga.
        """
        text = self.toolbox.guard.clean(text).model_text
        problem = self.admit(text)
        if problem:
            self.toolbox.ctx.engine.policy.audit.record("asistente", "mensaje", "deny", detail=problem)
            emit("error", {"message": problem})
            return
        if not self.started:  # p. ej. el primer intento falló y el usuario cambió de proveedor
            self._endpoint = resolve(self.settings)
        self.turns += 1
        self.toolbox.cancel = cancel
        self.history.append(UserText(text))
        outcome = "error"
        try:
            outcome = self._loop(emit, cancel)
        except Exception as exc:
            if isinstance(exc, (AssistantError, ProviderError)):
                log.warning("Asistente: %s", exc)
            else:
                log.exception("Asistente: error inesperado")
            emit("error", {"message": friendly_error(exc, self._endpoint)})
        finally:
            self._settle(outcome)

    def _loop(self, emit: Emit, cancel: threading.Event) -> str:
        """Devuelve cómo terminó el turno: reply | stopped | refused."""
        for _ in range(MAX_STEPS):
            reply = self._complete_cancellable(cancel)
            if reply is None:
                emit("stopped", {})
                return "stopped"
            if reply.stop == "refusal":
                emit(
                    "refusal",
                    {"message": "No puedo ayudar con eso. Reformula la petición o configura esa parte desde las pantallas."},
                )
                return "refused"
            if reply.stop == "max_tokens" and reply.tool_calls:
                raise AssistantError("La respuesta quedó incompleta. Vuelve a intentarlo con una petición más corta.")
            self.history.append(reply)
            text = reply.text.strip()
            if not reply.tool_calls:
                if reply.stop == "pause":
                    continue
                emit("reply", {"text": text or "Listo."})
                return "reply"
            if text:
                emit("note", {"text": text})
            for call in reply.tool_calls:
                if cancel.is_set():
                    output, is_error = "El usuario detuvo el asistente antes de ejecutar esta herramienta.", True
                else:
                    emit("tool", {"id": call.id, "name": call.name, "activity": self.toolbox.activity(call.name)})
                    if isinstance(call.args, dict):
                        output, is_error = self.toolbox.run(call.name, call.args, self.permissions)
                    else:
                        output, is_error = "Los argumentos no son un JSON válido. Vuelve a intentarlo.", True
                    emit("tool_done", {"id": call.id, "name": call.name, "ok": not is_error, "detail": output if is_error else ""})
                self.history.append(ToolResult(call.id, call.name, output, is_error, call.native_id))
            if cancel.is_set():
                emit("stopped", {})
                return "stopped"
            budget = float(self.settings.daily_budget_usd or 0)
            if budget > 0 and self.meter.today()["cost"] >= budget:
                raise AssistantError("Se alcanzó el límite diario de gasto del asistente (Ajustes → Asistente).")
        raise AssistantError("El asistente dio demasiados pasos seguidos. Dime cómo quieres seguir.")

    def _settle(self, outcome: str) -> None:
        """Deja el historial listo para el siguiente mensaje.

        - Cada llamada a herramienta necesita su resultado (lo exigen todas las API).
        - Si el turno se cortó (error o «Detener»), el mensaje del usuario se marca como sin respuesta y se envía
          junto al siguiente; si el modelo lo rechazó, se quita para no repetirlo.
        """
        last = max((i for i, entry in enumerate(self.history) if isinstance(entry, Reply)), default=-1)
        if last >= 0:
            answered = {e.call_id for e in self.history[last + 1 :] if isinstance(e, ToolResult)}
            for call in self.history[last].tool_calls:
                if call.id not in answered:
                    self.history.append(ToolResult(call.id, call.name, "Interrumpido.", True, call.native_id))
        if outcome == "reply":
            return
        tail = self.history[last + 1 :]
        if outcome == "refused":
            tail = [entry for entry in tail if not isinstance(entry, UserText)]
        else:
            for entry in tail:
                if isinstance(entry, UserText) and not entry.text.startswith(UNANSWERED):
                    entry.text = UNANSWERED + entry.text
        self.history[last + 1 :] = tail

    # --- API ---------------------------------------------------------------------------------
    def _complete_cancellable(self, cancel: threading.Event) -> Reply | None:
        """La llamada corre en su propio hilo: «Detener» responde al instante y la respuesta tardía se descarta."""
        history = list(self.history)
        client = self.client()
        outcome: dict[str, Any] = {}

        def call() -> None:
            try:
                reply = client.complete(history)
                self._record(reply)  # aunque se haya pulsado «Detener»: la llamada se paga igual
                outcome["reply"] = reply
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
        return outcome["reply"]

    def _record(self, reply: Reply) -> None:
        if reply.usage is None:
            return
        endpoint = self._endpoint or self.endpoint
        cost = cost_of(price_for(endpoint, self.settings), reply.usage)
        self.meter.record(reply.usage, cost)
        log.info(
            "Asistente: %s · %s · %s · entrada %s (caché leída %s, escrita %s) · salida %s · ≈ %.4f USD",
            endpoint.provider.id,
            endpoint.model,
            reply.stop,
            reply.usage.input,
            reply.usage.cache_read,
            reply.usage.cache_write,
            reply.usage.output,
            cost,
        )

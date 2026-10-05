"""Claude (Anthropic) con el SDK oficial: razonamiento adaptativo, nivel de esfuerzo, caché del prompt y modelo de
respaldo del servidor si un filtro rechaza una petición legítima."""

from __future__ import annotations

import logging
from typing import Any, Callable

import anthropic
from anthropic.types.beta import BetaTextBlock

from ...i18n import tr
from .base import MAX_TOKENS, Entry, ProviderError, Reply, ToolCall, ToolResult, Usage, UserText, groups, rejects

log = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Parámetros que algunos modelos (antiguos o de otros servidores) no aceptan: se quitan si la API los rechaza.
_OPTIONAL = {"thinking": ("thinking",), "effort": ("effort", "output_config")}
_UNSUPPORTED: dict[tuple[str, str, str], set[str]] = {}  # (servidor, modelo, nivel) -> lo que no admite

SdkFactory = Callable[[str, str], Any]


def default_sdk_client(api_key: str, base_url: str = "") -> anthropic.Anthropic:
    # Sin seguir redirecciones: la clave solo viaja al servidor configurado (al redirigir, el cliente HTTP quita
    # «Authorization» pero no «x-api-key»).
    return anthropic.Anthropic(
        api_key=api_key,
        base_url=base_url or None,
        max_retries=2,
        timeout=anthropic.Timeout(600.0, connect=15.0),
        http_client=anthropic.DefaultHttpxClient(follow_redirects=False),
    )


def _kept_after_fallback(content: list) -> list:
    """Tras un cambio de modelo a mitad de respuesta, lo anterior al último bloque «fallback» no se reenvía."""
    last = max((i for i, block in enumerate(content) if block.type == "fallback"), default=-1)
    if last < 0:
        return list(content)
    dropped = {"thinking", "redacted_thinking", "tool_use", "server_tool_use"}
    return [block for i, block in enumerate(content) if i > last or block.type not in dropped]


def provider_error(exc: Exception) -> ProviderError:
    message = getattr(exc, "message", "") or str(exc)
    if isinstance(exc, anthropic.AuthenticationError):
        return ProviderError("auth", message, 401)
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ProviderError("permission", message, 403)
    if isinstance(exc, anthropic.NotFoundError):
        return ProviderError("not_found", message, 404)
    if isinstance(exc, anthropic.RateLimitError):
        return ProviderError("rate_limit", message, 429)
    if isinstance(exc, anthropic.APIStatusError):
        status = exc.status_code
        if 300 <= status < 400:
            return ProviderError("redirect", tr("El servidor redirige a otra dirección (HTTP {0})", status), status)
        if status == 402 or "credit balance" in message.lower():
            return ProviderError("quota", message, status)
        if status == 413:
            return ProviderError("too_large", message, status)
        if status >= 500:
            return ProviderError("overloaded", message, status)
        return ProviderError("bad_request", message, status)
    if isinstance(exc, anthropic.APITimeoutError):
        return ProviderError("timeout", message)
    if isinstance(exc, anthropic.APIConnectionError):
        return ProviderError("network", message)
    return ProviderError("invalid_response", message)


class ClaudeModel:
    def __init__(self, endpoint, key: str, system: str, tools: list[dict], sdk_factory: SdkFactory | None = None) -> None:
        self.endpoint = endpoint
        self.client = (sdk_factory or default_sdk_client)(key, endpoint.base_url)
        self.system = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
        self.tools = tools
        self.use_fallbacks = True
        self.unsupported = _UNSUPPORTED.setdefault((endpoint.base_url, endpoint.model, endpoint.effort), set())

    # --- conversación --------------------------------------------------------------------------
    def _messages(self, history: list[Entry]) -> list[dict]:
        messages: list[dict] = []
        for role, item in groups(history):
            if role == "assistant":
                messages.append({"role": "assistant", "content": item.raw})
                continue
            content: list[dict] = []
            for entry in item:  # los resultados de herramientas van primero, como pide la API
                if isinstance(entry, ToolResult):
                    block = {"type": "tool_result", "tool_use_id": entry.call_id, "content": entry.output}
                    if entry.is_error:
                        block["is_error"] = True
                    content.append(block)
            content += [{"type": "text", "text": e.text} for e in item if isinstance(e, UserText)]
            messages.append({"role": "user", "content": content})
        return messages

    def complete(self, history: list[Entry]) -> Reply:
        params: dict[str, Any] = dict(
            model=self.endpoint.model,
            max_tokens=MAX_TOKENS,
            system=self.system,
            tools=self.tools,
            messages=self._messages(history),
            cache_control={"type": "ephemeral"},
        )
        for _ in range(4):
            if "thinking" not in self.unsupported:
                params["thinking"] = {"type": "adaptive"}
            else:
                params.pop("thinking", None)
            if "effort" not in self.unsupported:
                params["output_config"] = {"effort": self.endpoint.effort}
            else:
                params.pop("output_config", None)
            try:
                if self.use_fallbacks:
                    response = self.client.beta.messages.create(betas=[FALLBACK_BETA], fallbacks="default", **params)
                else:
                    response = self.client.beta.messages.create(**params)
                return self._reply(response)
            except anthropic.BadRequestError as exc:
                text = str(exc).lower()
                if self.use_fallbacks and ("fallback" in text or "anthropic-beta" in text):
                    log.warning("Asistente: la cuenta no admite fallbacks del servidor; se sigue sin ellos (%s)", exc)
                    self.use_fallbacks = False
                    continue
                dropped = next((name for name, words in _OPTIONAL.items() if name not in self.unsupported
                                and rejects(text, words)), None)
                if dropped is None:
                    raise provider_error(exc) from None
                log.warning("Asistente: %s no admite «%s»; se sigue sin él", self.endpoint.model, dropped)
                self.unsupported.add(dropped)
            except anthropic.APIError as exc:
                raise provider_error(exc) from None
        raise ProviderError("bad_request", tr("La API rechazó la petición"))

    def _reply(self, response) -> Reply:
        usage = getattr(response, "usage", None)
        counted = Usage(
            input=getattr(usage, "input_tokens", 0) or 0,
            output=getattr(usage, "output_tokens", 0) or 0,
            cache_read=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_write=getattr(usage, "cache_creation_input_tokens", 0) or 0,
        ) if usage is not None else None
        if response.stop_reason == "refusal":
            return Reply(stop="refusal", usage=counted)
        # Una respuesta vacía no se puede reenviar: se guarda un texto mínimo para que el historial siga válido.
        content = _kept_after_fallback(response.content) or [BetaTextBlock(type="text", text=tr("Listo."))]
        calls = [ToolCall(block.id, block.name, block.input) for block in content if block.type == "tool_use"]
        text = "\n\n".join(block.text.strip() for block in content if block.type == "text" and block.text.strip())
        stop = {"max_tokens": "max_tokens", "pause_turn": "pause"}.get(response.stop_reason, "end")
        if calls and stop == "end":
            stop = "tool_use"
        return Reply(text, calls, stop, counted, raw=content)

    # --- clave y modelos ---------------------------------------------------------------------
    def check(self) -> str:
        try:
            info = self.client.models.retrieve(self.endpoint.model)
        except anthropic.APIError as exc:
            raise provider_error(exc) from None
        return getattr(info, "display_name", "") or self.endpoint.model

    def models(self) -> list[str]:
        try:
            return [model.id for model in self.client.models.list(limit=100)]
        except anthropic.APIError as exc:
            raise provider_error(exc) from None

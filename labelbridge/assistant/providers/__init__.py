"""Proveedores de IA para el asistente: Claude, OpenAI, Gemini y cualquier API compatible con OpenAI."""

from __future__ import annotations

from .base import MAX_TOKENS, Entry, ModelClient, ProviderError, Reply, ToolCall, ToolResult, Usage, UserText
from .catalog import DEFAULT_PROVIDER, EFFORTS, PROVIDERS, Provider
from .endpoint import Endpoint, check_base_url, is_local, is_plain_http, key_name, provider_of, resolve, valid_model
from .transport import HttpJson, Transport

__all__ = [
    "DEFAULT_PROVIDER",
    "EFFORTS",
    "MAX_TOKENS",
    "PROVIDERS",
    "Endpoint",
    "Entry",
    "HttpJson",
    "ModelClient",
    "Provider",
    "ProviderError",
    "Reply",
    "ToolCall",
    "ToolResult",
    "Transport",
    "Usage",
    "UserText",
    "check_base_url",
    "connect",
    "is_local",
    "is_plain_http",
    "key_name",
    "provider_of",
    "resolve",
    "valid_model",
]


def connect(
    endpoint: Endpoint,
    key: str,
    *,
    system: str = "",
    tools: list[dict] | None = None,
    session: str = "",
    transport: Transport | None = None,
    sdk_factory=None,
) -> ModelClient:
    """Cliente para el proveedor del endpoint. La dirección pasa siempre por la política de seguridad."""
    base_url = check_base_url(endpoint.base_url)
    if base_url != endpoint.base_url:
        endpoint = Endpoint(endpoint.provider, base_url, endpoint.model, endpoint.effort)
    tools = tools or []
    protocol = endpoint.provider.protocol
    if protocol == "anthropic":
        from .claude import ClaudeModel

        return ClaudeModel(endpoint, key, system, tools, sdk_factory=sdk_factory)
    http = HttpJson(transport)
    if protocol == "openai-responses":
        from .openai_api import ResponsesModel

        return ResponsesModel(endpoint, key, system, tools, session, http)
    if protocol == "openai-chat":
        from .openai_api import ChatModel

        return ChatModel(endpoint, key, system, tools, session, http)
    if protocol == "gemini":
        from .gemini import GeminiModel

        return GeminiModel(endpoint, key, system, tools, session, http)
    raise ProviderError("bad_request", f"Protocolo desconocido: {protocol}")

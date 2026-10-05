"""Google Gemini (API de Gemini con clave de Google AI Studio) por HTTPS directo, sin SDK.

El turno del modelo se reenvía tal cual (con sus firmas de razonamiento, obligatorias en las llamadas a
herramientas de Gemini 3), el prompt del sistema y las herramientas van siempre igual (caché implícita) y el
nivel de razonamiento se ajusta con thinkingLevel (Gemini 3) o thinkingBudget (Gemini 2.5).
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any
from urllib.parse import quote

from ...i18n import tr
from .base import (
    MAX_TOKENS,
    Entry,
    ProviderError,
    Reply,
    ToolCall,
    ToolResult,
    Usage,
    UserText,
    groups,
    parse_args,
    rejects,
)
from .transport import HttpJson, HttpReply

log = logging.getLogger(__name__)

_UNSUPPORTED: dict[tuple[str, str, str], set[str]] = {}  # (servidor, modelo, nivel) -> lo que no admite
SCHEMA_KEYS = {"type", "description", "enum", "properties", "required", "items", "nullable", "format", "minimum",
               "maximum", "minItems", "maxItems", "minLength", "maxLength"}
REFUSED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII", "IMAGE_SAFETY", "IMAGE_PROHIBITED_CONTENT",
           "IMAGE_RECITATION", "LANGUAGE"}
RETRY = {"MALFORMED_FUNCTION_CALL", "MALFORMED_RESPONSE"}  # el modelo se equivocó: se repite la llamada una vez
FAILED = {"UNEXPECTED_TOOL_CALL", "TOO_MANY_TOOL_CALLS", "MISSING_THOUGHT_SIGNATURE"}
LEVELS = {"low": "low", "medium": "medium", "high": "high"}
BUDGETS = {"low": 1024, "medium": 4096, "high": 12288}  # Gemini 2.5: tokens de razonamiento


def schema(spec: dict) -> dict:
    """El esquema de una herramienta en el subconjunto de OpenAPI que acepta Gemini (sin additionalProperties y con
    enum solo de texto: los valores numéricos pasan a la descripción; la app los valida igual antes de ejecutar)."""
    out: dict[str, Any] = {}
    for key, value in spec.items():
        if key not in SCHEMA_KEYS:
            continue
        if key == "properties":
            out[key] = {name: schema(sub) for name, sub in value.items()}
        elif key == "items" and isinstance(value, dict):
            out[key] = schema(value)
        elif key == "enum" and not all(isinstance(item, str) for item in value):
            values = ", ".join(str(item) for item in value)
            out["description"] = f"{spec.get('description', '')} Valores posibles: {values}.".strip()
        elif key == "description" and "description" in out:
            continue
        else:
            out[key] = value
    return out


def _error(reply: HttpReply) -> tuple[str, str, list[dict]]:
    error = reply.data.get("error") if isinstance(reply.data, dict) else None
    if isinstance(error, list) and error:  # algunos proxys devuelven una lista
        error = error[0].get("error") if isinstance(error[0], dict) else None
    if not isinstance(error, dict):
        return reply.text[:300], "", []
    return str(error.get("message") or ""), str(error.get("status") or ""), list(error.get("details") or [])


def _retry_delay(details: list[dict]) -> float:
    for detail in details:
        if str(detail.get("@type", "")).endswith("RetryInfo"):
            try:
                return float(str(detail.get("retryDelay", "0s")).rstrip("s") or 0)
            except ValueError:
                return 0.0
    return 0.0


def classify(reply: HttpReply) -> ProviderError:
    message, status_name, details = _error(reply)
    status = reply.status
    reasons = {str(d.get("reason", "")) for d in details if isinstance(d, dict)}
    lower = message.lower()
    if ("API_KEY_INVALID" in reasons or "api key not valid" in lower or "leaked" in lower or status == 401
            or status_name == "UNAUTHENTICATED"):
        return ProviderError("auth", message, status)
    if status == 402 or (status_name == "FAILED_PRECONDITION" and "billing" in lower):
        return ProviderError("quota", message, status)
    if status == 403 or status_name == "PERMISSION_DENIED":
        return ProviderError("permission", message, status)
    if status == 404 or status_name == "NOT_FOUND":
        return ProviderError("not_found", message, status)
    if status == 429 or status_name == "RESOURCE_EXHAUSTED":
        delay = _retry_delay(details)
        if 0 < delay <= 60:
            return ProviderError("rate_limit", message, status, retry_after=delay)
        return ProviderError("quota", message, status)
    if status_name == "FAILED_PRECONDITION" or (status == 400 and "location is not supported" in lower):
        return ProviderError("permission", message, status)  # p. ej. el nivel gratuito no está en tu región
    if status == 400 and ("token count" in lower or "too long" in lower or "exceeds the maximum" in lower):
        return ProviderError("too_large", message, status)
    if status >= 500:
        return ProviderError("overloaded", message, status)
    return ProviderError("bad_request", message, status)


def _retryable(reply: HttpReply) -> bool | float:
    """429 por ritmo: se espera lo que indica Google (RetryInfo; no envía Retry-After). Sin cupo: no se reintenta."""
    if reply.status == 429:
        _message, _status, details = _error(reply)
        delay = _retry_delay(details)
        return delay if 0 < delay <= 20 else False
    return reply.status in (408, 500, 502, 503, 504)


def _result(output: str) -> Any:
    try:
        value = json.loads(output)
    except (TypeError, ValueError):
        return output
    return value


class GeminiModel:
    def __init__(self, endpoint, key: str, system: str, tools: list[dict], session: str, http: HttpJson) -> None:
        self.endpoint = endpoint
        self.key = key
        self.system = system
        self.tools = tools
        self.http = http
        self.unsupported = _UNSUPPORTED.setdefault((endpoint.base_url, endpoint.model, endpoint.effort), set())

    def _headers(self) -> dict:
        return {"x-goog-api-key": self.key} if self.key else {}

    @property
    def _model_path(self) -> str:
        model = self.endpoint.model.removeprefix("models/")
        return "models/" + quote(model, safe="-._")

    def _declarations(self) -> list[dict]:
        declarations = []
        for tool in self.tools:
            declaration = {"name": tool["name"], "description": tool["description"]}
            if tool["input_schema"].get("properties"):  # un objeto sin propiedades no se acepta: se omite
                declaration["parameters"] = schema(tool["input_schema"])
            declarations.append(declaration)
        return [{"functionDeclarations": declarations}] if declarations else []

    def _contents(self, history: list[Entry]) -> list[dict]:
        contents: list[dict] = []
        for role, item in groups(history):
            if role == "assistant":
                contents.append(item.raw)  # tal cual: lleva las firmas de razonamiento
                continue
            parts: list[dict] = []
            for entry in item:
                if isinstance(entry, ToolResult):
                    response = {"error": entry.output} if entry.is_error else {"result": _result(entry.output)}
                    function_response: dict[str, Any] = {"name": entry.name, "response": response}
                    if entry.native_id:
                        function_response["id"] = entry.call_id
                    parts.append({"functionResponse": function_response})
            parts += [{"text": e.text} for e in item if isinstance(e, UserText)]
            contents.append({"role": "user", "parts": parts})
        return contents

    def _thinking(self, unsupported: set[str]) -> tuple[dict | None, str]:
        effort = self.endpoint.effort
        legacy = self.endpoint.model.removeprefix("models/").startswith("gemini-2.")
        if not legacy and "thinking_level" not in unsupported:
            return {"thinkingLevel": LEVELS.get(effort, "low")}, "thinking_level"
        if "thinking_budget" not in unsupported:
            return {"thinkingBudget": BUDGETS.get(effort, 1024)}, "thinking_budget"
        return None, ""

    def complete(self, history: list[Entry]) -> Reply:
        contents = self._contents(history)
        tools = self._declarations()
        spent: Usage | None = None  # lo que costaron los intentos repetidos (Google los cobra)
        malformed = 0
        for _ in range(6):
            body: dict[str, Any] = {
                "systemInstruction": {"parts": [{"text": self.system}]},
                "contents": contents,
                "generationConfig": {"maxOutputTokens": MAX_TOKENS},
            }
            if tools:
                body["tools"] = tools
                body["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}
            thinking, name = self._thinking(self.unsupported)
            if thinking:
                body["generationConfig"]["thinkingConfig"] = thinking
            url = f"{self.endpoint.base_url}/{self._model_path}:generateContent"
            reply = self.http.call("POST", url, self._headers(), body, retryable=_retryable)
            if reply.status >= 300:
                message, _status, _details = _error(reply)
                words = ("thinking_level", "thinkinglevel", "thinking level") if name == "thinking_level" else (
                    "thinking_budget", "thinkingbudget", "thinking budget", "thinking is not supported")
                if reply.status == 400 and name and rejects(message, words):
                    log.warning("Asistente: %s no admite «%s»; se sigue sin él", self.endpoint.model, name)
                    self.unsupported.add(name)
                    continue
                raise classify(reply)
            result = self._reply(reply.data)
            if result is not None:
                if spent is not None:
                    result.usage = spent.plus(result.usage)
                return result
            usage = self._usage(reply.data.get("usageMetadata") if isinstance(reply.data, dict) else None)
            spent = usage.plus(spent) if usage is not None else spent
            malformed += 1
            log.warning("Asistente: Gemini generó una llamada a herramienta mal formada; se repite (%s)", malformed)
            if malformed >= 2:
                raise ProviderError("invalid_response", tr("El modelo se equivocó al usar una herramienta. Vuelve a intentarlo."))
        raise ProviderError("bad_request", tr("La API rechazó la petición"))

    def _reply(self, data: Any) -> Reply | None:
        """La respuesta en formato común (None si hay que repetir la llamada)."""
        if not isinstance(data, dict):
            raise ProviderError("invalid_response", tr("Respuesta sin formato"))
        usage = self._usage(data.get("usageMetadata"))
        candidates = data.get("candidates") or []
        if not candidates:
            if (data.get("promptFeedback") or {}).get("blockReason"):
                return Reply(stop="refusal", usage=usage)
            raise ProviderError("invalid_response", tr("Respuesta sin contenido"))
        candidate = candidates[0] or {}
        finish = str(candidate.get("finishReason") or "")
        if finish in RETRY:
            return None
        if finish in FAILED:
            raise ProviderError("invalid_response", tr("Gemini cortó la respuesta ({0})", finish))
        parts = [part for part in (candidate.get("content") or {}).get("parts") or [] if isinstance(part, dict)]
        texts = [str(part["text"]).strip() for part in parts if "text" in part and not part.get("thought")]
        calls: list[ToolCall] = []
        for index, part in enumerate(parts):
            function_call = part.get("functionCall")
            if not isinstance(function_call, dict):
                continue
            call_id = str(function_call.get("id") or "")
            native = bool(call_id)
            calls.append(ToolCall(call_id or f"call_{uuid.uuid4().hex[:12]}_{index}", str(function_call.get("name")),
                                  parse_args(function_call.get("args")), native))
        if finish in REFUSED and not calls:
            return Reply(stop="refusal", usage=usage)
        stop = "max_tokens" if finish == "MAX_TOKENS" else ("tool_use" if calls else "end")
        raw = {"role": "model", "parts": parts or [{"text": "Listo."}]}
        return Reply("\n\n".join(t for t in texts if t), calls, stop, usage, raw=raw)

    @staticmethod
    def _usage(raw: Any) -> Usage | None:
        if not isinstance(raw, dict):
            return None
        prompt = int(raw.get("promptTokenCount") or 0) + int(raw.get("toolUsePromptTokenCount") or 0)
        cached = int(raw.get("cachedContentTokenCount") or 0)
        output = int(raw.get("candidatesTokenCount") or 0) + int(raw.get("thoughtsTokenCount") or 0)
        return Usage(input=max(0, prompt - cached), output=output, cache_read=cached)

    # --- clave y modelos ---------------------------------------------------------------------
    def check(self) -> str:
        reply = self.http.call("GET", f"{self.endpoint.base_url}/{self._model_path}", self._headers(), timeout=20.0,
                               retryable=_retryable)
        if reply.status >= 300:
            raise classify(reply)
        return str(reply.data.get("displayName") or self.endpoint.model)

    def models(self) -> list[str]:
        names: list[str] = []
        token = ""
        for _ in range(5):
            url = f"{self.endpoint.base_url}/models?pageSize=1000" + (f"&pageToken={quote(token)}" if token else "")
            reply = self.http.call("GET", url, self._headers(), timeout=20.0, retryable=_retryable)
            if reply.status >= 300:
                raise classify(reply)
            for model in reply.data.get("models") or []:
                if "generateContent" in (model.get("supportedGenerationMethods") or []):
                    names.append(str(model.get("name", "")).removeprefix("models/"))
            token = str(reply.data.get("nextPageToken") or "")
            if not token:
                break
        return sorted(dict.fromkeys(n for n in names if n))

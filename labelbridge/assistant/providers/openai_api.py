"""OpenAI y las API compatibles con OpenAI, por HTTPS directo (sin SDK).

- OpenAI usa la API Responses: el razonamiento cifrado se reenvía entre llamadas (no se repite ni se paga dos
  veces), nada queda guardado en sus servidores (store=false) y prompt_cache_key mejora la caché del prompt.
- Las compatibles (OpenRouter, DeepSeek, Groq, xAI, Mistral, Ollama, LM Studio, servidores propios…) usan
  Chat Completions, el formato que todas entienden.
Si un modelo no admite un parámetro opcional (nivel de razonamiento, límite de salida…), se reintenta sin él y
se recuerda para las siguientes llamadas.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any
from urllib.parse import quote

from .base import (
    MAX_TOKENS,
    Entry,
    ProviderError,
    Reply,
    ToolCall,
    ToolResult,
    Usage,
    error_output,
    groups,
    parse_args,
    rejects,
)
from .transport import HttpJson, HttpReply

log = logging.getLogger(__name__)

_UNSUPPORTED: dict[tuple[str, str, str], set[str]] = {}  # (servidor, modelo, nivel) -> lo que no admite
NO_TOOLS_HINTS = ("does not support tools", "tool use is not supported", "tools are not supported",
                  "support tool use", "function calling is not", "does not support function")
# Sin saldo o con el tope de gasto alcanzado (OpenAI usa el tipo insufficient_quota con varios códigos).
QUOTA_HINTS = ("insufficient_quota", "credit_balance", "spend_limit", "usage_limit_exceeded", "insufficient balance",
               "billing", "payment required", "out of credits", "credits")
# Lo que la API devuelve y no se debe reenviar.
OUTPUT_ONLY = ("created_by", "parsed", "parsed_arguments")


def _error_info(reply: HttpReply) -> tuple[str, str, str]:
    """(mensaje, tipo y código, parámetro) del cuerpo de error, sea cual sea el formato del servidor."""
    data = reply.data
    error: Any = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, dict):
        codes = " ".join(str(error.get(k)) for k in ("type", "code") if error.get(k))
        return str(error.get("message") or ""), codes, str(error.get("param") or "")
    if isinstance(error, str):
        return error, "", ""
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"]), str(data.get("code") or ""), ""
    return reply.text[:300], "", ""


def _is_quota(message: str, code: str) -> bool:
    """¿Falta de saldo? (no un simple «demasiadas peticiones», que se resuelve esperando)."""
    code = code.lower()
    if "rate_limit" in code or "slow_down" in code:
        return False
    return any(hint in f"{message} {code}".lower() for hint in QUOTA_HINTS)


def classify(reply: HttpReply) -> ProviderError:
    message, code, _param = _error_info(reply)
    status = reply.status
    lower = f"{message} {code}".lower()
    if any(hint in lower for hint in NO_TOOLS_HINTS):
        return ProviderError("no_tools", message, status)
    if status == 401:
        return ProviderError("auth", message, status)
    if status == 402 or (status in (400, 403, 429) and _is_quota(message, code)):
        return ProviderError("quota", message, status)
    if status == 403:
        return ProviderError("permission", message, status)
    if status == 404 or "model_not_found" in lower:
        return ProviderError("not_found", message, status)
    if status == 413 or "context_length" in lower or "maximum context" in lower or "too long" in lower:
        return ProviderError("too_large", message, status)
    if status == 429:
        return ProviderError("rate_limit", message, status)
    if status >= 500:
        return ProviderError("overloaded", message, status)
    return ProviderError("bad_request", message, status)


def _retryable(reply: HttpReply) -> bool:
    should = reply.headers.get("x-should-retry", "").lower()
    if should in ("true", "false"):
        return should == "true"
    if reply.status == 429:
        message, code, _ = _error_info(reply)
        return not _is_quota(message, code)  # sin saldo, reintentar no sirve
    return reply.status in (408, 409, 500, 502, 503, 504, 520, 521, 522, 523, 524, 529)


def _rejected(reply: HttpReply, sent: dict[str, tuple[str, ...]], unsupported: set[str]) -> str | None:
    """Parámetro opcional que la API rechazó, o None. (Together responde 403 si la salida pedida no cabe.)"""
    if reply.status not in (400, 403, 404, 422):
        return None
    message, code, param = _error_info(reply)
    text = f"{message} {code} {param}"
    for name, words in sent.items():
        if name not in unsupported and rejects(text, words):
            return name
    return None


class _OpenAIBase:
    def __init__(self, endpoint, key: str, system: str, tools: list[dict], session: str, http: HttpJson) -> None:
        self.endpoint = endpoint
        self.key = key
        self.system = system
        self.tools = tools
        self.session = session
        self.http = http
        self.unsupported = _UNSUPPORTED.setdefault((endpoint.base_url, endpoint.model, endpoint.effort), set())

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.key}"} if self.key else {}

    def _post(self, path: str, build) -> Any:
        """POST con negociación de parámetros opcionales: build(unsupported) -> (cuerpo, {parámetro: palabras})."""
        for _ in range(5):
            body, optional = build(self.unsupported)
            reply = self.http.call("POST", f"{self.endpoint.base_url}{path}", self._headers(), body, retryable=_retryable)
            if reply.status < 300:
                return reply.data
            dropped = _rejected(reply, optional, self.unsupported)
            if dropped is None:
                raise classify(reply)
            log.warning("Asistente: %s no admite «%s»; se sigue sin él", self.endpoint.model, dropped)
            self.unsupported.add(dropped)
        raise ProviderError("bad_request", "La API rechazó la petición")

    def _get(self, path: str) -> Any:
        reply = self.http.call("GET", f"{self.endpoint.base_url}{path}", self._headers(), timeout=20.0, retryable=_retryable)
        if reply.status >= 300:
            raise classify(reply)
        return reply.data

    def models(self) -> list[str]:
        data = self._get("/models")
        items = data.get("data", []) if isinstance(data, dict) else data if isinstance(data, list) else []
        names = [str(item.get("id")) for item in items if isinstance(item, dict) and item.get("id")]
        return sorted(dict.fromkeys(names))


# --- OpenAI: API Responses ----------------------------------------------------------------------

class ResponsesModel(_OpenAIBase):
    def _tools(self) -> list[dict]:
        # strict=false: los parámetros opcionales de las herramientas no son obligatorios.
        return [
            {"type": "function", "name": t["name"], "description": t["description"], "parameters": t["input_schema"],
             "strict": False}
            for t in self.tools
        ]

    def _input(self, history: list[Entry]) -> list[dict]:
        items: list[dict] = []
        for role, item in groups(history):
            if role == "assistant":
                items.extend(item.raw)
                continue
            for entry in item:
                if isinstance(entry, ToolResult):
                    items.append({"type": "function_call_output", "call_id": entry.call_id, "output": error_output(entry)})
                else:
                    items.append({"role": "user", "content": entry.text})
        return items

    def complete(self, history: list[Entry]) -> Reply:
        tools = self._tools()
        items = self._input(history)

        def build(unsupported: set[str]):
            body: dict[str, Any] = {
                "model": self.endpoint.model,
                "instructions": self.system,
                "input": items,
                "tools": tools,
                "max_output_tokens": MAX_TOKENS,
                "store": False,
            }
            optional: dict[str, tuple[str, ...]] = {}
            if "reasoning" not in unsupported:
                body["reasoning"] = {"effort": self.endpoint.effort}
                body["include"] = ["reasoning.encrypted_content"]
                optional["reasoning"] = ("reasoning", "encrypted_content", "include")
            if "prompt_cache_key" not in unsupported and self.session:
                body["prompt_cache_key"] = self.session
                optional["prompt_cache_key"] = ("prompt_cache_key",)
            if "parallel_tool_calls" not in unsupported:
                body["parallel_tool_calls"] = True
                optional["parallel_tool_calls"] = ("parallel_tool_calls",)
            return body, optional

        return self._reply(self._post("/responses", build))

    def _reply(self, data: dict) -> Reply:
        if not isinstance(data, dict):
            raise ProviderError("invalid_response", "Respuesta sin formato")
        if data.get("status") == "failed" or data.get("error"):
            error = data.get("error") or {}
            raise ProviderError("overloaded", str(error.get("message") or "La respuesta falló"))
        output = [item for item in data.get("output") or [] if isinstance(item, dict)]
        texts: list[str] = []
        calls: list[ToolCall] = []
        refusal = False
        for item in output:
            kind = item.get("type")
            if kind == "message":
                for part in item.get("content") or []:
                    if part.get("type") == "output_text" and str(part.get("text", "")).strip():
                        texts.append(str(part["text"]).strip())
                    elif part.get("type") == "refusal":
                        refusal = True
            elif kind == "function_call":
                calls.append(ToolCall(str(item.get("call_id") or item.get("id")), str(item.get("name")),
                                      parse_args(item.get("arguments"))))
        usage = self._usage(data.get("usage"))
        incomplete = str((data.get("incomplete_details") or {}).get("reason") or "")
        if (refusal and not calls) or incomplete == "content_filter":
            return Reply(stop="refusal", usage=usage)
        stop = "max_tokens" if incomplete == "max_output_tokens" else ("tool_use" if calls else "end")
        return Reply("\n\n".join(texts), calls, stop, usage, raw=self._replayable(output))

    @staticmethod
    def _replayable(output: list[dict]) -> list[dict]:
        """Lo que se reenvía en la siguiente llamada: los elementos tal como llegaron (con sus id, call_id y fase),
        como indica OpenAI con store=false. El razonamiento sin contenido cifrado no se puede reenviar, y uno al
        final (sin la respuesta que le sigue) tampoco."""
        kept = [
            {key: value for key, value in item.items() if key not in OUTPUT_ONLY}
            for item in output
            if item.get("type") != "reasoning" or item.get("encrypted_content")
        ]
        while kept and kept[-1].get("type") == "reasoning":
            kept.pop()
        return kept or [{"role": "assistant", "content": "Listo."}]

    @staticmethod
    def _usage(raw: Any) -> Usage | None:
        if not isinstance(raw, dict):
            return None
        details = raw.get("input_tokens_details") or {}
        cached = int(details.get("cached_tokens") or 0)
        written = int(details.get("cache_write_tokens") or 0)
        total_in = int(raw.get("input_tokens") or 0)
        return Usage(input=max(0, total_in - cached - written), output=int(raw.get("output_tokens") or 0),
                     cache_read=cached, cache_write=written)

    def check(self) -> str:
        data = self._get(f"/models/{quote(self.endpoint.model, safe=':')}")
        return str(data.get("id") or self.endpoint.model) if isinstance(data, dict) else self.endpoint.model


# --- Compatibles: Chat Completions ---------------------------------------------------------------

REASONING_FIELDS = ("reasoning_content", "reasoning_details", "reasoning")


class ChatModel(_OpenAIBase):
    """Solo se envían los campos estándar; lo propio de cada servidor (nivel de razonamiento, razonamiento que hay
    que devolver, clave de caché) lo dice su ficha en catalog.py."""

    def _tools(self) -> list[dict]:
        return [
            {"type": "function", "function": {"name": t["name"], "description": t["description"],
                                              "parameters": t["input_schema"]}}
            for t in self.tools
        ]

    def _messages(self, history: list[Entry], replay_reasoning: bool) -> list[dict]:
        messages: list[dict] = [{"role": "system", "content": self.system}]
        for role, item in groups(history):
            if role == "assistant":
                message = dict(item.raw)
                if not replay_reasoning:
                    for name in REASONING_FIELDS:
                        message.pop(name, None)
                messages.append(message)
                continue
            texts = []
            for entry in item:
                if isinstance(entry, ToolResult):
                    messages.append({"role": "tool", "tool_call_id": entry.call_id, "content": error_output(entry)})
                else:
                    texts.append(entry.text)
            if texts:  # varios mensajes seguidos del usuario van juntos (algunos servidores exigen alternar)
                messages.append({"role": "user", "content": "\n\n".join(texts)})
        return messages

    def complete(self, history: list[Entry]) -> Reply:
        tools = self._tools()
        provider = self.endpoint.provider
        replayed = sorted({name for entry in history if isinstance(entry, Reply) and isinstance(entry.raw, dict)
                           for name in REASONING_FIELDS if name in entry.raw})

        def build(unsupported: set[str]):
            replay = "reasoning_replay" not in unsupported
            body: dict[str, Any] = {"model": self.endpoint.model, "messages": self._messages(history, replay),
                                    "tools": tools, "stream": False}
            optional: dict[str, tuple[str, ...]] = {}
            if provider.effort_param and "effort" not in unsupported:
                if provider.effort_param == "reasoning":
                    body["reasoning"] = {"effort": self.endpoint.effort}
                    optional["effort"] = ("reasoning.effort", "reasoning effort")
                else:
                    body[provider.effort_param] = self.endpoint.effort
                    optional["effort"] = (provider.effort_param, "support thinking", "thinking is not supported")
            # Límite de salida con el nombre que use el servidor; si no lo acepta, con el otro; si tampoco, ninguno.
            names = [provider.max_param, "max_completion_tokens" if provider.max_param == "max_tokens" else "max_tokens"]
            limit = next((name for name in names if name not in unsupported), None)
            if limit:
                body[limit] = MAX_TOKENS
                optional[limit] = (limit,)
            if provider.cache_key and self.session and "prompt_cache_key" not in unsupported:
                body["prompt_cache_key"] = self.session
                optional["prompt_cache_key"] = ("prompt_cache_key",)
            if replay and replayed:
                optional["reasoning_replay"] = tuple(replayed)
            return body, optional

        return self._reply(self._post("/chat/completions", build))

    def _reply(self, data: dict) -> Reply:
        choices = data.get("choices") if isinstance(data, dict) else None
        if not choices:
            error = data.get("error") if isinstance(data, dict) else None
            raise ProviderError("invalid_response", str(error or "Respuesta sin contenido"))
        choice = choices[0] or {}
        message = choice.get("message") or {}
        finish = str(choice.get("finish_reason") or "")
        content = message.get("content")
        if isinstance(content, str):
            text = content
        else:  # lista de partes (p. ej. Mistral con razonamiento): solo el texto visible
            text = "".join(str(part.get("text", "")) for part in content or []
                           if isinstance(part, dict) and part.get("type", "text") == "text")
        calls: list[ToolCall] = []
        raw_calls: list[dict] = []
        for index, call in enumerate(message.get("tool_calls") or []):
            function = call.get("function") or {}
            call_id = str(call.get("id") or "")
            native = call_id not in ("", "null", "None")
            if not native:
                call_id = f"call_{uuid.uuid4().hex[:12]}_{index}"
            arguments = function.get("arguments")
            if not isinstance(arguments, str):
                arguments = "{}" if arguments is None else _json(arguments)
            calls.append(ToolCall(call_id, str(function.get("name")), parse_args(arguments), native))
            raw_calls.append({"id": call_id, "type": "function", "function": {"name": function.get("name"), "arguments": arguments}})
        usage = self._usage(data.get("usage"))
        if (message.get("refusal") and not calls) or finish == "content_filter":
            return Reply(stop="refusal", usage=usage)
        stop = "max_tokens" if finish in ("length", "model_length") else ("tool_use" if calls else "end")
        # Se reenvía lo estándar, el contenido tal como llegó y solo el razonamiento que pide este servidor.
        if content is None or (isinstance(content, str) and not content and not raw_calls):
            content = "" if raw_calls else "Listo."
        raw: dict[str, Any] = {"role": "assistant", "content": content}
        if raw_calls:
            raw["tool_calls"] = raw_calls
        field = self.endpoint.provider.reasoning_field
        for name in REASONING_FIELDS if field == "*" else ((field,) if field else ()):
            if message.get(name) is not None:
                raw[name] = message[name]
                if field == "*":
                    break
        return Reply(text.strip(), calls, stop, usage, raw=raw)

    @staticmethod
    def _usage(raw: Any) -> Usage | None:
        if not isinstance(raw, dict):
            return None
        prompt = int(raw.get("prompt_tokens") or 0)
        details = raw.get("prompt_tokens_details") or {}
        cached = int(details.get("cached_tokens") or raw.get("cached_tokens") or raw.get("prompt_cache_hit_tokens") or 0)
        written = int(details.get("cache_write_tokens") or 0)
        cost = raw.get("cost")
        if not isinstance(cost, (int, float)) or isinstance(cost, bool):
            ticks = raw.get("cost_in_usd_ticks")  # xAI: 10 000 000 000 = 1 USD
            cost = ticks / 1e10 if isinstance(ticks, (int, float)) and not isinstance(ticks, bool) else None
        return Usage(
            input=max(0, prompt - cached - written),
            output=int(raw.get("completion_tokens") or 0),
            cache_read=cached,
            cache_write=written,
            cost=float(cost) if cost is not None else None,
        )

    def check(self) -> str:
        """Comprueba la clave sin gastar tokens (si el servidor tiene cómo). Si el modelo existe se ve con models()."""
        path = self.endpoint.provider.key_check or "/models"
        try:
            self._get(path)
        except ProviderError as exc:
            # Sin ruta para comprobarla (p. ej. un servidor propio sin /models): se verá con el primer mensaje.
            if exc.kind != "not_found" or (path == "/models" and self.endpoint.provider.base_url):
                raise
        return self.endpoint.model


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)

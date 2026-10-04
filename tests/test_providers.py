"""Proveedores de IA del asistente: OpenAI, Gemini y API compatibles con OpenAI (servidores simulados).

Comprueba el formato exacto de cada API, que el historial se reenvía como cada una exige (razonamiento cifrado,
firmas de Gemini, razonamiento de DeepSeek), que lo que un modelo no admite se quita solo y que los límites,
permisos y filtros del asistente son los mismos con cualquier proveedor.
"""

from __future__ import annotations

import http.server
import json
import threading
from functools import partial
from types import SimpleNamespace

import pytest

from labelbridge.assistant import pricing
from labelbridge.assistant.agent import Conversation
from labelbridge.assistant.providers import (
    Endpoint,
    ProviderError,
    Usage,
    check_base_url,
    connect,
    key_name,
    provider_of,
    resolve,
)
from labelbridge.assistant.providers import claude, gemini, openai_api, transport
from labelbridge.assistant.secrets_guard import SecretsGuard
from labelbridge.assistant.tools import Toolbox
from labelbridge.secret_store import ANTHROPIC_API_KEY, SHOPIFY_CLIENT_SECRET

from conftest import wait_for
from test_assistant import FakeUI, _context

KEY = "clave-de-prueba-0123456789"
ADD_PRINTER = {"connection": "network", "ip": "192.168.1.77", "alias": "etiquetas"}


@pytest.fixture(autouse=True)
def fresh_providers(monkeypatch):
    """Sin esperas entre reintentos y sin lo aprendido en otras pruebas (parámetros no admitidos)."""
    monkeypatch.setattr(transport, "_sleep", lambda seconds: None)
    for module in (claude, gemini, openai_api):
        module._UNSUPPORTED.clear()


class FakeHttp:
    """Servidor simulado: guarda cada petición y contesta con respuestas preparadas: dict (200),
    (código, dict), (código, dict, cabeceras) o una excepción."""

    def __init__(self, *replies) -> None:
        self.replies = list(replies)
        self.requests: list[SimpleNamespace] = []

    def __call__(self, method, url, headers, body, timeout):
        self.requests.append(SimpleNamespace(method=method, url=url, headers=dict(headers),
                                             body=json.loads(body) if body else None))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        if isinstance(reply, dict):
            reply = (200, reply)
        status, data, *extra = reply
        return status, (extra[0] if extra else {}), json.dumps(data).encode("utf-8")

    @property
    def bodies(self) -> list:
        return [request.body for request in self.requests]


def chat_with(core, provider_id: str, http: FakeHttp, ui: FakeUI | None = None, key: str | None = KEY, **settings):
    assistant = core.cfg.data.assistant
    assistant.provider = provider_id
    assistant.model = settings.get("model", provider_of(provider_id).default_model)
    assistant.base_url = settings.get("base_url", "")
    if key:
        core.secrets.set(resolve(assistant).key_name, key)
    box = Toolbox(_context(core), ui or FakeUI(), SecretsGuard(core.secrets))
    return Conversation(box, core.secrets, partial(connect, transport=http))


def send(conversation: Conversation, text: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    conversation.send(text, lambda kind, data: events.append((kind, data)), threading.Event())
    return events


# --- OpenAI (API Responses) -------------------------------------------------------------------------

REASONING = {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "gAAAA-cifrado", "status": "completed"}
CALL = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "add_printer",
        "arguments": json.dumps(ADD_PRINTER), "status": "completed"}


def answer(text: str = "Listo: tu impresora quedó configurada.") -> dict:
    return {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed", "phase": "final_answer",
            "content": [{"type": "output_text", "text": text, "annotations": [], "logprobs": []}]}


def response(output: list, status: str = "completed", incomplete: dict | None = None) -> dict:
    return {
        "id": "resp_1", "object": "response", "status": status, "error": None, "incomplete_details": incomplete,
        "model": "gpt-6.1-sol", "output": output,
        "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 800, "cache_write_tokens": 0},
                  "output_tokens": 50, "output_tokens_details": {"reasoning_tokens": 20}, "total_tokens": 1050},
    }


def openai_error(status: int, message: str, kind: str, code: str | None = None, param: str | None = None):
    return (status, {"error": {"message": message, "type": kind, "param": param, "code": code}})


def test_openai_runs_tools_and_replays_its_reasoning(core):
    http = FakeHttp(response([REASONING, CALL]), response([answer()]))
    conversation = chat_with(core, "openai", http)
    events = send(conversation, "Mi Zebra está en 192.168.1.77")

    assert core.cfg.find_printer("etiquetas").host == "192.168.1.77"
    assert [kind for kind, _ in events] == ["tool", "tool_done", "reply"]
    first, second = http.requests
    assert first.url == "https://api.openai.com/v1/responses" and first.headers["Authorization"] == f"Bearer {KEY}"
    body = first.body
    assert body["model"] == "gpt-6.1-sol" and body["store"] is False and body["max_output_tokens"] == 16000
    assert body["reasoning"] == {"effort": "low"} and body["include"] == ["reasoning.encrypted_content"]
    assert body["instructions"].startswith("Eres el asistente") and body["prompt_cache_key"] == conversation.session
    tool = next(t for t in body["tools"] if t["name"] == "add_printer")
    assert tool["type"] == "function" and tool["strict"] is False and tool["parameters"]["type"] == "object"
    assert body["input"] == [{"role": "user", "content": "Mi Zebra está en 192.168.1.77"}]
    # El prefijo se repite idéntico y lo devuelto por la API vuelve tal cual (id, call_id, razonamiento cifrado).
    assert {k: v for k, v in second.body.items() if k != "input"} == {k: v for k, v in body.items() if k != "input"}
    assert second.body["input"][:3] == [body["input"][0], REASONING, CALL]
    output = second.body["input"][3]
    assert output["type"] == "function_call_output" and output["call_id"] == "call_1" and "etiquetas" in output["output"]
    today = conversation.meter.today()
    assert today["calls"] == 2 and today["input"] == 400 and today["cache_read"] == 1600 and today["output"] == 100
    assert today["cost"] == pytest.approx(2 * (200 * 2.0 + 800 * 0.10 + 50 * 10.0) / 1e6)


def test_openai_without_reasoning_support_is_negotiated_once(core):
    http = FakeHttp(
        openai_error(400, "Unsupported parameter: 'reasoning.effort' is not supported with this model.",
                     "invalid_request_error", "unsupported_parameter", "reasoning.effort"),
        response([answer("Hola")]),
        response([answer("¿Algo más?")]),
    )
    conversation = chat_with(core, "openai", http, model="gpt-4.1")
    assert send(conversation, "hola")[-1] == ("reply", {"text": "Hola"})
    send(conversation, "sigo")
    assert "reasoning" in http.bodies[0]
    assert all("reasoning" not in body and "include" not in body for body in http.bodies[1:])  # se recuerda


def test_openai_errors_are_explained(core):
    http = FakeHttp(
        openai_error(401, "Incorrect API key provided: sk-****1234.", "invalid_request_error", "invalid_api_key"),
        openai_error(429, "You exceeded your current quota.", "insufficient_quota", "credit_balance_exhausted"),
        openai_error(429, "Rate limit reached for requests", "requests", "rate_limit_exceeded"),
        response([answer("Ya respondo")]),
        (302, {}, {"location": "https://otro.example.com"}),
    )
    conversation = chat_with(core, "openai", http)
    assert "clave de API de OpenAI" in send(conversation, "uno")[-1][1]["message"]
    assert "saldo" in send(conversation, "dos")[-1][1]["message"] and len(http.requests) == 2  # sin saldo: no reintenta
    assert send(conversation, "tres")[-1] == ("reply", {"text": "Ya respondo"}) and len(http.requests) == 4
    assert "redirige" in send(conversation, "cuatro")[-1][1]["message"]


# --- Compatibles con OpenAI (Chat Completions) ------------------------------------------------------

def completion(message: dict, finish: str = "stop", usage: dict | None = None) -> dict:
    return {"id": "chatcmpl-1", "object": "chat.completion", "choices": [{"index": 0, "message": message,
            "finish_reason": finish}], "usage": usage or {"prompt_tokens": 1000, "completion_tokens": 40}}


def test_deepseek_gets_its_reasoning_back_with_tools(core):
    tool_call = {"id": "call_00_x", "type": "function",
                 "function": {"name": "set_sender", "arguments": json.dumps({"store_name": "Mi Tienda"})}}
    http = FakeHttp(
        completion({"role": "assistant", "content": None, "reasoning_content": "Debo guardar el remitente.",
                    "tool_calls": [tool_call]}, "tool_calls",
                   {"prompt_tokens": 1000, "completion_tokens": 40, "prompt_cache_hit_tokens": 900,
                    "prompt_cache_miss_tokens": 100}),
        completion({"role": "assistant", "content": "Listo, guardé el remitente."}),
    )
    conversation = chat_with(core, "deepseek", http)
    events = send(conversation, "Mi tienda se llama Mi Tienda")
    assert events[-1] == ("reply", {"text": "Listo, guardé el remitente."})
    assert core.cfg.data.labels.store_name == "Mi Tienda"
    first, second = http.requests
    assert first.url == "https://api.deepseek.com/chat/completions"
    assert first.body["messages"][0]["role"] == "system" and first.body["reasoning_effort"] == "low"
    assert first.body["max_tokens"] == 16000 and first.body["stream"] is False
    assert "tool_choice" not in first.body and "parallel_tool_calls" not in first.body
    assert first.body["tools"][0]["type"] == "function" and "parameters" in first.body["tools"][0]["function"]
    _system, _user, assistant, result = second.body["messages"]
    assert assistant == {"role": "assistant", "content": "", "tool_calls": [tool_call],
                         "reasoning_content": "Debo guardar el remitente."}
    assert result == {"role": "tool", "tool_call_id": "call_00_x", "content": result["content"]}  # sin «name»
    assert conversation.meter.today()["cache_read"] == 900


def test_compatible_servers_quirks_are_learned(core):
    """Servidor propio: sin clave, sin ids en las llamadas, argumentos como objeto y parámetros que no acepta."""
    http = FakeHttp(
        (422, {"detail": [{"loc": ["body", "reasoning_effort"], "msg": "Extra inputs are not permitted"}],
               "message": "reasoning_effort: Extra inputs are not permitted"}),
        (400, {"error": {"message": "Invalid max_tokens value, the valid range of max_tokens is [1, 8192]"}}),
        completion({"role": "assistant", "content": "Agrego la impresora.",
                    "tool_calls": [{"type": "function", "function": {"name": "add_printer", "arguments": ADD_PRINTER}}]},
                   "tool_calls"),
        completion({"role": "assistant", "content": "Listo."}),
    )
    conversation = chat_with(core, "custom", http, key=None, model="qwen3.6-27b", base_url="http://192.168.1.20:8000/v1")
    events = send(conversation, "agrega la zebra 192.168.1.77")
    assert [kind for kind, _ in events] == ["note", "tool", "tool_done", "reply"]
    assert core.cfg.find_printer("etiquetas") is not None
    assert all("Authorization" not in request.headers for request in http.requests)
    assert http.requests[0].url == "http://192.168.1.20:8000/v1/chat/completions"
    third, fourth = http.bodies[2:]
    assert "reasoning_effort" not in third and "max_tokens" not in third and third["max_completion_tokens"] == 16000
    call_id = fourth["messages"][2]["tool_calls"][0]["id"]
    assert call_id.startswith("call_") and fourth["messages"][3]["tool_call_id"] == call_id
    assert json.loads(fourth["messages"][2]["tool_calls"][0]["function"]["arguments"]) == ADD_PRINTER


def test_strict_servers_never_get_extra_fields(core):
    """Groq y Mistral rechazan campos que no conocen: ni razonamiento devuelto ni «name» en los resultados."""
    http = FakeHttp(
        completion({"role": "assistant", "content": None, "reasoning": "pienso…",
                    "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_status", "arguments": "{}"}}]},
                   "tool_calls"),
        completion({"role": "assistant", "content": "Todo bien."}),
    )
    conversation = chat_with(core, "groq", http)
    send(conversation, "¿cómo va todo?")
    first, second = http.bodies
    assert first["max_completion_tokens"] == 16000 and "max_tokens" not in first
    assistant = second["messages"][2]
    assert set(assistant) == {"role", "content", "tool_calls"}
    assert set(second["messages"][3]) == {"role", "tool_call_id", "content"}


def test_openrouter_cost_and_xai_ticks_are_used(core):
    http = FakeHttp(completion({"role": "assistant", "content": "Hola"}, usage={
        "prompt_tokens": 100, "completion_tokens": 10, "cost": 0.0123,
        "prompt_tokens_details": {"cached_tokens": 0}}))
    conversation = chat_with(core, "openrouter", http)
    send(conversation, "hola")
    assert http.bodies[0]["reasoning"] == {"effort": "low"} and "usage" not in http.bodies[0]
    assert conversation.meter.today()["cost"] == pytest.approx(0.0123)  # el costo exacto que informa OpenRouter

    http = FakeHttp(completion({"role": "assistant", "content": "Hola"}, usage={
        "prompt_tokens": 100, "completion_tokens": 10, "cost_in_usd_ticks": 25_000_000}))
    core.store.kv_set(conversation.meter._key(), "{}")
    conversation = chat_with(core, "xai", http)
    send(conversation, "hola")
    assert http.bodies[0]["prompt_cache_key"] == conversation.session
    assert conversation.meter.today()["cost"] == pytest.approx(0.0025)


def test_models_without_tools_are_explained(core):
    http = FakeHttp((400, {"error": {"message": "registry.ollama.ai/library/llama2:latest does not support tools"}}))
    conversation = chat_with(core, "ollama", http, key=None, model="llama2")
    message = send(conversation, "hola")[-1][1]["message"]
    assert "herramientas" in message and "llama2" in message


# --- Gemini ---------------------------------------------------------------------------------------

def gemini_reply(parts: list, finish: str = "STOP") -> dict:
    return {"candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": finish, "index": 0}],
            "usageMetadata": {"promptTokenCount": 5000, "cachedContentTokenCount": 4000, "candidatesTokenCount": 30,
                              "thoughtsTokenCount": 70, "totalTokenCount": 5100}}


SIGNED_CALL = {"functionCall": {"id": "fc-1", "name": "add_printer", "args": ADD_PRINTER}, "thoughtSignature": "c2lnbmF0dXJh"}


def test_gemini_runs_tools_and_keeps_thought_signatures(core):
    http = FakeHttp(gemini_reply([SIGNED_CALL]), gemini_reply([{"text": "Listo, impresora agregada."}]))
    conversation = chat_with(core, "gemini", http)
    events = send(conversation, "Mi Zebra está en 192.168.1.77")
    assert events[-1] == ("reply", {"text": "Listo, impresora agregada."})
    assert core.cfg.find_printer("etiquetas") is not None
    first, second = http.requests
    assert first.url == "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    assert first.headers["x-goog-api-key"] == KEY and "Authorization" not in first.headers
    body = first.body
    assert body["systemInstruction"]["parts"][0]["text"].startswith("Eres el asistente")
    assert body["generationConfig"] == {"maxOutputTokens": 16000, "thinkingConfig": {"thinkingLevel": "low"}}
    assert body["toolConfig"] == {"functionCallingConfig": {"mode": "AUTO"}}
    declarations = {d["name"]: d for d in body["tools"][0]["functionDeclarations"]}
    assert "additionalProperties" not in json.dumps(declarations)  # Gemini lo rechaza
    assert "parameters" not in declarations["list_windows_printers"]  # sin parámetros: se omite
    dpi = declarations["update_printer"]["parameters"]["properties"]["dpi"]
    assert "enum" not in dpi and "203, 300, 600" in dpi["description"]  # Gemini solo acepta enum de texto
    user, model, results = second.body["contents"]
    assert user == {"role": "user", "parts": [{"text": "Mi Zebra está en 192.168.1.77"}]}
    assert model == {"role": "model", "parts": [SIGNED_CALL]}  # tal cual, con su firma
    response_part = results["parts"][0]["functionResponse"]
    assert results["role"] == "user" and response_part["id"] == "fc-1" and response_part["name"] == "add_printer"
    assert isinstance(response_part["response"]["result"], dict)
    today = conversation.meter.today()
    assert today["input"] == 2000 and today["cache_read"] == 8000 and today["output"] == 200


def test_gemini_errors_and_fallbacks(core):
    invalid_key = (400, {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                                   "status": "INVALID_ARGUMENT", "details": [
                                       {"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "API_KEY_INVALID"}]}})
    busy = (429, {"error": {"code": 429, "message": "Resource exhausted", "status": "RESOURCE_EXHAUSTED", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "2s"}]}})
    no_level = (400, {"error": {"code": 400, "message": "thinking_level is not supported for this model.",
                                "status": "INVALID_ARGUMENT"}})
    http = FakeHttp(invalid_key, busy, no_level, gemini_reply([{"text": "Hola"}]),
                    gemini_reply([], "MALFORMED_FUNCTION_CALL"), gemini_reply([{"text": "Sigo"}]))
    conversation = chat_with(core, "gemini", http, model="gemini-3.5-flash")
    assert "clave de API de Google" in send(conversation, "uno")[-1][1]["message"]
    assert send(conversation, "dos")[-1] == ("reply", {"text": "Hola"})
    assert http.bodies[2]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    assert http.bodies[3]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 1024}  # se quitó y se recuerda
    assert send(conversation, "tres")[-1] == ("reply", {"text": "Sigo"})  # la llamada mal formada se repite

    http = FakeHttp(gemini_reply([{"text": "Hola"}]))
    conversation = chat_with(core, "gemini", http, model="gemini-2.5-flash")
    send(conversation, "hola")
    assert http.bodies[0]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 1024}


# --- iguales para todos -------------------------------------------------------------------------------

REPLIES = {
    "openai": lambda: response([answer("Hola")]),
    "gemini": lambda: gemini_reply([{"text": "Hola"}]),
    "deepseek": lambda: completion({"role": "assistant", "content": "Hola"}),
}
DENIED_CALL = {
    "openai": lambda: response([{"type": "function_call", "id": "fc_9", "call_id": "call_9", "name": "set_sender",
                                 "arguments": json.dumps({"store_name": "Otra"})}]),
    "gemini": lambda: gemini_reply([{"functionCall": {"name": "set_sender", "args": {"store_name": "Otra"}}}]),
    "deepseek": lambda: completion({"role": "assistant", "content": None, "tool_calls": [
        {"id": "c9", "type": "function", "function": {"name": "set_sender", "arguments": json.dumps({"store_name": "Otra"})}}]},
        "tool_calls"),
}


@pytest.mark.parametrize("provider_id", sorted(REPLIES))
def test_same_limits_permissions_and_secrets_with_every_provider(core, provider_id):
    from datetime import date

    ui = FakeUI()
    ui.approval = "deny"
    http = FakeHttp(DENIED_CALL[provider_id](), REPLIES[provider_id](), REPLIES[provider_id]())
    conversation = chat_with(core, provider_id, http, ui=ui)
    send(conversation, "cambia el remitente; mi client secret es shpss_0123456789abcdef0123456789abcdef")
    sent = json.dumps(http.bodies, ensure_ascii=False)
    assert "shpss_0123456789abcdef" not in sent and core.secrets.get(SHOPIFY_CLIENT_SECRET)  # el secreto no sale
    assert "NO dio permiso" in sent and core.cfg.data.labels.store_name != "Otra"  # el permiso se respeta

    core.store.kv_set(f"assistant:usage:{date.today().isoformat()}", json.dumps({"cost": 99.0}))
    before = len(http.requests)
    assert "límite diario" in send(conversation, "otra")[-1][1]["message"] and len(http.requests) == before

    core.store.kv_set(f"assistant:usage:{date.today().isoformat()}", "{}")
    core.cfg.data.assistant.messages_per_hour = 2
    assert send(conversation, "una más")[-1][0] == "reply"  # segunda de la hora (la bloqueada no cuenta)
    assert "por hora" in send(conversation, "y otra")[-1][1]["message"]


# --- direcciones, claves y precios ----------------------------------------------------------------

def test_server_addresses_are_checked():
    assert check_base_url("https://api.example.com/v1/") == "https://api.example.com/v1"
    for allowed in ("http://localhost:11434/v1", "http://127.0.0.1:1234/v1", "http://192.168.1.20:8000/v1",
                    "http://pc-bodega.local:1234/v1"):
        assert check_base_url(allowed)
    for denied in ("http://api.example.com/v1", "https://usuario:clave@api.example.com/v1", "https://api.example.com/v1?key=1",
                   "http://169.254.169.254/latest", "ftp://api.example.com", "https://0.0.0.0/v1", "api.example.com"):
        with pytest.raises(ProviderError) as info:
            check_base_url(denied)
        assert info.value.kind == "policy"


def test_keys_are_bound_to_their_server(core):
    openai = provider_of("openai")
    assert key_name(provider_of("anthropic"), "https://api.anthropic.com") == ANTHROPIC_API_KEY  # compatible
    assert key_name(openai, "https://api.openai.com/v1") == "assistant_key_openai"
    other = key_name(openai, "https://proxy.example.com/v1")
    assert other.startswith("assistant_key_openai_") and other != "assistant_key_openai"
    custom = provider_of("custom")
    assert key_name(custom, "https://a.example.com/v1") != key_name(custom, "https://b.example.com/v1")

    conversation = chat_with(core, "openai", FakeHttp())
    assert conversation.has_key()
    core.cfg.data.assistant.base_url = "https://proxy.example.com/v1"  # otra dirección: la clave no se envía allí
    assert not conversation.has_key()


def test_settings_resolve_to_a_usable_model():
    settings = SimpleNamespace(provider="openai", model="claude-opus-5-5", base_url="", effort="low")
    assert resolve(settings).model == "gpt-6.1-sol"  # un modelo de otro proveedor no se envía a OpenAI
    settings = SimpleNamespace(provider="openrouter", model="anthropic/claude-sonnet-5.5", base_url="", effort="max")
    assert resolve(settings).model == "anthropic/claude-sonnet-5.5" and resolve(settings).effort == "low"
    settings = SimpleNamespace(provider="no-existe", model="", base_url="", effort="high")
    assert resolve(settings).provider.id == "anthropic" and resolve(settings).model == "claude-opus-5-5"


def test_ai_keys_typed_in_chat_never_reach_the_model(core):
    guard = SecretsGuard(core.secrets)
    keys = ["sk-proj-AbCdEf0123456789AbCdEf0123", "AIzaSyA0123456789abcdefghijklmnopqrstuv", "gsk_0123456789abcdefghij0123",
            "sk-or-v1-0123456789abcdef0123456789", "xai-0123456789abcdefghijABCD", "sk-ant-api03-0123456789abcdefABCD"]
    result = guard.clean("mis claves: " + " , ".join(keys))
    assert result.ai_keys == len(keys)
    for value in keys:
        assert value not in result.model_text and value not in result.display_text
    assert not core.secrets.get("assistant_key_openai") and not core.secrets.get(ANTHROPIC_API_KEY)  # no se guardan


def test_prices_for_any_model():
    def price(provider_id: str, model: str, **custom):
        endpoint = Endpoint(provider_of(provider_id), provider_of(provider_id).base_url, model, "low")
        settings = SimpleNamespace(price_input=custom.get("i", -1.0), price_output=custom.get("o", -1.0))
        return pricing.price_for(endpoint, settings)

    sol = price("openai", "gpt-6.1-sol")
    assert (sol.source, sol.input, sol.output, sol.cache_read) == ("catalog", 2.0, 10.0, 0.10)
    assert price("openai", "gpt-5.4-mini-2026-03-17").output == 4.50  # versión con fecha
    routed = price("openrouter", "anthropic/claude-sonnet-5.5")  # OpenRouter informa el costo de cada llamada;
    assert (routed.source, routed.input, routed.output) == ("reported", 2.0, 10.0)  # la lista es el respaldo
    assert price("ollama", "qwen3.6:27b").free and price("ollama", "qwen3.6:27b").source == "local"
    unknown = price("custom", "mi-modelo")
    assert unknown.source == "estimate" and unknown.output >= 20  # prudente: el límite diario no se queda corto
    mine = price("gemini", "gemini-3.8-flash", i=1.0, o=2.0)
    assert (mine.source, mine.input, mine.output, mine.cache_read) == ("custom", 1.0, 2.0, 0.1)
    exact = Usage(input=100, output=100, cost=0.5)
    assert pricing.cost_of(price("openrouter", "x/y"), exact) == 0.5
    assert pricing.cost_of(mine, exact) == pytest.approx(300 / 1e6)  # con precio propio manda el del usuario


# --- transporte -------------------------------------------------------------------------------------

def test_real_http_never_follows_redirects_and_waits_when_asked():
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", 0)))
            self.send_response(307)
            self.send_header("Location", "https://otro.example.com/robar-clave")
            self.end_headers()

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        client = transport.HttpJson()
        with pytest.raises(ProviderError) as info:
            client.call("POST", f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                        {"Authorization": "Bearer secreta"}, {"x": 1}, timeout=5)
        assert info.value.kind == "redirect"
    finally:
        server.shutdown()

    waits: list[float] = []
    fake = FakeHttp((429, {"error": {"message": "slow down", "code": "slow_down"}}, {"retry-after": "3"}), {"ok": True})
    reply = transport.HttpJson(fake, sleep=waits.append).call("POST", "https://x.example.com", {}, {})
    assert reply.data == {"ok": True} and waits == [3.0]


# --- pantallas --------------------------------------------------------------------------------------

def test_connect_card_tests_and_saves_the_key_for_the_chosen_provider(core):
    from PySide6.QtWidgets import QApplication

    from labelbridge.ui.pages.assistant import AssistantPage

    http = FakeHttp(
        openai_error(401, "Incorrect API key provided.", "invalid_request_error", "invalid_api_key"),
        {"object": "list", "data": [{"id": "gpt-6-luna"}, {"id": "gpt-6.1-sol"}, {"id": "gpt-4.1"}]},
        {"id": "gpt-6.1-sol", "object": "model"},
    )
    page = AssistantPage(_context(core), connector=partial(connect, transport=http))
    page.provider_combo.setCurrentIndex(page.provider_combo.findData("openai"))
    assert page.model_field.currentText().startswith("GPT-6.1 Sol") and page.open_keys.isVisible() is not None

    page.key_field.setText("sk-proj-mala-0000000000000000")
    page._save_key()
    assert wait_for(lambda: (QApplication.processEvents(), page.key_result.text().startswith("✗"))[1])
    assert "clave de API de OpenAI" in page.key_result.text() and not core.secrets.get("assistant_key_openai")

    page.key_field.setText("sk-proj-buena-111111111111111111")
    page._save_key()
    assert wait_for(lambda: (QApplication.processEvents(), page.key_result.text().startswith("✓"))[1])
    settings = core.cfg.data.assistant
    assert core.secrets.get("assistant_key_openai") == "sk-proj-buena-111111111111111111"
    assert (settings.provider, settings.model, settings.base_url) == ("openai", "gpt-6.1-sol", "")
    assert [r.url for r in http.requests[1:]] == ["https://api.openai.com/v1/models",
                                                  "https://api.openai.com/v1/models/gpt-6.1-sol"]
    assert page.conversation.endpoint.provider.id == "openai" and page.input.isEnabled()
    assert "OpenAI" in page.status_text() and "OpenAI" in page.footnote.toolTip()


def test_settings_switch_provider_model_and_address(core):
    from labelbridge.ui.pages.settings import SettingsPage

    page = SettingsPage(_context(core))
    page.assistant_provider.setCurrentIndex(page.assistant_provider.findData("gemini"))
    assert page._model_text() == "gemini-3.8-flash"
    assert page.assistant_url.placeholderText().startswith("https://generativelanguage.googleapis.com")
    assert "0.75" in page.price_hint.text()
    page._save()
    settings = core.cfg.data.assistant
    assert (settings.provider, settings.model, settings.base_url) == ("gemini", "gemini-3.8-flash", "")

    page.assistant_provider.setCurrentIndex(page.assistant_provider.findData("custom"))
    page.assistant_model.setEditText("mi-modelo")
    page.assistant_url.setText("http://api.example.com/v1")  # internet sin https: no se guarda
    page._save()  # con la dirección rechazada no cambia nada: ni proveedor, ni modelo, ni dirección
    assert (settings.provider, settings.model, settings.base_url) == ("gemini", "gemini-3.8-flash", "")
    assert "https" in page.url_hint.text() and page.assistant_url.text() == "http://api.example.com/v1"
    page.assistant_url.setText("http://192.168.1.20:8000/v1")
    page.price_in.setValue(0.5)
    page.price_out.setValue(1.5)
    assert "sin cifrar" in page.url_hint.text()
    page._save()
    assert (settings.provider, settings.model, settings.base_url) == ("custom", "mi-modelo", "http://192.168.1.20:8000/v1")
    assert (settings.price_input, settings.price_output) == (0.5, 1.5)
    page.price_in.setValue(-1)
    assert page.price_in.text() == "Automático"


def test_switching_provider_after_a_failed_first_message(core):
    """Si el primer mensaje falla (p. ej. sin saldo) y el usuario cambia de IA, el siguiente ya va a la nueva."""
    http = FakeHttp(openai_error(429, "You exceeded your current quota.", "insufficient_quota", "credit_balance_exhausted"))
    conversation = chat_with(core, "openai", http)
    assert "saldo" in send(conversation, "hola")[-1][1]["message"] and not conversation.started

    http.replies.append(gemini_reply([{"text": "¡Hola! Te ayudo."}]))
    core.cfg.data.assistant.provider, core.cfg.data.assistant.model = "gemini", "gemini-3.8-flash"
    core.secrets.set(resolve(core.cfg.data.assistant).key_name, KEY)
    assert send(conversation, "¿sigues?")[-1] == ("reply", {"text": "¡Hola! Te ayudo."})
    assert "generativelanguage.googleapis.com" in http.requests[-1].url
    texts = [part["text"] for part in http.bodies[-1]["contents"][0]["parts"]]
    assert texts == ["(Mensaje anterior sin respuesta) hola", "¿sigues?"]
    assert conversation.started and conversation.endpoint.provider.id == "gemini"
    core.cfg.data.assistant.provider = "openai"  # ya respondió: la conversación sigue con Gemini
    assert conversation.endpoint.provider.id == "gemini"


# --- lo que encontró la revisión ------------------------------------------------------------------

def test_a_mistyped_port_never_breaks_the_app(core):
    from labelbridge.ui.pages.assistant import AssistantPage
    from labelbridge.ui.pages.guide import _assistant_ready
    from labelbridge.ui.pages.settings import SettingsPage

    for bad in ("https://proxy.example.com:8443x/v1", "https://proxy.example.com:99999/v1", "http://[::1"):
        with pytest.raises(ProviderError) as info:
            check_base_url(bad)
        assert info.value.kind == "policy"
    settings = core.cfg.data.assistant
    settings.provider, settings.base_url = "openai", "https://proxy.example.com:8443x/v1"  # guardada por error
    assert key_name(provider_of("openai"), settings.base_url).startswith("assistant_key_openai_")
    ctx = _context(core)
    SettingsPage(ctx)  # las pantallas se abren igual
    page = AssistantPage(ctx)
    assert not page.conversation.has_key() and _assistant_ready(ctx) is False
    core.secrets.set(resolve(settings).key_name, KEY)
    message = send(page.conversation, "hola")[-1][1]["message"]
    assert "no está permitida" in message and "puerto" in message


def test_claude_never_follows_redirects_with_the_key(core):
    seen: list[dict] = []

    class Other(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append(dict(self.headers))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    other = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Other)

    class Redirect(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            self.send_response(307)
            self.send_header("Location", f"http://localhost:{other.server_port}{self.path}")
            self.end_headers()

        def log_message(self, *args):
            pass

    first = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    for server in (other, first):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        endpoint = Endpoint(provider_of("anthropic"), f"http://127.0.0.1:{first.server_port}", "claude-opus-5-5", "low")
        with pytest.raises(ProviderError) as info:
            connect(endpoint, "sk-ant-secreta-0123456789").check()  # el cliente real del SDK
        assert info.value.kind == "redirect" and seen == []
    finally:
        first.shutdown()
        other.shutdown()


def test_stopped_calls_still_count_towards_the_daily_budget(core):
    gate = threading.Event()
    replies = [response([answer("respuesta tardía")])]

    def slow(method, url, headers, body, timeout):
        gate.wait(10)
        return 200, {}, json.dumps(replies.pop(0)).encode("utf-8")

    conversation = chat_with(core, "openai", FakeHttp())
    conversation.connector = partial(connect, transport=slow)
    cancel = threading.Event()
    events: list[str] = []
    worker = threading.Thread(target=conversation.send, args=("hola", lambda kind, data: events.append(kind), cancel))
    worker.start()
    cancel.set()
    worker.join(5)
    assert events == ["stopped"] and conversation.meter.today()["calls"] == 0
    gate.set()
    assert wait_for(lambda: conversation.meter.today()["calls"] == 1)  # la llamada se pagó: cuenta igual


def test_an_unrelated_error_does_not_switch_off_options(core):
    http = FakeHttp(
        openai_error(400, "The encrypted content for item rs_1 could not be verified.", "invalid_request_error",
                     "invalid_encrypted_content"),
        response([answer("Hola")]),
    )
    conversation = chat_with(core, "openai", http)
    assert "no aceptó" in send(conversation, "hola")[-1][1]["message"]
    send(conversation, "otra vez")
    assert all("reasoning" in body for body in http.bodies)  # sigue usando el razonamiento


def test_gemini_retries_are_billed(core):
    http = FakeHttp(gemini_reply([], "MALFORMED_FUNCTION_CALL"), gemini_reply([{"text": "Hola"}]))
    conversation = chat_with(core, "gemini", http)
    send(conversation, "hola")
    today = conversation.meter.today()
    assert today["calls"] == 1 and today["output"] == 200 and today["input"] == 2000  # los dos intentos


def test_premium_variants_are_not_priced_as_the_base_model():
    def source(provider_id: str, model: str) -> tuple:
        endpoint = Endpoint(provider_of(provider_id), provider_of(provider_id).base_url, model, "low")
        found = pricing.price_for(endpoint, SimpleNamespace(price_input=-1.0, price_output=-1.0))
        return found.source, found.input

    assert source("openai", "gpt-5.4-pro") == ("estimate", 5.0)
    assert source("openai", "gpt-6-luna-ultra")[0] == "estimate"
    assert source("openai", "gpt-5.4-2026-03-05") == ("catalog", 2.5)
    assert source("gemini", "gemini-3.1-pro-preview") == ("catalog", 2.0)
    assert source("anthropic", "claude-opus-5-5-20261001") == ("catalog", 4.0)


def test_a_rejected_address_changes_nothing_and_connect_saves_nothing(core):
    from labelbridge.ui.pages.settings import SettingsPage

    settings = core.cfg.data.assistant
    settings.provider, settings.model, settings.base_url = "ollama", "qwen3.6:27b", "http://192.168.1.50:11434/v1"
    page = SettingsPage(_context(core))
    page.assistant_provider.setCurrentIndex(page.assistant_provider.findData("openai"))
    page.assistant_url.setText("http://api.example.com/v1")
    page._save()
    assert (settings.provider, settings.model, settings.base_url) == ("ollama", "qwen3.6:27b", "http://192.168.1.50:11434/v1")

    page.require_https.setChecked(not core.cfg.data.security.require_https)
    before = core.cfg.data.security.require_https

    class Window:
        def open_assistant(self, connect=False, provider=None):
            self.opened = (connect, provider)

    window = Window()
    page.window = lambda: window
    page._open_connect()
    assert window.opened == (True, "openai")  # abre «Conecta tu IA» con el proveedor elegido aquí
    assert core.cfg.data.security.require_https == before  # y no guardó nada de Seguridad

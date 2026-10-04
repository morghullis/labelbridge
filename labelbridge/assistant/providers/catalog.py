"""Proveedores de IA que se pueden elegir para el asistente y sus precios públicos.

Son datos, no reglas: cualquier proveedor admite otro modelo o dirección en Ajustes → Asistente, y «Otra API
compatible con OpenAI» sirve para cualquier servidor que hable ese formato. Los precios (USD por millón de tokens)
solo sirven para el límite de gasto diario; se pueden sustituir por los de tu cuenta en Ajustes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ...config import ASSISTANT_EFFORTS, AssistantSettings

EFFORTS = dict(ASSISTANT_EFFORTS)
DEFAULT_PROVIDER = AssistantSettings().provider
# Para modelos sin precio conocido: alto a propósito, para que el límite diario no se quede corto.
ESTIMATE = (5.0, 25.0, 6.25, 0.5)


@dataclass(frozen=True)
class Provider:
    id: str
    name: str
    protocol: str  # anthropic | openai-responses | openai-chat | gemini
    base_url: str  # "" = la escribe el usuario
    keys_url: str = ""  # dónde se crea la clave
    key_hint: str = ""  # cómo empieza la clave (para el campo de texto)
    signup: str = ""  # breve guía para conseguir la clave
    models: tuple[tuple[str, str], ...] = ()  # (id, descripción); el primero es el recomendado
    needs_key: bool = True
    local: bool = False  # el modelo funciona en este PC o en la red local (gratis)
    any_model: bool = False  # sirve modelos de otros fabricantes (OpenRouter, servidores propios…)
    # Solo Chat Completions (cada servidor tiene sus reglas; lo que no acepte se quita solo y se recuerda):
    effort_param: str = ""  # reasoning_effort | reasoning (objeto, OpenRouter) | "" (no se envía)
    max_param: str = "max_tokens"  # o max_completion_tokens donde max_tokens está obsoleto
    reasoning_field: str = ""  # razonamiento que se devuelve en el historial: reasoning_content | reasoning |
    #                            reasoning_details | * (el que llegue) | "" (ninguno: hay servidores que lo rechazan)
    cache_key: bool = False  # enviar prompt_cache_key (mejora la caché del prompt)
    reports_cost: bool = False  # la API informa el costo exacto de cada llamada (OpenRouter, xAI)
    key_check: str = ""  # ruta para comprobar la clave si /models es público
    note: str = ""
    prices: dict[str, tuple[float, float, float, float]] = field(default_factory=dict, compare=False)

    @property
    def short(self) -> str:
        """Nombre corto para los mensajes: «OpenAI», «Google», «Ollama»…"""
        return self.name.split(" (")[0]

    @property
    def default_model(self) -> str:
        return self.models[0][0] if self.models else ""

    def model_label(self, model: str) -> str:
        return next((text.split(" · ")[0] for key, text in self.models if key == model), model)


PROVIDERS: dict[str, Provider] = {}


def _add(provider: Provider) -> None:
    PROVIDERS[provider.id] = provider


_add(
    Provider(
        "anthropic",
        "Anthropic (Claude)",
        "anthropic",
        "https://api.anthropic.com",
        keys_url="https://platform.claude.com/settings/keys",
        key_hint="sk-ant-…",
        signup="Entra a platform.claude.com, agrega saldo en Billing y crea la clave en API Keys.",
        models=(
            ("claude-opus-5-5", "Claude Opus 5.5 · el más preciso"),
            ("claude-sonnet-5-5", "Claude Sonnet 5.5 · más económico"),
        ),
        prices={
            "claude-opus-5-5": (4.0, 20.0, 5.0, 0.20),
            "claude-sonnet-5-5": (2.0, 10.0, 2.50, 0.20),
        },
    )
)

_add(
    Provider(
        "openai",
        "OpenAI (ChatGPT)",
        "openai-responses",
        "https://api.openai.com/v1",
        keys_url="https://platform.openai.com/api-keys",
        key_hint="sk-…",
        signup="Entra a platform.openai.com, agrega saldo en Billing y crea la clave en API keys.",
        models=(
            ("gpt-6.1-sol", "GPT-6.1 Sol · equilibrado"),
            ("gpt-6-astra", "GPT-6 Astra · el más capaz"),
            ("gpt-6-luna", "GPT-6 Luna · el más económico"),
        ),
        # Precios públicos (estándar, prompts de hasta 272 K tokens) a octubre de 2026.
        prices={
            "gpt-6-astra": (10.0, 50.0, 12.50, 1.00),
            "gpt-6.1-sol": (2.0, 10.0, 2.50, 0.10),
            "gpt-6-sol": (2.0, 10.0, 2.50, 0.20),
            "gpt-6-luna": (0.10, 0.50, 0.125, 0.01),
            "gpt-5.6-sol": (4.0, 20.0, 5.0, 0.40),
            "gpt-5.6-terra": (2.0, 12.0, 2.50, 0.20),
            "gpt-5.6-luna": (0.20, 1.20, 0.25, 0.02),
            "gpt-5.5": (5.0, 30.0, 5.0, 0.50),
            "gpt-5.4": (2.50, 15.0, 2.50, 0.25),
            "gpt-5.4-mini": (0.75, 4.50, 0.75, 0.075),
            "gpt-5.4-nano": (0.20, 1.25, 0.20, 0.02),
            "gpt-5-mini": (0.25, 2.0, 0.25, 0.025),
            "gpt-5-nano": (0.05, 0.40, 0.05, 0.005),
            "gpt-4.1": (2.0, 8.0, 2.0, 0.50),
            "gpt-4.1-mini": (0.40, 1.60, 0.40, 0.10),
        },
    )
)

_add(
    Provider(
        "gemini",
        "Google (Gemini)",
        "gemini",
        "https://generativelanguage.googleapis.com/v1beta",
        keys_url="https://aistudio.google.com/apikey",
        key_hint="AIza…",
        signup="Entra a aistudio.google.com con tu cuenta de Google y pulsa Get API key → Create API key. Hay un "
        "nivel gratuito con límites (en ese nivel Google puede usar las conversaciones para mejorar sus productos); "
        "para más uso, activa la facturación.",
        models=(
            ("gemini-3.8-flash", "Gemini 3.8 Flash · equilibrado"),
            ("gemini-3.1-pro-preview", "Gemini 3.1 Pro (preview) · el más capaz"),
            ("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite · el más económico"),
        ),
        # Nivel de pago estándar, prompts de hasta 200 K tokens, a octubre de 2026. La caché implícita no cobra
        # escritura. Gemini 3.6–3.8 Flash suben a 1.50 / 7.50 USD desde el 1 de enero de 2027.
        prices={
            "gemini-3.8-flash": (0.75, 3.75, 0.75, 0.075),
            "gemini-3.7-flash": (0.75, 3.75, 0.75, 0.075),
            "gemini-3.6-flash": (0.75, 3.75, 0.75, 0.075),
            "gemini-3.5-flash": (1.50, 9.0, 1.50, 0.15),
            "gemini-3.5-flash-lite": (0.30, 2.50, 0.30, 0.03),
            "gemini-3.1-flash-lite": (0.25, 1.50, 0.25, 0.025),
            "gemini-3.1-pro": (2.0, 12.0, 2.0, 0.20),
            "gemini-3-flash": (0.50, 3.0, 0.50, 0.05),
            "gemini-2.5-pro": (1.25, 10.0, 1.25, 0.125),
            "gemini-2.5-flash": (0.30, 2.50, 0.30, 0.03),
            "gemini-2.5-flash-lite": (0.10, 0.40, 0.10, 0.01),
        },
    )
)

# --- Compatibles con OpenAI (Chat Completions) ----------------------------------------------------
_add(
    Provider(
        "openrouter",
        "OpenRouter (cientos de modelos)",
        "openai-chat",
        "https://openrouter.ai/api/v1",
        keys_url="https://openrouter.ai/settings/keys",
        key_hint="sk-or-…",
        signup="Entra a openrouter.ai, agrega créditos y crea la clave en Settings → Keys. Con una sola clave usas "
        "modelos de Anthropic, OpenAI, Google, DeepSeek y más.",
        models=(
            ("anthropic/claude-sonnet-5.5", "Claude Sonnet 5.5 (vía OpenRouter)"),
            ("openai/gpt-6.1-sol", "GPT-6.1 Sol (vía OpenRouter)"),
            ("deepseek/deepseek-v4.1-flash", "DeepSeek V4.1 Flash (vía OpenRouter) · económico"),
        ),
        any_model=True,
        effort_param="reasoning",
        reasoning_field="reasoning_details",
        reports_cost=True,
        key_check="/key",
    )
)
_add(
    Provider(
        "deepseek",
        "DeepSeek",
        "openai-chat",
        "https://api.deepseek.com",
        keys_url="https://platform.deepseek.com/api_keys",
        key_hint="sk-…",
        signup="Entra a platform.deepseek.com, recarga saldo (Top up) y crea la clave en API keys.",
        models=(
            ("deepseek-flash", "DeepSeek V4.1 Flash · económico"),
            ("deepseek-v4-pro", "DeepSeek V4 Pro · el más capaz"),
        ),
        effort_param="reasoning_effort",
        reasoning_field="reasoning_content",  # con herramientas, DeepSeek exige que se devuelva
        # Horario punta; fuera de él cuesta la mitad.
        prices={"deepseek-flash": (0.30, 1.20, 0.30, 0.006), "deepseek-v4-pro": (1.32, 3.96, 1.32, 0.044)},
    )
)
_add(
    Provider(
        "groq",
        "Groq",
        "openai-chat",
        "https://api.groq.com/openai/v1",
        keys_url="https://console.groq.com/keys",
        key_hint="gsk_…",
        signup="Entra a console.groq.com y crea la clave en API Keys.",
        models=(("openai/gpt-oss-120b", "GPT-OSS 120B (en Groq) · muy rápido"),),
        any_model=True,
        effort_param="reasoning_effort",
        max_param="max_completion_tokens",
        prices={"openai/gpt-oss-120b": (0.15, 0.60, 0.15, 0.075)},
    )
)
_add(
    Provider(
        "xai",
        "xAI (Grok)",
        "openai-chat",
        "https://api.x.ai/v1",
        keys_url="https://console.x.ai",
        key_hint="xai-…",
        signup="Entra a console.x.ai, agrega créditos y crea la clave en API Keys.",
        models=(("grok-4.3", "Grok 4.3 · equilibrado"), ("grok-4.7", "Grok 4.7 · el más capaz")),
        effort_param="reasoning_effort",
        max_param="max_completion_tokens",
        cache_key=True,
        reports_cost=True,
        prices={"grok-4.3": (1.25, 2.50, 1.25, 0.20), "grok-4.7": (2.0, 6.0, 2.0, 0.50)},
    )
)
_add(
    Provider(
        "mistral",
        "Mistral AI",
        "openai-chat",
        "https://api.mistral.ai/v1",
        keys_url="https://console.mistral.ai/api-keys",
        signup="Entra a console.mistral.ai, activa un plan de pago o de prueba y crea la clave en API Keys.",
        models=(
            ("mistral-medium-latest", "Mistral Medium · equilibrado"),
            ("mistral-small-latest", "Mistral Small · económico"),
        ),
        prices={"mistral-medium-latest": (1.5, 7.5, 1.5, 0.15), "mistral-small-latest": (0.15, 0.60, 0.15, 0.015)},
    )
)
_add(
    Provider(
        "together",
        "Together AI",
        "openai-chat",
        "https://api.together.ai/v1",
        keys_url="https://api.together.ai/settings/api-keys",
        signup="Entra a together.ai, agrega créditos y crea la clave en Settings → API Keys.",
        models=(("zai-org/GLM-5.3-Flash", "GLM-5.3 Flash (en Together) · económico"),),
        any_model=True,
        effort_param="reasoning_effort",
        reasoning_field="*",
        prices={"zai-org/GLM-5.3-Flash": (0.15, 0.50, 0.15, 0.015)},
    )
)
_add(
    Provider(
        "ollama",
        "Ollama (en tu equipo)",
        "openai-chat",
        "http://localhost:11434/v1",
        keys_url="https://ollama.com/download",
        signup="Instala Ollama (ollama.com) y descarga un modelo que use herramientas, por ejemplo "
        "<code>ollama pull qwen3.6:27b</code>.",
        models=(("qwen3.6:27b", "Qwen 3.6 27B · con herramientas"),),
        needs_key=False,
        local=True,
        any_model=True,
        effort_param="reasoning_effort",
        reasoning_field="reasoning",
        note="Sube el contexto a 64000 o más (en la app de Ollama, o con OLLAMA_CONTEXT_LENGTH=64000): con el de "
        "fábrica el modelo no ve todas las instrucciones.",
    )
)
_add(
    Provider(
        "lmstudio",
        "LM Studio (en tu equipo)",
        "openai-chat",
        "http://localhost:1234/v1",
        keys_url="https://lmstudio.ai/download",
        signup="Instala LM Studio, descarga un modelo que use herramientas y en Developer pulsa «Start Server».",
        needs_key=False,
        local=True,
        any_model=True,
        note="Carga el modelo con un contexto de 32000 o más.",
    )
)
_add(
    Provider(
        "custom",
        "Otra API compatible con OpenAI",
        "openai-chat",
        "",
        signup="Escribe la dirección de tu servidor (por ejemplo https://mi-servidor.com/v1), el modelo y, si lo pide, "
        "la clave.",
        needs_key=False,
        any_model=True,
        effort_param="reasoning_effort",
        reasoning_field="*",
    )
)


def owner_of(model: str) -> str | None:
    """Proveedor oficial de un modelo conocido (para no mandar un modelo de Claude a OpenAI, por ejemplo)."""
    for provider in PROVIDERS.values():
        if not provider.any_model and any(key == model for key, _ in provider.models):
            return provider.id
    return None


# Lo que puede ir tras el nombre de un modelo de la lista y seguir siendo el mismo modelo (fecha, versión, preview).
VERSION_SUFFIX = re.compile(r"[-@:](?:\d[\d\-.]*|latest|preview(?:-[\d\-]+)?|exp(?:-?\d[\d\-]*)?)")


def _match(table: dict[str, tuple], model: str) -> tuple | None:
    for name in dict.fromkeys((model, model.replace(".", "-"))):  # «claude-sonnet-5.5» en OpenRouter
        if name in table:
            return table[name]
        for key in sorted(table, key=len, reverse=True):
            if name.startswith(key) and VERSION_SUFFIX.fullmatch(name[len(key):]):
                return table[key]
    return None


def price_of(provider_id: str, model: str) -> tuple[float, float, float, float] | None:
    """Precio público conocido del modelo, o None."""
    provider = PROVIDERS.get(provider_id)
    if provider is None or provider.local:
        return None
    found = _match(provider.prices, model)
    if found:
        return found
    if "/" in model:  # OpenRouter y similares: «openai/gpt-…», «google/gemini-…»
        vendor_model = model.split("/", 1)[1]
        for other in PROVIDERS.values():
            found = _match(other.prices, vendor_model)
            if found:
                return found
    return None

"""A qué servidor y modelo habla el asistente, con qué clave y si la dirección es segura."""

from __future__ import annotations

import hashlib
import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from ...secret_store import ANTHROPIC_API_KEY
from .base import ProviderError
from .catalog import DEFAULT_PROVIDER, EFFORTS, PROVIDERS, Provider, owner_of
from ...i18n import tr

INTERNAL_SUFFIXES = (".local", ".localhost", ".internal", ".lan", ".home.arpa")
MODEL_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}")


@dataclass(frozen=True)
class Endpoint:
    provider: Provider
    base_url: str
    model: str
    effort: str

    @property
    def key_name(self) -> str:
        return key_name(self.provider, self.base_url)

    @property
    def needs_key(self) -> bool:
        return self.provider.needs_key

    @property
    def label(self) -> str:
        return tr("{0} de {1}", self.provider.model_label(self.model) or tr("el modelo que elijas"), self.provider.short)


def provider_of(provider_id: str) -> Provider:
    return PROVIDERS.get(provider_id) or PROVIDERS[DEFAULT_PROVIDER]


def valid_model(model: str) -> bool:
    return bool(MODEL_ID.fullmatch(model or "")) and ".." not in model


def check_base_url(url: str) -> str:
    """Dirección base normalizada o ProviderError("policy").

    https siempre, salvo en este PC (localhost) o en la red local, donde se admite http para servidores como
    Ollama o LM Studio. Nada de usuario/contraseña en la URL, ni parámetros, ni direcciones reservadas.
    """
    url = (url or "").strip()
    try:
        parsed = urlparse(url)
        parsed.port  # noqa: B018 - valida el puerto (lanza ValueError si no es un número de 0 a 65535)
    except ValueError:
        raise ProviderError("policy", tr("La dirección no es válida (revisa el servidor y el puerto)")) from None
    if parsed.scheme not in ("https", "http"):
        raise ProviderError("policy", tr("La dirección debe empezar por https://"))
    if parsed.username or parsed.password:
        raise ProviderError("policy", tr("La dirección no puede llevar usuario ni contraseña"))
    if parsed.query or parsed.fragment:
        raise ProviderError("policy", tr("La dirección no puede llevar parámetros (?…) ni #"))
    host = (parsed.hostname or "").rstrip(".").lower()
    if not host:
        raise ProviderError("policy", tr("Falta el servidor en la dirección"))
    local = host == "localhost" or host.endswith(INTERNAL_SUFFIXES)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if address.is_link_local or address.is_multicast or address.is_unspecified or address.is_reserved:
            raise ProviderError("policy", tr("Esa dirección está reservada y no se permite"))
        local = local or address.is_loopback or address.is_private
    if parsed.scheme == "http" and not local:
        raise ProviderError("policy", tr("Para servidores en internet la dirección debe usar https://"))
    return url.rstrip("/")


def is_local(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_plain_http(url: str) -> bool:
    return urlparse(url).scheme == "http"


def _origin(url: str) -> str:
    """Esquema, servidor y puerto. Nunca falla: una dirección mal escrita da un origen propio (sin clave)."""
    text = (url or "").strip().lower()
    try:
        parsed = urlparse(text)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return f"invalid:{text}"
    return f"{parsed.scheme}://{parsed.hostname or ''}:{port}"


def key_name(provider: Provider, base_url: str) -> str:
    """Nombre de la clave en el almacén seguro. Va ligada al servidor: si se cambia la dirección, la clave
    guardada no se envía al servidor nuevo (hay que volver a pegarla)."""
    official = bool(provider.base_url) and _origin(base_url) == _origin(provider.base_url)
    if provider.id == "anthropic" and official:
        return ANTHROPIC_API_KEY  # nombre de las versiones anteriores
    name = f"assistant_key_{provider.id}"
    if not official:
        name += "_" + hashlib.sha256(_origin(base_url).encode("utf-8")).hexdigest()[:12]
    return name


def resolve(settings) -> Endpoint:
    """Proveedor, dirección, modelo y nivel según Ajustes (con valores seguros si algo no es válido)."""
    provider = provider_of(getattr(settings, "provider", "") or DEFAULT_PROVIDER)
    base_url = (getattr(settings, "base_url", "") or "").strip() or provider.base_url
    model = (settings.model or "").strip()
    owner = owner_of(model)
    foreign = not provider.any_model and owner is not None and owner != provider.id
    if not valid_model(model) or foreign:
        model = provider.default_model  # p. ej. un modelo de Claude guardado y el proveedor cambiado a OpenAI
    effort = settings.effort if settings.effort in EFFORTS else "low"
    return Endpoint(provider, base_url.rstrip("/"), model, effort)

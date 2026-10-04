"""Los datos secretos que se escriben en el chat no salen de este PC.

Antes de enviar un mensaje al asistente se buscan claves y tokens:
- los de formato conocido (shpss_…, shpat_…, sk-ant-…) se guardan directamente en el almacén seguro;
- el resto (lo que va después de «contraseña», «secret», «token»…) se aparta en una caja local y el
  asistente solo ve una referencia ([SECRETO_1]) que puede asignar con la herramienta save_secret.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass, field

from ..secret_store import ANTHROPIC_API_KEY, SHOPIFY_ACCESS_TOKEN, SHOPIFY_CLIENT_SECRET

# (nombre en el almacén seguro, descripción, patrón)
KNOWN_SECRETS = (
    (SHOPIFY_CLIENT_SECRET, "Client secret de Shopify", re.compile(r"\bshpss_[A-Za-z0-9]{16,}")),
    (SHOPIFY_ACCESS_TOKEN, "token de acceso de Shopify", re.compile(r"\bshp(?:at|ca|pa)_[A-Za-z0-9]{16,}")),
    (ANTHROPIC_API_KEY, "clave de API de Anthropic", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}")),
)

KEYWORD = re.compile(
    r"(?i)\b(secret|secreto|token|clave|contraseña|contrasena|password|passwd|pwd|pass|api[ _-]?key|llave)\b"
)
WINDOW = 6  # palabras que se revisan después de la palabra clave (misma línea)
# Comillas y signos que rodean al dato; «!» y «?» se conservan porque suelen ser parte de la contraseña.
LEADING = "\"'`«“‘([{<¡¿"
TRAILING = "\"'`»”’)]}>.,;:"


def _looks_random(token: str) -> bool:
    """¿Parece una clave (mezcla de letras, números o símbolos) y no una palabra, IP o dominio?"""
    if len(token) < 8 or "://" in token or "@" in token or token.lower().endswith((".com", ".net", ".org")):
        return False
    try:
        ipaddress.ip_address(token)
        return False
    except ValueError:
        pass
    classes = sum(
        (
            any(c.islower() for c in token),
            any(c.isupper() for c in token),
            any(c.isdigit() for c in token),
            any(not c.isalnum() for c in token),
        )
    )
    return classes >= 2 or (len(token) >= 20 and token.isalnum())


@dataclass
class GuardResult:
    model_text: str  # lo que recibe el asistente (sin secretos)
    display_text: str  # lo que se muestra en la burbuja del chat
    saved: list[str] = field(default_factory=list)  # descripciones de lo guardado en el almacén seguro
    refs: dict[str, str] = field(default_factory=dict)  # SECRETO_n -> valor (solo en este PC)


class SecretsGuard:
    def __init__(self, secrets) -> None:
        self.secrets = secrets
        self.vault: dict[str, str] = {}

    def take(self, ref: str) -> str | None:
        return self.vault.get(ref.strip().strip("[]").upper())

    def clean(self, text: str) -> GuardResult:
        result = GuardResult(text, text)
        # 1) Formatos conocidos: se guardan de inmediato donde corresponde.
        for secret_name, description, pattern in KNOWN_SECRETS:
            for value in dict.fromkeys(pattern.findall(result.model_text)):
                self.secrets.set(secret_name, value)
                result.saved.append(description)
                note = f"[🔒 dato guardado de forma segura en este PC: {description}]"
                result.model_text = result.model_text.replace(value, note)
                result.display_text = result.display_text.replace(value, f"🔒 {value[:6]}••••{value[-4:]}")
        # 2) Lo que sigue a «contraseña», «secret», «token»… y parece una clave.
        found: list[str] = []
        words = result.model_text.split()
        for index, word in enumerate(words):
            match = KEYWORD.search(word)
            if not match:
                continue
            candidates = words[index + 1 : index + 1 + WINDOW]
            rest = word[match.end():]
            if rest[:1] in (":", "="):  # password=Abc123… o secret:xyz…
                candidates = [rest[1:], *candidates]
            for candidate in candidates:
                token = candidate.lstrip(LEADING).rstrip(TRAILING)
                if "🔒" not in candidate and _looks_random(token) and token not in found:
                    found.append(token)
        for token in found:
            existing = next((ref for ref, value in self.vault.items() if value == token), None)
            ref = existing or f"SECRETO_{len(self.vault) + 1}"
            self.vault[ref] = token
            result.refs[ref] = token
            result.model_text = result.model_text.replace(token, f"[{ref}]")
            result.display_text = result.display_text.replace(token, "🔒 ••••••")
        if result.refs:
            refs = ", ".join(result.refs)
            result.model_text += (
                f"\n\n[Nota de la app: {refs} son datos secretos que el usuario escribió; se quedaron en este PC y tú no "
                "puedes verlos. Guárdalos con save_secret indicando a qué corresponde cada uno.]"
            )
        return result

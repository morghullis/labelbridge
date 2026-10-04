"""Borra de los registros lo que nunca debe quedar escrito: claves, tokens, firmas de URLs y correos."""

from __future__ import annotations

import logging
import re

_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bshp(?:ss|at|ca|pa)_[A-Za-z0-9]{6,}"), "shp_•••"),
    (re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{6,}"), "sk-ant-•••"),
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{12,}"), "sk-•••"),  # OpenAI, OpenRouter, DeepSeek…
    (re.compile(r"\bAIza[0-9A-Za-z_\-]{20,}"), "AIza•••"),  # Google (Gemini)
    (re.compile(r"\b(gsk|xai|hf)[_\-][A-Za-z0-9]{16,}"), r"\1_•••"),  # Groq, xAI, Hugging Face
    (re.compile(r"\blb_[A-Za-z0-9_\-]{16,}"), "lb_•••"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=\-]{8,}"), r"\1 •••"),
    (
        re.compile(
            r"(?i)([?&](?:token|access_token|key|api_key|apikey|signature|sig|x-amz-signature|x-amz-credential|"
            r"x-goog-signature|x-goog-credential|auth|password|secret)=)[^&\s\"']+"
        ),
        r"\1•••",
    ),
    (re.compile(r"(?i)(\"(?:client_secret|access_token|password|api_key|token)\"\s*:\s*\")[^\"]+"), r"\1•••"),
    (re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"), "•••@•••"),
)


def redact(text: str) -> str:
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    """Formatea el registro (incluidas las trazas de error) y después borra los datos sensibles."""

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))

"""Pistas para sugerir la configuración de una impresora a partir de su nombre o modelo."""

from __future__ import annotations

import re

LABEL_HINTS = (
    "zebra", "zdesigner", "zpl", "rollo", "munbyn", "dymo", "label", "4x6", "thermal", "termica", "térmica",
    "xprinter", "beeprt", "idprt", "itari", "polono", "nelko", "jadens", "phomemo", "brother ql", "tsc", "godex",
    "honeywell", "intermec", "sato", "citizen", "arkscan", "hprt",
)


def looks_like_label_printer(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in LABEL_HINTS)


def looks_like_zpl(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in ("zebra", "zdesigner", "zpl"))


def suggest_alias(name: str, existing: list[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "impresora"
    alias, counter = base, 2
    while alias in existing:
        alias, counter = f"{base}-{counter}", counter + 1
    return alias

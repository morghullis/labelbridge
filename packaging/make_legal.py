"""Los términos de uso y la política de privacidad para la página «Acuerdo de licencia» del instalador de Windows
(Inno Setup muestra RTF y pide «Acepto el acuerdo» antes de seguir).

Uso: python packaging/make_legal.py  →  build/legal/license-es.rtf y license-en.rtf

Lee los mismos textos que muestra la app (assets/legal/*.md): nunca hay dos versiones distintas de un documento.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LEGAL = ROOT / "assets" / "legal"
OUT = ROOT / "build" / "legal"


def _escape(text: str) -> str:
    """Texto plano a RTF: llaves y barras escapadas, y todo lo que no es ASCII como \\uN? (tildes, eñes, «»)."""
    out = []
    for char in text:
        code = ord(char)
        if char in "\\{}":
            out.append("\\" + char)
        elif code < 128:
            out.append(char)
        else:
            out.append(f"\\u{code if code < 32768 else code - 65536}?")
    return "".join(out)


def _inline(text: str) -> str:
    """**negrita** y `código` de una línea de Markdown."""
    parts = re.split(r"(\*\*[^*]+\*\*)", text.replace("`", ""))
    return "".join(f"{{\\b {_escape(part[2:-2])}}}" if part.startswith("**") else _escape(part) for part in parts)


def markdown_to_rtf(markdown: str) -> str:
    lines = ["{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0\\fswiss Segoe UI;}}\\f0\\fs19\\sa80"]
    for raw in markdown.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line == "---":
            lines.append("\\par\\pard\\sa80")
        elif line.startswith("# "):
            lines.append(f"{{\\pard\\sb120\\sa120\\b\\fs30 {_escape(line[2:])}\\par}}")
        elif line.startswith("## "):
            lines.append(f"{{\\pard\\sb160\\sa60\\b\\fs22 {_escape(line[3:])}\\par}}")
        elif line.startswith("- "):
            lines.append(f"{{\\pard\\li360\\fi-220\\sa40 \\bullet\\tab {_inline(line[2:])}\\par}}")
        else:
            lines.append(f"{{\\pard\\sa100 {_inline(line)}\\par}}")
    lines.append("}")
    return "\n".join(lines)


def build() -> list[Path]:
    OUT.mkdir(parents=True, exist_ok=True)
    made = []
    for lang in ("es", "en"):
        text = "\n\n---\n\n".join((LEGAL / f"{name}.{lang}.md").read_text(encoding="utf-8")
                                  for name in ("terms", "privacy"))
        path = OUT / f"license-{lang}.rtf"
        path.write_text(markdown_to_rtf(text), encoding="ascii")
        made.append(path)
    return made


if __name__ == "__main__":
    for made in build():
        print(made.relative_to(ROOT))

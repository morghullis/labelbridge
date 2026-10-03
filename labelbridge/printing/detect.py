"""Detección del formato de un archivo por su contenido."""

from __future__ import annotations

_IMAGE_MAGIC = (
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"BM",
    b"II*\x00",
    b"MM\x00*",
)

_CONTENT_TYPES = {
    "application/pdf": "pdf",
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/bmp": "image",
    "image/tiff": "image",
    "application/zpl": "zpl",
    "x-application/zpl": "zpl",
}


def detect_format(data: bytes, content_type: str = "") -> str:
    head = data[:2048]
    stripped = head.lstrip()
    if stripped.startswith(b"%PDF"):
        return "pdf"
    if any(head.startswith(m) for m in _IMAGE_MAGIC) or (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
        return "image"
    upper = stripped.upper()
    if b"^XA" in upper or upper.startswith((b"~DG", b"~DY", b"~JA", b"~HS", b"^XA")):
        return "zpl"
    mapped = _CONTENT_TYPES.get(content_type.split(";")[0].strip().lower())
    return mapped or "raw"

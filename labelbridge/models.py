"""Formato de los trabajos de impresión (mismo esquema para API local, n8n y Shopify).

Ver docs/PROTOCOLO.md.
"""

from __future__ import annotations

import base64
import binascii
import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

MAX_CONTENT_BYTES = 25 * 1024 * 1024
MAX_COPIES = 50

FORMAT_ALIASES = {
    "auto": "auto",
    "pdf": "pdf",
    "image": "image",
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "gif": "image",
    "bmp": "image",
    "zpl": "zpl",
    "raw": "raw",
    "epl": "raw",
    "tspl": "raw",
    "dpl": "raw",
    "escpos": "raw",
    "order": "order",
    "order_label": "order",
    "label": "order",
}

STATUS_LABELS = {
    "queued": "En cola",
    "printing": "Imprimiendo",
    "done": "Impreso",
    "failed": "Error",
    "canceled": "Cancelado",
}

def status_text(job: dict) -> str:
    """Estado legible; un trabajo en cola con error está esperando reintento."""
    if job.get("status") == "queued" and job.get("error"):
        return "Reintentando"
    return STATUS_LABELS.get(job.get("status", ""), job.get("status", ""))


SOURCE_LABELS = {
    "api": "API local",
    "mqtt": "n8n · tiempo real",
    "shopify": "Shopify",
    "manual": "Manual",
    "test": "Prueba",
}


class JobError(ValueError):
    """El trabajo recibido no es válido (error del cliente, no se reintenta)."""


@dataclass
class JobRequest:
    external_id: str
    title: str = ""
    printer: str = ""
    copies: int = 1
    format: str = "auto"
    url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    content: bytes | None = None
    order: dict[str, Any] | None = None
    meta: dict[str, Any] = field(default_factory=dict)


_DATA_URI = re.compile(r"^data:[^;,]*(;base64)?,", re.IGNORECASE)


def _decode_base64(value: str) -> bytes:
    text = _DATA_URI.sub("", value.strip())
    text = re.sub(r"\s+", "", text)
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise JobError("content_base64 no es base64 válido") from exc
    if not data:
        raise JobError("content_base64 está vacío")
    return data


def parse_job(obj: Any, *, require_id: bool = True) -> JobRequest:
    if not isinstance(obj, dict):
        raise JobError("Cada trabajo debe ser un objeto JSON")

    external_id = str(obj.get("id") or "").strip()
    if not external_id:
        if require_id:
            raise JobError("Falta el campo 'id'")
        external_id = uuid.uuid4().hex
    if len(external_id) > 200:
        raise JobError("'id' es demasiado largo (máx. 200)")

    fmt_raw = str(obj.get("format") or "auto").strip().lower()
    fmt = FORMAT_ALIASES.get(fmt_raw)
    if fmt is None:
        raise JobError(f"Formato desconocido: {fmt_raw}")

    raw_copies = obj.get("copies")
    try:
        copies = 1 if raw_copies in (None, "") else int(raw_copies)
    except (TypeError, ValueError) as exc:
        raise JobError("'copies' debe ser un número") from exc
    if not 1 <= copies <= MAX_COPIES:
        raise JobError(f"'copies' debe estar entre 1 y {MAX_COPIES}")

    sources = [k for k in ("url", "content_base64", "content", "order") if obj.get(k) not in (None, "", {})]
    if len(sources) != 1:
        raise JobError("Incluye exactamente uno de: url, content_base64, content u order")
    source = sources[0]

    req = JobRequest(
        external_id=external_id,
        printer=str(obj.get("printer") or "").strip()[:200],
        copies=copies,
        format=fmt,
    )

    if source == "url":
        url = str(obj["url"]).strip()
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            raise JobError("'url' debe empezar por http:// o https://")
        req.url = url
        headers = obj.get("headers") or {}
        if not isinstance(headers, dict):
            raise JobError("'headers' debe ser un objeto")
        req.headers = {str(k): str(v) for k, v in headers.items()}
    elif source == "content_base64":
        req.content = _decode_base64(str(obj["content_base64"]))
    elif source == "content":
        content = obj["content"]
        if not isinstance(content, str):
            raise JobError("'content' debe ser texto (usa content_base64 para binarios)")
        req.content = content.encode("utf-8")
        if fmt == "auto":
            req.format = "zpl" if "^XA" in content.upper() else "raw"
    else:
        order = obj["order"]
        if not isinstance(order, dict):
            raise JobError("'order' debe ser un objeto (pedido de Shopify)")
        req.order = order
        req.format = "order"

    if req.content is not None and len(req.content) > MAX_CONTENT_BYTES:
        raise JobError("El contenido supera el máximo de 25 MB")
    if req.format == "order" and req.order is None:
        raise JobError("format 'order' requiere el campo 'order'")

    meta = obj.get("meta")
    req.meta = meta if isinstance(meta, dict) else {}
    title = obj.get("title") or (req.order or {}).get("name") or external_id
    req.title = str(title)[:200]
    return req


def parse_jobs_payload(payload: Any, *, require_id: bool) -> list[JobRequest]:
    """Acepta un trabajo, una lista o {"jobs": [...]}."""
    if isinstance(payload, dict) and isinstance(payload.get("jobs"), list):
        items = payload["jobs"]
    elif isinstance(payload, list):
        items = payload
    else:
        items = [payload]
    if not items:
        raise JobError("No se recibió ningún trabajo")
    if len(items) > 100:
        raise JobError("Máximo 100 trabajos por solicitud")
    return [parse_job(item, require_id=require_id) for item in items]

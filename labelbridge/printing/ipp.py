"""Cliente IPP mínimo (RFC 8010/8011): Get-Printer-Attributes y Print-Job.

Permite imprimir PDF en impresoras de red modernas (IPP Everywhere / AirPrint) sin driver.
"""

from __future__ import annotations

import hashlib
import http.client
import itertools
import ssl
import struct
from typing import Any

# Etiquetas de grupo
OPERATION_ATTRS = 0x01
JOB_ATTRS = 0x02
END_ATTRS = 0x03
# Etiquetas de valor
INTEGER = 0x21
BOOLEAN = 0x22
ENUM = 0x23
TEXT = 0x41
NAME = 0x42
KEYWORD = 0x44
URI = 0x45
CHARSET = 0x47
LANGUAGE = 0x48
MIME = 0x49

OP_PRINT_JOB = 0x0002
OP_GET_PRINTER_ATTRIBUTES = 0x000B

_request_ids = itertools.count(1)


class IppError(Exception):
    pass


class CertificateMismatch(IppError):
    pass


def _attr(tag: int, name: str, values: list[Any]) -> bytes:
    out = b""
    for index, value in enumerate(values):
        attr_name = name.encode("utf-8") if index == 0 else b""
        if tag in (INTEGER, ENUM):
            raw = struct.pack(">i", int(value))
        elif tag == BOOLEAN:
            raw = b"\x01" if value else b"\x00"
        else:
            raw = str(value).encode("utf-8")
        out += struct.pack(">BH", tag, len(attr_name)) + attr_name + struct.pack(">H", len(raw)) + raw
    return out


def encode_request(
    operation: int,
    printer_uri: str,
    operation_attrs: list[tuple[int, str, list[Any]]] | None = None,
    job_attrs: list[tuple[int, str, list[Any]]] | None = None,
    request_id: int | None = None,
) -> bytes:
    rid = request_id if request_id is not None else next(_request_ids)
    body = struct.pack(">BBHI", 2, 0, operation, rid)
    body += bytes([OPERATION_ATTRS])
    body += _attr(CHARSET, "attributes-charset", ["utf-8"])
    body += _attr(LANGUAGE, "attributes-natural-language", ["en"])
    body += _attr(URI, "printer-uri", [printer_uri])
    for tag, name, values in operation_attrs or []:
        body += _attr(tag, name, values)
    if job_attrs:
        body += bytes([JOB_ATTRS])
        for tag, name, values in job_attrs:
            body += _attr(tag, name, values)
    body += bytes([END_ATTRS])
    return body


def decode_response(data: bytes) -> tuple[int, dict[str, list[Any]]]:
    """Devuelve (status-code, atributos) aplanando todos los grupos."""
    if len(data) < 8:
        raise IppError("Respuesta IPP demasiado corta")
    _major, _minor, status, _rid = struct.unpack(">BBHI", data[:8])
    attrs: dict[str, list[Any]] = {}
    pos, last_name = 8, ""
    while pos < len(data):
        tag = data[pos]
        pos += 1
        if tag == END_ATTRS:
            break
        if tag < 0x10:  # inicio de grupo
            continue
        if pos + 2 > len(data):
            break
        (name_len,) = struct.unpack(">H", data[pos : pos + 2])
        pos += 2
        name = data[pos : pos + name_len].decode("utf-8", "replace")
        pos += name_len
        (value_len,) = struct.unpack(">H", data[pos : pos + 2])
        pos += 2
        raw = data[pos : pos + value_len]
        pos += value_len
        if tag in (INTEGER, ENUM) and value_len == 4:
            value: Any = struct.unpack(">i", raw)[0]
        elif tag == BOOLEAN:
            value = raw != b"\x00"
        elif 0x40 <= tag <= 0x4F:
            value = raw.decode("utf-8", "replace")
        else:
            value = raw
        if name:
            last_name = name
            attrs.setdefault(name, []).append(value)
        elif last_name:
            attrs[last_name].append(value)
    return status, attrs


def _post(
    host: str, port: int, path: str, body: bytes, tls: bool, timeout: float, pin: str = ""
) -> tuple[bytes, str]:
    """POST application/ipp. Con TLS devuelve la huella SHA-256 del certificado de la impresora.

    Las impresoras de red usan certificados autofirmados, así que no se valida contra una CA:
    se fija la huella la primera vez (TOFU) y se rechaza la conexión si después cambia.
    """
    fingerprint = ""
    if tls:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        conn: http.client.HTTPConnection = http.client.HTTPSConnection(host, port, timeout=timeout, context=ctx)
    else:
        conn = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        conn.connect()
        if tls:
            der = conn.sock.getpeercert(binary_form=True) or b""  # type: ignore[union-attr]
            fingerprint = hashlib.sha256(der).hexdigest()
            if pin and fingerprint != pin.lower():
                raise CertificateMismatch(
                    f"El certificado de {host} cambió desde que se configuró la impresora. "
                    "Si es correcto, edita la impresora y marca «Volver a confiar en el certificado»."
                )
        conn.request("POST", path or "/", body=body, headers={"Content-Type": "application/ipp"})
        resp = conn.getresponse()
        payload = resp.read()
        if resp.status != 200:
            raise IppError(f"HTTP {resp.status} desde {host}:{port}{path}")
        return payload, fingerprint
    finally:
        conn.close()


def printer_uri(host: str, port: int, path: str, tls: bool = False) -> str:
    scheme = "ipps" if tls else "ipp"
    return f"{scheme}://{host}:{port}{path if path.startswith('/') else '/' + path}"


def get_printer_attributes(
    host: str, port: int = 631, path: str = "/ipp/print", tls: bool = False, timeout: float = 4.0
) -> dict[str, list[Any]]:
    body = encode_request(
        OP_GET_PRINTER_ATTRIBUTES,
        printer_uri(host, port, path, tls),
        operation_attrs=[
            (
                KEYWORD,
                "requested-attributes",
                [
                    "printer-make-and-model",
                    "printer-name",
                    "printer-info",
                    "printer-state",
                    "document-format-supported",
                ],
            )
        ],
    )
    payload, _ = _post(host, port, path, body, tls, timeout)
    status, attrs = decode_response(payload)
    if status >= 0x0100:
        raise IppError(f"La impresora respondió con error IPP 0x{status:04x}")
    return attrs


def print_job(
    host: str,
    port: int,
    path: str,
    document: bytes,
    *,
    mime: str = "application/pdf",
    job_name: str = "LabelBridge",
    copies: int = 1,
    tls: bool = False,
    pin: str = "",
    timeout: float = 60.0,
    scaling: str = "",
) -> tuple[int, str]:
    """Envía un trabajo. Devuelve (job-id, huella del certificado si hay TLS). `scaling="none"` pide tamaño real
    (las impresoras que no lo entienden lo ignoran)."""
    job_attrs = [(INTEGER, "copies", [copies])] if copies > 1 else []
    if scaling:
        job_attrs.append((KEYWORD, "print-scaling", [scaling]))
    body = encode_request(
        OP_PRINT_JOB,
        printer_uri(host, port, path, tls),
        operation_attrs=[
            (NAME, "requesting-user-name", ["LabelBridge"]),
            (NAME, "job-name", [job_name[:255] or "LabelBridge"]),
            (MIME, "document-format", [mime]),
        ],
        job_attrs=job_attrs or None,
    )
    payload, fingerprint = _post(host, port, path, body + document, tls, timeout, pin)
    status, attrs = decode_response(payload)
    if status >= 0x0100:
        message = (attrs.get("status-message") or [""])[0]
        raise IppError(f"La impresora rechazó el trabajo (IPP 0x{status:04x}) {message}".strip())
    return int((attrs.get("job-id") or [0])[0]), fingerprint

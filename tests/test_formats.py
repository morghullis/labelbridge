"""Código de barras, ZPL, detección de formato e IPP."""

from __future__ import annotations

import struct

import pytest

from labelbridge.printing import ipp
from labelbridge.printing.barcode import PATTERNS, code128_modules
from labelbridge.printing.detect import detect_format
from labelbridge.printing.zpl import _compress_row, zpl_text


def test_code128_table_is_consistent():
    assert len(PATTERNS) == 107
    assert all(sum(map(int, p)) == 11 for p in PATTERNS[:106])
    assert len(set(PATTERNS)) == 107


@pytest.mark.parametrize("value", ["1001", "123456", "ABC-xyz", "#A1B2", "Hola Mundo!"])
def test_code128_matches_reference(value):
    barcode = pytest.importorskip("barcode.codex")
    assert code128_modules(value) == barcode.Code128(value).build()[0]


def _decode_code128(modules: str) -> str:
    """Decodificador mínimo: valida patrones, checksum y STOP."""
    runs, current, count = [], modules[0], 0
    for bit in modules:
        if bit == current:
            count += 1
        else:
            runs.append(count)
            current, count = bit, 1
    runs.append(count)
    lookup = {p: i for i, p in enumerate(PATTERNS)}
    values = [lookup["".join(map(str, runs[i : i + 6]))] for i in range(0, len(runs) - 7, 6)]
    assert "".join(map(str, runs[-7:])) == PATTERNS[106]
    start, *data, checksum = values
    assert (start + sum(i * v for i, v in enumerate(data, 1))) % 103 == checksum
    if start == 105:
        return "".join(f"{v:02d}" for v in data)
    return "".join(chr(v + 32) for v in data)


@pytest.mark.parametrize("value", ["SO-2026/77", "1001", "#1002", "12", "Pedido-ÑÓ"])
def test_code128_decodes_back(value):
    expected = value.encode("ascii", "replace").decode().replace("\ufffd", "?")
    assert _decode_code128(code128_modules(value)) == expected


def test_zpl_text_escapes_control_characters():
    assert zpl_text("a^b~c_d") == "a_5Eb_7Ec_5Fd"
    assert zpl_text("línea\nnueva") == "línea nueva"


def _expand_row(encoded: str, width_chars: int, previous: str) -> str:
    """Decodificador de la compresión ACS de ZPL (para verificar el codificador)."""
    if encoded == ":":
        return previous
    out, count = [], 0
    for ch in encoded:
        if "G" <= ch <= "Y":
            count += ord(ch) - ord("G") + 1
        elif "g" <= ch <= "z":
            count += (ord(ch) - ord("g") + 1) * 20
        elif ch == ",":
            out.append("0" * (width_chars - len("".join(out))))
        else:
            out.append(ch * (count or 1))
            count = 0
    return "".join(out)


@pytest.mark.parametrize(
    "row",
    ["0" * 204, "F" * 204, "FFFF0000" * 25 + "0000", "0123456789ABCDEF" * 12 + "000000000000", "A" * 450 + "0" * 10],
)
def test_zpl_compression_roundtrip(row):
    assert _expand_row(_compress_row(row), len(row), "") == row


def test_image_to_gfa_dimensions(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QImage, QPainter

    from labelbridge.printing.zpl import image_to_gfa

    image = QImage(100, 20, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.white)
    painter = QPainter(image)
    painter.fillRect(0, 0, 8, 20, Qt.GlobalColor.black)
    painter.end()
    command = image_to_gfa(image, compress=False)
    _, total, _, row_bytes, data = command[: -len("^FS")].split(",", 4)
    assert int(row_bytes) == 13 and int(total) == 13 * 20
    assert data.startswith("FF" + "00" * 12)
    assert len(data) == 2 * 13 * 20


def test_detect_format():
    assert detect_format(b"%PDF-1.7 ...") == "pdf"
    assert detect_format(b"\x89PNG\r\n\x1a\n....") == "image"
    assert detect_format(b"\n^XA^FO10,10^FDx^FS^XZ") == "zpl"
    assert detect_format(b"N\nq812\n") == "raw"
    assert detect_format(b"???", "application/pdf") == "pdf"


def test_ipp_roundtrip():
    request = ipp.encode_request(
        ipp.OP_GET_PRINTER_ATTRIBUTES,
        "ipp://127.0.0.1:631/ipp/print",
        operation_attrs=[(ipp.KEYWORD, "requested-attributes", ["printer-name", "printer-state"])],
        request_id=7,
    )
    status, attrs = ipp.decode_response(request)
    assert status == ipp.OP_GET_PRINTER_ATTRIBUTES  # en una petición, ese campo es el operation-id
    assert attrs["printer-uri"] == ["ipp://127.0.0.1:631/ipp/print"]
    assert attrs["requested-attributes"] == ["printer-name", "printer-state"]

    response = struct.pack(">BBHI", 2, 0, 0x0000, 7) + bytes([0x01])
    response += ipp._attr(ipp.CHARSET, "attributes-charset", ["utf-8"])
    response += bytes([0x04])
    response += ipp._attr(ipp.TEXT, "printer-make-and-model", ["Zebra ZD421"])
    response += ipp._attr(ipp.ENUM, "printer-state", [3])
    response += bytes([0x03])
    status, attrs = ipp.decode_response(response)
    assert status == 0
    assert attrs["printer-make-and-model"] == ["Zebra ZD421"]
    assert attrs["printer-state"] == [3]


def test_ipp_print_job_against_fake_server():
    import http.server
    import threading

    captured: dict = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            body = self.rfile.read(int(self.headers["Content-Length"]))
            captured["path"] = self.path
            captured["body"] = body
            reply = struct.pack(">BBHI", 2, 0, 0, 1) + bytes([0x01])
            reply += ipp._attr(ipp.CHARSET, "attributes-charset", ["utf-8"]) + bytes([0x02])
            reply += ipp._attr(ipp.INTEGER, "job-id", [42]) + bytes([0x03])
            self.send_response(200)
            self.send_header("Content-Type", "application/ipp")
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        job_id, fingerprint = ipp.print_job("127.0.0.1", server.server_address[1], "/ipp/print", b"%PDF-1.4 fake")
        plain = captured["body"]
        ipp.print_job("127.0.0.1", server.server_address[1], "/ipp/print", b"%PDF-1.4 fake", scaling="none")
    finally:
        server.shutdown()
    assert job_id == 42 and fingerprint == ""
    assert captured["path"] == "/ipp/print"
    assert plain.endswith(b"%PDF-1.4 fake") and b"print-scaling" not in plain
    _, attrs = ipp.decode_response(plain)
    assert attrs["document-format"] == ["application/pdf"]
    _, attrs = ipp.decode_response(captured["body"])
    assert attrs["print-scaling"] == ["none"]  # la guía a tamaño real, sin «ajustar a la página»

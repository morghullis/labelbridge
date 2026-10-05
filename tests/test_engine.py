"""Motor de impresión de punta a punta con impresoras falsas (archivo y RAW 9100)."""

from __future__ import annotations

import base64

import pytest
from conftest import wait_for

from labelbridge.config import PrinterProfile
from labelbridge.labels.order import normalize_order, sample_order
from labelbridge.models import parse_job
from labelbridge.printing.engine import Document, resolve_printer


def _add_network(core, port: int, language: str = "zpl") -> PrinterProfile:
    profile = PrinterProfile(alias="zebra", kind="network", host="127.0.0.1", port=port, language=language)
    core.cfg.data.printers.append(profile)
    core.cfg.save("printers")
    return profile


def test_resolve_printer(core):
    assert resolve_printer("", core.cfg).alias == "archivo"
    assert resolve_printer("ARCHIVO", core.cfg).alias == "archivo"
    adhoc = resolve_printer("tcp://10.0.0.9:9101", core.cfg)
    assert (adhoc.kind, adhoc.host, adhoc.port, adhoc.language) == ("network", "10.0.0.9", 9101, "zpl")
    ipp_profile = resolve_printer("ipp://10.0.0.8/ipp/print", core.cfg)
    assert (ipp_profile.protocol, ipp_profile.port, ipp_profile.ipp_path) == ("ipp", 631, "/ipp/print")


def test_queue_prints_to_network_printer(core, raw_printer):
    _add_network(core, raw_printer.port)
    pdf = core.engine._as_pdf(Document("order", order=sample_order()), core.cfg.data.printers[0])
    jobs = [
        {"id": "z1", "printer": "zebra", "content": "^XA^FDhola^FS^XZ", "copies": 2},
        {"id": "z2", "printer": "zebra", "order": sample_order()},
        {"id": "z3", "printer": "zebra", "content_base64": base64.b64encode(pdf).decode()},
    ]
    for raw in jobs:
        core.store.add(parse_job(raw), "api")
    core.engine.start()
    assert wait_for(lambda: len(raw_printer.received) == 3)
    passthrough, native, raster = raw_printer.received
    assert passthrough == b"^XA^FDhola^FS^XZ" * 2
    assert native.startswith(b"^XA") and b"^CI28" in native and "María".encode() in native
    assert raster.startswith(b"^XA") and b"^GFA," in raster and b"^PW812" in raster
    assert wait_for(lambda: all(j["status"] == "done" for j in core.store.list()))


def test_file_printer_writes_pdf_and_zpl(core):
    core.store.add(parse_job({"id": "o1", "order": sample_order()}), "api")
    core.store.add(parse_job({"id": "z1", "content": "^XA^XZ"}), "api")
    core.engine.start()
    assert wait_for(lambda: all(j["status"] == "done" for j in core.store.list()))
    files = sorted(p.suffix for p in (core.home / "out").iterdir())
    assert files == [".pdf", ".zpl"]
    pdf = next((core.home / "out").glob("*.pdf")).read_bytes()
    assert pdf.startswith(b"%PDF")


def test_missing_printer_is_retried_then_fails(core):
    core.cfg.data.general.max_retries = 1
    job, _ = core.store.add(parse_job({"id": "x", "printer": "no-existe", "content": "^XA^XZ"}), "api")
    core.engine._run(core.store.next_ready())
    first = core.store.get(job["id"])
    assert first["status"] == "queued" and "no existe" in first["error"]
    core.engine._run(core.store.get(job["id"]))
    assert core.store.get(job["id"])["status"] == "failed"
    assert any(kind == "notify" for kind, _ in core.received)


def test_unreachable_network_printer_is_retryable(core):
    _add_network(core, 1)  # puerto cerrado
    job, _ = core.store.add(parse_job({"id": "x", "printer": "zebra", "content": "^XA^XZ"}), "api")
    core.engine._run(core.store.next_ready())
    updated = core.store.get(job["id"])
    assert updated["status"] == "queued" and "127.0.0.1:1" in updated["error"]


def test_normalize_rest_and_graphql_orders():
    rest = normalize_order(sample_order(), shop_domain="mi-tienda.myshopify.com")
    assert rest.ship_name == "María Fernanda López"
    assert rest.address_lines[2] == "Ciudad de México, CDMX 03100"
    assert rest.item_count == 6 and rest.barcode == "1001"
    assert rest.qr_url == "https://admin.shopify.com/store/mi-tienda/orders/5551234567890"

    graphql = {
        "id": "gid://shopify/Order/42",
        "legacyResourceId": "42",
        "name": "#1042",
        "createdAt": "2026-10-03T12:00:00Z",
        "shippingAddress": {"firstName": "Ana", "lastName": "Ruiz", "address1": "Calle 1", "city": "Madrid", "zip": "28001", "country": "España"},
        "shippingLine": {"title": "Express"},
        "lineItems": {"nodes": [{"name": "Taza", "sku": "T-1", "quantity": 2, "currentQuantity": 1}]},
    }
    data = normalize_order(graphql)
    assert data.ship_name == "Ana Ruiz" and data.order_id == "42" and data.shipping_method == "Express"
    assert data.items == [(1, "Taza · T-1")]


def test_qt_label_renders_all_sizes(qapp):
    from labelbridge.labels.qt_label import draw_order_label
    from labelbridge.printing.pages import VectorSource

    data = normalize_order(sample_order())
    for size in ((101.6, 152.4), (101.6, 101.6), (57.15, 31.75)):
        source = VectorSource(size, lambda p, r, dpi: draw_order_label(p, r, dpi, data))
        image = source.render(0, round(size[0] / 25.4 * 203), round(size[1] / 25.4 * 203), True)
        assert not image.isNull()
        assert image.pixelColor(image.width() // 2, image.height() // 2).isValid()


# --- la guía de envío a 4×6 in en cualquier papel ------------------------------------------------

def _pdf_page(size_mm: tuple[float, float], ink_mm: tuple[float, float, float, float] | None = None) -> bytes:
    """PDF de una página de `size_mm` con tinta negra en `ink_mm` (x, y, ancho, alto) o en toda la página."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QMarginsF, QRectF, QSizeF
    from PySide6.QtGui import QColor, QPageLayout, QPageSize, QPainter, QPdfWriter

    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    writer = QPdfWriter(buffer)
    writer.setResolution(300)
    writer.setPageSize(QPageSize(QSizeF(*size_mm), QPageSize.Unit.Millimeter, "", QPageSize.SizeMatchPolicy.ExactMatch))
    writer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Unit.Millimeter)
    painter = QPainter(writer)
    px = 300 / 25.4
    x, y, w, h = ink_mm or (0.0, 0.0, *size_mm)
    painter.fillRect(QRectF(x * px, y * px, w * px, h * px), QColor("black"))
    painter.end()
    buffer.close()
    return bytes(data)


def _ink_mm(pdf: bytes) -> tuple[tuple[float, float], tuple[float, float, float, float]]:
    """Tamaño de la página y rectángulo con tinta (en mm) de un PDF generado."""
    from labelbridge.printing.pages import PdfSource, content_bbox

    source = PdfSource(pdf)
    try:
        w_mm, h_mm = source.size_mm(0)
        ppm = 100 / 25.4
        box = content_bbox(source.render(0, round(w_mm * ppm), round(h_mm * ppm), True))
    finally:
        source.close()
    return (w_mm, h_mm), (box.x() / ppm, box.y() / ppm, box.width() / ppm, box.height() / ppm)


def test_shipping_label_is_4x6_on_any_paper(core):
    """La guía sale a 4×6 in reales en cualquier papel: llena un rollo 4×6 y en carta o A4 va arriba y centrada."""
    from labelbridge.printing.engine import label_size_mm

    four_by_six = label_size_mm({"label_size": "4x6"})
    assert four_by_six == (101.6, 152.4) and label_size_mm({"label_size": "fit"}) is None and label_size_mm({}) is None
    label_pdf = _pdf_page((101.6, 152.4))
    sheet_pdf = _pdf_page((215.9, 279.4), (20, 30, 101.6, 152.4))  # Shopify en formato carta: la guía dentro de la hoja
    for media in ("letter", "a4", "4x6"):
        profile = PrinterProfile(alias=media, kind="file", media=media)
        for source in (label_pdf, sheet_pdf):
            out = core.engine._as_pdf(Document("pdf", data=source, size_mm=four_by_six), profile)
            page, (x, y, w, h) = _ink_mm(out)
            assert abs(w - 101.6) < 1.5 and abs(h - 152.4) < 1.5, (media, page, w, h)  # 4×6 reales
            if media == "4x6":
                assert page == pytest.approx((101.6, 152.4), abs=0.5)
            else:
                assert abs(x - (page[0] - w) / 2) < 2 and y < 10, (media, x, y)  # arriba y centrada

    # Sin tamaño fijo («Ajustar a la hoja» y el resto de trabajos) llena el papel, como siempre.
    page, (_x, _y, _w, h) = _ink_mm(
        core.engine._as_pdf(Document("pdf", data=label_pdf), PrinterProfile(alias="carta", kind="file", media="letter"))
    )
    assert page == pytest.approx((215.9, 279.4), abs=0.5) and h > 250

    # En una térmica ZPL de 4×6 ocupa la etiqueta entera.
    zpl = core.engine._as_zpl(
        Document("pdf", data=label_pdf, size_mm=four_by_six), PrinterProfile(kind="network", language="zpl"), 1
    )
    assert zpl.startswith(b"^XA") and b"^PW812" in zpl


def test_shipping_label_size_reaches_the_printer(core):
    """El tamaño elegido viaja con el trabajo (también en las reimpresiones) hasta la impresora."""
    core.cfg.data.printers.append(PrinterProfile(alias="carta", kind="file", media="letter", output_dir=str(core.home / "out")))
    core.cfg.save("printers")
    pdf = _pdf_page((101.6, 152.4))
    core.store.add(
        parse_job({"id": "guia-1", "printer": "carta", "content_base64": base64.b64encode(pdf).decode(),
                   "meta": {"label_size": "4x6"}}),
        "api",
    )
    core.engine.start()
    assert wait_for(lambda: all(j["status"] == "done" for j in core.store.list()))
    printed = next((core.home / "out").glob("*.pdf")).read_bytes()
    page, (_x, y, w, h) = _ink_mm(printed)
    assert page == pytest.approx((215.9, 279.4), abs=0.5) and abs(w - 101.6) < 1.5 and abs(h - 152.4) < 1.5 and y < 10

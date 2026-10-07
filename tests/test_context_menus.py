"""Clic derecho en toda la app: pedidos (tabla y tarjetas), Historial, Impresoras e Inicio. Y reimprimir nunca compra
otra guía."""

from __future__ import annotations

from test_orders import _ui_context, make_order, row_of, usps
from test_print_labels import _page_with_rows, _shipping_job


def _texts(menu) -> list[tuple[str, bool]]:
    return [(action.text(), action.isEnabled()) for action in menu.actions() if action.text()]


def test_order_menus_say_reprint_is_free_and_explain_what_cannot_be_bought(core, monkeypatch):
    labeled = row_of(make_order(11, labels=(("2", "5,68"),), fulfillments=(usps(),), fulfillment="FULFILLED"))
    fedex = row_of(make_order(14, subtotal=1500))
    pickup = row_of(make_order(16, delivery="PICK_UP"))
    page, _ctx = _page_with_rows(core, monkeypatch, [labeled, fedex, pickup])
    texts = _texts(page.order_menu([labeled]))
    assert texts[0] == ("Reimprimir guía (sin pagar otra)", True)
    assert ("Rastrear el envío", True) in texts and ("Cancelar preparación en Shopify", True) in texts
    texts = _texts(page.order_menu([fedex]))
    assert texts[:2] == [("Comprar guía FedEx", True), ("Otra paquetería…", True)]
    assert _texts(page.order_menu([pickup]))[0] == ("Imprimir guía", False)  # se ve por qué no, sin comprar
    assert _texts(page.order_menu([labeled, fedex]))[0] == ("Imprimir 2 guías", True)


def test_history_menu_reprints_without_buying(core):
    from labelbridge.ui.pages.history import HistoryPage

    done = _shipping_job(core, "9", 0)
    page = HistoryPage(_ui_context(core))
    page.refresh()
    page.view.selectRow(0)
    texts = dict(_texts(page._menu()))
    assert "Reimprimir (sin pagar otra guía)" in texts and "Copiar el número de seguimiento" in texts
    assert "Cancelar" not in texts  # ya se imprimió
    before = len(core.store.list(limit=50))
    next(a for a in page._menu().actions() if a.text().startswith("Reimprimir")).trigger()
    jobs = core.store.list(limit=50)
    assert len(jobs) == before + 1 and jobs[0]["title"].startswith(done["title"])  # la misma etiqueta, otra vez


def test_printer_and_home_menus(core):
    from labelbridge.ui.pages.dashboard import DashboardPage
    from labelbridge.ui.pages.printers import PrintersPage

    ctx = _ui_context(core)
    printers = PrintersPage(ctx)
    printers.table.selectRow(0)
    assert [text for text, _on in _texts(printers._mine_menu())] == [
        "Editar", "Usar como predeterminada", "Imprimir prueba", "Eliminar"]
    _shipping_job(core, "7", 0)
    home = DashboardPage(ctx, lambda key: None)
    home.refresh()
    home.table.selectRow(0)
    assert [text for text, _on in _texts(home._recent_menu())] == ["Reimprimir (sin pagar otra guía)",
                                                                   "Ver en Historial"]

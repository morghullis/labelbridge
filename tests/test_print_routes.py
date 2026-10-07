"""La etiqueta del pedido por otra impresora que la guía: por dónde sale cada cosa, el aviso cuando coinciden (aunque
tengan alias distintos) y la prueba por la impresora de cada fila."""

from __future__ import annotations

import time

from test_orders import _ui_context


def _profiles(core):
    from labelbridge.config import PrinterProfile

    office = PrinterProfile(alias="oficina", kind="system", system_name="HP_LaserJet_Pro_M404")
    same_office = PrinterProfile(alias="oficina-2", kind="system", system_name="hp_laserjet_pro_m404")
    zebra = PrinterProfile(alias="etiquetas", kind="network", host="192.168.1.50", port=9100, language="zpl")
    core.cfg.data.printers += [office, same_office, zebra]
    return office, same_office, zebra


def _wait_qt(predicate, timeout: float = 10.0) -> bool:
    from PySide6.QtWidgets import QApplication

    deadline = time.time() + timeout
    while time.time() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


# --- routing (sin pantalla) ------------------------------------------------------------------------------
def test_each_document_says_which_printer_it_goes_to(core):
    from labelbridge.printing import routing

    _profiles(core)
    default = core.cfg.default_printer()
    assert routing.printer_for("", core.cfg) is default  # «predeterminada»
    assert routing.printer_for("oficina", core.cfg).system_name == "HP_LaserJet_Pro_M404"
    assert routing.printer_for("no-existe", core.cfg) is None
    keys = [route.key for route in routing.routes(core.cfg)]
    assert keys == [routing.SHIPPING, routing.ORDER, routing.METAFIELD]
    assert routing.routes(core.cfg)[0].active  # las guías salen aunque no se compren solas (FedEx, reimpresiones)


def test_two_aliases_of_the_same_printer_count_as_the_same_printer(core):
    from labelbridge.printing import routing

    office, _same, zebra = _profiles(core)
    assert routing.shared_printer("oficina", "oficina-2", core.cfg) is office  # mismo aparato, otro alias
    assert routing.shared_printer("oficina", "etiquetas", core.cfg) is None
    assert routing.shared_printer("etiquetas", "no-existe", core.cfg) is None  # sin saber, no se avisa
    core.cfg.data.general.default_printer_id = zebra.id
    assert routing.shared_printer("", "etiquetas", core.cfg) is zebra


def test_order_label_clash_only_when_the_order_label_prints(core):
    from labelbridge.printing import routing

    _profiles(core)
    s = core.cfg.data.shopify
    s.print_order_label = False
    assert routing.order_label_clash(core.cfg) is None
    s.print_order_label = True
    assert routing.order_label_clash(core.cfg) is core.cfg.default_printer()  # las dos a la predeterminada
    s.order_label_printer = "oficina"
    assert routing.order_label_clash(core.cfg) is None
    s.shipping_label_printer = "oficina-2"
    assert routing.order_label_clash(core.cfg).alias == "oficina"


def test_the_order_label_job_goes_to_its_own_printer(core):
    """Con impresoras distintas, la etiqueta del pedido y la guía salen por la suya (el motor usa el alias de cada una)."""
    from labelbridge.printing.engine import resolve_printer

    _profiles(core)
    s = core.cfg.data.shopify
    s.order_label_printer, s.shipping_label_printer = "oficina", "etiquetas"
    assert resolve_printer(s.order_label_printer, core.cfg).system_name == "HP_LaserJet_Pro_M404"
    assert resolve_printer(s.shipping_label_printer, core.cfg).host == "192.168.1.50"


# --- la tarjeta «Por dónde sale cada cosa» --------------------------------------------------------------
def test_card_warns_when_the_order_label_shares_the_shipping_printer(core):
    from labelbridge.printing import routing
    from labelbridge.ui.print_routes import PrintRoutesCard

    core.cfg.data.shopify.print_order_label = True
    went: list[str] = []
    card = PrintRoutesCard(_ui_context(core), went.append)
    # Una sola impresora: las dos salen por ella y la salida es agregar otra.
    assert not card.clash.isHidden() and "«archivo»" in card.clash.text()
    assert not card.add_printer.isHidden()
    card.add_printer.click()
    assert went == ["printers"]
    # Con otra impresora en la lista: se elige ahí mismo.
    _profiles(core)
    card.reload_printers()
    assert not card.clash.isHidden() and card.add_printer.isHidden()
    assert "Elige otra" in card.clash.text()
    order = card.combos[routing.ORDER]
    order.setCurrentIndex(order.findData("oficina"))
    assert card.clash.isHidden()
    assert card.alias(routing.ORDER) == "oficina"
    # La etiqueta del pedido apagada: no hay nada que avisar.
    order.setCurrentIndex(0)
    assert not card.clash.isHidden()
    card.set_order_label_active(False)
    assert card.clash.isHidden()


def test_card_names_the_default_printer_and_flags_unknown_aliases(core):
    from labelbridge.printing import routing
    from labelbridge.ui.print_routes import PrintRoutesCard

    core.cfg.data.shopify.metafield_printer = "fantasma"
    card = PrintRoutesCard(_ui_context(core))
    shipping = card.combos[routing.SHIPPING]
    assert shipping.itemText(0) == "Predeterminada (archivo)" and shipping.itemData(0) == ""
    assert card.alias(routing.METAFIELD) == "fantasma"
    assert not card.unknown.isHidden() and "fantasma" in card.unknown.text()
    core.cfg.data.printers = []
    card.reload_printers()
    assert shipping.itemText(0) == "Predeterminada (no hay ninguna)"


def test_test_print_goes_out_through_that_rows_printer(core):
    from labelbridge.printing import routing
    from labelbridge.ui.print_routes import PrintRoutesCard

    card = PrintRoutesCard(_ui_context(core))
    card.test(routing.ORDER)
    assert _wait_qt(lambda: not card.sent.isHidden())
    assert "«archivo»" in card.sent.text()
    assert any((core.home / "out").iterdir())  # salió por la impresora de archivo de las pruebas (nunca una real)


def test_shopify_page_saves_where_each_document_prints(core):
    from labelbridge.printing import routing
    from labelbridge.ui.pages.shopify import ShopifyPage

    _profiles(core)
    page = ShopifyPage(_ui_context(core))
    page.print_order.setChecked(True)
    order = page.routes.combos[routing.ORDER]
    order.setCurrentIndex(order.findData("oficina"))
    shipping = page.routes.combos[routing.SHIPPING]
    shipping.setCurrentIndex(shipping.findData("etiquetas"))
    assert page._collect() is None
    s = core.cfg.data.shopify
    assert (s.print_order_label, s.order_label_printer, s.shipping_label_printer) == (True, "oficina", "etiquetas")
    # Lo que cambie el asistente se ve al volver a cargar.
    s.order_label_printer = "oficina-2"
    page.load_from_config()
    assert page.routes.alias(routing.ORDER) == "oficina-2"


# --- el asistente ------------------------------------------------------------------------------------------
def test_the_assistant_sees_and_fixes_a_shared_printer(core):
    from test_assistant import FakeUI, _context

    from labelbridge.assistant.secrets_guard import SecretsGuard
    from labelbridge.assistant.tools import Toolbox

    _profiles(core)
    core.cfg.data.shopify.print_order_label = True
    box = Toolbox(_context(core), FakeUI(), SecretsGuard(core.secrets))
    status = box.get_status({"section": "shopify"})["shopify"]
    assert "misma impresora" in status["printer_warning"] and status["order_label_printer"] == "(predeterminada)"
    result = box.set_shopify_printing({"order_label_printer": "oficina"})
    assert result["applied"] and result["order_label_printer"] == "oficina" and "printer_warning" not in result
    assert "printer_warning" not in box.get_status({"section": "shopify"})["shopify"]

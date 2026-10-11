"""Asistente de inicio: la primera vez, según el objetivo, deja lo esencial funcionando (impresoras, tienda, qué hacer
con cada pedido, que trabaje solo) y después sigue el asistente de IA. Nunca activa la compra de guías sin confirmar."""

from __future__ import annotations

import pytest
from test_orders import _ui_context

from labelbridge.ui import setup_wizard as sw

SYSTEM = [
    {"name": "ZDesigner ZD421-203dpi ZPL", "model": "Zebra ZD421", "driver": "ZDesigner", "is_default": False},
    {"name": "Canon PRO-4000", "model": "imagePROGRAF PRO-4000", "driver": "", "is_default": True},
    {"name": "HP LaserJet Pro M404", "model": "HP LaserJet", "driver": "", "is_default": False},
    {"name": "Microsoft Print to PDF", "model": "", "driver": "", "is_default": False},
]


@pytest.fixture()
def fresh(core):
    core.cfg.data.printers = []
    core.cfg.data.general.setup_done = False
    return core


def _wizard(core, monkeypatch, **kwargs):
    monkeypatch.setattr(sw, "error", lambda _parent, _title, text: _wizard.errors.append(text))
    _wizard.errors = []
    return sw.SetupWizard(None, _ui_context(core), system_printers=SYSTEM, **kwargs)


def test_it_opens_only_the_first_time(fresh):
    ctx = _ui_context(fresh)
    assert sw.should_open(ctx)
    fresh.cfg.data.general.setup_done = True
    assert not sw.should_open(ctx)
    fresh.cfg.data.general.setup_done = False
    fresh.cfg.data.shopify.enabled = True  # ya usaba LabelBridge: no se le aparece
    assert not sw.should_open(ctx)


def test_it_proposes_the_label_printer_and_a_real_sheet_printer(fresh):
    choices = sw.printer_choices(_ui_context(fresh), SYSTEM)
    assert sw.pick_label_printer(choices) == "sys:ZDesigner ZD421-203dpi ZPL"
    # La predeterminada es un plotter (Canon PRO-4000): nunca se propone sola para las hojas, ni el PDF.
    assert sw.pick_page_printer(choices, "Canon PRO-4000") == "sys:HP LaserJet Pro M404"
    assert sw.pick_page_printer([c for c in choices if "HP" not in c.title], "Canon PRO-4000") == ""


def test_buying_labels_walks_every_step_and_asks_before_buying(fresh, monkeypatch):
    confirms: list[str] = []
    monkeypatch.setattr("labelbridge.ui.widgets.confirm", lambda _p, _t, text: confirms.append(text) or True)
    monkeypatch.setattr("labelbridge.system.autostart.apply", lambda on: None)
    wizard = _wizard(fresh, monkeypatch)
    assert wizard.steps() == ["goal", "printers", "store", "orders", "always", "done"]
    wizard.next()  # objetivo: comprar guías (el primero)
    assert wizard.current == "printers" and wizard.label_combo.currentData() == "sys:ZDesigner ZD421-203dpi ZPL"
    wizard.next()  # las impresoras elegidas se agregan a LabelBridge
    s, g = fresh.cfg.data.shopify, fresh.cfg.data.general
    labels = fresh.cfg.find_printer(s.shipping_label_printer)
    assert labels.system_name == "ZDesigner ZD421-203dpi ZPL" and labels.language == "zpl" and labels.media == "4x6"
    assert fresh.cfg.find_printer(s.order_page_printer).system_name == "HP LaserJet Pro M404"
    assert g.default_printer_id == labels.id and s.print_order_page
    assert wizard.current == "store"
    wizard.next()  # sin tienda por ahora: se puede seguir
    assert wizard.current == "orders" and wizard.buy_switch.isChecked()  # propuesto por el objetivo
    wizard.next()
    assert s.buy_shipping_label and len(confirms) == 1 and "cargo a tu cuenta" in confirms[0]
    wizard.next()
    assert wizard.current == "done" and g.start_with_windows and g.keep_awake
    wizard.next()  # Terminar
    assert g.setup_done


def test_without_confirming_nothing_is_bought(fresh, monkeypatch):
    monkeypatch.setattr("labelbridge.ui.widgets.confirm", lambda *_a: False)
    wizard = _wizard(fresh, monkeypatch)
    wizard.next()
    wizard.next()
    wizard.next()
    wizard.next()
    assert not fresh.cfg.data.shopify.buy_shipping_label and not wizard.buy_switch.isChecked()


def test_a_half_written_store_must_be_tested_or_cleared(fresh, monkeypatch):
    wizard = _wizard(fresh, monkeypatch)
    wizard.next()
    wizard.next()
    wizard.shop_edit.setText("mi-tienda")
    wizard.next()
    assert wizard.current == "store" and "Client ID" in _wizard.errors[-1]
    wizard.client_id_edit.setText("abc")
    wizard.secret_edit.setText("shpss_x")
    wizard.next()
    assert wizard.current == "store" and "Conectar y probar" in _wizard.errors[-1]
    assert fresh.cfg.data.shopify.shop == "mi-tienda.myshopify.com" and fresh.secrets.get("shopify_client_secret")
    wizard._connected({"name": "MI TIENDA", "missing": []})
    assert fresh.cfg.data.shopify.enabled and "Conectado" in wizard.store_result.text()
    wizard.next()
    assert wizard.current == "orders"


def test_n8n_skips_the_store_and_the_same_printer_twice_is_refused(fresh, monkeypatch):
    wizard = _wizard(fresh, monkeypatch)
    wizard.goal_group.button(2).setChecked(True)
    wizard.next()
    assert wizard.steps() == ["goal", "printers", "always", "done"]
    wizard.page_combo.setCurrentIndex(wizard.page_combo.findData("sys:ZDesigner ZD421-203dpi ZPL"))
    wizard.next()
    assert wizard.current == "printers" and "hoja" in _wizard.errors[-1]


def test_closing_it_never_reopens_it_by_itself(fresh, monkeypatch):
    wizard = _wizard(fresh, monkeypatch)
    wizard.reject()  # «Lo hago después»
    assert fresh.cfg.data.general.setup_done
    assert not sw.should_open(_ui_context(fresh))


def test_at_the_end_the_ai_assistant_takes_over(fresh, monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr("labelbridge.system.autostart.apply", lambda on: None)
    wizard = _wizard(fresh, monkeypatch, open_assistant=asked.append)
    wizard._show("done")
    wizard._continue_with_assistant()
    assert asked and "Qué me falta configurar" in asked[0]

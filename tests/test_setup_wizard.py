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
    monkeypatch.setattr(sw, "confirm", lambda _p, _t, text: confirms.append(text) or True)
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
    monkeypatch.setattr(sw, "confirm", lambda *_a: False)
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
    assert fresh.cfg.data.shopify.shop == "" and not fresh.secrets.get("shopify_client_secret")  # nada sin probar
    monkeypatch.setattr(sw, "run_async", lambda work, done, failed, what="": done({"name": "MI TIENDA", "missing": []}))
    wizard._connect()
    assert fresh.cfg.data.shopify.shop == "mi-tienda.myshopify.com" and fresh.secrets.get("shopify_client_secret")
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


# --- lo que encontró la segunda revisión --------------------------------------------------------------

def test_saves_tell_open_screens_to_reload_so_they_do_not_undo_it(fresh, monkeypatch):
    from labelbridge.events import CONFIG_EXTERNAL

    seen: list[str] = []
    fresh.events.subscribe(lambda kind, data: seen.append(data) if kind == CONFIG_EXTERNAL else None)
    monkeypatch.setattr("labelbridge.system.autostart.apply", lambda on: None)
    wizard = _wizard(fresh, monkeypatch)
    wizard.next()
    wizard.next()
    assert "shopify" in seen and "general" in seen  # las pantallas Shopify y Ajustes recargan lo guardado


def test_an_existing_user_reopening_it_keeps_what_works(core, monkeypatch):
    from labelbridge.config import PrinterProfile

    s, g = core.cfg.data.shopify, core.cfg.data.general
    office = PrinterProfile(alias="oficina", kind="system", system_name="HP LaserJet Pro M404", media="letter")
    big = PrinterProfile(alias="grande", kind="system", system_name="Canon PRO-4000", media="auto")
    core.cfg.data.printers += [office, big]
    g.default_printer_id = office.id
    s.enabled, s.activated_once, s.shop, s.client_id = True, True, "mi-tienda.myshopify.com", "abc"
    core.secrets.set("shopify_client_secret", "secreto-que-funciona")
    s.buy_shipping_label = s.notify_customer = True
    system = [{"name": "Canon PRO-4000", "model": "imagePROGRAF PRO-4000", "driver": "", "is_default": True},
              {"name": "HP LaserJet Pro M404", "model": "HP", "driver": "", "is_default": False}]
    monkeypatch.setattr(sw, "error", lambda *_a: None)
    wizard = sw.SetupWizard(None, _ui_context(core), system_printers=system)
    assert wizard.page_combo.currentData() != "lb:grande"  # el plotter nunca se propone para la hoja
    wizard.goal_group.button(1).setChecked(True)  # «Solo imprimir»
    wizard.next()
    wizard.label_combo.setCurrentIndex(wizard.label_combo.findData("lb:archivo"))
    wizard.next()
    assert g.default_printer_id == office.id  # la predeterminada no cambia
    assert wizard.current == "store"
    wizard.next()  # la tienda ya conectada y sin tocar: se sigue sin volver a probarla
    assert wizard.current == "orders"
    wizard.next()
    assert s.buy_shipping_label and s.notify_customer  # «Solo imprimir» no apaga la compra sin preguntar


def test_a_failed_store_test_goes_back_to_the_store_that_worked(core, monkeypatch):
    s = core.cfg.data.shopify
    s.enabled, s.activated_once, s.shop, s.client_id = True, True, "mi-tienda.myshopify.com", "abc"
    core.secrets.set("shopify_client_secret", "secreto-que-funciona")
    monkeypatch.setattr(sw, "error", lambda *_a: None)
    calls: list = []
    monkeypatch.setattr(sw, "run_async", lambda work, done, failed, what="": calls.append(failed) or failed("401"))
    wizard = sw.SetupWizard(None, _ui_context(core), system_printers=[])
    wizard.client_id_edit.setText("otro-mal-escrito")
    wizard._connect()
    assert (s.shop, s.client_id) == ("mi-tienda.myshopify.com", "abc")
    assert core.secrets.get("shopify_client_secret") == "secreto-que-funciona"
    assert "401" in wizard.store_result.text()


def test_the_page_test_refuses_label_printers_and_asks_for_plotters(fresh, monkeypatch):
    errors: list[str] = []
    asked: list[str] = []
    monkeypatch.setattr(sw, "error", lambda _p, _t, text: errors.append(text))
    monkeypatch.setattr(sw, "confirm", lambda _p, _t, text: asked.append(text) or False)
    wizard = sw.SetupWizard(None, _ui_context(fresh), system_printers=SYSTEM)
    wizard.page_combo.setCurrentIndex(wizard.page_combo.findData("sys:ZDesigner ZD421-203dpi ZPL"))
    wizard._test_printer("page")
    assert errors and "hoja" in errors[-1]
    wizard.label_combo.setCurrentIndex(0)
    wizard.page_combo.setCurrentIndex(wizard.page_combo.findData("sys:Canon PRO-4000"))
    wizard._test_printer("page")
    assert asked and "gran formato" in asked[-1]  # y como se dijo que no, no se imprimió nada
    assert not any(p.system_name == "Canon PRO-4000" for p in fresh.cfg.data.printers)


def test_closing_it_during_the_store_test_still_turns_the_store_on(fresh, monkeypatch):
    wizard = _wizard(fresh, monkeypatch)
    wizard.shop_edit.setText("mi-tienda")
    wizard.client_id_edit.setText("abc")
    wizard.secret_edit.setText("shpss_x")
    pending: list = []
    monkeypatch.setattr(sw, "run_async", lambda work, done, failed, what="": pending.append(done))
    wizard._connect()
    wizard.connect_btn.deleteLater()  # la ventana se cerró mientras se probaba
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    pending[0]({"name": "MI TIENDA", "missing": []})
    assert fresh.cfg.data.shopify.enabled


def test_a_large_format_printer_for_pages_needs_a_yes(fresh, monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(sw, "confirm", lambda _p, _t, text: asked.append(text) or False)
    wizard = _wizard(fresh, monkeypatch)
    wizard.next()
    assert "(gran formato)" in wizard.page_combo.itemText(wizard.page_combo.findData("sys:Canon PRO-4000"))
    wizard.page_combo.setCurrentIndex(wizard.page_combo.findData("sys:Canon PRO-4000"))
    wizard.next()
    assert wizard.current == "printers" and asked and "gran formato" in asked[-1]

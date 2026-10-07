"""Avisos nativos de Windows: el aviso bien formado (con su botón y sin sonido de Windows), a dónde lleva cada uno y
que, si Windows no los muestra, avise la bandeja."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from labelbridge.system import toasts


def test_the_toast_is_valid_windows_xml_with_its_button():
    xml = toasts.toast_xml("Pedido nuevo #1001 <&>", "2 piezas · Florida", target="orders", action="Ver pedidos",
                           attribution="PC-BODEGA", logo="file:///C:/LabelBridge/icon.png")
    root = ET.fromstring(xml)  # escapado: «<&>» no rompe el aviso
    assert root.get("launch") == "open:orders" and root.find("audio").get("silent") == "true"
    texts = [t.text for t in root.iter("text")]
    assert texts[0] == "Pedido nuevo #1001 <&>" and "PC-BODEGA" in texts
    actions = root.find("actions").findall("action")
    assert actions[0].get("content") == "Ver pedidos" and actions[0].get("arguments") == "open:orders"
    assert actions[1].get("activationType") == "system"  # «Descartar», el de Windows
    assert root.find(".//image").get("hint-crop") == "circle"


def test_buttons_say_where_they_go():
    assert toasts.target_of("open:report") == "report" and toasts.target_of("open:") == ""
    assert toasts.target_of("dismiss") is None


def test_each_notice_opens_its_screen_and_falls_back_to_the_tray(core, qapp, monkeypatch):
    from test_orders import _ui_context

    from labelbridge.ui import tray as tray_module

    ctx = _ui_context(core)
    opened: list[str] = []
    tray = tray_module.Tray(ctx, lambda: None, lambda: None, lambda: None, open_target=opened.append)
    shown: list[tuple] = []

    class FakeToasts:
        def show(self, title, message, **kw):
            shown.append((title, kw["target"], kw["action"], kw["attribution"]))

    tray.toasts = FakeToasts()
    tray.notice("Pedido nuevo #1001", "2 piezas", "new_order")
    tray.notice("Tu reporte semanal está listo", "Semana…", "report")
    assert shown[0][1:3] == ("orders", "Ver pedidos") and shown[1][1:3] == ("report", "Ver reporte")
    assert shown[0][3] == ctx.cfg.data.general.agent_name  # de qué equipo viene
    tray.toast_clicked.emit("report")  # el clic en el aviso
    assert opened == ["report"]

    class BrokenToasts:
        def show(self, *a, **kw):
            raise OSError("Windows no lo mostró")

    tray.toasts = BrokenToasts()
    tray_messages: list[str] = []
    monkeypatch.setattr(tray, "showMessage", lambda title, *a: tray_messages.append(title))
    tray.notice("No se pudo imprimir", "detalle", "print_error", True)
    assert tray_messages == ["No se pudo imprimir"] and tray.toasts is None  # siguió por la bandeja

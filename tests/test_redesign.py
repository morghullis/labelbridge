"""El diseño 1.16: el menú pasa abajo en una ventana chica, el estado de la impresión, ninguna página se sale por los
lados en la ventana más angosta, y las piezas que se acomodan al ancho (filas, formularios, franjas de números)."""

from __future__ import annotations

import time

import pytest
from test_orders import _ui_context


def _settle(qapp, seconds: float = 0.05) -> None:
    deadline = time.time() + seconds
    while time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.01)


def _wait_qt(qapp, predicate, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        qapp.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture()
def window(core, qapp):
    from labelbridge.ui.main_window import MainWindow

    win = MainWindow(_ui_context(core))
    win.resize(1280, 820)
    win.show()
    _settle(qapp)
    yield win
    win.quitting = True
    win.close()
    win.deleteLater()  # sin esto, sus temporizadores siguen vivos y leen la base de la prueba siguiente (ya cerrada)
    from PySide6.QtCore import QCoreApplication, QEvent

    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


# --- la ventana --------------------------------------------------------------------------------------------
def test_the_menu_moves_to_the_bottom_when_the_window_is_narrow(window, qapp):
    assert not window.narrow and window.sidebar.isVisible() and not window.bottom_nav.isVisible()
    window.resize(860, 760)
    _settle(qapp)
    assert window.narrow and window.bottom_nav.isVisible() and not window.sidebar.isVisible()
    window.resize(1020, 760)  # entre los dos anchos no salta de un lado al otro
    _settle(qapp)
    assert window.narrow
    window.resize(1100, 760)
    _settle(qapp)
    assert not window.narrow and window.sidebar.isVisible()


def test_the_bottom_bar_marks_the_page_and_sends_the_rest_to_more(window, qapp):
    window.resize(860, 760)
    _settle(qapp)
    window.navigate("orders")
    assert window.bottom_nav.items["orders"].isChecked() and not window.bottom_nav.more.isChecked()
    window.navigate("settings")  # no está en la barra: queda marcada «Más»
    assert window.bottom_nav.more.isChecked() and not window.bottom_nav.items["orders"].isChecked()
    chosen = []
    window.bottom_nav.selected.connect(chosen.append)
    window.bottom_nav.items["history"].click()
    assert chosen == ["history"] and window.stack.currentWidget() is window.pages["history"]
    window._set_orders_badge(3)
    assert window.bottom_nav.items["orders"].badge == "3"


def test_the_status_says_what_is_happening_and_pulses_only_while_printing(window, core, monkeypatch, qapp):
    from labelbridge.models import parse_job
    from labelbridge.ui import navigation

    monkeypatch.setattr(navigation, "reduced_motion", lambda: False)
    window._update_status()
    assert (window.status.state, window.status.title, window.status.live) == ("ok", "Listo para imprimir", False)
    core.cfg.data.general.paused = True
    window._update_status()
    assert (window.status.state, window.status.title) == ("warn", "En pausa")
    core.cfg.data.general.paused = False
    core.store.add(parse_job({"id": "estado-1", "content": "^XA^XZ", "title": "Pedido #1"}), "api")
    window._update_status()
    assert (window.status.title, window.status.live) == ("Imprimiendo", True)
    assert window.status._pulse.state() == window.status._pulse.State.Running
    assert "1 en cola" in window.status.toolTip()
    monkeypatch.setattr(navigation, "reduced_motion", lambda: True)  # Windows pidió menos animaciones
    window._update_status()
    assert window.status._pulse.state() != window.status._pulse.State.Running
    core.cfg.data.printers = []
    window._update_status()
    assert (window.status.state, window.status.title) == ("error", "Sin impresora")


def test_no_page_spills_sideways_in_the_narrowest_window(window, qapp):
    from PySide6.QtWidgets import QScrollArea

    from labelbridge.ui.pages.settings import SETTINGS_TABS

    window.resize(window.minimumWidth(), 700)
    _settle(qapp, 0.1)
    too_wide = {}
    for key, page in window.pages.items():
        scroll = page.findChild(QScrollArea)
        if scroll is None:
            continue
        window.navigate(key)
        tabs = SETTINGS_TABS if key == "settings" else [("", "")]
        for tab, _title in tabs:
            if tab:
                page.show_tab(tab)
            _settle(qapp)
            need, room = scroll.widget().minimumSizeHint().width(), scroll.viewport().width()
            if need > room:
                too_wide[f"{key}/{tab}" if tab else key] = (need, room)
    assert not too_wide, too_wide


# --- piezas que se acomodan al ancho --------------------------------------------------------------------
def test_split_row_stacks_its_groups_instead_of_widening_the_page(qapp):
    from PySide6.QtWidgets import QLineEdit, QPushButton

    from labelbridge.ui.widgets import SplitRow

    row = SplitRow()
    for text in ("Todos", "Pendientes", "Procesados", "Retrasos"):
        row.add_start(QPushButton(text))
    search = QLineEdit()
    search.setMinimumWidth(240)
    row.add_end(search, 1)
    row.show()
    row.resize(1000, 40)
    _settle(qapp)
    assert not row.stacked()
    row.resize(380, 40)
    _settle(qapp)
    assert row.stacked() and row.minimumSizeHint().width() <= row.width()
    assert row.minimumWidth() == 1  # el ancho lo pone la ventana
    row.close()


def test_flow_layout_wraps_controls_like_text(qapp):
    from PySide6.QtWidgets import QPushButton, QWidget

    from labelbridge.ui.widgets import FlowLayout

    box = QWidget()
    flow = FlowLayout(box, spacing=10, line_spacing=8)
    buttons = []
    for index in range(3):
        btn = QPushButton(f"Botón {index}")
        btn.setFixedSize(100, 30)
        flow.addWidget(btn)
        buttons.append(btn)
    assert flow.minimumSize().width() == 100  # la pieza más ancha, nunca la fila entera
    assert flow.sizeHint().width() == 320 and flow.heightForWidth(1000) == 30
    assert flow.heightForWidth(150) == 3 * 30 + 2 * 8
    box.show()
    box.resize(230, flow.heightForWidth(230))
    _settle(qapp)
    assert buttons[0].y() == buttons[1].y() < buttons[2].y()  # dos en la primera línea, la tercera abajo
    buttons[1].hide()
    _settle(qapp)
    assert flow.heightForWidth(230) == 30  # las ocultas no ocupan lugar
    box.close()


def test_number_strip_wraps_into_even_rows(qapp):
    from PySide6.QtWidgets import QLabel

    from labelbridge.ui.widgets import FlowRow

    strip = FlowRow(min_column=100)
    for index in range(5):
        strip.add_block(QLabel(str(index)))
    strip.show()
    strip.resize(600, 80)
    _settle(qapp)
    assert strip.columns == 5
    strip.resize(450, 80)  # caben 4: mejor 3 + 2 que 4 + 1
    _settle(qapp)
    assert strip.columns == 3
    strip.close()


def test_forms_move_labels_above_fields_when_there_is_no_room(qapp):
    from PySide6.QtWidgets import QFormLayout, QLineEdit, QWidget

    from labelbridge.ui.widgets import form_layout

    box = QWidget()
    form = form_layout()
    box.setLayout(form)
    field = QLineEdit()
    field.setMinimumWidth(300)
    form.addRow("Una etiqueta bastante larga para un campo", field)
    assert form.rowWrapPolicy() == QFormLayout.RowWrapPolicy.WrapLongRows
    label_width = form.itemAt(0, QFormLayout.ItemRole.LabelRole).widget().minimumSizeHint().width()
    assert form.minimumSize().width() < label_width + 300  # la fila se parte en vez de exigir los dos lado a lado


def test_page_header_puts_the_buttons_under_the_title_when_narrow(qapp):
    from PySide6.QtWidgets import QBoxLayout, QPushButton

    from labelbridge.ui.widgets import Page

    page = Page("Pedidos", "Todo lo que llega de Shopify")
    for text in ("Últimos 30 días", "Actualizar", "Nueva guía manual"):
        page.header_actions.addWidget(QPushButton(text))
    page.finish()
    page.show()
    page.resize(1200, 600)
    _settle(qapp)
    assert page.header.direction() == QBoxLayout.Direction.LeftToRight
    page.resize(560, 600)
    _settle(qapp)
    assert page.header.direction() == QBoxLayout.Direction.TopToBottom
    page.close()


def test_checked_boxes_draw_a_check_mark_from_the_packaged_image(qapp):
    from PySide6.QtCore import QFile

    from labelbridge.paths import resource_path
    from labelbridge.ui.theme import STYLESHEET, apply_theme

    for name in ("check.png", "check@2x.png"):
        assert resource_path("assets", "ui", name).is_file()
    apply_theme(qapp)
    assert QFile("theme:check.png").exists()  # la ruta real (con «;» o espacios) nunca entra en el estilo
    assert "url(theme:check.png)" in STYLESHEET

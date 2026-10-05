"""Componentes reutilizables de la interfaz."""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QObject,
    QPropertyAnimation,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import QColor, QGuiApplication, QPainter
from PySide6.QtWidgets import (
    QAbstractButton,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..i18n import tr
from . import icons
from .theme import ACCENT, STATE_COLORS


# --- tareas en segundo plano -----------------------------------------------------------------

class _TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Task(QRunnable):
    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.fn = fn
        self.signals = _TaskSignals()

    def run(self) -> None:
        try:
            result = self.fn()
        except Exception as exc:  # se muestra al usuario
            traceback.print_exc()
            self.signals.failed.emit(str(exc) or exc.__class__.__name__)
        else:
            self.signals.done.emit(result)


_active_tasks: set[_Task] = set()


def run_async(
    fn: Callable[[], Any],
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[str], None] | None = None,
) -> None:
    """Ejecuta fn fuera del hilo de la UI y entrega el resultado en el hilo de la UI."""
    task = _Task(fn)
    task.setAutoDelete(False)
    _active_tasks.add(task)

    def finish() -> None:
        _active_tasks.discard(task)

    if on_done:
        task.signals.done.connect(on_done)
    if on_error:
        task.signals.failed.connect(on_error)
    task.signals.done.connect(lambda _r: finish())
    task.signals.failed.connect(lambda _e: finish())
    QThreadPool.globalInstance().start(task)


# --- botones y textos --------------------------------------------------------------------------

def button(text: str, icon_name: str | None = None, variant: str | None = None, tooltip: str = "") -> QPushButton:
    btn = QPushButton(text)
    if variant:
        btn.setProperty("variant", variant)
    if icon_name:
        color = "#FFFFFF" if variant == "primary" else ("#B91C1C" if variant == "danger" else "#334155")
        btn.setIcon(icons.icon(icon_name, color, 16))
        btn.setIconSize(QSize(16, 16))
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def label(text: str = "", name: str | None = None, wrap: bool = False, selectable: bool = False) -> QLabel:
    lbl = QLabel(text)
    if name:
        lbl.setObjectName(name)
    lbl.setWordWrap(wrap)
    if selectable:
        lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return lbl


def repolish(widget: QWidget) -> None:
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def info(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def error(parent: QWidget, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


def confirm(parent: QWidget, title: str, text: str) -> bool:
    return QMessageBox.question(parent, title, text) == QMessageBox.StandardButton.Yes


def copy_to_clipboard(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


# --- contenedores ----------------------------------------------------------------------------------

class Card(QFrame):
    def __init__(self, title: str = "", description: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(20, 18, 20, 18)
        self.body.setSpacing(12)
        if title:
            header = QHBoxLayout()
            header.setSpacing(10)
            self.title_label = label(title, "CardTitle")
            header.addWidget(self.title_label)
            header.addStretch(1)
            self.header = header
            self.body.addLayout(header)
        if description:
            self.body.addWidget(label(description, "CardDesc", wrap=True))

    def add(self, widget: QWidget | None = None, layout=None, stretch: int = 0) -> None:
        if widget is not None:
            self.body.addWidget(widget, stretch)
        if layout is not None:
            self.body.addLayout(layout, stretch)


class Pill(QLabel):
    """Etiqueta de estado coloreada (ok / warn / error / off / info)."""

    def __init__(self, state: str = "off", text: str = "") -> None:
        super().__init__(text)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self.set_state(state, text)

    def set_state(self, state: str, text: str) -> None:
        bg, fg = STATE_COLORS.get(state, STATE_COLORS["off"])
        self.setText(text)
        self.setStyleSheet(
            f"QLabel {{ background: {bg}; color: {fg}; border-radius: 10px; padding: 3px 10px;"
            " font-weight: 600; font-size: 12px; }"
        )


class ToggleSwitch(QAbstractButton):
    """Interruptor animado (más claro que una casilla para activar/desactivar servicios)."""

    def __init__(self, checked: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(44, 24)
        self._offset = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.toggled.connect(self._animate)

    def _animate(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._offset)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def get_offset(self) -> float:
        return self._offset

    def set_offset(self, value: float) -> None:
        self._offset = value
        self.update()

    offset = Property(float, get_offset, set_offset)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(44, 24)

    def paintEvent(self, _event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QColor(ACCENT) if self.isChecked() else QColor("#CBD5E1")
        if not self.isEnabled():
            track.setAlpha(110)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(track)
        p.drawRoundedRect(QRectF(0, 0, 44, 24), 12, 12)
        p.setBrush(QColor("white"))
        p.drawEllipse(QRectF(3 + self._offset * 20, 3, 18, 18))
        p.end()


class SwitchRow(QWidget):
    """Interruptor con título y descripción."""

    def __init__(self, title: str, description: str = "", checked: bool = False) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(12)
        self.switch = ToggleSwitch(checked)
        row.addWidget(self.switch, 0, Qt.AlignmentFlag.AlignTop)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        title_label = label(title)
        title_label.setStyleSheet("font-weight: 600;")
        texts.addWidget(title_label)
        if description:
            texts.addWidget(label(description, "Muted", wrap=True))
        row.addLayout(texts, 1)

    def isChecked(self) -> bool:  # noqa: N802
        return self.switch.isChecked()

    def setChecked(self, value: bool) -> None:  # noqa: N802
        self.switch.setChecked(value)


class SecretField(QWidget):
    """Campo para claves: oculto por defecto, con botones mostrar y copiar."""

    def __init__(self, placeholder: str = "", copyable: bool = True) -> None:
        super().__init__()
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        self.edit = QLineEdit()
        self.edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit.setPlaceholderText(placeholder)
        row.addWidget(self.edit, 1)
        self.toggle = button("", "eye", "ghost", tr("Mostrar / ocultar"))
        self.toggle.clicked.connect(self._toggle)
        row.addWidget(self.toggle)
        if copyable:
            copy = button("", "copy", "ghost", tr("Copiar"))
            copy.clicked.connect(lambda: copy_to_clipboard(self.edit.text()))
            row.addWidget(copy)

    def _toggle(self) -> None:
        hidden = self.edit.echoMode() == QLineEdit.EchoMode.Password
        self.edit.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        self.toggle.setIcon(icons.icon("eye-off" if hidden else "eye", "#334155", 16))

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, value: str) -> None:  # noqa: N802
        self.edit.setText(value)


class Page(QWidget):
    """Página con encabezado y contenido desplazable."""

    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__()
        self.setObjectName("Content")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("Content")
        self.layout_ = QVBoxLayout(inner)
        # Abajo, espacio para que nada quede tapado por el bubble del asistente.
        self.layout_.setContentsMargins(32, 28, 32, 104)
        self.layout_.setSpacing(18)
        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        titles.addWidget(label(title, "PageTitle"))
        if subtitle:
            titles.addWidget(label(subtitle, "PageSubtitle", wrap=True))
        header.addLayout(titles, 1)
        self.header_actions = QHBoxLayout()
        self.header_actions.setSpacing(8)
        header.addLayout(self.header_actions)
        self.layout_.addLayout(header)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def add(self, widget: QWidget | None = None, layout=None, stretch: int = 0) -> None:
        if widget is not None:
            self.layout_.addWidget(widget, stretch)
        if layout is not None:
            self.layout_.addLayout(layout, stretch)

    def finish(self) -> None:
        self.layout_.addStretch(1)

    def refresh(self) -> None:
        pass


def hrow(*widgets: QWidget | None, stretch_first: bool = False, spacing: int = 8) -> QHBoxLayout:
    row = QHBoxLayout()
    row.setSpacing(spacing)
    for index, widget in enumerate(widgets):
        if widget is None:
            row.addStretch(1)
        else:
            row.addWidget(widget, 1 if (stretch_first and index == 0) else 0)
    return row

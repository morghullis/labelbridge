"""Componentes reutilizables de la interfaz."""

from __future__ import annotations

import traceback
from typing import Any, Callable

from PySide6.QtCore import (
    Property,
    QEasingCurve,
    QObject,
    QPoint,
    QPropertyAnimation,
    QRect,
    QRectF,
    QRunnable,
    QSize,
    Qt,
    QThreadPool,
    Signal,
)
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (
    QAbstractButton,
    QBoxLayout,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
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
from .theme import ACCENT, STATE_COLORS, Color


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
        color = "#FFFFFF" if variant == "primary" else ("#8E1F0B" if variant == "danger" else Color.ICON)
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
    """Sí/No con «No» como respuesta predeterminada: Enter nunca confirma algo que cuesta o borra."""
    buttons = QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
    return QMessageBox.question(parent, title, text, buttons, QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes


def run_dialog(dialog) -> int:
    """Abre un diálogo modal y lo libera al terminar (si no, queda en memoria hasta cerrar la ventana padre)."""
    try:
        return dialog.exec()
    finally:
        dialog.deleteLater()


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
        track = QColor(ACCENT) if self.isChecked() else QColor(Color.INPUT_BORDER)
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
        self.toggle.setIcon(icons.icon("eye-off" if hidden else "eye", Color.ICON, 16))

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
        # Título y botones en una fila; si no caben (ventana chica), los botones pasan debajo del título.
        self.header = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.header.setSpacing(12)
        titles = QVBoxLayout()
        titles.setSpacing(4)
        titles.addWidget(label(title, "PageTitle"))
        if subtitle:
            titles.addWidget(label(subtitle, "PageSubtitle", wrap=True))
        self.header.addLayout(titles, 1)
        self.header_actions = QHBoxLayout()
        self.header_actions.setSpacing(8)
        self.header.addLayout(self.header_actions)
        self.layout_.addLayout(self.header)
        scroll.setWidget(inner)
        outer.addWidget(scroll)

    def add(self, widget: QWidget | None = None, layout=None, stretch: int = 0) -> None:
        if widget is not None:
            self.layout_.addWidget(widget, stretch)
        if layout is not None:
            self.layout_.addLayout(layout, stretch)

    def finish(self) -> None:
        self.layout_.addStretch(1)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        needed = self.header_actions.sizeHint().width() + 320  # los botones y un título que se lea
        stacked = needed > self.width() - 64
        direction = QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight
        if self.header.direction() != direction:
            self.header.setDirection(direction)
            self.header_actions.setAlignment(Qt.AlignmentFlag.AlignLeft if stacked else Qt.AlignmentFlag.AlignRight)

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


def step_title(number: int, text: str) -> QWidget:
    """Un paso numerado de una guía («1  Abre el pedido…»), como en las ventanas «Guía FedEx» y «Shopify Flow»."""
    box = QWidget()
    line = QHBoxLayout(box)
    line.setContentsMargins(0, 6, 0, 0)
    line.setSpacing(10)
    badge = label(str(number))
    badge.setFixedSize(24, 24)
    badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
    badge.setStyleSheet(f"QLabel {{ background: {Color.PRIMARY}; color: white; border-radius: 12px; font-weight: 600; }}")
    line.addWidget(badge)
    line.addWidget(label(text, "SectionTitle", wrap=True), 1)
    return box


def fit_height(window: QWidget) -> None:
    """El alto justo para el contenido de una ventana con textos que ocupan varias líneas (Qt la deja apretada)."""
    layout = window.layout()
    if layout is None:
        return
    layout.activate()
    width = max(window.minimumWidth(), window.width())
    needed = layout.heightForWidth(width) if layout.hasHeightForWidth() else layout.sizeHint().height()
    window.resize(width, max(needed, layout.minimumSize().height()))


def context_menu(view, build: Callable[[], Any]) -> None:
    """Clic derecho en una tabla: elige la fila bajo el puntero (si no estaba elegida) y muestra el menú que arma
    `build` (None o un menú vacío: no se muestra nada)."""
    view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

    def show(pos) -> None:
        index = view.indexAt(pos)
        model = view.selectionModel()
        if index.isValid() and model is not None and not model.isRowSelected(index.row(), index.parent()):
            view.selectRow(index.row())
        menu = build()
        if menu is not None and not menu.isEmpty():
            menu.exec(view.viewport().mapToGlobal(pos))

    view.customContextMenuRequested.connect(show)


def divider(vertical: bool = False) -> QFrame:
    """Una línea fina para separar grupos (en vez de cajas con borde)."""
    line = QFrame()
    line.setObjectName("VDivider" if vertical else "Divider")
    return line


def link_button(text: str) -> QPushButton:
    """Un enlace dentro de la app («Ver historial»): texto azul, sin caja."""
    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(f"QPushButton {{ background: transparent; border: none; color: {Color.FOCUS}; "
                      "font-weight: 600; padding: 4px 2px; } QPushButton:hover { text-decoration: underline; }")
    return btn


class FlowRow(QWidget):
    """Bloques en una fila separados por líneas finas (los números de una franja). Si no caben en el ancho, pasan a
    filas de las columnas que quepan, sin barra para desplazarse a los lados."""

    def __init__(self, min_column: int = 170, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.min_column = min_column
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(0)
        self.grid.setVerticalSpacing(14)
        self.setMinimumWidth(1)  # el ancho lo pone la ventana (las columnas se acomodan), nunca al revés
        self.blocks: list[QWidget] = []
        self.columns = 0
        self._dividers: list[QFrame] = []

    def add_block(self, widget: QWidget) -> None:
        self.blocks.append(widget)
        self._relayout(force=True)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._relayout()

    def _relayout(self, force: bool = False) -> None:
        if not self.blocks:
            return
        width = self.width() or self.min_column * len(self.blocks)
        fits = max(1, min(len(self.blocks), width // self.min_column))
        rows = -(-len(self.blocks) // fits)
        columns = -(-len(self.blocks) // rows)  # filas parejas: 5 bloques en 3 + 2, no en 4 + 1
        if columns == self.columns and not force:
            return
        self.columns = columns
        for line in self._dividers:
            self.grid.removeWidget(line)
            line.deleteLater()
        self._dividers = []
        for block in self.blocks:
            self.grid.removeWidget(block)
        for index, block in enumerate(self.blocks):
            row, column = divmod(index, columns)
            if column:
                line = divider(vertical=True)
                self._dividers.append(line)
                self.grid.addWidget(line, row, column * 2 - 1)
            self.grid.addWidget(block, row, column * 2)
        for column in range(columns * 2 - 1):
            self.grid.setColumnStretch(column, 0 if column % 2 else 1)



class SplitRow(QWidget):
    """Una fila de controles con dos grupos: el del inicio (filtros) y el del final (búsqueda). Si la ventana es
    angosta y no caben en una línea, el del final pasa debajo a todo el ancho, en vez de empujar la página a los lados."""

    def __init__(self, spacing: int = 8, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.start = QHBoxLayout()
        self.end = QHBoxLayout()
        for group in (self.start, self.end):
            group.setContentsMargins(0, 0, 0, 0)
            group.setSpacing(spacing)
        self.start.addStretch(1)  # lo que sobra queda entre los dos grupos
        self.box = QBoxLayout(QBoxLayout.Direction.LeftToRight, self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(spacing)
        self.box.addLayout(self.start, 1)
        self.box.addLayout(self.end)
        self.setMinimumWidth(1)  # el ancho lo pone la ventana: si no cabe, se apila (resizeEvent)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    def add_start(self, widget: QWidget) -> None:
        self.start.insertWidget(self.start.count() - 1, widget)  # antes del espacio que empuja al final

    def add_end(self, widget: QWidget, stretch: int = 0) -> None:
        self.end.addWidget(widget, stretch)

    def stacked(self) -> bool:
        return self.box.direction() == QBoxLayout.Direction.TopToBottom

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        needed = self.start.sizeHint().width() + self.box.spacing() + self.end.sizeHint().width()
        direction = QBoxLayout.Direction.TopToBottom if needed > self.width() else QBoxLayout.Direction.LeftToRight
        if self.box.direction() != direction:
            self.box.setDirection(direction)


# --- filas que se adaptan al ancho --------------------------------------------------------------------

def form_layout(spacing: int = 10) -> QFormLayout:
    """Etiqueta a la izquierda y campo a la derecha, igual en Windows y en Mac; si la ventana es angosta, la etiqueta
    de una fila larga pasa arriba de su campo (en vez de empujar la página hacia los lados)."""
    form = QFormLayout()
    form.setSpacing(spacing)
    form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFormAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
    return form


def inline(*widgets: QWidget, spacing: int = 6) -> QWidget:
    """Varias piezas que van siempre juntas en una línea (una etiqueta y su campo) dentro de una FlowLayout."""
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(spacing)
    for widget in widgets:
        row.addWidget(widget)
    return box


class FlowLayout(QLayout):
    """Controles en fila que pasan a la línea siguiente cuando no caben, como el texto: nunca obligan a la ventana a
    ser más ancha. Cada pieza conserva su tamaño y queda centrada en el alto de su línea."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 8, line_spacing: int = 8) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing
        self._line_spacing = line_spacing
        self.setContentsMargins(0, 0, 0, 0)

    # La API de QLayout.
    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802
        """Todo en una línea, si hay espacio."""
        visible = [item for item in self._items if not item.isEmpty()]
        margins = self.contentsMargins()
        width = sum(item.sizeHint().width() for item in visible) + self._spacing * max(0, len(visible) - 1)
        height = max((item.sizeHint().height() for item in visible), default=0)
        return QSize(width + margins.left() + margins.right(), height + margins.top() + margins.bottom())

    def minimumSize(self) -> QSize:  # noqa: N802
        """Lo que pide la pieza más ancha: lo demás pasa a otra línea."""
        margins = self.contentsMargins()
        width = max((item.minimumSize().width() for item in self._items if not item.isEmpty()), default=0)
        return QSize(width + margins.left() + margins.right(), margins.top() + margins.bottom())

    def _arrange(self, rect: QRect, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        lines: list[list[tuple[QLayoutItem, QSize]]] = [[]]
        used = 0
        for item in self._items:
            if item.isEmpty():  # piezas ocultas
                continue
            size = item.sizeHint().boundedTo(QSize(max(area.width(), item.minimumSize().width()), 10_000))
            needed = size.width() if not lines[-1] else used + self._spacing + size.width()
            if lines[-1] and needed > area.width():
                lines.append([])
                needed = size.width()
            lines[-1].append((item, size))
            used = needed
        y = area.y()
        for index, line in enumerate(lines):
            if not line:
                continue
            height = max(size.height() for _item, size in line)
            if apply:
                x = area.x()
                for item, size in line:
                    item.setGeometry(QRect(QPoint(x, y + (height - size.height()) // 2), size))
                    x += size.width() + self._spacing
            y += height + (self._line_spacing if index < len(lines) - 1 else 0)
        return y - rect.y() + margins.bottom()


class ScaledImage(QWidget):
    """Una imagen que se achica con la ventana (nunca pasa su tamaño real ni `max_width`) sin deformarse, con las
    esquinas redondeadas. `clicked`: para abrirla en grande."""

    clicked = Signal()

    def __init__(self, pixmap: QPixmap, max_width: int = 760, radius: float = 8.0) -> None:
        super().__init__()
        self.pixmap = pixmap
        self.radius = radius
        natural = pixmap.deviceIndependentSize()
        self._ratio = natural.height() / max(1.0, natural.width())
        self.max_width = max(1, int(min(max_width, natural.width())))
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
        policy.setHeightForWidth(True)
        self.setSizePolicy(policy)

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return round(min(width, self.max_width) * self._ratio)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(self.max_width, self.heightForWidth(self.max_width))

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        width = min(160, self.max_width)
        return QSize(width, self.heightForWidth(width))

    def paintEvent(self, _event) -> None:  # noqa: N802
        width = min(self.width(), self.max_width)
        target = QRectF(0, 0, width, round(width * self._ratio))
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        clip = QPainterPath()
        clip.addRoundedRect(target, self.radius, self.radius)
        painter.setClipPath(clip)
        painter.drawPixmap(target, self.pixmap, QRectF(self.pixmap.rect()))
        painter.end()

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

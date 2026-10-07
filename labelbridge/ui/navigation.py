"""La navegación de la ventana principal: el menú lateral (ventana ancha), la barra inferior (ventana chica) y el
indicador de estado de la impresión.

Se dibujan a mano para que se vean limpios: la pestaña activa lleva un fondo blanco y una barra fina dorada (la marca),
el paso del mouse se funde en 120 ms y el punto del estado late suave solo mientras imprime. Todas las animaciones son
cortas (≤ 250 ms, salvo el latido) y se apagan si Windows pide menos animaciones.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import QEasingCurve, QPointF, QRectF, QSize, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QAbstractButton, QFrame, QHBoxLayout, QMenu, QSizePolicy, QWidget

from ..i18n import tr
from ..system.accessibility import reduced_motion
from . import icons
from .theme import STATE_DOTS, Color

FAST_MS = 120  # paso del mouse
MOTION_MS = 200  # cambio de pestaña
PULSE_MS = 1800  # latido del estado mientras imprime


@dataclass(frozen=True)
class Destination:
    """Una pestaña de la ventana: su clave, el texto (sin traducir) y el icono."""

    key: str
    text: str
    icon: str


def motion_enabled() -> bool:
    return not reduced_motion()


def _mix(a: QColor, b: QColor, t: float) -> QColor:
    t = max(0.0, min(1.0, t))
    return QColor(round(a.red() + (b.red() - a.red()) * t), round(a.green() + (b.green() - a.green()) * t),
                  round(a.blue() + (b.blue() - a.blue()) * t), round(a.alpha() + (b.alpha() - a.alpha()) * t))


class _Fade:
    """Un valor de 0 a 1 que se anima (o salta, sin animaciones) y repinta el widget."""

    def __init__(self, widget: QWidget, value: float = 0.0, duration: int = FAST_MS) -> None:
        self.widget = widget
        self.value = value
        self.animation = QVariantAnimation(widget)
        self.animation.setDuration(duration)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._set)

    def _set(self, value) -> None:
        self.value = float(value)
        self.widget.update()

    def to(self, target: float) -> None:
        self.animation.stop()
        if not motion_enabled() or not self.widget.isVisible():
            self._set(target)
            return
        self.animation.setStartValue(self.value)
        self.animation.setEndValue(float(target))
        self.animation.start()


def _badge(painter: QPainter, right: float, center_y: float, text: str, attention: bool) -> None:
    """El contador de una pestaña («3»): ámbar si algo espera a una persona, gris si no."""
    font = QFont(painter.font())
    font.setPixelSize(11)
    font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font)
    width = max(20.0, painter.fontMetrics().horizontalAdvance(text) + 12.0)
    rect = QRectF(right - width, center_y - 9, width, 18)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#FFD79D") if attention else QColor(Color.SURFACE_PRESSED))
    painter.drawRoundedRect(rect, 9, 9)
    painter.setPen(QColor("#5E4200") if attention else QColor(Color.TEXT))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)


class NavItem(QAbstractButton):
    """Una pestaña del menú lateral: icono, texto y, si hay, un contador."""

    HEIGHT = 36

    def __init__(self, destination: Destination, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.destination = destination
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setText(tr(destination.text))
        self.setAccessibleName(tr(destination.text))
        self.badge = ""
        self.attention = False
        self._hover = _Fade(self)
        self._active = _Fade(self, duration=MOTION_MS)
        self.toggled.connect(lambda on: self._active.to(1.0 if on else 0.0))

    def set_badge(self, text: str, attention: bool = False) -> None:
        self.badge, self.attention = text, attention
        self.update()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover.to(0.0)
        super().leaveEvent(event)

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(180, self.HEIGHT)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        active, hover = self._active.value, self._hover.value
        background = _mix(QColor(0, 0, 0, 0), QColor(Color.NAV_HOVER), hover * (1 - active))
        background = _mix(background, QColor(Color.SURFACE), active)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(rect, 8, 8)
        if active > 0.01:  # la barra dorada de la pestaña activa, que crece desde el centro
            height = (rect.height() - 16) * active
            painter.setBrush(QColor(Color.GOLD))
            painter.drawRoundedRect(QRectF(rect.left() + 4, rect.center().y() - height / 2, 3, height), 1.5, 1.5)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(Color.FOCUS), 1.5))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 7, 7)
        strong = active > 0.5
        color = Color.TEXT_STRONG if strong else Color.ICON
        pixmap = icons.pixmap(self.destination.icon, color, 18)
        painter.drawPixmap(QRectF(rect.left() + 14, rect.center().y() - 9, 18, 18), pixmap, QRectF(pixmap.rect()))
        font = QFont(self.font())
        font.setPixelSize(13)
        font.setWeight(QFont.Weight.DemiBold if strong else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(Color.TEXT_STRONG if strong else Color.TEXT))
        text_rect = QRectF(rect.left() + 42, rect.top(), rect.width() - 80, rect.height())
        elided = painter.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, int(text_rect.width()))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)
        if self.badge:
            _badge(painter, rect.right() - 8, rect.center().y(), self.badge, self.attention)
        painter.end()


class StatusIndicator(QAbstractButton):
    """El estado de la impresión: un punto de color (que late suave mientras imprime), qué pasa y el detalle.
    `compact`: solo el punto (barra inferior), con el texto en el tooltip."""

    def __init__(self, compact: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.compact = compact
        self.state = "off"
        self.title = ""
        self.detail = ""
        self.live = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setFixedHeight(40 if compact else 46)
        if compact:
            self.setFixedWidth(40)
        self._pulse = QVariantAnimation(self)
        self._pulse.setStartValue(0.0)
        self._pulse.setEndValue(1.0)
        self._pulse.setDuration(PULSE_MS)
        self._pulse.setLoopCount(-1)
        self._pulse.setEasingCurve(QEasingCurve.Type.OutSine)
        self._pulse.valueChanged.connect(lambda _v: self.update())
        self._hover = _Fade(self)

    def set_status(self, state: str, title: str, detail: str, live: bool = False) -> None:
        """state: ok | warn | error | off (colores de STATE_DOTS); live: late (está imprimiendo)."""
        self.state, self.title, self.detail, self.live = state, title, detail, live
        self.setToolTip(f"{title}\n{detail}" if detail else title)
        self.setAccessibleName(f"{title}. {detail}")
        self._sync_pulse()
        self.update()

    def _sync_pulse(self) -> None:
        running = self.live and self.isVisible() and motion_enabled()
        if running and self._pulse.state() != QVariantAnimation.State.Running:
            self._pulse.start()
        elif not running and self._pulse.state() == QVariantAnimation.State.Running:
            self._pulse.stop()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._sync_pulse()

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        self._sync_pulse()

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover.to(0.0)
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._hover.value > 0.01:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_mix(QColor(0, 0, 0, 0), QColor(Color.NAV_HOVER), self._hover.value))
            painter.drawRoundedRect(rect, 8, 8)
        color = QColor(STATE_DOTS.get(self.state, STATE_DOTS["off"]))
        center = QPointF(rect.left() + (rect.width() / 2 if self.compact else 18), rect.center().y())
        if self._pulse.state() == QVariantAnimation.State.Running:  # el halo que se abre y se apaga
            t = float(self._pulse.currentValue() or 0.0)
            halo = QColor(color)
            halo.setAlphaF(0.35 * (1 - t))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(halo)
            painter.drawEllipse(center, 4.5 + 6 * t, 4.5 + 6 * t)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(center, 4.5, 4.5)
        if not self.compact:
            font = QFont(self.font())
            font.setPixelSize(13)
            font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(Color.TEXT_STRONG))
            painter.drawText(QRectF(rect.left() + 32, rect.top() + 5, rect.width() - 36, 18),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)
            font.setPixelSize(12)
            font.setWeight(QFont.Weight.Normal)
            painter.setFont(font)
            painter.setPen(QColor(Color.TEXT_SECONDARY))
            detail = painter.fontMetrics().elidedText(self.detail, Qt.TextElideMode.ElideRight, int(rect.width() - 36))
            painter.drawText(QRectF(rect.left() + 32, rect.top() + 23, rect.width() - 36, 16),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, detail)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(Color.FOCUS), 1.5))
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 7, 7)
        painter.end()


class _BottomItem(QAbstractButton):
    """Una pestaña de la barra inferior: icono arriba y texto abajo (al menos 48 px de alto para el clic)."""

    def __init__(self, destination: Destination, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.destination = destination
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setMinimumSize(64, 52)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setText(tr(destination.text))
        self.setAccessibleName(tr(destination.text))
        self.badge = ""
        self.attention = False
        self._hover = _Fade(self)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._hover.to(1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover.to(0.0)
        super().leaveEvent(event)

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        if self._hover.value > 0.01:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(_mix(QColor(0, 0, 0, 0), QColor(Color.NAV_HOVER), self._hover.value))
            painter.drawRoundedRect(rect.adjusted(4, 4, -4, -4), 8, 8)
        active = self.isChecked()
        color = Color.TEXT_STRONG if active else Color.TEXT_SECONDARY
        pixmap = icons.pixmap(self.destination.icon, color, 20)
        painter.drawPixmap(QRectF(rect.center().x() - 10, rect.top() + 8, 20, 20), pixmap, QRectF(pixmap.rect()))
        font = QFont(self.font())
        font.setPixelSize(11)
        font.setWeight(QFont.Weight.DemiBold if active else QFont.Weight.Normal)
        painter.setFont(font)
        painter.setPen(QColor(color))
        label = painter.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, int(rect.width() - 6))
        painter.drawText(QRectF(rect.left() + 3, rect.top() + 30, rect.width() - 6, 16), Qt.AlignmentFlag.AlignCenter, label)
        if self.badge:
            _badge(painter, rect.center().x() + 24, rect.top() + 12, self.badge, self.attention)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(Color.FOCUS), 1.5))
            painter.drawRoundedRect(rect.adjusted(3, 3, -3, -3), 8, 8)
        painter.end()


class _Indicator(QWidget):
    """La raya dorada arriba de la pestaña activa: se desliza a la nueva (200 ms)."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(MOTION_MS)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(lambda rect: self.setGeometry(rect.toRect()))

    def move_to(self, rect: QRectF) -> None:
        self.animation.stop()
        if not motion_enabled() or not self.isVisible() or self.geometry().width() == 0:
            self.setGeometry(rect.toRect())
            return
        self.animation.setStartValue(QRectF(self.geometry()))
        self.animation.setEndValue(rect)
        self.animation.start()

    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()), 1.5, 1.5)
        painter.fillPath(path, QColor(Color.GOLD))
        painter.end()


class BottomNav(QFrame):
    """La barra inferior para ventanas chicas: las pestañas principales y «Más» con el resto, y el estado."""

    selected = Signal(str)

    def __init__(self, main: list[Destination], more: list[Destination], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("BottomNav")
        self.setFixedHeight(60)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 2, 8, 4)
        row.setSpacing(2)
        self.items: dict[str, _BottomItem] = {}
        for destination in main:
            item = _BottomItem(destination)
            item.clicked.connect(lambda _=False, k=destination.key: self.selected.emit(k))
            row.addWidget(item, 1)
            self.items[destination.key] = item
        self.more_destinations = more
        self.more = _BottomItem(Destination("more", tr("Más"), "more"))
        self.more.setCheckable(True)
        self.more.clicked.connect(self._open_more)
        row.addWidget(self.more, 1)
        self.status = StatusIndicator(compact=True)
        row.addWidget(self.status)
        self.indicator = _Indicator(self)
        self.current = ""

    def _open_more(self) -> None:
        self.more.setChecked(self.current not in self.items)
        menu = QMenu(self)
        for destination in self.more_destinations:
            action = menu.addAction(icons.icon(destination.icon, Color.ICON, 18), tr(destination.text))
            action.setCheckable(True)
            action.setChecked(destination.key == self.current)
            action.triggered.connect(lambda _=False, k=destination.key: self.selected.emit(k))
        size = menu.sizeHint()
        menu.exec(self.more.mapToGlobal(self.more.rect().topRight()) - QPointF(size.width(), size.height()).toPoint())

    def set_current(self, key: str) -> None:
        self.current = key
        for name, item in self.items.items():
            item.setChecked(name == key)
        in_more = key not in self.items
        self.more.setChecked(in_more)
        target = self.more if in_more else self.items[key]
        width = min(28.0, target.width() * 0.5)
        center = target.geometry().center().x()
        self.indicator.move_to(QRectF(center - width / 2, 0, width, 3))

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self.current:  # al cambiar el ancho, la raya va directo a su lugar
            self.indicator.animation.stop()
            target = self.items.get(self.current, self.more)
            width = min(28.0, target.width() * 0.5)
            self.indicator.setGeometry(QRectF(target.geometry().center().x() - width / 2, 0, width, 3).toRect())

    def set_badge(self, key: str, text: str, attention: bool = False) -> None:
        item = self.items.get(key)
        if item is not None:
            item.badge, item.attention = text, attention
            item.update()


def fade_in(widget: QWidget, on_done: Callable[[], None] | None = None) -> None:
    """La pantalla nueva aparece con un fundido corto (160 ms); sin animaciones, aparece y listo."""
    from PySide6.QtCore import QPropertyAnimation
    from PySide6.QtWidgets import QGraphicsOpacityEffect

    if not motion_enabled() or not widget.isVisible():
        if on_done:
            on_done()
        return
    effect = QGraphicsOpacityEffect(widget)
    effect.setOpacity(0.0)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(160)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.setEasingCurve(QEasingCurve.Type.OutCubic)

    def finished() -> None:
        widget.setGraphicsEffect(None)  # sin efecto, la pantalla se dibuja normal (más rápido)
        if on_done:
            on_done()

    animation.finished.connect(finished)
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)

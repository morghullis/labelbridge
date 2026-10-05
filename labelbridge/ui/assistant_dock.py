"""El asistente flotante: un bubble con la máscara viva abajo a la derecha, encima de todas las pantallas, que abre
el chat en un panel. La máscara reacciona a lo que pasa en la conversación (MoodDirector).

Diseño: Claude Design, «Asistente LabelBridge: máscara animada» (expresiones, bubble y panel).
"""

from __future__ import annotations

import re
from typing import Callable

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QPointF, QRectF, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QCursor, QFocusEvent, QFont, QKeySequence, QPainter, QPainterPath, QPen, QRadialGradient, QShortcut
from PySide6.QtWidgets import QFrame, QGraphicsOpacityEffect, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from . import icons
from .mascot import Mascot, MascotView, paint_mascot
from .widgets import label

DISC = 60  # diámetro del bubble
EDGE = 24  # distancia a los bordes de la zona de pantallas
PAD = 12  # margen del bubble para la sombra, el aumento y el aviso
PANEL_W = 400
PANEL_MAX_H = 660
SHADOW = 18  # margen del panel para su sombra
SLEEP_AFTER = 120_000  # ms sin actividad (con el chat cerrado) para dormirse
NUDGE_EVERY = 7000  # ms entre saltitos mientras hay respuestas sin leer
NUDGES = 3
OPEN_MS = 220
CLOSE_MS = 160

QUESTION = re.compile(r"\?[\s*_)»\"'”]*$")
SUCCESS = re.compile(r"(?i)\b(listo|lista|quedó|quedaron|ya est[áa]n?|conectad[ao]s?|agregu[eé]|guard[eé]|activ[eé]|impres[ao])\b")


def talk_seconds(text: str) -> float:
    """Cuánto «habla» la máscara al llegar una respuesta (según lo largo que sea)."""
    return max(0.8, min(3.0, len(text) * 0.022))


def is_question(text: str) -> bool:
    last = next((line for line in reversed(text.strip().splitlines()) if line.strip()), "")
    return bool(QUESTION.search(last.strip()))


# --- la cara según lo que pasa -------------------------------------------------------------------

class MoodDirector(QObject):
    """Traduce lo que pasa en el chat a la expresión de la máscara.

    Base (dura mientras dure la situación): Atenta, Escuchando, Pensando, Trabajando, Pregunta, Dormida.
    Reacciones breves encima: Hola, Contenta, Problema, Sorpresa, Guiño, Esto no, Respondiendo.
    """

    unread_changed = Signal(int)

    def __init__(self, mascot: Mascot, page, is_open: Callable[[], bool]) -> None:
        super().__init__(mascot)
        self.mascot = mascot
        self.page = page
        self.is_open = is_open
        self.unread = 0
        self._busy = False
        self._tool = False
        self._typing = False
        self._asking = False
        self._question = False
        self._asleep = False
        self._success = False
        self._hover_hello = False
        self._sleep = QTimer(self)
        self._sleep.setSingleShot(True)
        self._sleep.timeout.connect(self._fall_asleep)
        self._nudges = 0
        self._nudge = QTimer(self)
        self._nudge.timeout.connect(self._nudge_once)
        self._after = QTimer(self)
        self._after.setSingleShot(True)
        self._pending_reply = ""
        self._after.timeout.connect(self._after_reply)
        page.mood.connect(self.on_mood)
        page.changed.connect(self.update_base)  # p. ej. se conectó o cambió la IA desde Ajustes
        self._restart_sleep()
        self.update_base()

    # --- situación de fondo ------------------------------------------------------------------------
    def update_base(self) -> None:
        if not self.page.conversation.has_key():
            base = "sleepy"  # sin IA conectada todavía: descansa
        elif self._asking:
            base = "curious"  # ventana de permiso o de confirmación abierta (en pleno turno)
        elif self._busy:
            base = "working" if self._tool else "thinking"
        elif self._asleep:
            base = "sleepy"
        elif self._typing:
            base = "listening"
        elif self._question:
            base = "curious"
        else:
            base = "neutral"
        self.mascot.set_base(base)

    def _restart_sleep(self) -> None:
        self._sleep.start(SLEEP_AFTER)

    def _wake(self) -> None:
        self._restart_sleep()
        if self._asleep:
            self._asleep = False
            self.update_base()

    def _fall_asleep(self) -> None:
        if self._busy or self.is_open():
            self._restart_sleep()
            return
        self._asleep = True
        self.update_base()

    # --- lo que pasa en el chat -------------------------------------------------------------------
    def on_mood(self, kind: str, data) -> None:
        self._wake()
        m = self.mascot
        if kind == "typing":
            self._typing = bool(data)
            if self._typing:
                self._question = False
        elif kind == "sent":
            self._busy, self._tool, self._typing, self._question, self._success = True, False, False, False, False
            self._after.stop()
            if m.expression != "wink":  # el guiño de un secreto escrito en el mensaje se deja terminar
                m.calm()
        elif kind == "tool":
            self._tool = True
        elif kind == "tool_done":
            self._tool = False
            if data:
                self._success = True
                m.gesture("nod")
            else:
                m.react("worried", 1.2)
        elif kind == "note":
            m.talk(talk_seconds(str(data)))
            self._count_unread()
        elif kind == "reply":
            text = str(data)
            seconds = talk_seconds(text)
            m.talk(seconds)
            self._question = False
            self._pending_reply = text
            self._after.start(int(seconds * 1000))
            self._count_unread()
        elif kind == "finished":
            self._busy, self._tool = False, False
        elif kind == "error":
            self._busy, self._tool = False, False
            self._after.stop()
            m.react("worried", 3.0)
            self._count_unread()
        elif kind == "refusal":
            self._busy = False
            self._after.stop()
            m.react("no", 1.3)
        elif kind == "stopped":
            self._busy, self._tool = False, False
            self._after.stop()
            m.react("surprised", 0.9)
        elif kind == "secret":
            m.react("wink", 1.4)
        elif kind == "asking":
            self._asking = bool(data)
        elif kind == "connected":
            m.react("happy", 1.6)
            m.shine()
        elif kind == "new":
            self._question = self._success = self._typing = False
            self._after.stop()
            m.react("hello", 1.0)
        self.update_base()

    def _after_reply(self) -> None:
        text = self._pending_reply
        if is_question(text):
            self._question = True
        elif self._success or SUCCESS.search(text):
            self.mascot.react("happy", 1.6)
            self.mascot.shine()
        self._success = False
        self.update_base()

    # --- el bubble ----------------------------------------------------------------------------------
    def hover(self, inside: bool) -> None:
        self._wake()
        if inside:
            self._hover_hello = True
            self.mascot.react("hello", 600.0)  # mientras siga el ratón encima
            self.mascot.shine()
        elif self._hover_hello:
            self._hover_hello = False
            if self.mascot.expression == "hello":
                self.mascot.calm()  # solo se quita el saludo del ratón (no otras reacciones)
        self.update_base()

    def opened(self) -> None:
        self._wake()
        self._hover_hello = False  # el bubble se oculta al abrir: su «salida» no corta este saludo
        self._set_unread(0)
        self.mascot.react("hello", 1.1)
        self.mascot.shine()
        self.update_base()

    def closed(self) -> None:
        self._restart_sleep()
        if self.mascot.expression == "hello":
            self.mascot.calm()
        self.update_base()

    def _count_unread(self) -> None:
        if not self.is_open():
            self._set_unread(self.unread + 1)
            self.mascot.gesture("hop")

    def _set_unread(self, count: int) -> None:
        self.unread = count
        self.unread_changed.emit(count)
        if count:
            self._nudges = 0
            self._nudge.start(NUDGE_EVERY)
        else:
            self._nudge.stop()

    def _nudge_once(self) -> None:
        self._nudges += 1
        if self._nudges > NUDGES or not self.unread:
            self._nudge.stop()
            return
        self.mascot.gesture("hop")


# --- el bubble -----------------------------------------------------------------------------------

class AssistantLauncher(QWidget):
    """El botón redondo con la máscara (60 px), siempre abajo a la derecha."""

    clicked = Signal()
    hovered = Signal(bool)

    def __init__(self, mascot: Mascot, parent: QWidget) -> None:
        super().__init__(parent)
        self.mascot = mascot
        self.unread = 0
        self._scale = 1.0
        self._glow = 0.0
        self._pressed = False
        self._keyboard = False
        self.setFixedSize(DISC + 2 * PAD, DISC + 2 * PAD)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAccessibleName("Asistente")
        self.setAccessibleDescription("Abre el chat del asistente")
        self._tooltip()
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(170)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._animated)
        self._from = (1.0, 0.0)
        self._to = (1.0, 0.0)
        mascot.changed.connect(self.update)

    def _tooltip(self) -> None:
        extra = f" · {self.unread} respuesta(s) nueva(s)" if self.unread else ""
        self.setToolTip(f"Asistente (F1){extra}")

    def set_unread(self, count: int) -> None:
        self.unread = count
        self._tooltip()
        self.update()

    def pop_in(self) -> None:
        """Aparece con un pequeño rebote (al minimizar el chat)."""
        if self.mascot.reduced:
            return
        self._from, self._to = (0.55, 0.0), (1.0, 0.0)
        self._anim.stop()
        self._anim.setEasingCurve(QEasingCurve.Type.OutBack)
        self._anim.setDuration(260)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def _go(self, scale: float, glow: float) -> None:
        self._from, self._to = (self._scale, self._glow), (scale, glow)
        if self.mascot.reduced:
            self._animated(1.0)
            return
        self._anim.stop()
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.setDuration(170)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()

    def _animated(self, value) -> None:
        t = float(value)
        self._scale = self._from[0] + (self._to[0] - self._from[0]) * t
        self._glow = self._from[1] + (self._to[1] - self._from[1]) * t
        self.update()

    # --- ratón y teclado -------------------------------------------------------------------------
    def enterEvent(self, event) -> None:  # noqa: N802
        self.hovered.emit(True)
        self._go(1.07, 1.0)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hovered.emit(False)
        self._go(1.0, 0.0)
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._pressed = True
            self._go(0.93, self._glow)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton or not self._pressed:
            return
        self._pressed = False
        inside = self.rect().contains(event.position().toPoint())
        self._go(1.07 if inside else 1.0, 1.0 if inside else 0.0)
        if inside:
            self.clicked.emit()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
            self.clicked.emit()
            return
        super().keyPressEvent(event)

    def focusInEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        self._keyboard = event.reason() in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason)
        self.update()
        super().focusInEvent(event)

    def focusOutEvent(self, event: QFocusEvent) -> None:  # noqa: N802
        self._keyboard = False
        self.update()
        super().focusOutEvent(event)

    # --- la máscara mira al cursor --------------------------------------------------------------
    def cursor_gaze(self) -> tuple[float, float] | None:
        if not self.isVisible():
            return None
        center = self.mapToGlobal(self.rect().center())
        pos = QCursor.pos()
        dx, dy = pos.x() - center.x(), pos.y() - center.y()
        return (round(max(-1.0, min(1.0, dx / 260.0)), 2), round(max(-1.0, min(1.0, dy / 200.0)), 2))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.mascot.gaze_source = self.cursor_gaze
        self.mascot.attach(self)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        if self.mascot.gaze_source == self.cursor_gaze:
            self.mascot.gaze_source = None
        self.mascot.detach(self)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        r = DISC / 2 * self._scale
        shadow = QRadialGradient(QPointF(c.x(), c.y() + 5), r + 10)
        shadow.setColorAt(0.68, QColor(15, 23, 42, 90))
        shadow.setColorAt(1.0, QColor(15, 23, 42, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(shadow)
        p.drawEllipse(QPointF(c.x(), c.y() + 5), r + 10, r + 10)
        paint_mascot(p, QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r), self.mascot.frame, badge=True, glow=self._glow)
        if self._keyboard and self.hasFocus():
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#6366F1"), 2.5))
            p.drawEllipse(c, r + 4, r + 4)
        if self.unread:
            bx, by, br = c.x() + r * 0.72, c.y() - r * 0.72, 10.0
            p.setPen(QPen(QColor("#FFFFFF"), 2))
            p.setBrush(QColor("#6366F1"))
            p.drawEllipse(QPointF(bx, by), br, br)
            font = QFont(self.font())
            font.setPixelSize(11)
            font.setBold(True)
            p.setFont(font)
            p.setPen(QColor("#FFFFFF"))
            p.drawText(QRectF(bx - br, by - br, 2 * br, 2 * br), Qt.AlignmentFlag.AlignCenter,
                       "9+" if self.unread > 9 else str(self.unread))
        p.end()


# --- el panel del chat -----------------------------------------------------------------------------

class AssistantPanel(QWidget):
    """Tarjeta flotante con la cabecera (máscara viva, estado, acciones) y el chat."""

    minimize = Signal()

    def __init__(self, mascot: Mascot, page, parent: QWidget) -> None:
        super().__init__(parent)
        self.page = page
        self.setObjectName("AssistantPanel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(SHADOW, SHADOW - 6, SHADOW, SHADOW + 6)  # la sombra cae hacia abajo
        self.card = QFrame()
        self.card.setObjectName("AssistantCard")
        outer.addWidget(self.card)
        column = QVBoxLayout(self.card)
        column.setContentsMargins(1, 1, 1, 1)
        column.setSpacing(0)

        header = QFrame()
        header.setObjectName("AssistantHeader")
        header.setFixedHeight(68)
        row = QHBoxLayout(header)
        row.setContentsMargins(14, 10, 8, 10)
        row.setSpacing(10)
        self.face = MascotView(mascot, 44, badge=True)
        self.face.setAccessibleName("Máscara del asistente")
        row.addWidget(self.face)
        titles = QVBoxLayout()
        titles.setSpacing(1)
        titles.addStretch(1)
        titles.addWidget(label("Asistente", "AssistantTitle"))
        self.status = label("", "AssistantStatus")
        titles.addWidget(self.status)
        titles.addStretch(1)
        row.addLayout(titles, 1)
        self.key_btn = self._icon_button("key", "Conectar IA: elegir el proveedor de IA y su clave")
        self.key_btn.clicked.connect(page.toggle_connect)
        row.addWidget(self.key_btn)
        self.new_btn = self._icon_button("refresh", "Nueva conversación")
        self.new_btn.clicked.connect(page.new_conversation)
        row.addWidget(self.new_btn)
        self.min_btn = self._icon_button("chevron-down", "Minimizar (Esc)")
        self.min_btn.clicked.connect(self.minimize.emit)
        row.addWidget(self.min_btn)
        column.addWidget(header)
        column.addWidget(page, 1)

        escape = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        escape.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        escape.activated.connect(self.minimize.emit)
        page.changed.connect(self._update_status)
        self._update_status()

    def _icon_button(self, name: str, tip: str) -> QPushButton:
        btn = QPushButton("")
        btn.setObjectName("HeaderButton")
        btn.setIcon(icons.icon(name, "#CBD5E1", 18))
        btn.setFixedSize(34, 34)
        btn.setToolTip(tip)
        btn.setAccessibleName(tip.split(":")[0].split(" (")[0])
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def _update_status(self) -> None:
        text = self.page.status_text()
        width = max(80, self.status.width() or 200)
        self.status.setText(self.status.fontMetrics().elidedText(text, Qt.TextElideMode.ElideRight, width))
        self.status.setToolTip(text)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        QTimer.singleShot(0, self._update_status)

    def paintEvent(self, event) -> None:  # noqa: N802
        """Sombra suave alrededor de la tarjeta (dibujada, sin efectos que cuesten en cada fotograma)."""
        if self.card.geometry().contains(event.rect()):
            return  # se repinta algo dentro de la tarjeta (p. ej. la máscara): la sombra no cambia
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(self.card.geometry())
        steps = 14
        for i in range(steps):
            grow = SHADOW * (i + 1) / steps
            alpha = int(14 * (1 - i / steps) ** 1.6)
            path = QPainterPath()
            path.addRoundedRect(card.adjusted(-grow * 0.7, -grow * 0.4, grow * 0.7, grow * 0.9).translated(0, 5),
                                16 + grow, 16 + grow)
            p.fillPath(path, QColor(15, 23, 42, alpha))
        p.end()


# --- todo junto ------------------------------------------------------------------------------------

class AssistantDock(QObject):
    """El bubble y el panel, por encima de las pantallas de la ventana principal."""

    toggled = Signal(bool)  # el chat se abrió (True) o se cerró (False): la pestaña «Asistente» lo refleja

    def __init__(self, ctx, host: QWidget, area: QWidget, connector=None) -> None:
        super().__init__(host)
        from .pages.assistant import AssistantPage

        self.host = host
        self.area = area
        self.is_open = False
        self.mascot = Mascot(self)
        self.page = AssistantPage(ctx, connector)
        self.panel = AssistantPanel(self.mascot, self.page, host)
        self.panel.hide()
        self.launcher = AssistantLauncher(self.mascot, host)
        self.director = MoodDirector(self.mascot, self.page, lambda: self.is_open)
        self.director.unread_changed.connect(self.launcher.set_unread)
        self.launcher.clicked.connect(self.open)
        self.launcher.hovered.connect(self.director.hover)
        self.panel.minimize.connect(self.close)
        self._effect: QGraphicsOpacityEffect | None = None
        self._anim = QVariantAnimation(self)
        self._anim.valueChanged.connect(self._animated)
        self._anim.finished.connect(self._animation_done)
        self._closing = False
        host.installEventFilter(self)
        area.installEventFilter(self)
        self.place()
        self.launcher.show()
        self.launcher.raise_()

    # --- posición -------------------------------------------------------------------------------
    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show, QEvent.Type.LayoutRequest):
            QTimer.singleShot(0, self.place)
        return False

    def _panel_rect(self) -> tuple[int, int, int, int]:
        area = self.area.geometry()
        right, bottom = area.x() + area.width(), area.y() + area.height()
        card_h = max(360, min(PANEL_MAX_H, area.height() - 2 * EDGE))
        x = right - EDGE - PANEL_W - SHADOW
        y = bottom - EDGE - card_h - (SHADOW - 6)
        return x, y, PANEL_W + 2 * SHADOW, card_h + 2 * SHADOW

    def place(self) -> None:
        area = self.area.geometry()
        right, bottom = area.x() + area.width(), area.y() + area.height()
        self.launcher.move(right - EDGE - DISC - PAD, bottom - EDGE - DISC - PAD)
        if self._anim.state() != QVariantAnimation.State.Running:
            self.panel.setGeometry(*self._panel_rect())
        self.launcher.raise_()
        self.panel.raise_()

    # --- abrir y cerrar ---------------------------------------------------------------------------
    def toggle(self) -> None:
        self.close() if self.is_open else self.open()

    def open(self, connect: bool = False, provider: str | None = None) -> None:
        if connect:
            self.page.show_connect(provider)
        if self.is_open and not self._closing:
            self._focus()
            return
        self.is_open = True
        self._closing = False
        self.toggled.emit(True)
        self.director.opened()
        self.launcher.hide()
        self.place()
        self.panel.show()
        self.panel.raise_()
        self._focus()
        self._run(opening=True)

    def close(self) -> None:
        if not self.is_open:
            return
        self.is_open = False
        self._closing = True
        self.toggled.emit(False)
        focus = self.panel.focusWidget()
        self._return_focus = focus is not None and focus.hasFocus()
        self.director.closed()
        self._run(opening=False)

    def _focus(self) -> None:
        if self.page._card_wanted and not self.page.conversation.has_key():
            self.page.key_field.edit.setFocus()
        elif self.page.input.isEnabled():
            self.page.input.setFocus()

    def _run(self, opening: bool) -> None:
        """Aparece deslizándose hacia arriba y desvaneciéndose; al cerrar, al revés. Sin animación si Windows
        pide menos movimiento."""
        self._anim.stop()
        if self.mascot.reduced or not self.host.isVisible():
            self._animated(1.0 if opening else 0.0)
            self._animation_done()
            return
        if self._effect is None:
            self._effect = QGraphicsOpacityEffect(self.panel)
            self.panel.setGraphicsEffect(self._effect)
        self._anim.setDuration(OPEN_MS if opening else CLOSE_MS)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic if opening else QEasingCurve.Type.InCubic)
        self._anim.setStartValue(0.0 if opening else 1.0)
        self._anim.setEndValue(1.0 if opening else 0.0)
        self._anim.start()

    def _animated(self, value) -> None:
        t = float(value)
        x, y, w, h = self._panel_rect()
        self.panel.move(x, int(y + (1 - t) * 16))
        if self._effect is not None:
            self._effect.setOpacity(t)

    def _animation_done(self) -> None:
        # Sin efecto mientras está quieto: el chat se pinta directo (la máscara anima sin coste extra).
        self.panel.setGraphicsEffect(None)
        self._effect = None
        if self._closing:
            self._closing = False
            self.panel.hide()
            self.place()
            self.launcher.show()
            self.launcher.raise_()
            self.launcher.pop_in()
            if getattr(self, "_return_focus", False):  # cerrado con el teclado: el foco vuelve al bubble
                self.launcher.setFocus(Qt.FocusReason.TabFocusReason)
        else:
            self.place()

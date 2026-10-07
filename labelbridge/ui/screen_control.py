"""La parte visible del control de la pantalla del asistente (assistant/screen.py): capturas, el permiso de la tarea,
el permiso de cada acción con el punto marcado y la barra de arriba con «Detener».

Todo corre en el hilo de la interfaz (lo llama QtAssistantUI). Las coordenadas de las capturas y de las ventanas de
LabelBridge se dan en píxeles físicos de la pantalla principal, los mismos que usa el mouse de Windows.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPoint, QRect, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..assistant.screen import SHOT_LONG_EDGE, Frame, scale_for
from ..i18n import tr
from .theme import Color
from .widgets import button, label

THUMB_WIDTH = 560


def _png(image: QImage) -> bytes:
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data.data())


def _grab() -> tuple[QPixmap, float]:
    screen = QGuiApplication.primaryScreen()
    return screen.grabWindow(0), screen.devicePixelRatio()


def blocked_areas() -> list[tuple[int, int, int, int]]:
    """Las ventanas de LabelBridge que se ven (en píxeles físicos): el asistente nunca las usa con el mouse."""
    screen = QGuiApplication.primaryScreen()
    ratio = screen.devicePixelRatio()
    origin = screen.geometry().topLeft()
    areas = []
    for widget in QApplication.topLevelWidgets():
        if not widget.isVisible() or widget.isMinimized():
            continue
        rect = widget.frameGeometry().translated(-origin)
        areas.append((round(rect.x() * ratio), round(rect.y() * ratio), round(rect.width() * ratio),
                      round(rect.height() * ratio)))
    return areas


def capture_screen() -> Frame:
    """La pantalla principal, achicada para el modelo (mejor puntería y menos tokens)."""
    pixmap, _ratio = _grab()
    width, height = pixmap.width(), pixmap.height()
    scale = scale_for(width, height)
    image = pixmap.toImage()
    if scale < 1:
        image = image.scaled(round(width * scale), round(height * scale), Qt.AspectRatioMode.IgnoreAspectRatio,
                             Qt.TransformationMode.SmoothTransformation)
    return Frame(_png(image), scale, width, height, blocked_areas())


def capture_region(rect: tuple[int, int, int, int]) -> bytes:
    """Un trozo de la pantalla a resolución completa (el zoom del modelo), achicado solo si es muy grande."""
    pixmap, _ratio = _grab()
    image = pixmap.toImage().copy(QRect(*rect))
    long_edge = max(image.width(), image.height())
    if long_edge > SHOT_LONG_EDGE:
        image = image.scaled(round(image.width() * SHOT_LONG_EDGE / long_edge),
                             round(image.height() * SHOT_LONG_EDGE / long_edge),
                             Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
    return _png(image)


def marked_thumbnail(frame: Frame | None, points: list[tuple[int, int]]) -> QPixmap | None:
    """La última captura con el punto (o los puntos de un arrastre) marcados en rojo, del ancho del permiso."""
    if frame is None:
        return None
    image = QImage.fromData(frame.png, "PNG")
    if image.isNull():
        return None
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    radius = max(14, image.width() // 45)
    for index, (x, y) in enumerate(points):
        painter.setPen(QPen(QColor("#E11D48"), max(3, radius // 3)))
        painter.setBrush(QColor(225, 29, 72, 60))
        painter.drawEllipse(QPoint(x, y), radius, radius)
        if len(points) > 1:  # arrastre: 1 → 2
            font = QFont()
            font.setBold(True)
            font.setPixelSize(radius * 2)
            painter.setFont(font)
            painter.drawText(QPoint(x + radius + 4, y - radius), str(index + 1))
    painter.end()
    return QPixmap.fromImage(image.scaledToWidth(min(THUMB_WIDTH, image.width()),
                                                  Qt.TransformationMode.SmoothTransformation))


class ScreenConsentDialog(QDialog):
    """El permiso de la tarea: ver la pantalla (y lo que implica), una vez por conversación."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(tr("Control de la pantalla"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        layout.addWidget(label(tr("¿Dejar que el asistente use tu pantalla?"), "CardTitle"))
        layout.addWidget(label(tr(
            "Para esta tarea el asistente quiere ver tu pantalla y usar el mouse y el teclado.\n\n"
            "• Cada acción te pedirá permiso, con el punto marcado o el texto exacto.\n"
            "• Lo que se ve en la pantalla se envía a tu proveedor de IA (Claude) mientras dure la tarea: cierra antes "
            "lo que tenga contraseñas o datos de clientes.\n"
            "• LabelBridge se minimiza mientras tanto. Para pararlo, pulsa «Detener» en la barra de arriba o en "
            "cualquier permiso."), wrap=True))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        no = button(tr("No"))
        no.clicked.connect(self.reject)
        yes = button(tr("Permitir para esta tarea"), "check", "primary")
        yes.clicked.connect(self.accept)
        buttons.addWidget(no)
        buttons.addWidget(yes)
        layout.addLayout(buttons)
        no.setFocus()


class ScreenActionDialog(QDialog):
    """El permiso de UNA acción: qué va a hacer, para qué (lo que dijo el modelo) y dónde."""

    def __init__(self, what: str, reason: str, frame: Frame | None, points: list[tuple[int, int]],
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.choice = "deny"
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(tr("El asistente pide permiso"))
        self.setMinimumWidth(THUMB_WIDTH + 44)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 16)
        layout.setSpacing(8)
        layout.addWidget(label(tr("El asistente quiere usar tu pantalla"), "CardTitle"))
        if reason.strip():
            layout.addWidget(label(f"«{reason.strip()[:300]}»", "Muted", wrap=True))
        layout.addWidget(label(what, "SectionTitle", wrap=True))
        thumbnail = marked_thumbnail(frame, points)
        if thumbnail is not None and points:
            picture = QLabel()
            picture.setPixmap(thumbnail)
            picture.setObjectName("Card")
            layout.addWidget(picture, 0, Qt.AlignmentFlag.AlignHCenter)
        buttons = QHBoxLayout()
        stop = button(tr("Detener todo"), "stop", "danger")
        stop.clicked.connect(lambda: self._done("stop"))
        buttons.addWidget(stop)
        buttons.addStretch(1)
        no = button(tr("No"))
        no.clicked.connect(lambda: self._done("deny"))
        allow = button(tr("Permitir"), "check", "primary")
        allow.clicked.connect(lambda: self._done("allow"))
        buttons.addWidget(no)
        buttons.addWidget(allow)
        layout.addLayout(buttons)
        no.setFocus()

    def _done(self, choice: str) -> None:
        self.choice = choice
        self.accept()


class ScreenBar(QFrame):
    """Arriba de todo mientras el asistente usa la pantalla: se ve que está pasando y se para con un clic."""

    def __init__(self, on_stop: Callable[[], None]) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint)
        self.setObjectName("ScreenBar")
        self.setStyleSheet(f"QFrame#ScreenBar {{ background: {Color.NAV}; border: 1px solid {Color.INPUT_BORDER}; border-radius: 10px; }}"
                           " QLabel { color: white; font-weight: 600; }")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 6, 8, 6)
        row.setSpacing(12)
        row.addWidget(QLabel(tr("El asistente está usando la pantalla · cada acción te pide permiso")))
        stop = button(tr("Detener"), "stop", "danger")
        stop.clicked.connect(on_stop)
        row.addWidget(stop)

    def place(self) -> None:
        self.adjustSize()
        area = QGuiApplication.primaryScreen().availableGeometry()
        self.move(area.center().x() - self.width() // 2, area.top() + 6)

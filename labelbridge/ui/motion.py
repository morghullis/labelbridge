"""Animaciones de la interfaz, cortas y sobrias (150–450 ms), en un solo lugar.

- `NeonGlow`: el botón principal se enciende con un brillo verde neón al pasar el mouse (y se apaga al salir).
- `count_up`: un número sube hasta su valor (las cifras del inicio y del reporte).
- `busy_bar`: una barra fina que corre mientras algo carga.

Todas respetan «menos animaciones» del sistema (system.accessibility): sin animaciones, el resultado aparece directo.
Los efectos gráficos se quitan al terminar para que la pantalla se dibuje normal (más rápido y sin efectos anidados).
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QEasingCurve, QEvent, QObject, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QGraphicsDropShadowEffect, QLabel, QProgressBar, QWidget

from .navigation import motion_enabled
from .theme import Color

GLOW_MS = 180
GLOW_BLUR = 22
COUNT_MS = 450


class NeonGlow(QObject):
    """Brillo neón al pasar el mouse por un botón (o al llegar con el teclado)."""

    def __init__(self, button: QWidget, color: str = Color.PRIMARY) -> None:
        super().__init__(button)
        self.button = button
        self.color = QColor(color)
        self.effect: QGraphicsDropShadowEffect | None = None
        self.value = 0.0
        self.animation = QVariantAnimation(self)
        self.animation.setDuration(GLOW_MS)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.animation.valueChanged.connect(self._apply)
        self.animation.finished.connect(self._settle)
        button.installEventFilter(self)

    def eventFilter(self, watched, event) -> bool:  # noqa: N802
        kind = event.type()
        if kind in (QEvent.Type.Enter, QEvent.Type.FocusIn) and self.button.isEnabled():
            self.to(1.0)
        elif kind in (QEvent.Type.Leave, QEvent.Type.FocusOut, QEvent.Type.Hide) or (
                kind == QEvent.Type.EnabledChange and not self.button.isEnabled()):
            self.to(0.0)
        return False

    def to(self, target: float) -> None:
        if not motion_enabled():
            return  # sin animaciones, sin brillo: el color del hover ya lo indica
        if target > 0 and self.effect is None:
            self.effect = QGraphicsDropShadowEffect(self.button)
            self.effect.setOffset(0, 0)
            self.effect.setBlurRadius(0)
            self.effect.setColor(QColor(0, 0, 0, 0))
            self.button.setGraphicsEffect(self.effect)
        if self.effect is None:
            return
        self.animation.stop()
        self.animation.setStartValue(self.value)
        self.animation.setEndValue(float(target))
        self.animation.start()

    def _apply(self, value) -> None:
        self.value = float(value)
        if self.effect is None:
            return
        color = QColor(self.color)
        color.setAlpha(round(150 * self.value))
        self.effect.setColor(color)
        self.effect.setBlurRadius(GLOW_BLUR * self.value)

    def _settle(self) -> None:
        if self.value <= 0.01 and self.effect is not None:
            self.button.setGraphicsEffect(None)  # apagado: sin efecto (Qt lo borra)
            self.effect = None


def count_up(label: QLabel, target: float, text: Callable[[float], str], duration: int = COUNT_MS) -> None:
    """Muestra `text(target)`; con animaciones, el número sube desde 0 (o desde el anterior) hasta ahí."""
    previous = getattr(label, "_count_value", 0.0)
    animation: QVariantAnimation | None = getattr(label, "_count_animation", None)
    if animation is not None:
        animation.stop()
    label._count_value, label._count_text = target, text
    if not motion_enabled() or not label.isVisible() or target == previous:
        label.setText(text(target))
        return
    if animation is None:  # una sola animación por cifra, reusada (la app queda abierta semanas)
        animation = QVariantAnimation(label)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.valueChanged.connect(lambda value: label.setText(label._count_text(float(value))))
        animation.finished.connect(lambda: label.setText(label._count_text(label._count_value)))  # el valor exacto
        label._count_animation = animation
    animation.setDuration(duration)
    animation.setStartValue(float(previous))
    animation.setEndValue(float(target))
    animation.start()


def busy_bar() -> QProgressBar:
    """La barra fina que corre mientras algo carga (oculta hasta que se muestre)."""
    bar = QProgressBar()
    bar.setRange(0, 0)
    bar.setTextVisible(False)
    bar.setAccessibleName("Cargando")
    bar.setVisible(False)
    return bar

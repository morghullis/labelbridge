"""Gráficos de la app, dibujados con QPainter (QtCharts no va en la app empaquetada).

Barras finas (≤ 24 px) con el extremo redondeado y la base recta, una sola serie en el dorado de la marca (contraste
≥ 3:1 sobre blanco), líneas guía finas y grises, y el valor solo en la barra más alta: los demás aparecen al pasar el
mouse o con el teclado (flechas), y siempre se pueden ver como tabla. Las barras crecen en 200 ms al llegar datos
nuevos, salvo con «menos animaciones».
"""

from __future__ import annotations

import math
from typing import Callable

from PySide6.QtCore import QEasingCurve, QPoint, QRectF, QSize, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QToolTip, QWidget

from .navigation import motion_enabled
from .theme import Color, Font

BAR_MAX = 24.0  # grosor máximo de una barra
RADIUS = 4.0  # extremo redondeado (la base queda recta)
GRID_LINES = 4


def nice_step(top: float, lines: int = GRID_LINES) -> float:
    """Un paso «redondo» (1, 2, 2,5 o 5 × 10ⁿ) para que `lines` líneas guía cubran `top`."""
    if top <= 0:
        return 1.0
    raw = top / lines
    power = 10 ** math.floor(math.log10(raw))
    for factor in (1, 2, 2.5, 5, 10):
        if raw <= factor * power:
            return factor * power
    return 10 * power


class DailyBars(QWidget):
    """Una barra por día (o por categoría ordenada). `value_text` da el texto de un valor (p. ej. «US$1,450»)."""

    def __init__(self, value_text: Callable[[float], str] = lambda v: f"{v:,.0f}", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.value_text = value_text
        self.labels: list[str] = []
        self.values: list[float] = []
        self.details: list[str] = []  # lo que dice el tooltip debajo del valor (p. ej. «Martes 30 · 3 pedidos»)
        self.empty_text = ""
        self.hovered = -1
        self._grow = 1.0
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(200)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.setStartValue(0.0)
        self._animation.setEndValue(1.0)
        self._animation.valueChanged.connect(self._set_grow)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setMinimumHeight(180)

    def set_data(self, labels: list[str], values: list[float], details: list[str] | None = None,
                 empty_text: str = "") -> None:
        self.labels, self.values = list(labels), [max(0.0, float(v)) for v in values]
        self.details = list(details or [""] * len(values))
        self.empty_text = empty_text
        self.hovered = -1
        self.setAccessibleDescription("; ".join(f"{label}: {self.value_text(value)}"
                                                for label, value in zip(self.labels, self.values)))
        if motion_enabled() and self.isVisible() and any(self.values):
            self._animation.stop()
            self._animation.start()
        else:
            self._grow = 1.0
        self.update()

    def _set_grow(self, value) -> None:
        self._grow = float(value)
        self.update()

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(520, 220)

    def minimumSizeHint(self) -> QSize:  # noqa: N802
        return QSize(240, 180)

    # --- geometría ---------------------------------------------------------------------------------------
    def _scale(self) -> tuple[float, float]:
        """(paso de las líneas guía, tope del eje)."""
        step = nice_step(max(self.values, default=0.0))
        return step, step * GRID_LINES

    def _areas(self) -> tuple[QRectF, float]:
        """El área de las barras y el ancho de cada día."""
        metrics = self.fontMetrics()
        step, _top = self._scale()
        ticks = [self.value_text(step * i) for i in range(GRID_LINES + 1)]
        left = max(metrics.horizontalAdvance(t) for t in ticks) + 10
        plot = QRectF(left, 18, max(10.0, self.width() - left - 4), max(10.0, self.height() - 18 - metrics.height() - 10))
        slot = plot.width() / max(1, len(self.values))
        return plot, slot

    def _bar(self, index: int, plot: QRectF, slot: float, top: float) -> QRectF:
        width = min(BAR_MAX, slot * 0.5)
        height = plot.height() * (self.values[index] / top if top else 0) * self._grow
        x = plot.left() + slot * index + (slot - width) / 2
        return QRectF(x, plot.bottom() - height, width, height)

    def _index_at(self, x: float) -> int:
        plot, slot = self._areas()
        if not self.values or x < plot.left() or x > plot.right():
            return -1
        return min(len(self.values) - 1, int((x - plot.left()) // slot))  # todo el día es el blanco (no solo la barra)

    # --- dibujo -----------------------------------------------------------------------------------------
    def paintEvent(self, _event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        plot, slot = self._areas()
        step, top = self._scale()
        metrics = self.fontMetrics()
        # Líneas guía finas y grises, con su valor redondo.
        for i in range(GRID_LINES + 1):
            y = plot.bottom() - plot.height() * i / GRID_LINES
            painter.setPen(QPen(QColor(Color.DIVIDER), 1))
            painter.drawLine(int(plot.left()), int(y), int(plot.right()), int(y))
            painter.setPen(QColor(Color.TEXT_SECONDARY))
            text = self.value_text(step * i)
            painter.drawText(QRectF(0, y - metrics.height() / 2, plot.left() - 8, metrics.height()),
                             int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter), text)
        if not any(self.values):
            painter.setPen(QColor(Color.TEXT_SECONDARY))
            painter.drawText(plot, int(Qt.AlignmentFlag.AlignCenter), self.empty_text)
        best = max(range(len(self.values)), key=lambda i: self.values[i]) if any(self.values) else -1
        for index, label in enumerate(self.labels):
            bar = self._bar(index, plot, slot, top)
            if bar.height() > 0.5:
                color = QColor(Color.GOLD)
                if index == self.hovered:
                    color = color.lighter(112)  # la barra señalada se aclara un poco
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(color)
                painter.drawPath(_rounded_top(bar))
            # El día debajo; el valor solo arriba de la barra más alta.
            painter.setPen(QColor(Color.TEXT_STRONG if index == self.hovered else Color.TEXT_SECONDARY))
            painter.drawText(QRectF(plot.left() + slot * index, plot.bottom() + 6, slot, metrics.height()),
                             int(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop), label)
            if index == best and self._grow >= 1.0:
                font = QFont(painter.font())
                font.setWeight(QFont.Weight.DemiBold)
                font.setPixelSize(Font.SMALL)
                painter.setFont(font)
                painter.setPen(QColor(Color.TEXT_STRONG))
                text = self.value_text(self.values[index])
                width = painter.fontMetrics().horizontalAdvance(text) + 8
                center = bar.center().x()
                left = min(max(center - width / 2, plot.left()), plot.right() - width)
                painter.drawText(QRectF(left, bar.top() - metrics.height() - 2, width, metrics.height()),
                                 int(Qt.AlignmentFlag.AlignCenter), text)
                painter.setFont(self.font())
        if self.hasFocus() and 0 <= self.hovered < len(self.values):
            painter.setPen(QPen(QColor(Color.FOCUS), 1.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(plot.left() + slot * self.hovered + 1, plot.top(), slot - 2,
                                           plot.height() + metrics.height() + 8), 6, 6)
        painter.end()

    # --- puntero y teclado -------------------------------------------------------------------------------------
    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        self._point(self._index_at(event.position().x()), event.globalPosition().toPoint())

    def leaveEvent(self, event) -> None:  # noqa: N802
        self.hovered = -1
        QToolTip.hideText()
        self.update()
        super().leaveEvent(event)

    def keyPressEvent(self, event) -> None:  # noqa: N802
        if not self.values or event.key() not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            super().keyPressEvent(event)
            return
        move = -1 if event.key() == Qt.Key.Key_Left else 1
        index = (self.hovered + move) % len(self.values) if self.hovered >= 0 else 0
        plot, slot = self._areas()
        anchor = QPoint(int(plot.left() + slot * (index + 0.5)), int(plot.top()))
        self._point(index, self.mapToGlobal(anchor))

    def _point(self, index: int, global_pos: QPoint) -> None:
        if index != self.hovered:
            self.hovered = index
            self.update()
        if index < 0:
            QToolTip.hideText()
            return
        detail = self.details[index] if index < len(self.details) else ""
        value = self.value_text(self.values[index])
        # El valor primero (es lo que se busca), el día después.
        QToolTip.showText(global_pos, f"<b>{value}</b><br>{detail or self.labels[index]}", self)

    def table_html(self, value_title: str, label_title: str) -> str:
        """Los mismos datos como tabla (la vista accesible del gráfico)."""
        rows = "".join(f"<tr><td>{label}</td><td align='right'>{self.value_text(value)}</td>"
                       f"<td>&nbsp;&nbsp;{detail}</td></tr>"
                       for label, value, detail in zip(self.labels, self.values, self.details))
        return (f"<table cellspacing='0' cellpadding='4'><tr><th align='left'>{label_title}</th>"
                f"<th align='right'>{value_title}</th><th></th></tr>{rows}</table>")


def _rounded_top(rect: QRectF) -> QPainterPath:
    """Una barra con el extremo redondeado y la base recta (crece desde la línea base)."""
    radius = min(RADIUS, rect.width() / 2, rect.height())
    path = QPainterPath()
    path.moveTo(rect.left(), rect.bottom())
    path.lineTo(rect.left(), rect.top() + radius)
    path.quadTo(rect.left(), rect.top(), rect.left() + radius, rect.top())
    path.lineTo(rect.right() - radius, rect.top())
    path.quadTo(rect.right(), rect.top(), rect.right(), rect.top() + radius)
    path.lineTo(rect.right(), rect.bottom())
    path.closeSubpath()
    return path

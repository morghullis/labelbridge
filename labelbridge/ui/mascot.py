"""La máscara dorada de la marca convertida en el personaje del asistente.

Se dibuja con vectores (QPainter), no con imágenes: así se ve nítida a cualquier tamaño y cada rasgo se puede
animar (párpados, mirada, cejas, boca, inclinación, gemas de la corona). La geometría es la misma que la del
diseño en Claude Design (caja de 200 × 200).

- `Pose`: los rasgos de una expresión, como números.
- `EXPRESSIONS`: las expresiones del asistente (Atenta, Pensando, Contenta…).
- `paint_mascot`: dibuja la máscara con una pose y los efectos del momento.
- `Mascot`: la máscara viva (expresión de fondo, reacciones, parpadeo, mirada, gestos).
- `MascotView`: el widget que la muestra.
"""

from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field, fields, replace

from PySide6.QtCore import QObject, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QConicalGradient,
    QCursor,
    QFont,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QRadialGradient,
    QTransform,
)
from PySide6.QtWidgets import QWidget

# --- paleta (la del logo y la app) ---------------------------------------------------------------
GOLD_LIGHT = "#FCEABB"
GOLD = "#D9AE55"
GOLD_DEEP = "#8E6522"
SOCKET = "#170F06"  # el interior de los ojos y la boca
LINE = "#4E3510"  # surcos tallados
INDIGO = "#6366F1"
INDIGO_GLOW = "#7C83FF"  # luz de las gemas cuando el asistente piensa o trabaja
EMERALD = "#34D399"
AMBER = "#F59E0B"
ROSE = "#F2A27B"


@dataclass
class Pose:
    """Rasgos de la cara. 0 y 1 son los valores «normales»; la animación interpola entre poses."""

    eye_open: float = 1.0  # párpado superior (0 cerrado, 1 normal, >1 muy abierto)
    eye_lower: float = 1.0  # párpado inferior (negativo: ojos sonrientes en media luna)
    eye_wide: float = 1.0  # ancho de los ojos
    brow_l: float = 0.0  # altura de cada ceja (negativo = levantada)
    brow_r: float = 0.0
    brow_tilt: float = 0.0  # positivo: cejas juntas (concentración); negativo: preocupación
    smile: float = 0.25  # -1 triste … 1 sonrisa amplia
    mouth_open: float = 0.0
    mouth_width: float = 1.0
    tilt: float = 0.0  # inclinación de la cabeza (grados)
    blush: float = 0.0
    wink_l: float = 0.0
    wink_r: float = 0.0
    gaze_x: float = 0.0  # mirada: -1 izquierda … 1 derecha
    gaze_y: float = 0.0  # -1 arriba … 1 abajo

    def mix(self, other: "Pose", t: float) -> "Pose":
        return Pose(**{f.name: getattr(self, f.name) + (getattr(other, f.name) - getattr(self, f.name)) * t
                       for f in fields(Pose)})


@dataclass(frozen=True)
class Expression:
    key: str
    name: str  # como se llama en la app
    pose: Pose
    gems: str = "idle"  # idle | listen | think | work | happy | sad | off | flash
    fx: str = ""  # dots | question | sparkles | sweat | zzz | lock
    look: tuple[float, float] | None = None  # mirada fija de la expresión (None: sigue al cursor)
    motion: str = ""  # sway | hop | shake | nod | breathe-slow
    talk: bool = False


EXPRESSIONS: dict[str, Expression] = {e.key: e for e in (
    Expression("neutral", "Atenta", Pose()),
    Expression("hello", "Hola", Pose(eye_lower=-0.55, smile=0.9, mouth_open=0.18, brow_l=-2, brow_r=-2, tilt=-4),
               gems="happy", motion="hop"),
    Expression("listening", "Escuchando", Pose(eye_open=1.1, brow_l=-1.5, brow_r=-1.5, smile=0.35, tilt=3),
               gems="listen", look=(0.0, 0.6)),
    Expression("thinking", "Pensando", Pose(eye_open=0.85, brow_l=-3.2, brow_r=0.6, brow_tilt=-0.3, smile=0.0,
                                            mouth_open=0.3, mouth_width=0.55, tilt=-3),
               gems="think", fx="dots", look=(-0.7, -0.8), motion="sway"),
    Expression("working", "Trabajando", Pose(eye_open=0.72, eye_lower=1.25, brow_l=1, brow_r=1, brow_tilt=1,
                                             smile=0.15, mouth_width=0.8),
               gems="work", look=(0.2, 0.3)),
    Expression("talking", "Respondiendo", Pose(eye_lower=0.9, brow_l=-1, brow_r=-1, smile=0.45), gems="idle",
               look=(0.0, 0.0), talk=True),
    Expression("happy", "Contenta", Pose(eye_lower=-0.7, smile=1.0, mouth_open=0.35, brow_l=-2.5, brow_r=-2.5,
                                         blush=1.0), gems="happy", fx="sparkles", motion="hop"),
    Expression("curious", "Pregunta", Pose(eye_open=1.15, brow_l=-4, brow_r=-1, smile=0.2, mouth_open=0.1,
                                           mouth_width=0.75, tilt=9), gems="listen", fx="question", look=(0.15, 0.0)),
    Expression("worried", "Problema", Pose(eye_open=1.05, brow_l=-1, brow_r=-1, brow_tilt=-1.4, smile=-0.6,
                                           mouth_open=0.05), gems="sad", fx="sweat", look=(0.0, 0.25)),
    Expression("surprised", "Sorpresa", Pose(eye_open=1.35, eye_lower=1.2, eye_wide=1.1, brow_l=-4.5, brow_r=-4.5,
                                             smile=0.0, mouth_open=0.7, mouth_width=0.5), gems="flash",
               look=(0.0, 0.0)),
    Expression("wink", "Guiño", Pose(wink_r=1.0, smile=0.8, mouth_open=0.12, brow_l=-1, brow_r=0.5, tilt=-3),
               gems="happy", fx="lock", look=(0.0, 0.0)),
    Expression("sleepy", "Dormida", Pose(eye_open=0.16, brow_l=1.5, brow_r=1.5, smile=0.1, mouth_open=0.12,
                                         mouth_width=0.6, tilt=6), gems="off", fx="zzz", look=(0.0, 0.5),
               motion="breathe-slow"),
    Expression("no", "Esto no", Pose(eye_open=0.95, brow_l=0.5, brow_r=0.5, brow_tilt=0.4, smile=-0.15),
               gems="idle", motion="shake", look=(0.0, 0.0)),
)}


@dataclass
class Frame:
    """Lo que cambia de un instante a otro, además de la pose."""

    detail: float = 1.0  # 1 = todo el detalle; 0 = versión para tamaños pequeños (se calcula al pintar)

    pose: Pose = field(default_factory=Pose)
    blink: float = 0.0  # 0 abiertos … 1 cerrados
    gems: list[tuple[str, float]] = field(default_factory=lambda: [(GOLD_LIGHT, 0.0)] * 6)
    fx: str = ""
    fx_phase: float = 0.0  # 0…1, para animar el efecto
    fx_alpha: float = 0.0
    bob: float = 0.0  # desplazamiento vertical (unidades de la caja de 200)
    squash: float = 0.0  # >0 aplasta, <0 estira
    sheen: float = -1.0  # brillo que cruza el oro (-1 = ninguno; 0…1 posición)


# --- geometría (caja de 200 × 200) --------------------------------------------------------------

FACE = (  # contorno de la cara bajo la corona
    (40.0, 62.0), ((161.0, 98.0), (156.0, 128.0), (141.0, 150.0)),
    ((128.0, 168.0), (114.0, 178.0), (100.0, 179.0)),
)
GEM_X = tuple(46.0 + i * 21.6 for i in range(6))
GEM_Y = 48.0
BADGE_SCALE = 0.96  # la máscara dentro del disco del bubble (retrato en un medallón)


def face_path() -> QPainterPath:
    path = QPainterPath(QPointF(40, 62))
    path.lineTo(160, 62)
    path.cubicTo(161, 98, 156, 128, 141, 150)
    path.cubicTo(128, 168, 114, 178, 100, 179)
    path.cubicTo(86, 178, 72, 168, 59, 150)
    path.cubicTo(44, 128, 39, 98, 40, 62)
    path.closeSubpath()
    return path


def _quad(path: QPainterPath, ctrl: tuple[float, float], end: tuple[float, float]) -> None:
    path.quadTo(QPointF(*ctrl), QPointF(*end))


def eye_shape(cx: float, cy: float, side: int, pose: Pose, blink: float, wink: float) -> tuple[QPainterPath, dict]:
    """Abertura del ojo (almendra inclinada como en la máscara). side: -1 izquierdo, 1 derecho."""
    w = 15.0 * pose.eye_wide
    lo = 4.2 * pose.eye_lower
    up = 8.5 * pose.eye_open
    closed = max(blink, wink)
    up = up + (-lo * 0.85 - up) * closed  # el párpado baja hasta casi tocar el de abajo
    outer = (cx + side * w, cy - 1.5)
    inner = (cx - side * w, cy + 1.0)
    upper_ctrl = (cx, cy - 2 * up)
    lower_ctrl = (cx, cy + 2 * lo)
    path = QPainterPath(QPointF(*outer))
    _quad(path, upper_ctrl, inner)
    _quad(path, lower_ctrl, outer)
    path.closeSubpath()
    return path, {"outer": outer, "inner": inner, "upper": upper_ctrl, "lower": lower_ctrl, "up": up, "lo": lo, "w": w}


def brow_path(side: int, pose: Pose) -> QPainterPath:
    lift = pose.brow_l if side < 0 else pose.brow_r
    tilt = pose.brow_tilt
    outer = (100 + side * 48.0, 84.0 + lift - tilt * 1.0)
    mid = (100 + side * 29.0, 76.0 + lift)
    inner = (100 + side * 9.0, 80.0 + lift + tilt * 3.2)
    ctrl = (2 * mid[0] - (outer[0] + inner[0]) / 2, 2 * mid[1] - (outer[1] + inner[1]) / 2)
    path = QPainterPath(QPointF(*outer))
    _quad(path, ctrl, inner)
    return path


def mouth_geometry(pose: Pose) -> dict[str, QPainterPath]:
    cy = 148.0
    hw = 13.0 * pose.mouth_width
    corner_y = cy - 4.0 * pose.smile
    left, right = QPointF(100 - hw, corner_y), QPointF(100 + hw, corner_y)
    up_ctrl = QPointF(100, cy + 6.0 * pose.smile - 5.0 * pose.mouth_open)
    lo_ctrl = QPointF(100, cy + 6.0 * pose.smile + 12.0 * pose.mouth_open)

    opening = QPainterPath(left)
    opening.quadTo(up_ctrl, right)
    opening.quadTo(lo_ctrl, left)
    opening.closeSubpath()

    upper = QPainterPath(left)  # labio superior con arco de cupido
    upper.cubicTo(QPointF(left.x() + 5, corner_y - 5.5), QPointF(96, 140.5), QPointF(100, 142.2))
    upper.cubicTo(QPointF(104, 140.5), QPointF(right.x() - 5, corner_y - 5.5), right)
    upper.quadTo(up_ctrl, left)
    upper.closeSubpath()

    bottom = corner_y + 8.0 + 9.0 * pose.mouth_open
    lower = QPainterPath(left)
    lower.quadTo(lo_ctrl, right)
    lower.cubicTo(QPointF(right.x() - 3, bottom + 2), QPointF(left.x() + 3, bottom + 2), left)
    lower.closeSubpath()

    crease = QPainterPath(left)
    crease.quadTo(up_ctrl, right)
    return {"opening": opening, "upper": upper, "lower": lower, "crease": crease}


# --- pintura ----------------------------------------------------------------------------------------

def _gold_linear(x1: float, y1: float, x2: float, y2: float) -> QLinearGradient:
    gradient = QLinearGradient(x1, y1, x2, y2)
    for stop, color in ((0.0, GOLD_LIGHT), (0.3, GOLD), (0.55, GOLD_DEEP), (0.78, "#EBCB78"), (1.0, "#B5862F")):
        gradient.setColorAt(stop, QColor(color))
    return gradient


def _alpha(color: str, alpha: float) -> QColor:
    c = QColor(color)
    c.setAlphaF(max(0.0, min(1.0, alpha)))
    return c


def _pen(color, width: float, cap=Qt.PenCapStyle.RoundCap) -> QPen:
    pen = QPen(color if isinstance(color, (QColor, QBrush, QLinearGradient)) else QColor(color), width)
    pen.setCapStyle(cap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def paint_badge_disc(p: QPainter, glow: float = 0.0) -> None:
    """El fondo del bubble: disco oscuro con aro dorado (caja de 200)."""
    disc = QRadialGradient(QPointF(86, 72), 132)
    disc.setColorAt(0.0, QColor("#243049"))
    disc.setColorAt(0.55, QColor("#111827"))
    disc.setColorAt(1.0, QColor("#05070D"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(disc)
    p.drawEllipse(QPointF(100, 100), 100, 100)
    ring = QConicalGradient(QPointF(100, 100), 120)
    for stop, color in ((0.0, "#FCEABB"), (0.18, "#B5862F"), (0.42, "#6E4B16"), (0.6, "#EBCB78"), (0.82, "#8E6522"),
                        (1.0, "#FCEABB")):
        ring.setColorAt(stop, QColor(color))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(QPen(QBrush(ring), 4.2 + glow * 1.5))
    p.drawEllipse(QPointF(100, 100), 97.5, 97.5)
    if glow > 0:
        halo = QRadialGradient(QPointF(100, 100), 100)
        halo.setColorAt(0.78, _alpha("#FCD679", 0.0))
        halo.setColorAt(0.96, _alpha("#FCD679", 0.35 * glow))
        halo.setColorAt(1.0, _alpha("#FCD679", 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(halo)
        p.drawEllipse(QPointF(100, 100), 100, 100)


def _paint_crown(p: QPainter, frame: Frame) -> None:
    band = QRectF(30, 34, 140, 28)
    p.setPen(_pen("#3B2708", 1.2))
    p.setBrush(_gold_linear(30, 34, 30, 62))
    p.drawRoundedRect(band, 5, 5)
    inner = QRectF(35, 39, 130, 18)
    p.setPen(_pen("#2B1B06", 1.0))
    p.setBrush(QColor("#1D1307"))
    p.drawRoundedRect(inner, 3, 3)
    if frame.detail > 0.5:  # remaches entre gemas, como en el logo (a tamaño pequeño solo ensucian)
        p.setPen(_pen("#C99A45", 0.9))
        p.setBrush(QColor("#2A1B08"))
        for i in range(5):
            x = (GEM_X[i] + GEM_X[i + 1]) / 2
            for y in (43.5, 52.5):
                p.drawEllipse(QPointF(x, y), 1.7, 1.7)
    for i, x in enumerate(GEM_X):
        color, glow = frame.gems[i] if i < len(frame.gems) else (GOLD_LIGHT, 0.0)
        if glow > 0.01:
            halo = QRadialGradient(QPointF(x, GEM_Y), 15)
            halo.setColorAt(0.0, _alpha(color, 0.95 * glow))
            halo.setColorAt(0.55, _alpha(color, 0.45 * glow))
            halo.setColorAt(1.0, _alpha(color, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(halo)
            p.drawEllipse(QPointF(x, GEM_Y), 15, 15)
        _paint_gem(p, x, GEM_Y, 7.2, color, glow, frame.detail)


def _paint_gem(p: QPainter, x: float, y: float, r: float, color: str, glow: float, detail: float = 1.0) -> None:
    octagon = QPainterPath()
    for k in range(8):
        angle = math.pi / 8 + k * math.pi / 4
        point = QPointF(x + r * math.cos(angle), y + r * math.sin(angle))
        octagon.moveTo(point) if k == 0 else octagon.lineTo(point)
    octagon.closeSubpath()
    body = QRadialGradient(QPointF(x - r * 0.35, y - r * 0.4), r * 1.6)
    body.setColorAt(0.0, QColor("#FFF6D8"))
    body.setColorAt(0.45, QColor("#D9A94E"))
    body.setColorAt(1.0, QColor("#7A5216"))
    p.setPen(_pen("#3A2608", 1.0))
    p.setBrush(body)
    p.drawPath(octagon)
    if glow > 0.01:
        lit = QRadialGradient(QPointF(x, y), r)
        lit.setColorAt(0.0, _alpha("#FFFFFF", 0.9 * glow))
        lit.setColorAt(0.45, _alpha(color, 0.95 * glow))
        lit.setColorAt(1.0, _alpha(color, 0.75 * glow))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(lit)
        p.drawPath(octagon)
    if detail > 0.5:  # facetas
        p.setPen(_pen(_alpha("#5A3B0E", 0.75), 0.7))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(x, y), r * 0.48, r * 0.48)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_alpha("#FFFFFF", 0.55 + 0.4 * glow))
    p.drawEllipse(QPointF(x - r * 0.32, y - r * 0.36), r * 0.2, r * 0.2)


def _paint_face(p: QPainter) -> QPainterPath:
    face = face_path()
    skin = QRadialGradient(QPointF(80, 92), 118)
    skin.setColorAt(0.0, QColor("#FBE8B4"))
    skin.setColorAt(0.33, QColor("#E3B95E"))
    skin.setColorAt(0.68, QColor("#B7882E"))
    skin.setColorAt(1.0, QColor("#6A4714"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(skin)
    p.drawPath(face)
    # Sombra bajo la corona y borde con luz.
    p.save()
    p.setClipPath(face)
    shade = QLinearGradient(0, 62, 0, 74)
    shade.setColorAt(0.0, _alpha("#2A1A05", 0.6))
    shade.setColorAt(1.0, _alpha("#2A1A05", 0.0))
    p.setBrush(shade)
    p.drawRect(QRectF(30, 62, 140, 14))
    p.restore()
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_pen(_gold_linear(40, 62, 160, 179), 2.0))
    p.drawPath(face)
    return face


def _paint_features(p: QPainter, frame: Frame) -> None:
    pose = frame.pose
    bold = 1.0 + 0.7 * (1.0 - frame.detail)  # trazos más gruesos cuanto más pequeña se ve
    # Marcas en las mejillas (escarificación del logo) y rubor.
    if pose.blush > 0.01:
        for x in (60.0, 140.0):
            glow = QRadialGradient(QPointF(x, 122), 16)
            glow.setColorAt(0.0, _alpha(ROSE, 0.55 * pose.blush))
            glow.setColorAt(1.0, _alpha(ROSE, 0.0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(glow)
            p.drawEllipse(QPointF(x, 122), 16, 16)
    if frame.detail > 0.35:
        p.setPen(_pen(LINE, 1.25))
        p.setBrush(_alpha("#9C6D24", 0.55))
        for side in (-1, 1):
            for dy, dx in ((0, 0), (8, -1), (16, 1)):
                cx = 100 + side * (43 + dx)
                p.save()
                p.translate(cx, 112 + dy)
                p.rotate(-10 * side)
                p.drawRoundedRect(QRectF(-4.5, -1.9, 9, 3.8), 1.9, 1.9)
                p.restore()

    # Nariz: puente largo con luz y aletas.
    p.setPen(_pen(_alpha("#FFF1C9", 0.75), 2.2))
    p.drawLine(QPointF(99.5, 76), QPointF(99.5, 118))
    p.setPen(_pen(_alpha("#7A5218", 0.55), 1.8))
    p.drawLine(QPointF(104.5, 80), QPointF(104.0, 119))
    nose = QPainterPath(QPointF(90, 121.5))
    nose.quadTo(QPointF(94, 128.5), QPointF(100, 127.5))
    nose.quadTo(QPointF(106, 128.5), QPointF(110, 121.5))
    p.setPen(_pen(LINE, 1.9 * bold))
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.drawPath(nose)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#2A1A08"))
    for x in (94.5, 105.5):
        p.drawEllipse(QPointF(x, 124.2), 2.1, 1.3)

    # Cejas: surco oscuro con un filo de luz encima.
    for side in (-1, 1):
        brow = brow_path(side, pose)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_pen(_alpha("#FFF1C9", 0.55), 1.4))
        p.drawPath(brow.translated(0, -2.0))
        p.setPen(_pen(LINE, 2.6 * bold))
        p.drawPath(brow)

    # Ojos.
    for side, cx in ((-1, 74.0), (1, 126.0)):
        wink = pose.wink_l if side < 0 else pose.wink_r
        path, g = eye_shape(cx, 99.0, side, pose, frame.blink, wink)
        p.setPen(Qt.PenStyle.NoPen)
        socket = QLinearGradient(0, 99 - g["up"], 0, 99 + g["lo"])
        socket.setColorAt(0.0, QColor("#0B0603"))
        socket.setColorAt(1.0, QColor("#2A1B0A"))
        p.setBrush(socket)
        p.drawPath(path)
        if g["up"] + g["lo"] > 2.0:  # iris y brillo, dentro del ojo
            p.save()
            p.setClipPath(path)
            ix = cx + pose.gaze_x * g["w"] * 0.4
            iy = 99.5 + pose.gaze_y * 2.8
            r = 5.2 * min(1.15, pose.eye_wide)
            iris = QRadialGradient(QPointF(ix, iy), r)
            iris.setColorAt(0.0, QColor("#6B4A15"))
            iris.setColorAt(0.75, QColor("#3B2708"))
            iris.setColorAt(1.0, QColor("#B98A35"))
            p.setBrush(iris)
            p.drawEllipse(QPointF(ix, iy), r, r)
            p.setBrush(QColor("#050302"))
            p.drawEllipse(QPointF(ix, iy), r * 0.48, r * 0.48)
            p.setBrush(QColor("#FFF6DA"))
            p.drawEllipse(QPointF(ix - r * 0.33, iy - r * 0.36), r * 0.26, r * 0.26)
            p.setBrush(_alpha("#FFF6DA", 0.6))
            p.drawEllipse(QPointF(ix + r * 0.32, iy + r * 0.28), r * 0.12, r * 0.12)
            p.restore()
        # Párpado superior tallado y línea de abajo.
        lid = QPainterPath(QPointF(*g["outer"]))
        _quad(lid, g["upper"], g["inner"])
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(_pen("#4A300C", 2.4 * bold))
        p.drawPath(lid)
        p.setPen(_pen(_alpha("#FFF1C9", 0.5), 1.1))
        p.drawPath(lid.translated(0, -2.4))
        low = QPainterPath(QPointF(*g["inner"]))
        _quad(low, g["lower"], g["outer"])
        p.setPen(_pen(_alpha("#5C3F12", 0.85), 1.2))
        p.drawPath(low)

    # Boca.
    mouth = mouth_geometry(pose)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor("#1A0E05"))
    p.drawPath(mouth["opening"])
    lips = QLinearGradient(0, 138, 0, 166)
    lips.setColorAt(0.0, QColor("#E0AE5A"))
    lips.setColorAt(0.5, QColor("#B57E2C"))
    lips.setColorAt(1.0, QColor("#7E5418"))
    p.setBrush(lips)
    p.setPen(_pen(_alpha(LINE, 0.9), 1.1))
    p.drawPath(mouth["upper"])
    p.drawPath(mouth["lower"])
    p.setBrush(Qt.BrushStyle.NoBrush)
    p.setPen(_pen(_alpha("#2E1C07", 0.95), 1.7 * bold))
    p.drawPath(mouth["crease"])
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_alpha("#FFEDB8", 0.55))
    bottom = 148.0 - 4.0 * pose.smile + 5.5 + 7.0 * pose.mouth_open
    p.drawEllipse(QPointF(100, bottom), 5.5 * pose.mouth_width, 1.6)
    # Luz en el mentón.
    p.setBrush(_alpha("#FFF1C9", 0.28))
    p.drawEllipse(QPointF(100, 168), 9, 3)


def _paint_sheen(p: QPainter, face: QPainterPath, position: float) -> None:
    """Un destello que cruza el oro en diagonal (al pasar el ratón o al terminar algo)."""
    if not 0.0 <= position <= 1.0:
        return
    x = -40 + position * 280
    sheen = QLinearGradient(x - 30, 0, x + 30, 60)
    sheen.setColorAt(0.0, _alpha("#FFFFFF", 0.0))
    sheen.setColorAt(0.5, _alpha("#FFFFFF", 0.38))
    sheen.setColorAt(1.0, _alpha("#FFFFFF", 0.0))
    p.save()
    clip = QPainterPath(face)
    clip.addRoundedRect(QRectF(30, 34, 140, 28), 5, 5)
    p.setClipPath(clip)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(sheen)
    p.drawRect(QRectF(0, 0, 200, 200))
    p.restore()


def _paint_fx(p: QPainter, frame: Frame, badge: bool) -> None:
    kind, phase, alpha = frame.fx, frame.fx_phase, frame.fx_alpha
    if not kind or alpha <= 0.01:
        return
    p.save()
    p.setOpacity(alpha)
    if kind == "sweat":
        drop = QPainterPath(QPointF(52, 86))
        drop.cubicTo(QPointF(46, 96), QPointF(47, 103), QPointF(52, 103))
        drop.cubicTo(QPointF(57, 103), QPointF(58, 96), QPointF(52, 86))
        y = 6 * phase
        p.translate(0, y)
        grad = QLinearGradient(0, 86, 0, 103)
        grad.setColorAt(0.0, QColor("#E0F2FE"))
        grad.setColorAt(1.0, QColor("#7DD3FC"))
        p.setPen(_pen("#0C4A6E", 1.0))
        p.setBrush(grad)
        p.drawPath(drop)
    elif kind == "sparkles":
        for i, (x, y, s) in enumerate(((30, 70, 9), (172, 88, 7), (160, 160, 6), (38, 150, 5))):
            twinkle = 0.5 + 0.5 * math.sin((phase * 2 + i * 0.27) * math.pi * 2)
            size = s * (0.6 + 0.6 * twinkle)
            _sparkle(p, x, y, size, "#E2B04A", 0.5 + 0.5 * twinkle)
            _sparkle(p, x, y, size * 0.55, "#FFF8DD", 0.5 + 0.5 * twinkle)
    else:
        # Insignia arriba a la derecha: «…», «?», «z», candado.
        cx, cy, r = (170.0, 30.0, 24.0) if badge else (176.0, 28.0, 21.0)
        p.setPen(_pen("#FFFFFF", 3.0))
        p.setBrush(QColor(INDIGO if kind != "zzz" else "#334155"))
        p.drawEllipse(QPointF(cx, cy), r, r)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#FFFFFF"))
        if kind == "dots":
            for i in range(3):
                bounce = math.sin((phase - i * 0.18) * math.pi * 2)
                p.drawEllipse(QPointF(cx - 9 + i * 9, cy - 1.5 * max(0.0, bounce)), 3.2, 3.2)
        elif kind in ("question", "zzz"):
            font = QFont("Segoe UI")
            font.setPixelSize(30 if kind == "question" else 24)
            font.setWeight(QFont.Weight.Bold)
            p.setFont(font)
            p.setPen(QColor("#FFFFFF"))
            text = "?" if kind == "question" else "z"
            dy = -2 * math.sin(phase * math.pi * 2)
            p.drawText(QRectF(cx - r, cy - r + dy, 2 * r, 2 * r), Qt.AlignmentFlag.AlignCenter, text)
        elif kind == "lock":
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(_pen("#FFFFFF", 3.0))
            p.drawArc(QRectF(cx - 6, cy - 12, 12, 13), 0, 180 * 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#FFFFFF"))
            p.drawRoundedRect(QRectF(cx - 9, cy - 4, 18, 14), 3, 3)
    p.restore()


def _sparkle(p: QPainter, x: float, y: float, size: float, color: str, alpha: float) -> None:
    star = QPainterPath(QPointF(x, y - size))
    for angle in range(1, 9):
        radius = size if angle % 2 == 0 else size * 0.28
        a = -math.pi / 2 + angle * math.pi / 4
        star.lineTo(QPointF(x + radius * math.cos(a), y + radius * math.sin(a)))
    star.closeSubpath()
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(_alpha(color, alpha))
    p.drawPath(star)


def paint_mascot(p: QPainter, rect: QRectF, frame: Frame, badge: bool = False, glow: float = 0.0) -> None:
    """Dibuja la máscara en `rect` (cuadrado). Con `badge`, dentro del disco oscuro del bubble."""
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    side = min(rect.width(), rect.height())
    shown = side * (BADGE_SCALE if badge else 1.0)  # tamaño al que se ve la máscara
    frame.detail = max(0.0, min(1.0, (shown - 40.0) / 80.0))
    p.translate(rect.x() + (rect.width() - side) / 2, rect.y() + (rect.height() - side) / 2)
    p.scale(side / 200.0, side / 200.0)
    if badge:
        paint_badge_disc(p, glow)
        p.save()
        p.setClipPath(_circle(100, 100, 95.5))
        p.translate(100, 108)
        p.scale(BADGE_SCALE, BADGE_SCALE)
        p.translate(-100, -106)
    pose = frame.pose
    head = QTransform()
    head.translate(100, 179 + frame.bob)
    head.rotate(pose.tilt)
    head.scale(1 + frame.squash * 0.6, 1 - frame.squash)
    head.translate(-100, -179)
    p.save()
    p.setTransform(head, True)
    face = _paint_face(p)
    _paint_crown(p, frame)
    _paint_features(p, frame)
    _paint_sheen(p, face, frame.sheen)
    if frame.fx == "sweat" or frame.fx == "sparkles":
        _paint_fx(p, frame, badge)
    p.restore()
    if badge:
        p.restore()
    if frame.fx not in ("sweat", "sparkles"):
        _paint_fx(p, frame, badge)
    p.restore()


def _circle(cx: float, cy: float, r: float) -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QPointF(cx, cy), r, r)
    return path


def still_frame(key: str, phase: float = 0.35) -> Frame:
    """Un fotograma fijo de una expresión (para iconos, capturas y pruebas)."""
    expression = EXPRESSIONS.get(key, EXPRESSIONS["neutral"])
    pose = replace(expression.pose)
    if expression.look is not None:
        pose.gaze_x, pose.gaze_y = expression.look
    if expression.talk:
        pose.mouth_open = 0.45
    return Frame(pose=pose, gems=gem_lights(expression.gems, phase), fx=expression.fx, fx_phase=phase,
                 fx_alpha=1.0 if expression.fx else 0.0)


def gem_lights_still(mode: str) -> list[tuple[str, float]]:
    """Las mismas luces, sin movimiento (menos animación, o una espera larga)."""
    if mode in ("think", "work", "listen"):
        return [(INDIGO_GLOW, {"think": 0.6, "work": 0.7, "listen": 0.35}[mode])] * 6
    if mode == "happy":
        return [(GOLD_LIGHT, 0.8)] * 6
    return gem_lights(mode, 0.0)


def gem_lights(mode: str, t: float) -> list[tuple[str, float]]:
    """Luz de cada gema de la corona según el estado (t en segundos o fase)."""
    if mode == "think":  # una luz índigo que va y viene, como un indicador de carga
        pos = 2.5 + 2.5 * math.sin(t * 2.6)
        return [(INDIGO_GLOW, max(0.0, 1 - abs(i - pos) * 0.75)) for i in range(6)]
    if mode == "work":  # gira de izquierda a derecha sin parar
        pos = (t * 7.0) % 6
        return [(INDIGO_GLOW, max(0.0, 1 - ((pos - i) % 6) * 0.38)) for i in range(6)]
    if mode == "listen":
        return [(INDIGO_GLOW, 0.3 + 0.2 * math.sin(t * 3.0 + i * 0.4)) for i in range(6)]
    if mode == "happy":
        return [(GOLD_LIGHT, 0.55 + 0.45 * math.sin(t * 5.0 + i * 1.1)) for i in range(6)]
    if mode == "sad":
        return [(AMBER, 0.35 if i in (2, 3) else 0.1) for i in range(6)]
    if mode == "flash":
        return [("#FFFFFF", 0.9) for _ in range(6)]
    if mode == "off":
        return [(GOLD_LIGHT, 0.0) for _ in range(6)]
    return [(GOLD_LIGHT, 0.12) for _ in range(6)]


# --- animación ----------------------------------------------------------------------------------

TRANSITION = 0.11  # s: constante de tiempo entre expresiones (~0.3 s hasta llegar)
GAZE_TIME = 0.06  # s: la mirada es más rápida (como un ojo de verdad)
GEM_TIME = 0.08
BLINK = 0.30  # s que dura un parpadeo
ACTIVE_MS = 33  # ~30 fotogramas por segundo mientras algo se mueve (transiciones, gestos, la corona trabajando)
AMBIENT_MS = 100  # ~10 por segundo para lo suave de fondo (respirar dormida, gemas que laten, el «?»)
IDLE_MS = 160  # en reposo solo se mira si toca parpadear o mirar, o si se movió el cursor
AMBIENT_LIMIT = 20.0  # s: después, lo de fondo se queda quieto (esperar horas no gasta nada)
MOTIONS = {"hop": 0.5, "shake": 0.65, "nod": 0.45}
SHEEN = 0.9
ACTIVE_GEMS = {"think", "work", "happy"}  # mientras trabaja o en reacciones breves
AMBIENT_GEMS = {"listen"}
ACTIVE_FX = {"dots", "sparkles", "sweat"}
AMBIENT_FX = {"question", "zzz"}
MOVING_FX = ACTIVE_FX | AMBIENT_FX
STILL_PHASE = 0.25  # posición fija de los efectos cuando no se animan


class Mascot(QObject):
    """El estado vivo de la máscara: una expresión base, reacciones breves encima y los gestos.

    Es una sola para toda la app: el bubble y la cabecera del chat muestran la misma cara. Solo anima mientras
    alguna de sus vistas se ve (con la ventana oculta o minimizada no gasta nada).
    """

    changed = Signal()

    def __init__(self, parent: QObject | None = None, reduced_motion: bool | None = None, clock=time.monotonic,
                 rng: random.Random | None = None) -> None:
        super().__init__(parent)
        self.clock = clock
        self.rng = rng or random.Random()
        self.reduced = reduced_motion if reduced_motion is not None else _reduced_motion()
        now = clock()
        self.base = "neutral"
        self._base_since = now
        self._transient: tuple[str, float] | None = None
        self.pose = Pose()
        self.frame = Frame()
        self._gems: list[tuple[str, float]] = [(GOLD_LIGHT, 0.12)] * 6
        self._last = now
        self._start = now
        self._blink_start: float | None = None
        self._next_blink = now + 1.6
        self._motion: tuple[str, float] | None = None
        self._talk_until = 0.0
        self._sheen_start: float | None = None
        self._fx_kind = ""
        self._fx_alpha = 0.0
        self._cursor: tuple[float, float] | None = None
        self._cursor_at = -10.0
        self._glance = (0.0, 0.0)
        self._glance_until = 0.0
        self._next_glance = now + 3.0
        self._signature: tuple | None = None
        self._views: list[QWidget] = []
        self.gaze_source = None  # función que devuelve (x, y) del cursor respecto a la vista, o None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)

    # --- qué cara pone --------------------------------------------------------------------------
    @property
    def expression(self) -> str:
        if self._transient and self._transient[1] > self.clock():
            return self._transient[0]
        return self.base

    def set_base(self, key: str) -> None:
        """La expresión de fondo (Atenta, Escuchando, Pensando, Trabajando, Dormida…)."""
        if key not in EXPRESSIONS or key == self.base:
            return
        self.base = key
        self._base_since = self.clock()
        self._wake()

    def react(self, key: str, seconds: float) -> None:
        """Una reacción breve encima de la base (Contenta 1.5 s, Sorpresa 0.8 s…)."""
        if key not in EXPRESSIONS:
            return
        self._transient = (key, self.clock() + seconds)
        motion = EXPRESSIONS[key].motion
        if motion in MOTIONS:
            self.gesture(motion)
        self._wake()

    def calm(self) -> None:
        """Quita la reacción en curso y vuelve a la base."""
        self._transient = None
        self._wake()

    def talk(self, seconds: float) -> None:
        """Mueve la boca mientras «responde» (la duración depende del largo de la respuesta)."""
        self._talk_until = self.clock() + seconds
        self.react("talking", seconds)

    def gesture(self, kind: str) -> None:
        if kind in MOTIONS and not self.reduced:
            self._motion = (kind, self.clock())
            self._wake()

    def shine(self) -> None:
        """Un destello que cruza el oro."""
        if not self.reduced:
            self._sheen_start = self.clock()
            self._wake()

    # --- vistas ---------------------------------------------------------------------------------
    def attach(self, view: QWidget) -> None:
        if view not in self._views:
            self._views.append(view)
        self._wake()

    def detach(self, view: QWidget) -> None:
        if view in self._views:
            self._views.remove(view)
        if not self._views:
            self._timer.stop()

    def _visible(self) -> bool:
        return any(v.isVisible() and not v.window().isMinimized() for v in self._views)

    def _wake(self) -> None:
        if self._views and (not self._timer.isActive() or self._timer.interval() != ACTIVE_MS):
            self._timer.start(ACTIVE_MS)

    # --- un instante --------------------------------------------------------------------------
    def _tick(self) -> None:
        if not self._visible():
            self._timer.start(1000)  # oculta o minimizada: casi no gasta nada
            return
        level = self.step()
        signature = self._sign()
        if signature != self._signature:
            self._signature = signature
            self.changed.emit()
        wanted = (IDLE_MS, AMBIENT_MS, ACTIVE_MS)[level]
        if self._timer.interval() != wanted:
            self._timer.start(wanted)

    def step(self) -> int:
        """Calcula el fotograma actual. Devuelve cuánto se mueve: 2 (fluido), 1 (de fondo, suave) o 0 (quieta)."""
        now = self.clock()
        dt = max(0.0, min(0.1, now - self._last))
        self._last = now
        t = now - self._start
        key = self.expression
        expression = EXPRESSIONS[key]
        moving = False  # necesita ~30 fps
        ambient = False  # basta con ~10 fps
        # Lo de fondo solo se anima un rato (y nunca con «menos animación» en Windows).
        ambient_on = not self.reduced and (key != self.base or now - self._base_since < AMBIENT_LIMIT)

        # Hacia dónde mira: la expresión manda; si no, el cursor; si el cursor está quieto, vistazos sueltos.
        if self.gaze_source is not None:
            cursor = self.gaze_source()
            if cursor is not None and cursor != self._cursor:
                self._cursor, self._cursor_at = cursor, now
        if expression.look is not None:
            look = expression.look
        elif self._cursor is not None and now - self._cursor_at < 2.5:
            look = self._cursor
        else:
            if now >= self._next_glance:
                if self.rng.random() < 0.45:
                    self._glance = (self.rng.uniform(-0.7, 0.7), self.rng.uniform(-0.5, 0.4))
                    self._glance_until = now + self.rng.uniform(0.6, 1.3)
                self._next_glance = now + self.rng.uniform(2.5, 6.0)
            look = self._glance if now < self._glance_until else (0.0, 0.0)

        target = replace(expression.pose, gaze_x=look[0], gaze_y=look[1])
        k = 1.0 if self.reduced else 1.0 - math.exp(-dt / TRANSITION)
        kg = 1.0 if self.reduced else 1.0 - math.exp(-dt / GAZE_TIME)
        gaze = (self.pose.gaze_x, self.pose.gaze_y)
        self.pose = self.pose.mix(target, k)
        self.pose.gaze_x = gaze[0] + (target.gaze_x - gaze[0]) * kg
        self.pose.gaze_y = gaze[1] + (target.gaze_y - gaze[1]) * kg
        if _distance(self.pose, target) > 0.004:
            moving = True
        pose = replace(self.pose)

        # Parpadeo (no con los ojos ya cerrados o sonrientes).
        blink = 0.0
        if self._blink_start is None and now >= self._next_blink:
            if pose.eye_open > 0.4 and pose.eye_lower > 0:
                self._blink_start = now
            else:
                self._next_blink = now + 1.0
        if self._blink_start is not None:
            u = (now - self._blink_start) / BLINK
            if u >= 1:
                self._blink_start = None
                self._next_blink = now + (0.14 if self.rng.random() < 0.18 else self.rng.uniform(2.4, 5.6))
            else:
                blink = math.sin(math.pi * u) ** 0.7
                moving = True

        # Boca al hablar.
        if expression.talk or now < self._talk_until:
            flap = abs(math.sin(t * 10.5)) * (0.55 + 0.45 * math.sin(t * 3.3 + 1.0))
            pose.mouth_open = max(pose.mouth_open, 0.1 + 0.48 * flap)
            moving = True

        # Gestos y movimientos continuos.
        bob, squash = 0.0, 0.0
        if self._motion is not None:
            kind, start = self._motion
            u = (now - start) / MOTIONS[kind]
            if u >= 1:
                self._motion = None
            else:
                moving = True
                if kind == "hop":
                    bob = -11.0 * math.sin(math.pi * u)
                    squash = 0.05 * math.cos(2 * math.pi * u) * (1 - u * 0.4)
                elif kind == "shake":
                    pose.tilt += 8.0 * math.sin(u * 6 * math.pi) * (1 - u)
                elif kind == "nod":
                    bob = 4.0 * math.sin(2 * math.pi * u) * (1 - u)
        if expression.motion == "sway" and not self.reduced:
            pose.tilt += 2.4 * math.sin(t * 1.7)  # pensando (dura lo que tarda la IA)
            moving = True
        elif expression.motion == "breathe-slow" and ambient_on:
            squash += 0.018 * math.sin(t * 2 * math.pi / 5.5)
            bob += 1.2 * math.sin(t * 2 * math.pi / 5.5)
            ambient = True

        # Gemas de la corona: luz que carga o gira mientras trabaja; que late suave al escuchar; fija si no.
        mode = expression.gems
        if self.reduced or (mode in AMBIENT_GEMS and not ambient_on):
            targets = gem_lights_still(mode)
        else:
            targets = gem_lights(mode, t)
            if mode in ACTIVE_GEMS:
                moving = True
            elif mode in AMBIENT_GEMS:
                ambient = True
        kgem = 1.0 if self.reduced else 1.0 - math.exp(-dt / GEM_TIME)
        gems = []
        for (color, glow), (want_color, want_glow) in zip(self._gems, targets):
            if color != want_color:
                glow -= dt / 0.12
                if glow <= 0.02:
                    color, glow = want_color, 0.0
            else:
                glow += (want_glow - glow) * kgem
            gems.append((color, max(0.0, glow)))
        if any(abs(a[1] - b[1]) > 0.003 or a[0] != b[0] for a, b in zip(gems, self._gems)):
            moving = True
        self._gems = gems

        # Efecto (puntos, «?», gotita, destellos…): se funde al cambiar.
        kind = expression.fx
        if kind == "zzz" and key == self.base and now - self._base_since > 8:
            kind = ""  # la «z» solo al quedarse dormida; después, solo la cara
        if self._fx_kind != kind and self._fx_alpha > 0.03:
            self._fx_alpha = max(0.0, self._fx_alpha - dt / 0.15)
            moving = True
        else:
            if self._fx_kind != kind:
                self._fx_kind = kind
            goal = 1.0 if kind else 0.0
            if abs(self._fx_alpha - goal) > 0.01:
                self._fx_alpha += (goal - self._fx_alpha) * (1.0 if self.reduced else min(1.0, dt / 0.2 * 3))
                moving = True
            else:
                self._fx_alpha = goal
        phase = (t * 0.9) % 1.0
        if self._fx_kind in ACTIVE_FX and self._fx_alpha > 0.01 and not self.reduced:
            moving = True
        elif self._fx_kind in AMBIENT_FX and self._fx_alpha > 0.01 and ambient_on:
            ambient = True
        elif self._fx_kind:
            phase = STILL_PHASE

        sheen = -1.0
        if self._sheen_start is not None:
            u = (now - self._sheen_start) / SHEEN
            if u >= 1:
                self._sheen_start = None
            else:
                sheen = u
                moving = True

        if self._transient and self._transient[1] <= now:
            self._transient = None
            moving = True

        self.frame = Frame(pose=pose, blink=blink, gems=list(self._gems), fx=self._fx_kind,
                           fx_phase=phase, fx_alpha=self._fx_alpha, bob=bob, squash=squash, sheen=sheen)
        return 2 if moving else (1 if ambient else 0)

    def _sign(self) -> tuple:
        f = self.frame
        pose = tuple(round(getattr(f.pose, name.name), 3) for name in fields(Pose))
        gems = tuple((c, round(g, 3)) for c, g in f.gems)
        return (pose, round(f.blink, 3), gems, f.fx, round(f.fx_phase, 2) if f.fx in MOVING_FX else 0,
                round(f.fx_alpha, 2), round(f.bob, 2), round(f.squash, 3), round(f.sheen, 3))


def _distance(a: Pose, b: Pose) -> float:
    return max(abs(getattr(a, f.name) - getattr(b, f.name)) for f in fields(Pose))


def _reduced_motion() -> bool:
    from ..system.accessibility import reduced_motion

    return reduced_motion()


class MascotView(QWidget):
    """Muestra la máscara viva. Con `badge`, dentro del disco del bubble; si no, la máscara sola."""

    def __init__(self, mascot: Mascot, size: int, badge: bool = True, parent: QWidget | None = None,
                 follow_cursor: bool = True) -> None:
        super().__init__(parent)
        self.mascot = mascot
        self.badge = badge
        self.glow = 0.0
        self.follow_cursor = follow_cursor
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        mascot.changed.connect(self.update)

    def cursor_gaze(self) -> tuple[float, float] | None:
        """Hacia dónde está el cursor visto desde la máscara (-1…1), con un poco de zona muerta en el centro."""
        if not self.isVisible():
            return None
        center = self.mapToGlobal(self.rect().center())
        pos = QCursor.pos()
        dx, dy = pos.x() - center.x(), pos.y() - center.y()
        if abs(dx) < 6 and abs(dy) < 6:
            return (0.0, 0.0)
        return (round(max(-1.0, min(1.0, dx / 260.0)), 2), round(max(-1.0, min(1.0, dy / 200.0)), 2))

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if self.follow_cursor:
            self.mascot.gaze_source = self.cursor_gaze
        self.mascot.attach(self)

    def hideEvent(self, event) -> None:  # noqa: N802
        super().hideEvent(event)
        if self.mascot.gaze_source == self.cursor_gaze:
            self.mascot.gaze_source = None
        self.mascot.detach(self)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        paint_mascot(painter, QRectF(self.rect()), self.mascot.frame, badge=self.badge, glow=self.glow)
        painter.end()


def mascot_pixmap(key: str, size: int, badge: bool = True, ratio: float = 2.0) -> QPixmap:
    """Imagen fija de una expresión (avatares del chat, ventanas de permiso, capturas)."""
    pixmap = QPixmap(int(size * ratio), int(size * ratio))
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    paint_mascot(painter, QRectF(0, 0, size * ratio, size * ratio), still_frame(key), badge=badge)
    painter.end()
    pixmap.setDevicePixelRatio(ratio)
    return pixmap

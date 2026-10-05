"""Prepara la máscara del asistente a partir del logo original (assets/brand-logo.png).

La máscara del asistente es el logo tal cual: la app la dibuja encima y anima solo lo necesario (párpados, las
«pupilas» —las ventanas oscuras de los ojos—, el labio inferior, las luces de las gemas). Este script recorta el
emblema con fondo transparente y mide en él dónde están esos rasgos.

Salida: assets/mascot/emblem.png y assets/mascot/features.json (coordenadas en píxeles de emblem.png).
Si cambia el logo, se vuelve a ejecutar: python packaging/make_mascot_assets.py
"""

from __future__ import annotations

import json
import math
import os
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "packaging"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from labelbridge.app import _ensure_qt_plugin_path  # noqa: E402

_ensure_qt_plugin_path()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import make_icon  # noqa: E402

OUT = ROOT / "assets" / "mascot"
WIDTH = 720  # ancho guardado: nítido hasta ~360 px en pantallas 2x
GOLD = 110  # opacidad mínima para contar un píxel como oro

# Semillas y zonas (en el emblema de 1074 × 876 que recorta make_icon): de aquí se mide todo lo demás.
EYES = (
    {"seed": (250, 345), "center": (378, 344), "half": (74, 33)},  # izquierdo (globo dorado con dos ventanas)
    {"seed": (800, 330), "center": (714, 349), "half": (66, 31)},  # derecho
)
LOWER_LIP_SEED = (520, 715)
GEMS = ((143, 108), (300, 108), (455, 108), (610, 108), (768, 108), (925, 108))
CHEEKS = ((300, 565, 88), (770, 565, 88))


class Mask:
    def __init__(self, image: QImage) -> None:
        alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
        self.w, self.h = alpha.width(), alpha.height()
        stride = alpha.bytesPerLine()
        raw = bytes(alpha.constBits())
        self.gold = [[raw[y * stride + x] >= GOLD for x in range(self.w)] for y in range(self.h)]


def flood(grid, seed, inside) -> set[tuple[int, int]]:
    """Píxeles conectados (4 vecinos) a `seed` que cumplen `inside`."""
    seen = {seed}
    queue = deque([seed])
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if (nx, ny) not in seen and 0 <= nx < len(grid[0]) and 0 <= ny < len(grid) and inside(nx, ny):
                seen.add((nx, ny))
                queue.append((nx, ny))
    return seen


def fill_holes(region: set[tuple[int, int]]) -> set[tuple[int, int]]:
    xs = [x for x, _ in region]
    ys = [y for _, y in region]
    x0, x1, y0, y1 = min(xs) - 1, max(xs) + 1, min(ys) - 1, max(ys) + 1
    outside: set[tuple[int, int]] = set()
    queue = deque([(x0, y0)])
    outside.add((x0, y0))
    while queue:
        x, y = queue.popleft()
        for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if x0 <= nx <= x1 and y0 <= ny <= y1 and (nx, ny) not in outside and (nx, ny) not in region:
                outside.add((nx, ny))
                queue.append((nx, ny))
    return {(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1) if (x, y) not in outside}


def contour(region: set[tuple[int, int]]) -> list[tuple[float, float]]:
    """Borde exterior (trazado de Moore) de una región sin agujeros."""
    start = min(region, key=lambda p: (p[1], p[0]))
    neighbours = ((1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1))
    points = [start]
    current, direction = start, 6
    for _ in range(len(region) * 4):
        for turn in range(8):
            d = (direction + 6 + turn) % 8  # empieza girando a la izquierda
            dx, dy = neighbours[d]
            nxt = (current[0] + dx, current[1] + dy)
            if nxt in region:
                current, direction = nxt, d
                break
        if current == start:
            break
        points.append(current)
    return [(x + 0.5, y + 0.5) for x, y in points]


def simplify(points: list[tuple[float, float]], epsilon: float) -> list[tuple[float, float]]:
    """Ramer–Douglas–Peucker (para un polígono cerrado: se parte en dos mitades)."""
    def rdp(pts):
        if len(pts) < 3:
            return pts
        (x1, y1), (x2, y2) = pts[0], pts[-1]
        length = math.hypot(x2 - x1, y2 - y1) or 1e-9
        best, index = 0.0, 0
        for i in range(1, len(pts) - 1):
            x0, y0 = pts[i]
            dist = abs((y2 - y1) * x0 - (x2 - x1) * y0 + x2 * y1 - y2 * x1) / length
            if dist > best:
                best, index = dist, i
        if best <= epsilon:
            return [pts[0], pts[-1]]
        return rdp(pts[: index + 1])[:-1] + rdp(pts[index:])

    half = len(points) // 2
    return rdp(points[: half + 1])[:-1] + rdp(points[half:] + [points[0]])[:-1]


def main() -> None:
    QApplication.instance() or QApplication([sys.argv[0]])
    emblem = make_icon.load_emblem()
    mask = Mask(emblem)
    gold = mask.gold
    scale = WIDTH / mask.w

    def scaled(points):
        return [[round(x * scale, 1), round(y * scale, 1)] for x, y in points]

    eyes = []
    for eye in EYES:
        cx, cy = eye["center"]
        hw, hh = eye["half"]
        component = flood(gold, eye["seed"], lambda x, y: gold[y][x])
        solid = fill_holes(component)
        zone = {(x, y) for x, y in solid if ((x - cx) / hw) ** 2 + ((y - cy) / hh) ** 2 <= 1.0}
        # Las ventanas oscuras (las «pupilas»): lo que no es oro dentro de la zona.
        holes, seen = [], set()
        for point in zone:
            if point in seen or gold[point[1]][point[0]]:
                continue
            hole = flood(gold, point, lambda x, y: (x, y) in zone and not gold[y][x])
            seen |= hole
            tall = max(y for _, y in hole) - min(y for _, y in hole) + 1
            if len(hole) > 40 and tall >= hh * 0.6:  # las pupilas; no las rendijas finas del borde
                holes.append(scaled(simplify(contour(fill_holes(hole)), 1.0)))
        eyes.append({
            "zone": scaled(simplify(contour(zone), 1.2)),
            "windows": holes,
            "center": [round(cx * scale, 1), round(cy * scale, 1)],
            "half": [round(hw * scale, 1), round(hh * scale, 1)],
        })

    lip = fill_holes(flood(gold, LOWER_LIP_SEED, lambda x, y: gold[y][x] and y > 650))
    ys = [y for _, y in lip]
    lower_lip = {"outline": scaled(simplify(contour(lip), 1.2)), "top": round(min(ys) * scale, 1),
                 "bottom": round(max(ys) * scale, 1)}

    gems = []
    for gx, gy in GEMS:
        points = [(x, y) for x in range(gx - 60, gx + 61) for y in range(gy - 60, gy + 61)
                  if 0 <= x < mask.w and 0 <= y < mask.h and gold[y][x] and (x - gx) ** 2 + (y - gy) ** 2 <= 3600]
        mx = sum(x for x, _ in points) / len(points)
        my = sum(y for _, y in points) / len(points)
        gems.append([round(mx * scale, 1), round(my * scale, 1), round(52 * scale, 1)])

    # Contorno exterior de todo el emblema (para el destello: solo cruza el oro, no el fondo).
    body = flood(gold, (mask.w // 2, 60), lambda x, y: gold[y][x])  # la corona y la cara están unidas
    outline = scaled(simplify(contour(fill_holes(body)), 1.5))

    bottom = max(y for y in range(mask.h) if any(gold[y][x] for x in range(mask.w)))
    chin_x = [x for x in range(mask.w) if gold[bottom][x]]
    OUT.mkdir(parents=True, exist_ok=True)
    small = emblem.scaledToWidth(WIDTH, Qt.TransformationMode.SmoothTransformation)
    small.save(str(OUT / "emblem.png"))
    features = {
        "size": [small.width(), small.height()],
        "eyes": eyes,
        "lower_lip": lower_lip,
        "gems": gems,
        "cheeks": [[round(x * scale, 1), round(y * scale, 1), round(r * scale, 1)] for x, y, r in CHEEKS],
        "pivot": [round(sum(chin_x) / len(chin_x) * scale, 1), round(bottom * scale, 1)],
        "outline": outline,
        "source": "assets/brand-logo.png",
    }
    (OUT / "features.json").write_text(json.dumps(features, indent=1), encoding="utf-8")
    print("emblema", small.width(), "x", small.height(), "·", len(eyes[0]["windows"]), "+", len(eyes[1]["windows"]),
          "ventanas · labio", len(lower_lip["outline"]), "puntos")


if __name__ == "__main__":
    main()

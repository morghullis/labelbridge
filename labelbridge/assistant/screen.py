"""Control de la pantalla del asistente (Ajustes → Asistente → Control de la pantalla; solo Windows y con Claude).

El modelo pide acciones del toolset de computer use de Claude (captura, clic, escribir, teclas…) y cada una pasa por
aquí antes de tocar nada:
- Ver la pantalla (captura, zoom, dónde está el puntero, esperar) necesita el permiso de la tarea, que se pide una vez
  por conversación y explica lo que implica (lo que se ve se envía al proveedor de IA).
- Todo lo que cambia algo (clic, mover, arrastrar, desplazar, escribir, teclas) pide permiso CADA vez, mostrando la
  pantalla con el punto marcado o el texto exacto.
- Nunca sobre las ventanas de LabelBridge (compras, ajustes, permisos: para eso están sus herramientas, con sus propias
  confirmaciones), y el teclado tampoco mientras LabelBridge está al frente.
- Topes por conversación; «Detener» siempre a mano (en cada permiso y en la barra de arriba).
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable

from ..i18n import tr
from ..system import screen_input

log = logging.getLogger(__name__)

TOOLSET_NAME = "computer"
# El toolset de Claude Opus 5.5 / Sonnet 5.5 (sin beta). Fuera lo que no hace falta y es más arriesgado.
DISABLED_MEMBERS = ("hold_key", "left_mouse_down", "left_mouse_up", "middle_click")
TOOLSET = {"type": "computer_toolset_20260801", "configs": {name: {"enabled": False} for name in DISABLED_MEMBERS}}

LOOK = {"screenshot", "zoom", "cursor_position", "wait"}
POINTER = {"left_click", "right_click", "double_click", "triple_click", "mouse_move", "scroll", "left_click_drag"}
KEYBOARD = {"type", "key"}
NOT_EXECUTED = "Not executed: an earlier computer action in this turn failed."

MAX_ACTIONS = 80  # acciones que cambian algo, por conversación
MAX_LOOKS = 60  # capturas y zooms por conversación (cada una son ~1.500 tokens)
MAX_TEXT = 300  # caracteres por «escribir»
MAX_WAIT = 10  # segundos por «esperar»
MAX_SCROLL = 15
MAX_REPEAT = 20
SHOT_LONG_EDGE = 1280  # las capturas se achican a esto: mejor puntería del modelo y menos tokens
# Atajos para salir de LabelBridge cuando está al frente (el resto del teclado no llega a sus ventanas).
NAVIGATION = {"super", "super+d", "super+r", "super+e", "super+s", "super+i", "super+tab", "alt+tab", "win", "win+d",
              "win+r", "win+e", "win+s", "win+i", "win+tab"}


@dataclass
class Frame:
    """Una captura de la pantalla principal tal como la vio el modelo."""

    png: bytes
    scale: float  # píxeles de la captura = píxeles físicos × scale
    width: int  # tamaño físico de la pantalla
    height: int
    blocked: list[tuple[int, int, int, int]] = field(default_factory=list)  # ventanas de LabelBridge (x, y, ancho, alto)

    def to_physical(self, point: Any) -> tuple[int, int]:
        x, y = _point(point)
        px, py = round(x / self.scale), round(y / self.scale)
        if not (0 <= px < self.width and 0 <= py < self.height):
            raise ScreenError("Ese punto está fuera de la pantalla.")
        return px, py

    def to_shot(self, x: int, y: int) -> tuple[int, int]:
        return round(x * self.scale), round(y * self.scale)


class ScreenError(Exception):
    """Lo que se le devuelve al modelo como error de la acción (la tanda se corta ahí)."""


def _point(value: Any) -> tuple[float, float]:
    if (not isinstance(value, (list, tuple)) or len(value) != 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)):
        raise ScreenError("Las coordenadas deben ser [x, y].")
    return float(value[0]), float(value[1])


def inside(rects: list[tuple[int, int, int, int]], x: int, y: int) -> bool:
    return any(rx <= x < rx + w and ry <= y < ry + h for rx, ry, w, h in rects)


def scale_for(width: int, height: int, long_edge: int = SHOT_LONG_EDGE) -> float:
    return min(1.0, long_edge / max(1, width, height))


class ScreenControl:
    """El control de la pantalla de UNA conversación."""

    def __init__(self, ui, audit, cancel: Callable[[], threading.Event],
                 backend_factory: Callable[[], Any] | None = None) -> None:
        self.ui = ui
        self.audit = audit
        self.cancel = cancel
        self.backend_factory = backend_factory or screen_input.WindowsInput
        self._backend = None
        self.allowed: bool | None = None  # None: todavía no se pidió el permiso de la tarea
        self.active = False
        self.frame: Frame | None = None
        self.actions = 0
        self.looks = 0
        self.reason = ""  # lo último que dijo el modelo: se muestra en cada permiso

    @property
    def backend(self):
        if self._backend is None:
            self._backend = self.backend_factory()
        return self._backend

    # --- tarea ---------------------------------------------------------------------------------
    def _start(self) -> None:
        if self.allowed is None:
            self.allowed = bool(self.ui.screen_consent())
            self.audit.record("asistente", "pantalla", "allow" if self.allowed else "declined", detail="permiso de la tarea")
        if not self.allowed:
            raise ScreenError("La persona no dejó usar la pantalla en esta conversación. No lo intentes de nuevo: "
                              "explícale cómo hacerlo a mano o usa las herramientas.")
        if not self.active:
            self.active = True
            self.ui.screen_session(True)

    def end(self) -> None:
        """Al terminar el turno: LabelBridge vuelve a mostrarse y se quita la barra."""
        if self.active:
            self.active = False
            try:
                self.ui.screen_session(False)
            except Exception:
                log.exception("Asistente: no se pudo cerrar el control de la pantalla")

    # --- acciones ------------------------------------------------------------------------------
    def run(self, name: str, args: Any) -> tuple[str, list[bytes], bool]:
        """(texto para el modelo, imágenes PNG, es_error)."""
        try:
            if not isinstance(args, dict):
                raise ScreenError("La entrada debe ser un objeto JSON.")
            self._start()
            if name in LOOK:
                return self._look(name, args)
            if name in POINTER or name in KEYBOARD:
                return self._act(name, args)
            raise ScreenError(f"Acción desconocida: {name}")
        except ScreenError as exc:
            return str(exc), [], True
        except Exception as exc:  # Windows rechazó algo: el modelo lo sabe y la persona también
            log.exception("Asistente: falló la acción de pantalla %s", name)
            return f"Error: {exc}", [], True

    def _look(self, name: str, args: dict) -> tuple[str, list[bytes], bool]:
        if name == "wait":
            seconds = min(float(args.get("duration") or 1), MAX_WAIT)
            if self.cancel().wait(max(0.0, seconds)):
                raise ScreenError("La persona detuvo el asistente.")
            return "OK", [], False
        if name == "cursor_position":
            frame = self.frame or self._capture()
            x, y = frame.to_shot(*self.backend.cursor())
            return f"X={x},Y={y}", [], False
        self.looks += 1
        if self.looks > MAX_LOOKS:
            raise ScreenError("Se alcanzó el máximo de capturas de esta conversación: explícale a la persona lo que "
                              "falta y que empiece una conversación nueva si hace falta.")
        if name == "screenshot":
            return "OK", [self._capture().png], False
        frame = self.frame or self._capture()  # zoom: la región en coordenadas de la última captura
        region = args.get("region")
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            raise ScreenError("region debe ser [x0, y0, x1, y1].")
        x0, y0 = frame.to_physical(region[:2])
        x1, y1 = frame.to_physical(region[2:])
        if x1 <= x0 or y1 <= y0:
            raise ScreenError("La región está vacía.")
        return "OK", [self.ui.capture_region((x0, y0, x1 - x0, y1 - y0))], False

    def _capture(self) -> Frame:
        self.frame = self.ui.capture_screen()
        return self.frame

    def _act(self, name: str, args: dict) -> tuple[str, list[bytes], bool]:
        if self.actions >= MAX_ACTIONS:
            raise ScreenError("Se alcanzó el máximo de acciones de pantalla de esta conversación.")
        frame = self.frame or self._capture()
        what, points, run = self._plan(name, args, frame)
        decision = self.ui.approve_screen(what, self.reason, frame, points)
        if decision == "stop":
            self.audit.record("asistente", f"pantalla:{name}", "declined", detail=tr("Detenido: {0}", what))
            self.cancel().set()
            raise ScreenError("La persona detuvo el control de la pantalla.")
        if decision != "allow":
            self.audit.record("asistente", f"pantalla:{name}", "declined", detail=what)
            raise ScreenError("La persona NO aprobó esta acción. No la repitas: pregúntale qué prefiere.")
        self.actions += 1
        self.audit.record("asistente", f"pantalla:{name}", "allow",
                          detail=what if name != "type" else tr("Escribir {0} caracteres", len(args.get("text", ""))))
        run()
        return "OK", [], False

    def _plan(self, name: str, args: dict, frame: Frame) -> tuple[str, list[tuple[int, int]], Callable[[], None]]:
        """Qué se va a hacer (para el permiso), dónde (en la captura) y la acción ya validada."""
        backend = self.backend
        modifiers_text = args.get("text") if name in POINTER else None
        try:
            modifiers = screen_input.modifier_codes(modifiers_text) if modifiers_text else []
        except ValueError as exc:
            raise ScreenError(str(exc)) from None
        held = tr(" con {0}", modifiers_text) if modifiers else ""

        def target(key: str = "coordinate", required: bool = False) -> tuple[int, int] | None:
            if key not in args:
                if required:
                    raise ScreenError(f"Falta {key}.")
                return None
            point = frame.to_physical(args[key])
            if inside(self.ui.blocked_areas(), *point):
                raise ScreenError("Ese punto está sobre una ventana de LabelBridge: el asistente no la usa con el "
                                  "mouse (para eso tiene sus herramientas). Elige otro punto o cambia de ventana.")
            return point

        if name in ("left_click", "right_click", "double_click", "triple_click"):
            point = target()
            button, count = {"left_click": ("left", 1), "right_click": ("right", 1), "double_click": ("left", 2),
                             "triple_click": ("left", 3)}[name]
            what = {"left_click": tr("Hacer clic{0}", held), "right_click": tr("Clic derecho{0}", held),
                    "double_click": tr("Doble clic{0}", held), "triple_click": tr("Triple clic{0}", held)}[name]
            if point is None:
                current = backend.cursor()
                if inside(self.ui.blocked_areas(), *current):
                    raise ScreenError("El puntero está sobre una ventana de LabelBridge: indica dónde hacer clic.")
                point = current

            def click(point=point) -> None:
                backend.move(*point)
                backend.click(button, count, modifiers)

            return what + tr(" en el punto marcado"), [frame.to_shot(*point)], click
        if name == "mouse_move":
            point = target(required=True)
            return tr("Mover el puntero al punto marcado"), [frame.to_shot(*point)], lambda: backend.move(*point)
        if name == "left_click_drag":
            start = target("start_coordinate", required=True)
            end = target(required=True)
            return (tr("Arrastrar del primer punto al segundo{0}", held), [frame.to_shot(*start), frame.to_shot(*end)],
                    lambda: backend.drag(start, end, modifiers))
        if name == "scroll":
            direction = args.get("scroll_direction")
            if direction not in ("up", "down", "left", "right"):
                raise ScreenError("scroll_direction debe ser up, down, left o right.")
            amount = max(1, min(int(args.get("scroll_amount") or 3), MAX_SCROLL))
            point = target()
            words = {"up": tr("hacia arriba"), "down": tr("hacia abajo"), "left": tr("a la izquierda"),
                     "right": tr("a la derecha")}

            def scroll() -> None:
                if point is not None:
                    backend.move(*point)
                backend.scroll(direction, amount, modifiers)

            where = [frame.to_shot(*point)] if point is not None else []
            return tr("Desplazar {0} ({1}){2}", words[direction], amount, held), where, scroll
        # Teclado: nunca hacia una ventana de LabelBridge (salvo los atajos para cambiar de ventana).
        combo = str(args.get("text") or "")
        if name == "key":
            normalized = combo.replace(" ", "").lower()
            try:
                screen_input.parse_combo(combo)
            except ValueError as exc:
                raise ScreenError(str(exc)) from None
            if backend.foreground_is_ours() and normalized not in NAVIGATION:
                raise ScreenError("LabelBridge está al frente: cambia de ventana primero (alt+Tab o la barra de "
                                  "tareas). El asistente no usa LabelBridge con el teclado.")
            repeat = max(1, min(int(args.get("repeat") or 1), MAX_REPEAT))
            what = tr("Pulsar las teclas {0}", combo) + (tr(" ({0} veces)", repeat) if repeat > 1 else "")
            return what, [], lambda: backend.key(combo, repeat)
        if not combo:
            raise ScreenError("Falta el texto para escribir.")
        if len(combo) > MAX_TEXT:
            raise ScreenError(f"Texto muy largo: máximo {MAX_TEXT} caracteres por vez.")
        if backend.foreground_is_ours():
            raise ScreenError("LabelBridge está al frente: cambia de ventana primero. El asistente no escribe en "
                              "LabelBridge (para eso tiene sus herramientas).")
        return tr("Escribir: «{0}»", combo), [], lambda: backend.type_text(combo)

"""Mouse y teclado de Windows para el control de la pantalla del asistente (SendInput, sin dependencias).

Solo se usa después de que la persona aprobó la acción en una ventana (assistant/screen.py). Las coordenadas son
píxeles físicos de la pantalla principal. Los nombres de teclas siguen el formato del toolset de Claude («Return»,
«ctrl+s», «alt+Tab»…); los que no se conocen dan error en vez de adivinarse.
"""

from __future__ import annotations

import ctypes
import os
import sys
import time
from ctypes import wintypes

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP = 0x0008, 0x0010
MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP = 0x0020, 0x0040
MOUSEEVENTF_WHEEL, MOUSEEVENTF_HWHEEL = 0x0800, 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0001, 0x0002, 0x0004
WHEEL_DELTA = 120

BUTTONS = {
    "left": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "right": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "middle": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}

# Teclas con nombre → código virtual de Windows. Letras y números se resuelven aparte.
KEYS: dict[str, int] = {
    "return": 0x0D, "enter": 0x0D, "kp_enter": 0x0D, "escape": 0x1B, "esc": 0x1B, "tab": 0x09,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "space": 0x20,
    "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27, "home": 0x24, "end": 0x23,
    "page_up": 0x21, "prior": 0x21, "pageup": 0x21, "page_down": 0x22, "next": 0x22, "pagedown": 0x22,
    "ctrl": 0x11, "control": 0x11, "control_l": 0x11, "control_r": 0x11,
    "alt": 0x12, "alt_l": 0x12, "alt_r": 0x12, "shift": 0x10, "shift_l": 0x10, "shift_r": 0x10,
    "super": 0x5B, "super_l": 0x5B, "super_r": 0x5C, "win": 0x5B, "windows": 0x5B, "meta": 0x5B, "cmd": 0x5B,
    "caps_lock": 0x14, "print": 0x2C, "menu": 0x5D,
    "minus": 0xBD, "equal": 0xBB, "plus": 0xBB, "comma": 0xBC, "period": 0xBE, "slash": 0xBF,
    "semicolon": 0xBA, "apostrophe": 0xDE, "bracketleft": 0xDB, "bracketright": 0xDD, "backslash": 0xDC,
    "grave": 0xC0,
}
KEYS.update({f"f{n}": 0x6F + n for n in range(1, 13)})
EXTENDED = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x5D}
MODIFIERS = {"ctrl", "control", "control_l", "control_r", "alt", "alt_l", "alt_r", "shift", "shift_l", "shift_r",
             "super", "super_l", "super_r", "win", "windows", "meta", "cmd"}


def supported() -> bool:
    return sys.platform == "win32"


def key_code(name: str) -> int:
    """Código virtual de una tecla por su nombre (sin distinguir mayúsculas)."""
    low = name.strip().lower()
    if low in KEYS:
        return KEYS[low]
    if len(low) == 1 and ("a" <= low <= "z" or "0" <= low <= "9"):
        return ord(low.upper())
    raise ValueError(f"Tecla desconocida: {name}")


def parse_combo(text: str) -> list[int]:
    """«ctrl+shift+Escape» → [ctrl, shift, Escape]. Error si alguna tecla no existe."""
    parts = [part for part in (text or "").replace(" ", "").split("+") if part]
    if not parts:
        raise ValueError("Falta la tecla")
    return [key_code(part) for part in parts]


def modifier_codes(text: str | None) -> list[int]:
    """Teclas que se mantienen durante un clic o un desplazamiento («shift», «ctrl+shift»)."""
    if not text:
        return []
    names = [part.strip().lower() for part in text.split("+") if part.strip()]
    unknown = [name for name in names if name not in MODIFIERS]
    if unknown:
        raise ValueError(f"Modificador desconocido: {unknown[0]}")
    return [KEYS[name] for name in names]


# --- SendInput ------------------------------------------------------------------------------------
# Tipos de ancho fijo (LONG/DWORD de Windows son de 32 bits): la estructura mide lo mismo en cualquier sistema (40
# bytes en 64 bits), así se puede probar fuera de Windows.
LONG, DWORD, WORD, ULONG_PTR = ctypes.c_int32, ctypes.c_uint32, ctypes.c_uint16, ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD), ("dwFlags", DWORD), ("time", DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


def mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    item = INPUT(type=INPUT_MOUSE)
    item.u.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, 0)
    return item


def key_input(vk: int = 0, scan: int = 0, flags: int = 0) -> INPUT:
    item = INPUT(type=INPUT_KEYBOARD)
    item.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
    return item


def key_events(codes: list[int]) -> list[INPUT]:
    """Pulsar las teclas en orden y soltarlas al revés (así funcionan las combinaciones)."""
    down = [key_input(code, 0, KEYEVENTF_EXTENDEDKEY if code in EXTENDED else 0) for code in codes]
    up = [key_input(code, 0, (KEYEVENTF_EXTENDEDKEY if code in EXTENDED else 0) | KEYEVENTF_KEYUP)
          for code in reversed(codes)]
    return down + up


def text_events(text: str) -> list[INPUT]:
    """Escribir texto tal cual (Unicode), con Enter para los saltos de línea."""
    events: list[INPUT] = []
    for char in text.replace("\r\n", "\n"):
        if char == "\n":
            events += key_events([KEYS["return"]])
            continue
        units = char.encode("utf-16-le")
        for index in range(0, len(units), 2):
            unit = int.from_bytes(units[index:index + 2], "little")
            events.append(key_input(0, unit, KEYEVENTF_UNICODE))
            events.append(key_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP))
    return events


class WindowsInput:
    """Las acciones del control de pantalla sobre la pantalla principal (coordenadas en píxeles físicos)."""

    def __init__(self) -> None:
        if not supported():
            raise RuntimeError("El control de la pantalla solo está disponible en Windows.")
        self.user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        self.user32.SendInput.argtypes = (wintypes.UINT, ctypes.POINTER(INPUT), ctypes.c_int)
        self.user32.SendInput.restype = wintypes.UINT

    def screen_size(self) -> tuple[int, int]:
        return self.user32.GetSystemMetrics(0), self.user32.GetSystemMetrics(1)

    def _send(self, events: list[INPUT]) -> None:
        if not events:
            return
        array = (INPUT * len(events))(*events)
        sent = self.user32.SendInput(len(events), array, ctypes.sizeof(INPUT))
        if sent != len(events):
            raise RuntimeError("Windows bloqueó la acción (¿hay una ventana de administrador al frente?).")

    def move(self, x: int, y: int) -> None:
        width, height = self.screen_size()
        nx = round(max(0, min(x, width - 1)) * 65535 / max(1, width - 1))
        ny = round(max(0, min(y, height - 1)) * 65535 / max(1, height - 1))
        self._send([mouse_input(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE, nx, ny)])

    def click(self, button: str = "left", count: int = 1, modifiers: list[int] | None = None) -> None:
        down, up = BUTTONS[button]
        held = [key_input(code, 0, KEYEVENTF_EXTENDEDKEY if code in EXTENDED else 0) for code in modifiers or []]
        self._send(held)
        try:
            for index in range(count):
                self._send([mouse_input(down), mouse_input(up)])
                if index + 1 < count:
                    time.sleep(0.06)
        finally:
            self._send([key_input(code, 0, (KEYEVENTF_EXTENDEDKEY if code in EXTENDED else 0) | KEYEVENTF_KEYUP)
                        for code in reversed(modifiers or [])])

    def drag(self, start: tuple[int, int], end: tuple[int, int], modifiers: list[int] | None = None) -> None:
        held = [key_input(code) for code in modifiers or []]
        self.move(*start)
        self._send(held)
        try:
            self._send([mouse_input(MOUSEEVENTF_LEFTDOWN)])
            steps = 12
            for step in range(1, steps + 1):
                time.sleep(0.02)
                self.move(round(start[0] + (end[0] - start[0]) * step / steps),
                          round(start[1] + (end[1] - start[1]) * step / steps))
            self._send([mouse_input(MOUSEEVENTF_LEFTUP)])
        finally:
            self._send([key_input(code, 0, KEYEVENTF_KEYUP) for code in reversed(modifiers or [])])

    def scroll(self, direction: str, amount: int, modifiers: list[int] | None = None) -> None:
        held = [key_input(code) for code in modifiers or []]
        self._send(held)
        try:
            for _ in range(max(1, amount)):
                if direction in ("up", "down"):
                    delta = WHEEL_DELTA if direction == "up" else -WHEEL_DELTA
                    self._send([mouse_input(MOUSEEVENTF_WHEEL, data=delta)])
                else:
                    delta = WHEEL_DELTA if direction == "right" else -WHEEL_DELTA
                    self._send([mouse_input(MOUSEEVENTF_HWHEEL, data=delta)])
                time.sleep(0.03)
        finally:
            self._send([key_input(code, 0, KEYEVENTF_KEYUP) for code in reversed(modifiers or [])])

    def key(self, combo: str, repeat: int = 1) -> None:
        codes = parse_combo(combo)
        for index in range(max(1, repeat)):
            self._send(key_events(codes))
            if index + 1 < repeat:
                time.sleep(0.03)

    def type_text(self, text: str) -> None:
        events = text_events(text)
        for start in range(0, len(events), 40):  # de a poco: algunos programas pierden teclas si llegan juntas
            self._send(events[start:start + 40])
            time.sleep(0.01)

    def cursor(self) -> tuple[int, int]:
        point = wintypes.POINT()
        self.user32.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def foreground_is_ours(self) -> bool:
        """¿La ventana al frente (la que recibe el teclado) es de LabelBridge?"""
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value == os.getpid()

"""Que el equipo no se duerma solo mientras LabelBridge está abierto (la pantalla sí puede apagarse).

Windows: SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED) desde el hilo principal, que vive tanto como la
app (el pedido se borra solo al cerrarla). Mac: «caffeinate -i -s -w <pid>», que se termina solo al cerrarse la app.
No impide que alguien lo suspenda a mano ni que se duerma al cerrar la tapa: nada puede funcionar con el equipo
dormido. Al despertar, ResumeWatcher (ui) pone todo al día enseguida.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from typing import Callable

log = logging.getLogger(__name__)

ES_CONTINUOUS = 0x80000000
ES_SYSTEM_REQUIRED = 0x00000001


class KeepAwake:
    """Pide al sistema que no se duerma por inactividad; release() lo deja como estaba."""

    def __init__(self) -> None:
        self.active = False
        self._caffeinate: subprocess.Popen | None = None

    def apply(self, enabled: bool) -> bool:
        """Activa o desactiva el pedido. Devuelve si quedó activo."""
        if enabled and not self.active:
            self.active = self._start()
            if self.active:
                log.info("El equipo no se dormirá solo mientras LabelBridge esté abierto (la pantalla sí se apaga)")
        elif not enabled and self.active:
            self.release()
        return self.active

    def release(self) -> None:
        if not self.active:
            return
        self.active = False
        if sys.platform == "win32":
            try:
                import ctypes

                ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS)
            except (AttributeError, OSError):
                pass
        elif self._caffeinate is not None:
            self._caffeinate.terminate()
            self._caffeinate = None
        log.info("El equipo vuelve a dormirse según su configuración")

    def _start(self) -> bool:
        if sys.platform == "win32":
            try:
                import ctypes

                return bool(ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED))
            except (AttributeError, OSError) as exc:
                log.warning("No se pudo mantener despierto el equipo: %s", exc)
                return False
        if sys.platform == "darwin":
            try:  # -i: sin reposo por inactividad; -s: tampoco con la tapa abierta enchufada; -w: termina con la app
                self._caffeinate = subprocess.Popen(
                    ["/usr/bin/caffeinate", "-i", "-s", "-w", str(os.getpid())],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                )
                return True
            except OSError as exc:
                log.warning("No se pudo mantener despierta la Mac: %s", exc)
                return False
        return False  # en desarrollo en otros sistemas: nada que hacer


class ResumeWatcher:
    """Se da cuenta de que el equipo volvió de una suspensión (el reloj saltó) para ponerse al día enseguida, sin
    esperar a la próxima vuelta de cada servicio. check() se llama cada `every` segundos (un QTimer)."""

    def __init__(self, on_resume: Callable[[float], None], every: float = 30.0) -> None:
        self.on_resume = on_resume
        self.every = every
        self.last = time.time()

    def check(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        gap, self.last = now - self.last, now
        if gap > max(120.0, self.every * 4):
            log.info("El equipo volvió de una suspensión (%d min sin funcionar): poniéndose al día", gap // 60)
            self.on_resume(gap)
            return True
        return False

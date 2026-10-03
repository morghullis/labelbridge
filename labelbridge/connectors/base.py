"""Servicio en segundo plano reiniciable, con estado visible en la UI."""

from __future__ import annotations

import logging
import threading

from .. import events as ev

log = logging.getLogger(__name__)


class BackgroundService:
    name = "service"
    label = "Servicio"

    def __init__(self, events: ev.Events) -> None:
        self.events = events
        self.state = "off"  # off | connecting | ok | warn | error
        self.message = "Desactivado"
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()

    # --- a implementar -------------------------------------------------------------
    def enabled(self) -> bool:
        raise NotImplementedError

    def run(self, stop: threading.Event, wake: threading.Event) -> None:
        raise NotImplementedError

    # --- ciclo de vida -------------------------------------------------------------
    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and not self._stop_event.is_set())

    def start(self) -> None:
        with self._lock:
            if self.running:
                return
            previous = self._thread
        # Si un stop() anterior agotó su espera, el hilo viejo aún puede estar terminando: se espera.
        if previous is not None and previous.is_alive() and previous is not threading.current_thread():
            previous.join()
        with self._lock:
            if self.running:
                return
            # Eventos nuevos por ejecución: un hilo viejo que tarda en salir no revive.
            self._stop_event = threading.Event()
            self._wake_event = threading.Event()
            self._thread = threading.Thread(
                target=self._run_safe,
                args=(self._stop_event, self._wake_event),
                name=f"svc-{self.name}",
                daemon=True,
            )
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            self._stop_event.set()
            self._wake_event.set()
            thread = self._thread
        if thread and thread is not threading.current_thread():
            thread.join(timeout)
        self.set_status("off", "Desactivado")

    def restart(self) -> None:
        self.stop()
        if self.enabled():
            self.start()

    def wake(self) -> None:
        self._wake_event.set()

    @staticmethod
    def sleep(stop: threading.Event, wake: threading.Event, seconds: float) -> bool:
        """Espera (interrumpible). Devuelve False si hay que terminar."""
        wake.wait(max(0.1, seconds))
        wake.clear()
        return not stop.is_set()

    def set_status(self, state: str, message: str) -> None:
        if (state, message) == (self.state, self.message):
            return
        self.state, self.message = state, message
        self.events.emit(ev.CONNECTOR, {"name": self.name, "state": state, "message": message})

    def _run_safe(self, stop: threading.Event, wake: threading.Event) -> None:
        try:
            self.run(stop, wake)
        except Exception as exc:
            log.exception("El servicio %s se detuvo por un error", self.name)
            self.set_status("error", f"Detenido por un error: {exc}")

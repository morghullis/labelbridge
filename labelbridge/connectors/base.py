"""Servicio en segundo plano reiniciable, con estado visible en la UI."""

from __future__ import annotations

import logging
import threading

from .. import errors
from .. import events as ev
from ..i18n import tr

log = logging.getLogger(__name__)

RESTART_FIRST_SECONDS = 15  # tras un error inesperado, el servicio vuelve a arrancar solo
RESTART_MAX_SECONDS = 300


class BackgroundService:
    name = "service"
    label = "Servicio"

    def __init__(self, events: ev.Events) -> None:
        self.events = events
        self.state = "off"  # off | connecting | ok | warn | error
        self.message = tr("Desactivado")
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
        self.set_status("off", tr("Desactivado"))

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
        """Un error inesperado no apaga el servicio para siempre: queda anotado, se avisa y se reinicia solo, cada vez
        más espaciado (15 s, 30 s, 1 min… hasta 5 min) para no insistir sin pausa si el problema sigue."""
        failures = 0
        while not stop.is_set():
            try:
                self.run(stop, wake)
                return  # terminó porque se pidió parar (o porque se apagó)
            except Exception as exc:  # noqa: BLE001  cualquier falla del servicio se anota, se avisa y se reintenta
                failures += 1
                delay = min(RESTART_MAX_SECONDS, RESTART_FIRST_SECONDS * 2 ** min(failures - 1, 5))
                log.exception("El servicio %s se detuvo por un error (vez %d); se reinicia en %d s",
                              self.name, failures, delay)
                reason = errors.user_message(exc)
                self.set_status("error", tr("Se detuvo por un error: {0}. Se reinicia solo en {1} s.", reason, delay))
                errors.notify(tr("{0} se detuvo por un error", tr(self.label)),
                              tr("{0}. LabelBridge lo reinicia solo; si se repite, revisa la pestaña Registro.", reason))
            if not self.sleep(stop, wake, delay):
                return

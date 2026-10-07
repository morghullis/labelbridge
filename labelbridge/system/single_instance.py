"""Una sola copia de LabelBridge trabajando por usuario: nunca dos imprimiendo o comprando guías a la vez.

Hay dos maneras de estar abierta: la app con ventana («app») y la de segundo plano sin sesión iniciada («background»,
ver background.py). La exclusión de verdad es un candado de archivo en la carpeta de datos (WorkLock): lo tiene la copia
que trabaja durante toda su vida y el sistema lo suelta solo si el proceso termina, aunque sea de golpe. Sirve también
entre la sesión de Windows y la de segundo plano del mismo usuario.

Además hay un canal local (QLocalServer) para hablar con la copia que trabaja: al conectarse dice quién es
(«role:app» o «role:background»); por la misma conexión se le pide «show» (traer la ventana) o «handoff» (el relevo) y
contesta «ok» cuando lo recibió. Abrir la app otra vez trae la ventana existente. Si la que trabaja es la de segundo
plano, la app le pide el relevo y espera a que termine lo que imprime y suelte el candado. La de segundo plano nunca
reemplaza a nadie.
"""

from __future__ import annotations

import getpass
import logging
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .. import APP_ID

log = logging.getLogger(__name__)

# El instalador (Inno Setup) usa este nombre para saber si la app con ventana está abierta (en la sesión del usuario).
WINDOWS_MUTEX = "LabelBridgeRunningMutex"
APP, BACKGROUND = "app", "background"
HANDOFF_SECONDS = 150  # lo que la app espera a que la de segundo plano termine lo que imprime y se cierre
ANSWER_SECONDS = 20  # lo que se espera a que la copia que trabaja conteste (p. ej. mientras termina de arrancar)


class WorkLock:
    """El candado: un archivo bloqueado (msvcrt en Windows, flock en la Mac). Al lado, el PID y el rol de quien lo
    tiene."""

    def __init__(self, path: Path, role: str = APP) -> None:
        self.path = path
        self.role = role
        self.pid_path = path.with_suffix(".pid")
        self._file = None

    @property
    def held(self) -> bool:
        return self._file is not None

    def try_acquire(self) -> bool:
        if self._file is not None:
            return True
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self.path, "a+b")  # noqa: SIM115  queda abierto mientras se tenga el candado
        except OSError as exc:  # p. ej. el antivirus lo tiene abierto un momento: cuenta como «no tomado»
            log.warning("No se pudo abrir el candado de LabelBridge (%s): se reintenta", exc)
            return False
        try:
            if sys.platform == "win32":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            return False
        self._file = handle
        try:
            self.pid_path.write_text(f"{os.getpid()} {self.role}", encoding="ascii")
        except OSError as exc:  # sin el PID solo se pierde poder cerrar una copia trabada
            log.warning("No se pudo anotar el PID de la copia que trabaja: %s", exc)
        return True

    def wait(self, seconds: float) -> bool:
        """Espera a que el candado quede libre y lo toma. True si lo tomó."""
        deadline = time.monotonic() + seconds
        while True:
            if self.try_acquire():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.5)

    def holder(self) -> tuple[int, str] | None:
        """(PID, rol) de quien tiene el candado, o None si no se sabe."""
        try:
            pid, _, role = self.pid_path.read_text(encoding="ascii").strip().partition(" ")
            return int(pid), role or APP
        except (OSError, ValueError):
            return None

    def release(self) -> None:
        if self._file is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._file.seek(0)
                msvcrt.locking(self._file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass  # al cerrar el archivo se suelta igual
        self._file.close()
        self._file = None


def default_lock_path() -> Path:
    from ..paths import data_dir

    return data_dir() / "trabajando.lock"


class SingleInstance(QObject):
    activated = Signal()  # alguien abrió la app otra vez: mostrar la ventana
    handoff_requested = Signal()  # (segundo plano) la app con ventana quiere seguir: terminar y cerrarse

    def __init__(self, role: str = APP, key: str | None = None, lock_path: Path | None = None) -> None:
        super().__init__()
        try:
            user = getpass.getuser()
        except Exception:
            user = "user"
        self.role = role
        self.key = key or f"{APP_ID}-{user}"
        self.lock = WorkLock(lock_path or default_lock_path(), role)
        self._server: QLocalServer | None = None
        self._mutex = None

    def acquire(self) -> bool:
        """True si esta copia queda como la que trabaja (con el candado). Si ya hay una: la app trae su ventana (o,
        si es la de segundo plano, le pide el relevo y la espera); la de segundo plano simplemente no arranca."""
        if self.lock.try_acquire():
            self._listen()
            return True
        if self.role == BACKGROUND:
            return False
        # Hasta que la otra suelte el candado: se le habla cada pocos segundos (si estaba arrancando, en cuanto
        # contesta recibe el pedido de relevo; repetirlo no hace daño).
        deadline = time.monotonic() + ANSWER_SECONDS + HANDOFF_SECONDS
        next_talk = 0.0
        asked = False
        while time.monotonic() < deadline:
            if self.lock.try_acquire():
                self._listen()
                return True
            if time.monotonic() >= next_talk:
                peer = self._talk(self._command_for)
                if peer == APP:
                    return False  # ya hay una ventana: se trajo al frente
                if peer == BACKGROUND and not asked:
                    log.info("LabelBridge trabajaba en segundo plano: se le pidió el relevo")
                    asked = True
                next_talk = time.monotonic() + 3.0
            time.sleep(0.5)
        if not self._close_stuck_holder():
            log.error("La otra copia de LabelBridge no se cerró: esta no arranca (nunca dos trabajando)")
            return False
        self._listen()
        return True

    def wait_until_free(self, seconds: float) -> bool:
        """Al reiniciar: espera a que la copia anterior termine de cerrarse (sin tomar el candado)."""
        if self.lock.wait(seconds):
            self.lock.release()
            return True
        return False

    def release(self) -> None:
        """Deja de trabajar (al cerrarse): otra copia ya puede quedar como la que trabaja."""
        if self._server is not None:
            self._server.close()
            self._server = None
        self.lock.release()

    def _listen(self) -> None:
        QLocalServer.removeServer(self.key)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        if not self._server.listen(self.key):  # otra copia no podría avisar a esta (el candado sigue protegiendo)
            log.warning("No se pudo abrir el canal de instancia única (%s)", self._server.errorString())
        if sys.platform == "win32" and self.role == APP:
            import ctypes

            self._mutex = ctypes.windll.kernel32.CreateMutexW(None, False, WINDOWS_MUTEX)

    def _close_stuck_holder(self) -> bool:
        """La copia de segundo plano no contestó ni soltó el candado: está trabada. Se cierra (solo si es la de segundo
        plano y de verdad es LabelBridge) para que la persona no se quede sin app. Una ventana nunca se cierra así."""
        holder = self.lock.holder()
        if holder is None:
            return False
        pid, role = holder
        if role != BACKGROUND or pid == os.getpid() or not _is_labelbridge(pid):
            return False
        log.error("La copia de segundo plano (PID %s) no se cerró en %s s: se cierra a la fuerza", pid, HANDOFF_SECONDS)
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=20,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            else:
                os.kill(pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError) as exc:
            log.error("No se pudo cerrar la copia trabada: %s", exc)
            return False
        return self.lock.wait(15)

    def _command_for(self, peer: str) -> bytes | None:
        """Qué pedirle a la copia abierta: la de segundo plano nunca pide nada; la app pide el relevo a una de segundo
        plano o que muestre su ventana a otra app."""
        if self.role == BACKGROUND:
            return None
        return b"handoff" if peer == BACKGROUND else b"show"

    def _talk(self, command_for) -> str | None:
        """Habla con la copia que trabaja: lee quién es y, si `command_for(rol)` da un pedido, se lo manda y espera su
        «ok» por la misma conexión. Devuelve su rol, o None si no contesta."""
        socket = QLocalSocket()
        socket.connectToServer(self.key)
        if not socket.waitForConnected(400):
            return None
        if not socket.waitForReadyRead(1500):  # conectó pero no contesta (arrancando o trabada)
            socket.disconnectFromServer()
            return None
        data = bytes(socket.readAll())
        role = BACKGROUND if b"role:" + BACKGROUND.encode() in data else APP
        command = command_for(role)
        if command:
            socket.write(command + b"\n")
            socket.flush()
            deadline = time.monotonic() + 2.0
            while time.monotonic() < deadline and b"ok" not in bytes(socket.peek(16)):
                if not socket.waitForReadyRead(200) and socket.state() != QLocalSocket.LocalSocketState.ConnectedState:
                    break
        socket.disconnectFromServer()
        return role

    def _on_connection(self) -> None:
        assert self._server is not None
        connection = self._server.nextPendingConnection()
        if connection is None:
            return
        connection.disconnected.connect(connection.deleteLater)
        connection.write(f"role:{self.role}\n".encode())
        connection.flush()

        def read() -> None:
            data = bytes(connection.readAll())
            if b"handoff" in data and self.role == BACKGROUND:
                self._ok(connection)
                self.handoff_requested.emit()
            elif b"show" in data:  # el reinicio solo comprueba si seguimos abiertos
                self._ok(connection)
                self.activated.emit()

        connection.readyRead.connect(read)
        if connection.bytesAvailable():
            read()

    @staticmethod
    def _ok(connection: QLocalSocket) -> None:
        connection.write(b"ok\n")
        connection.flush()


def _is_labelbridge(pid: int) -> bool:
    """¿Ese proceso es LabelBridge (el mismo programa que este)? Para no cerrar nunca otra cosa con ese PID."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True,
                                 text=True, timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
            return Path(sys.executable).name.lower() in out.lower()
        out = subprocess.run(["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=10).stdout
        return "labelbridge" in out.lower()
    except (OSError, ValueError, subprocess.SubprocessError):
        return False

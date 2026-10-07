"""Una sola copia de LabelBridge trabajando por usuario (nunca dos imprimiendo o comprando a la vez).

Hay dos maneras de estar abierta: la app con ventana («app») y la de segundo plano sin sesión iniciada («background»,
ver background.py). Abrir la app otra vez trae la ventana existente al frente. Si la que está abierta es la de segundo
plano, la app le pide el relevo: esa termina lo que está imprimiendo y se cierra, y la app sigue desde ahí. La de
segundo plano nunca reemplaza a nadie: si ya hay una abierta, no arranca.

El canal es local (QLocalServer: un «named pipe» en Windows, visible desde la sesión del usuario y desde la de segundo
plano del mismo usuario). Al conectarse, la abierta dice quién es («role:app» o «role:background»); por la misma
conexión se le pide «show» o «handoff» y contesta «ok» cuando lo recibió (así el pedido nunca se pierde al cortar).
"""

from __future__ import annotations

import getpass
import logging
import sys
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .. import APP_ID

log = logging.getLogger(__name__)

# El instalador (Inno Setup, AppMutex) usa este nombre para saber si la app está abierta.
WINDOWS_MUTEX = "LabelBridgeRunningMutex"
APP, BACKGROUND = "app", "background"
HANDOFF_SECONDS = 90  # lo que la app espera a que la de segundo plano termine de imprimir y se cierre


class SingleInstance(QObject):
    activated = Signal()  # alguien abrió la app otra vez: mostrar la ventana
    handoff_requested = Signal()  # (segundo plano) la app con ventana quiere seguir: terminar y cerrarse

    def __init__(self, role: str = APP, key: str | None = None) -> None:
        super().__init__()
        try:
            user = getpass.getuser()
        except Exception:
            user = "user"
        self.role = role
        self.key = key or f"{APP_ID}-{user}"
        self._server: QLocalServer | None = None
        self._mutex = None

    def acquire(self) -> bool:
        """True si esta copia queda como la que trabaja. Si ya hay una: la app trae su ventana (o, si es la de segundo
        plano, le pide el relevo y la espera); la de segundo plano simplemente no arranca."""
        peer = self._talk(self._command_for)
        if peer is not None:
            if self.role == BACKGROUND or peer != BACKGROUND:
                return False
            log.info("LabelBridge trabajaba en segundo plano: se le pidió el relevo")
            if not self.wait_until_free(HANDOFF_SECONDS):
                log.error("La copia de segundo plano no se cerró en %s s: se deja trabajando", HANDOFF_SECONDS)
                return False
        QLocalServer.removeServer(self.key)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        if not self._server.listen(self.key):  # otra copia no podría avisar a esta
            log.warning("No se pudo abrir el canal de instancia única (%s)", self._server.errorString())
        if sys.platform == "win32":
            import ctypes

            self._mutex = ctypes.windll.kernel32.CreateMutexW(None, False, WINDOWS_MUTEX)
        return True

    def release(self) -> None:
        """Deja de atender (al cerrarse): otra copia ya puede quedar como la que trabaja."""
        if self._server is not None:
            self._server.close()
            self._server = None

    def _command_for(self, peer: str) -> bytes | None:
        """Qué pedirle a la copia abierta: la de segundo plano nunca pide nada; la app pide el relevo a una de segundo
        plano o que muestre su ventana a otra app."""
        if self.role == BACKGROUND:
            return None
        return b"handoff" if peer == BACKGROUND else b"show"

    def _talk(self, command_for) -> str | None:
        """Habla con la copia abierta: lee quién es y, si `command_for(rol)` da un pedido, se lo manda y espera su «ok»
        por la misma conexión. Devuelve su rol, o None si no hay ninguna abierta. Las versiones de antes no dicen su
        rol (cuentan como «app») ni contestan «ok» (se espera un poco y listo)."""
        socket = QLocalSocket()
        socket.connectToServer(self.key)
        if not socket.waitForConnected(400):
            return None
        role = APP
        if socket.waitForReadyRead(600) and b"role:" + BACKGROUND.encode() in bytes(socket.readAll()):
            role = BACKGROUND
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

    def wait_until_free(self, seconds: float) -> bool:
        """Espera a que la otra copia termine de cerrarse (al reiniciar o en el relevo). True si quedó libre."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            socket = QLocalSocket()
            socket.connectToServer(self.key)
            if not socket.waitForConnected(300):
                return True
            socket.disconnectFromServer()
            time.sleep(0.4)
        return False

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

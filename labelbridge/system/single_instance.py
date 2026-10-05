"""Una sola instancia por usuario. Abrir la app otra vez trae la ventana existente al frente."""

from __future__ import annotations

import getpass
import sys
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from .. import APP_ID

# El instalador (Inno Setup, AppMutex) usa este nombre para saber si la app está abierta.
WINDOWS_MUTEX = "LabelBridgeRunningMutex"


class SingleInstance(QObject):
    activated = Signal()

    def __init__(self) -> None:
        super().__init__()
        try:
            user = getpass.getuser()
        except Exception:
            user = "user"
        self.key = f"{APP_ID}-{user}"
        self._server: QLocalServer | None = None
        self._mutex = None

    def acquire(self) -> bool:
        """True si somos la primera instancia; si no, avisa a la existente y devuelve False."""
        socket = QLocalSocket()
        socket.connectToServer(self.key)
        if socket.waitForConnected(400):
            socket.write(b"show")
            socket.flush()
            socket.waitForBytesWritten(400)
            socket.disconnectFromServer()
            return False
        QLocalServer.removeServer(self.key)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        self._server.listen(self.key)
        if sys.platform == "win32":
            import ctypes

            self._mutex = ctypes.windll.kernel32.CreateMutexW(None, False, WINDOWS_MUTEX)
        return True

    def wait_until_free(self, seconds: float) -> None:
        """Al reiniciar: espera a que la instancia anterior termine de cerrarse (sin pedirle que se muestre)."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            socket = QLocalSocket()
            socket.connectToServer(self.key)
            if not socket.waitForConnected(300):
                return
            socket.disconnectFromServer()
            time.sleep(0.4)

    def _on_connection(self) -> None:
        assert self._server is not None
        connection = self._server.nextPendingConnection()
        if connection is None:
            return
        connection.disconnected.connect(connection.deleteLater)

        def read() -> None:
            # Solo «show» trae la ventana: el reinicio solo comprueba si seguimos abiertos.
            if b"show" in bytes(connection.readAll()):
                self.activated.emit()

        connection.readyRead.connect(read)
        if connection.bytesAvailable():
            read()

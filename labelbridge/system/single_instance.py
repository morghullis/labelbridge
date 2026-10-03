"""Una sola instancia por usuario. Abrir la app otra vez trae la ventana existente al frente."""

from __future__ import annotations

import getpass
import sys

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

    def _on_connection(self) -> None:
        assert self._server is not None
        connection = self._server.nextPendingConnection()
        if connection is not None:
            connection.disconnected.connect(connection.deleteLater)
        self.activated.emit()

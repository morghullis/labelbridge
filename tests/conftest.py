"""Entorno de pruebas: Qt sin pantalla y datos en una carpeta temporal (nunca imprime en impresoras reales)."""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["LABELBRIDGE_SECRETS"] = "file"
os.environ["LABELBRIDGE_HOME"] = tempfile.mkdtemp(prefix="labelbridge-tests-")

from labelbridge.app import _ensure_qt_plugin_path  # noqa: E402

_ensure_qt_plugin_path()


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([sys.argv[0]])
    yield app


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("LABELBRIDGE_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture()
def core(qapp, home):
    """Config + cola + motor con una impresora de archivo como predeterminada."""
    from labelbridge import paths
    from labelbridge.config import ConfigManager, PrinterProfile
    from labelbridge.events import Events
    from labelbridge.jobstore import JobStore
    from labelbridge.printing.engine import PrintEngine
    from labelbridge.secret_store import SecretStore

    cfg = ConfigManager(paths.config_path())
    printer = PrinterProfile(alias="archivo", kind="file", output_dir=str(home / "out"), language="driver")
    cfg.data.printers = [printer]
    cfg.data.general.default_printer_id = printer.id
    cfg.data.general.auto_discover = False  # las pruebas nunca escanean la red
    cfg.data.general.auto_update_check = False
    cfg.data.general.terms_accepted = 99  # ya aceptados (las pruebas de la aceptación los ponen en 0)
    cfg.data.assistant.ai_notice_accepted = 99
    cfg.save()
    store = JobStore(paths.db_path(), paths.spool_dir())
    events = Events()
    received: list = []
    events.subscribe(lambda kind, data: received.append((kind, data)))
    engine = PrintEngine(store, cfg, events)
    secrets = SecretStore(home / "secrets.json", backend="file")

    class Core:
        pass

    c = Core()
    c.cfg, c.store, c.events, c.engine, c.secrets, c.received, c.home = cfg, store, events, engine, secrets, received, home
    yield c
    engine.stop()
    store.close()


class FakeRawPrinter:
    """Impresora RAW 9100 falsa: guarda lo que recibe."""

    def __init__(self) -> None:
        self.received: list[bytes] = []
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.event = threading.Event()
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            data = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk
            conn.close()
            self.received.append(data)
            self.event.set()

    def close(self) -> None:
        self.sock.close()


@pytest.fixture()
def raw_printer():
    printer = FakeRawPrinter()
    yield printer
    printer.close()


def wait_for(predicate, timeout: float = 10.0) -> bool:
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return False

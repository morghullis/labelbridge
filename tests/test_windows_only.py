"""Con Windows de verdad (corre en la compilación de Windows): los scripts de PowerShell de las herramientas del
asistente y el mouse/teclado del control de la pantalla, solo leyendo (nada se cambia ni se imprime)."""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="solo en Windows")


def test_overview_and_print_queue_scripts_run():
    from labelbridge.system import windows_admin

    data = windows_admin.overview()
    assert data.get("windows") and isinstance(data.get("restart_pending"), bool)
    assert data.get("print_spooler")  # Running, Stopped o missing
    assert isinstance(windows_admin.print_queue(), list)


def test_input_backend_reads_the_screen_and_the_cursor():
    from labelbridge.system.screen_input import WindowsInput

    backend = WindowsInput()
    width, height = backend.screen_size()
    assert width > 0 and height > 0
    x, y = backend.cursor()
    assert isinstance(x, int) and isinstance(y, int)
    assert isinstance(backend.foreground_is_ours(), bool)


def test_native_windows_notifications_are_ready(tmp_path):
    """Las API de avisos de Windows cargan, el aviso es válido y la app queda registrada con su nombre y logo."""
    import winreg

    from labelbridge.paths import resource_path
    from labelbridge.system import toasts

    opened: list[str] = []
    notifier = toasts.create(opened.append, resource_path("assets", "icon.png"))
    assert notifier is not None
    document = notifier._XmlDocument()
    document.load_xml(toasts.toast_xml("Prueba", "Aviso", target="orders", action="Ver pedidos", attribution="CI"))
    notifier._ToastNotification(document)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, toasts.REGISTRY_KEY) as key:
        assert winreg.QueryValueEx(key, "DisplayName")[0] == "LabelBridge"

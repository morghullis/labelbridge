"""La versión de Mac: inicio al iniciar sesión, la app abierta desde el .dmg y el icono de la barra de menús."""

from __future__ import annotations

import plistlib
import sys
from pathlib import PurePosixPath as MacPath  # rutas de Mac también al probar en Windows

from labelbridge.system import autostart, platform


def test_login_agent_opens_the_installed_app_hidden(tmp_path, monkeypatch):
    bundle = MacPath("/Applications/LabelBridge.app")
    agent = autostart.mac_agent(bundle)
    assert agent["ProgramArguments"] == ["/Applications/LabelBridge.app/Contents/MacOS/LabelBridge", "--minimized"]
    assert agent["RunAtLoad"] and agent["LimitLoadToSessionType"] == "Aqua"
    assert agent["AssociatedBundleIdentifiers"] == ["com.labelbridge.app"]  # Ítems de inicio la muestra como LabelBridge

    plist = tmp_path / "LaunchAgents" / "com.labelbridge.app.plist"
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(autostart, "supported", lambda: True)
    monkeypatch.setattr(autostart, "mac_agent_path", lambda: plist)
    monkeypatch.setattr(autostart, "mac_app_bundle", lambda: bundle)
    autostart.apply(True)
    assert plistlib.loads(plist.read_bytes()) == agent and autostart.is_enabled()
    written = plist.stat().st_mtime_ns
    autostart.apply(True)  # sin cambios no se reescribe (macOS avisaría otra vez de un ítem en segundo plano)
    assert plist.stat().st_mtime_ns == written
    autostart.apply(False)
    assert not plist.exists() and not autostart.is_enabled()


def test_app_opened_from_the_disk_image_is_detected():
    assert platform.mac_opened_from_disk_image(MacPath("/Volumes/LabelBridge/LabelBridge.app"))
    assert platform.mac_opened_from_disk_image(
        MacPath("/private/var/folders/x1/abc/T/AppTranslocation/1A2B/d/LabelBridge.app"))
    assert not platform.mac_opened_from_disk_image(MacPath("/Applications/LabelBridge.app"))
    assert not platform.mac_opened_from_disk_image()  # en desarrollo y en Windows no hay .app


def test_menu_bar_click_opens_the_menu_not_the_window(core, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon

    from labelbridge.services import Services
    from labelbridge.ui import tray as tray_module
    from labelbridge.ui.context import AppContext, QtBridge

    services = Services(core.cfg, core.secrets, core.store, core.engine, core.events)
    ctx = AppContext(core.cfg, core.secrets, core.store, core.engine, services, core.events,
                     QtBridge(core.events, core.cfg))
    opened = []
    for mac, expected in ((True, 0), (False, 1)):
        monkeypatch.setattr(tray_module, "MAC", mac)
        opened.clear()
        icon = tray_module.Tray(ctx, lambda: opened.append(1), lambda: None, lambda: None)
        icon.activated.emit(QSystemTrayIcon.ActivationReason.Trigger)  # en la Mac, Qt lo envía al desplegar el menú
        assert len(opened) == expected
        icon.hide()

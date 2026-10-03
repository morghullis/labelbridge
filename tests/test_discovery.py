"""Sugerencias de configuración y avisos de impresoras nuevas."""

from __future__ import annotations

from labelbridge.config import PrinterProfile
from labelbridge.discovery import DiscoveredPrinter


def test_suggested_settings():
    zebra = DiscoveredPrinter(ip="10.0.0.5", model="Zebra ZD421-203dpi ZPL", ports=[9100, 631])
    assert zebra.is_zpl and zebra.suggested() == {"protocol": "raw", "port": 9100, "language": "zpl"}
    laser = DiscoveredPrinter(ip="10.0.0.6", model="HP LaserJet", ports=[9100, 631], ipp_path="/ipp/print")
    assert laser.suggested()["protocol"] == "ipp"
    raw_only = DiscoveredPrinter(ip="10.0.0.7", ports=[9100])
    assert raw_only.suggested()["language"] == "pdf"


def test_network_watcher_announces_only_new_printers(core):
    from labelbridge.services import NetworkWatcher

    core.cfg.data.printers.append(PrinterProfile(alias="zebra", kind="network", host="10.0.0.5"))
    watcher = NetworkWatcher(core.cfg, core.events)
    found = [DiscoveredPrinter(ip="10.0.0.5", ports=[9100]), DiscoveredPrinter(ip="10.0.0.9", name="Brother", ports=[631])]
    watcher.publish(found)
    watcher.publish(found)
    notices = [data for kind, data in core.received if kind == "notify"]
    assert len(notices) == 1 and "10.0.0.9" in notices[0]["message"] and "10.0.0.5" not in notices[0]["message"]
    assert any(kind == "discovery" for kind, _ in core.received)

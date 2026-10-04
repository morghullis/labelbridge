"""Actualizaciones: comparación de versiones, manifiesto, descarga verificada y aviso."""

from __future__ import annotations

import hashlib
import http.server
import json
import threading

import pytest

from labelbridge import __version__, updater


def test_version_comparison():
    assert updater.is_newer("1.10.0", "1.9.9")
    assert updater.is_newer("2.0", "1.99.99")
    assert not updater.is_newer("1.0.2", "1.0.2")
    assert not updater.is_newer("1.0.1", "1.0.2")
    assert updater.parse_version("v1.2.3-beta") == (1, 2, 3)


@pytest.fixture()
def update_server(tmp_path):
    installer = b"MZ" + b"\x00" * 200_000
    state = {"installer": installer, "version": "99.0.0"}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            port = self.server.server_address[1]
            if self.path.startswith("/latest.json"):
                body = json.dumps(
                    {
                        "version": state["version"],
                        "published_at": "2026-10-04T18:00:00Z",
                        "notes": "- Guía con capturas\n- Actualizaciones automáticas",
                        "installer": {
                            "url": f"http://127.0.0.1:{port}/LabelBridge-Setup.exe",
                            "size": len(installer),
                            "sha256": hashlib.sha256(installer).hexdigest(),
                        },
                    }
                ).encode()
            else:
                body = state["installer"]
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/latest.json", state
    server.shutdown()


def test_download_is_verified(update_server, tmp_path):
    url, state = update_server
    info = updater.check_for_update(url)
    assert info is not None and info.version == "99.0.0"
    assert "Guía con capturas" in updater.notes_to_html(info.notes)
    seen = []
    path = updater.download(info, lambda done, total: seen.append((done, total)), folder=tmp_path)
    assert path.read_bytes() == state["installer"]
    assert seen[-1] == (len(state["installer"]), len(state["installer"]))

    state["installer"] = b"MZ" + b"\x01" * 200_000  # alguien alteró el archivo publicado
    with pytest.raises(updater.UpdateError, match="SHA-256"):
        updater.download(info, folder=tmp_path / "otra")
    assert not any((tmp_path / "otra").glob("*.exe"))


def test_no_update_when_current(update_server):
    url, state = update_server
    state["version"] = __version__
    assert updater.check_for_update(url) is None


def test_manifest_requires_https(monkeypatch):
    from labelbridge import net

    payload = {"version": "99.0.0", "installer": {"url": "http://evil.example/x.exe"}}
    monkeypatch.setattr(net, "request", lambda *a, **k: net.Response(200, json.dumps(payload).encode(), "x"))
    with pytest.raises(updater.UpdateError, match="segura"):
        updater.fetch_manifest("https://morghullis.github.io/labelbridge-updates/latest.json")


def test_service_announces_new_versions(core, update_server, monkeypatch):
    url, _ = update_server
    monkeypatch.setattr(updater, "MANIFEST_URL", url)
    monkeypatch.setattr(updater, "check_for_update", lambda u=url: updater.UpdateInfo.from_json(
        json.dumps({"version": "99.0.0", "url": "https://x/y.exe"})
    ))
    service = updater.UpdateService(core.cfg, core.events)
    info = service.check_now()
    assert info.version == "99.0.0" and service.available is info

    stored = updater.UpdateInfo(version=__version__, url="https://x/y.exe", notes="- algo")
    assert updater.pending_whats_new({"update:installed": stored.to_json()}.get).version == __version__
    assert updater.pending_whats_new({"update:installed": ""}.get) is None


def test_update_dialog_builds(core):
    from labelbridge.services import Services
    from labelbridge.ui.context import AppContext, QtBridge
    from labelbridge.ui.update_dialog import UpdateDialog, WhatsNewDialog

    core.cfg.data.local_api.enabled = False
    services = Services(core.cfg, core.secrets, core.store, core.engine, core.events)
    ctx = AppContext(core.cfg, core.secrets, core.store, core.engine, services, core.events, QtBridge(core.events, core.cfg))
    info = updater.UpdateInfo(version="99.0.0", url="https://x/y.exe", size=35_000_000, notes="- Nueva guía")
    dialog = UpdateDialog(None, ctx, info, lambda: None)
    dialog._skip()
    assert core.cfg.data.general.skipped_version == "99.0.0"
    WhatsNewDialog(None, info).close()

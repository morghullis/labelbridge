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
                        updater.INSTALLER_KEY: {  # el de este sistema (Windows o Mac)
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

    payload = {"version": "99.0.0", updater.INSTALLER_KEY: {"url": "http://evil.example/x.exe"}}
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


def test_apply_script_waits_installs_and_reopens(tmp_path):
    from pathlib import Path

    installer = Path(r"C:\Users\José O'Neil\AppData\Local\Temp\LabelBridge-actualizacion\LabelBridge-Setup-9.9.9.exe")
    app = Path(r"C:\Users\José O'Neil\AppData\Local\Programs\LabelBridge\LabelBridge.exe")
    logs = Path(r"C:\Users\José O'Neil\AppData\Local\LabelBridge\logs")
    script = updater.apply_script(installer, app, 4321, logs)
    assert "$appPid = 4321" in script
    assert "'C:\\Users\\José O''Neil\\AppData\\Local\\Programs\\LabelBridge\\LabelBridge.exe'" in script  # comillas escapadas
    assert "WaitForExit(120000)" in script and "Stop-Process -Id $appPid -Force" in script  # espera y, si no, cierra
    assert "'/SILENT', '/SUPPRESSMSGBOXES', '/NORESTART', '/NOCANCEL'" in script
    assert "instalador.log" in script and "actualizacion.log" in script  # deja registro de todo
    assert "Start-Process -FilePath $app -ArgumentList '--updated'" in script  # vuelve a abrir la app siempre
    assert "@@" not in script


def test_failed_update_is_detected():
    newer = updater.UpdateInfo(version="99.0.0", url="https://x/y.exe")
    current = updater.UpdateInfo(version=__version__, url="https://x/y.exe")
    assert updater.failed_update({"update:installed": newer.to_json()}.get).version == "99.0.0"
    assert updater.pending_whats_new({"update:installed": newer.to_json()}.get) is None
    assert updater.failed_update({"update:installed": current.to_json()}.get) is None
    assert updater.failed_update({"update:installed": ""}.get) is None


def test_notes_follow_the_app_language(update_server):
    from labelbridge import i18n

    url, _ = update_server
    info = updater.check_for_update(url)
    assert info.localized_notes() == info.notes  # el manifiesto de prueba no trae notas en inglés
    info.notes_en = "- Screenshots in the guide"
    i18n.set_language("en")
    try:
        assert info.localized_notes() == "- Screenshots in the guide"
        assert updater.UpdateInfo.from_json(info.to_json()).localized_notes() == "- Screenshots in the guide"
    finally:
        i18n.set_language("es")
    assert info.localized_notes() == info.notes


def test_every_release_has_notes_in_both_languages():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    from publish_update import changelog_notes

    spanish, english = changelog_notes(__version__), changelog_notes(__version__, "CHANGELOG.en.md")
    assert spanish.startswith("- ") and english.startswith("- ")
    assert spanish.count("\n- ") == english.count("\n- "), "las notas en inglés no tienen los mismos puntos"


def test_download_page_lists_every_version_from_1_5(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    import publish_update

    archive = tmp_path / "archive"
    archive.mkdir()
    for version in ("1.4.0", "1.5.2", "1.6.0", __version__):  # la 1.4 no se ofrece; la actual no se repite
        (archive / f"LabelBridge-Setup-{version}.exe").write_bytes(b"MZ" + version.encode())
    (archive / "releases.json").write_text(json.dumps({"1.6.0": "2026-10-05T01:41:31Z"}), encoding="utf-8")
    installer = tmp_path / f"LabelBridge-Setup-{__version__}.exe"
    installer.write_bytes(b"MZ actual")
    out = tmp_path / "site"
    publish_update.build_site(out, archive, installer)
    versions = json.loads((out / "versions.json").read_text(encoding="utf-8"))
    assert [v["version"] for v in versions] == [__version__, "1.6.0", "1.5.2"]
    assert versions[1]["published_at"] == "2026-10-05T01:41:31Z" and versions[1]["url"].endswith("Setup-1.6.0.exe")
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "Todas las versiones" in page and "LabelBridge-Setup-1.5.2.exe" in page and "1.4.0" not in page
    assert "All versions" in (out / "en.html").read_text(encoding="utf-8")
    assert (out / "LabelBridge-Setup-1.6.0.exe").exists() and not (out / "LabelBridge-Setup-1.4.0.exe").exists()

    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)

        class Done:
            returncode = 0
            stdout = json.dumps([{"tagName": f"v{__version__}", "publishedAt": "x"},
                                 {"tagName": "v1.7.1", "publishedAt": "2026-10-05T02:49:17Z"},
                                 {"tagName": "v1.4.0", "publishedAt": "y"}])
            stderr = ""
        return Done()

    monkeypatch.setattr(publish_update.subprocess, "run", fake_run)
    publish_update.fetch_archive(tmp_path / "fetched", __version__)
    downloads = [c for c in calls if c[:3] == ["gh", "release", "download"]]
    assert [c[3] for c in downloads] == ["v1.7.1"]  # ni la actual ni las de antes de la 1.5
    assert json.loads((tmp_path / "fetched" / "releases.json").read_text())["1.7.1"] == "2026-10-05T02:49:17Z"


def test_updates_without_a_fingerprint_are_refused(update_server, tmp_path):
    url, _state = update_server
    info = updater.check_for_update(url)
    info.sha256 = ""
    with pytest.raises(updater.UpdateError, match="SHA-256"):
        updater.download(info, folder=tmp_path)
    assert not any(tmp_path.iterdir())


def test_each_system_takes_its_own_installer(monkeypatch):
    from labelbridge import net

    payload = {"version": "99.0.0",
               "installer": {"url": "https://example.com/LabelBridge-Setup-99.0.0.exe", "sha256": "a" * 64},
               "installer_mac": {"url": "https://example.com/LabelBridge-99.0.0-mac.dmg", "sha256": "b" * 64}}
    monkeypatch.setattr(net, "request", lambda *a, **k: net.Response(200, json.dumps(payload).encode(), "x"))
    monkeypatch.setattr(updater, "INSTALLER_KEY", "installer")
    assert updater.fetch_manifest("https://example.com/latest.json").url.endswith(".exe")
    monkeypatch.setattr(updater, "INSTALLER_KEY", "installer_mac")
    assert updater.fetch_manifest("https://example.com/latest.json").url.endswith("-mac.dmg")
    del payload["installer_mac"]  # una versión publicada sin .dmg: la app de Mac no avisa de nada
    with pytest.raises(updater.NotForThisSystem):
        updater.fetch_manifest("https://example.com/latest.json")
    assert updater.check_for_update("https://example.com/latest.json") is None


def test_mac_apply_script_quotes_paths_and_keeps_the_old_app(tmp_path):
    import shlex
    import subprocess
    import sys
    from pathlib import PurePosixPath as MacPath  # rutas de Mac también al probar en Windows

    dmg = MacPath("/Users/José O'Neil/Library/Caches/LabelBridge-9.9.9-mac.dmg")
    app = MacPath("/Applications/Label Bridge.app")
    script = updater.mac_apply_script(dmg, app, 4321, MacPath("/Users/José O'Neil/Library/Application Support/LabelBridge/logs"))
    assert shlex.quote(str(dmg)) in script and shlex.quote(str(app) + ".anterior") in script
    assert "kill -0 4321" in script and "open " + shlex.quote(str(app)) + " --args --updated" in script
    assert script.index("mv " + shlex.quote(str(app))) < script.index("ditto")  # la anterior se aparta antes de copiar
    assert "open " + shlex.quote(str(dmg)) in script  # si no se pudo, queda el .dmg abierto para arrastrarla a mano
    if sys.platform != "win32":  # en Windows «bash» puede ser el de WSL sin Linux instalado
        path = tmp_path / "aplicar.sh"
        path.write_text(script, encoding="utf-8")
        assert subprocess.run(["bash", "-n", str(path)], check=False).returncode == 0  # sintaxis válida


def test_download_page_offers_the_mac_app(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    import publish_update

    archive = tmp_path / "archive"
    archive.mkdir()
    for version in ("1.6.0", "1.7.0", "1.7.1"):  # anteriores a la actual (en la prueba, dos con .dmg)
        (archive / f"LabelBridge-Setup-{version}.exe").write_bytes(b"MZ" + version.encode())
    for version in ("1.7.0", "1.7.1"):
        (archive / f"LabelBridge-{version}-mac.dmg").write_bytes(b"dmg" + version.encode())
    installer = tmp_path / f"LabelBridge-Setup-{__version__}.exe"
    installer.write_bytes(b"MZ actual")
    (tmp_path / f"LabelBridge-{__version__}-mac.dmg").write_bytes(b"dmg actual")
    out = tmp_path / "site"
    manifest = publish_update.build_site(out, archive, installer)
    assert manifest["installer_mac"]["url"].endswith(f"LabelBridge-{__version__}-mac.dmg")
    assert manifest["installer_mac"]["sha256"] == hashlib.sha256(b"dmg actual").hexdigest()
    versions = {v["version"]: v for v in json.loads((out / "versions.json").read_text(encoding="utf-8"))}
    assert versions["1.7.0"]["mac"]["url"].endswith("LabelBridge-1.7.0-mac.dmg") and "mac" not in versions["1.6.0"]
    page = (out / "index.html").read_text(encoding="utf-8")
    assert "Descargar para Mac" in page and "Descargar para Windows" in page and "Abrir igualmente" in page
    assert "Download for Mac" in (out / "en.html").read_text(encoding="utf-8")
    assert (out / f"LabelBridge-{__version__}-mac.dmg").exists() and (out / "LabelBridge-1.7.1-mac.dmg").exists()

    (tmp_path / f"LabelBridge-{__version__}-mac.dmg").unlink()  # si la compilación de Mac falló, se publica Windows igual
    manifest = publish_update.build_site(out, archive, installer)
    assert "installer_mac" not in manifest and "installer" in manifest


def test_old_mac_downloads_are_capped(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    import publish_update

    monkeypatch.setattr(publish_update, "ARCHIVE_MAX_MAC", 2)
    for version in ("1.9.0", "1.9.1", "1.9.2"):
        (tmp_path / f"LabelBridge-Setup-{version}.exe").write_bytes(b"MZ")
        (tmp_path / f"LabelBridge-{version}-mac.dmg").write_bytes(b"dmg")
    entries = publish_update.archived(tmp_path, "9.9.9")
    assert [e["version"] for e in entries if e["mac"]] == ["1.9.2"]  # la actual + 1 anterior de Mac
    assert [e["version"] for e in entries] == ["1.9.2", "1.9.1", "1.9.0"]  # las de Windows siguen todas


def test_a_cut_download_resumes_where_it_stopped(tmp_path):
    installer = b"MZ" + bytes(range(256)) * 2000
    requests: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self):  # noqa: N802
            ranged = self.headers.get("Range")
            requests.append(ranged or "")
            if ranged:  # el segundo intento pide lo que falta
                start = int(ranged.split("=")[1].rstrip("-"))
                body = installer[start:]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(installer) - 1}/{len(installer)}")
            else:  # el primero se corta a la mitad
                body = installer[: len(installer) // 2]
                self.send_response(200)
            self.send_header("Content-Length", str(len(body) if ranged else len(installer)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.close_connection = True

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        info = updater.UpdateInfo("99.0.0", f"http://127.0.0.1:{server.server_address[1]}/LabelBridge-Setup.exe",
                                  len(installer), hashlib.sha256(installer).hexdigest())
        cancel = threading.Event()
        path = updater.download(info, folder=tmp_path, cancel=cancel)
        assert path.read_bytes() == installer
        assert requests == ["", f"bytes={len(installer) // 2}-"]
    finally:
        server.shutdown()


def test_an_antivirus_block_is_explained_not_shown_as_a_code(tmp_path, monkeypatch):
    def blocked(*args, **kwargs):
        error = OSError(22, "La operación no se completó correctamente porque el archivo contiene un virus")
        error.winerror = 225
        raise error

    monkeypatch.setattr(updater, "_download_into", blocked)
    info = updater.UpdateInfo("99.0.0", "http://127.0.0.1:9/LabelBridge-Setup.exe", 10, "a" * 64)
    with pytest.raises(updater.BlockedByAntivirus, match="Historial de protección"):
        updater.download(info, folder=tmp_path)
    assert not list(tmp_path.glob("*.part"))

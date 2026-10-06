"""Funcionar siempre: el equipo no se duerme solo mientras LabelBridge está abierto, se pone al día al volver de una
suspensión, en la Mac se vuelve a abrir si se cierra por un error, y se actualiza sola en un momento tranquilo."""

from __future__ import annotations

import pytest

from labelbridge.system import autostart, power


class FakeProcess:
    def __init__(self, args, **_kwargs) -> None:
        self.args = args
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True


def test_the_mac_stays_awake_while_the_app_is_open(monkeypatch):
    launched: list[FakeProcess] = []
    monkeypatch.setattr(power.sys, "platform", "darwin")
    monkeypatch.setattr(power.subprocess, "Popen", lambda args, **kw: launched.append(FakeProcess(args)) or launched[-1])
    keep = power.KeepAwake()
    assert keep.apply(True) and launched[0].args[:4] == ["/usr/bin/caffeinate", "-i", "-s", "-w"]
    assert keep.apply(True) and len(launched) == 1  # una sola vez
    assert keep.apply(False) is False and launched[0].terminated  # apagado en Ajustes: vuelve a dormirse sola


def test_windows_asks_not_to_sleep_without_keeping_the_screen_on(monkeypatch):
    import ctypes

    calls: list[int] = []

    class Kernel:
        @staticmethod
        def SetThreadExecutionState(flags):  # noqa: N802
            calls.append(flags)
            return 1

    monkeypatch.setattr(power.sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", type("WinDLL", (), {"kernel32": Kernel})(), raising=False)
    keep = power.KeepAwake()
    assert keep.apply(True)
    assert calls == [power.ES_CONTINUOUS | power.ES_SYSTEM_REQUIRED]  # sin ES_DISPLAY_REQUIRED: la pantalla se apaga
    keep.release()
    assert calls[-1] == power.ES_CONTINUOUS


def test_waking_up_from_sleep_catches_up_at_once():
    gaps: list[float] = []
    watcher = power.ResumeWatcher(gaps.append, every=30)
    start = watcher.last
    assert not watcher.check(start + 31)  # una vuelta normal
    assert watcher.check(start + 31 + 3600)  # una hora suspendido
    assert gaps and gaps[0] == pytest.approx(3600)


def test_the_mac_reopens_the_app_after_a_crash_but_not_after_quitting(tmp_path):
    agent = autostart.mac_agent(tmp_path / "LabelBridge.app")
    assert agent["KeepAlive"] == {"SuccessfulExit": False} and agent["RunAtLoad"] is True


# --- actualizar sola ---------------------------------------------------------------------------

@pytest.fixture()
def auto(core, monkeypatch):
    from test_orders import _ui_context

    from labelbridge.ui import auto_update
    from labelbridge.updater import UpdateInfo

    ctx = _ui_context(core)
    events: dict = {"downloads": [], "installs": [], "quits": 0, "announced": []}

    class Window:
        def quit_app(self) -> None:
            events["quits"] += 1

        def announce_update(self, info, prompt=True, automatic=True) -> None:
            events["announced"].append((info.version, automatic))

    monkeypatch.setattr(auto_update.updater, "can_install", lambda: True)
    monkeypatch.setattr(auto_update.updater, "download",
                        lambda info, *a, **k: events["downloads"].append(info.version) or "/tmp/LabelBridge-Setup.exe")
    monkeypatch.setattr(auto_update.updater, "install_and_restart", lambda path: events["installs"].append(str(path)))
    monkeypatch.setattr(auto_update, "run_async", lambda fn, done, failed=None: done(fn()))
    updater = auto_update.AutoUpdater.__new__(auto_update.AutoUpdater)
    auto_update.QObject.__init__(updater)
    updater.window, updater.ctx, updater.info, updater.installer = Window(), ctx, None, None
    updater.timer = auto_update.QTimer(updater)
    info = UpdateInfo(version="99.0.0", url="https://x/y.exe", size=1, notes="- x", sha256="0" * 64)
    return updater, ctx, events, info


def test_a_new_version_installs_itself_when_nothing_is_happening(auto):
    updater, ctx, events, info = auto
    assert updater.offer(info) and events["downloads"] == ["99.0.0"]
    assert events["installs"] and events["quits"] == 1
    assert "99.0.0" in ctx.store.kv_get("update:installed")  # al abrirse: las novedades


def test_it_waits_while_printing_and_respects_the_setting(auto, monkeypatch):
    updater, ctx, events, info = auto
    monkeypatch.setattr(ctx.store, "working_now", lambda now=None: 1)  # una guía imprimiéndose
    assert updater.offer(info) and events["installs"] == [] and updater.timer.isActive()
    monkeypatch.setattr(ctx.store, "working_now", lambda now=None: 0)
    assert updater.try_install() and events["quits"] == 1
    updater.info = None
    ctx.cfg.data.general.auto_install_updates = False
    assert not updater.offer(info)  # apagado en Ajustes: se pregunta como antes
    ctx.cfg.data.general.auto_install_updates = True
    ctx.store.kv_set("update:auto_failed", "99.0.0")
    assert not updater.offer(info)  # esa versión ya falló sola: decide la persona


def test_jobs_waiting_for_a_retry_later_do_not_block_updates(core):
    from labelbridge.models import JobRequest

    job, _ = core.store.add(JobRequest(external_id="x", title="Guía", content=b"^XA^XZ", format="zpl"), "shopify")
    assert core.store.working_now() == 1  # lista para imprimirse ya
    core.store._conn.execute("UPDATE jobs SET next_attempt_at=? WHERE id=?", (job["created_at"] + 3600, job["id"]))
    assert core.store.working_now(now=job["created_at"]) == 0  # espera un reintento en una hora

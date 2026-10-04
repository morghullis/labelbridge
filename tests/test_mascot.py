"""La máscara animada y el asistente flotante: dibujo, animación, reacciones a la conversación y el bubble en todas
las pantallas."""

from __future__ import annotations

import random

import pytest
from PySide6.QtCore import QObject, QRectF, Signal
from PySide6.QtGui import QColor, QImage, QPainter

from labelbridge.ui import mascot as m

from conftest import wait_for
from test_assistant import _context


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def run(self, mascot: m.Mascot, seconds: float, fps: int = 30) -> list[m.Frame]:
        frames = []
        for _ in range(int(seconds * fps)):
            self.now += 1 / fps
            mascot.step()
            frames.append(mascot.frame)
        return frames


def _mascot(reduced: bool = False) -> tuple[m.Mascot, Clock]:
    clock = Clock()
    return m.Mascot(reduced_motion=reduced, clock=clock, rng=random.Random(7)), clock


# --- dibujo ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("key", sorted(m.EXPRESSIONS))
def test_every_expression_paints_at_every_size(qapp, key):
    for size, badge in ((200, False), (60, True), (44, True), (28, True)):
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(QColor(0, 0, 0, 0))
        painter = QPainter(image)
        m.paint_mascot(painter, QRectF(0, 0, size, size), m.still_frame(key), badge=badge)
        painter.end()
        center = image.pixelColor(size // 2, int(size * 0.62))
        assert center.alpha() == 255 and center.red() > center.blue()  # oro en la cara, no un hueco
    assert not m.mascot_pixmap(key, 28).isNull()


def test_small_sizes_drop_fine_detail(qapp):
    frame = m.still_frame("neutral")
    image = QImage(60, 60, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    m.paint_mascot(painter, QRectF(0, 0, 60, 60), frame, badge=True)
    painter.end()
    assert frame.detail < 0.35  # sin remaches, facetas ni marcas: trazos más gruesos
    big = QImage(240, 240, QImage.Format.Format_ARGB32)
    painter = QPainter(big)
    m.paint_mascot(painter, QRectF(0, 0, 240, 240), frame)
    painter.end()
    assert frame.detail == 1.0


# --- animación ---------------------------------------------------------------------------------------

def test_expressions_blend_and_reactions_wear_off(qapp):
    mascot, clock = _mascot()
    mascot.set_base("thinking")
    clock.run(mascot, 1.0)
    thinking = m.EXPRESSIONS["thinking"].pose
    assert abs(mascot.frame.pose.brow_l - thinking.brow_l) < 0.05 and mascot.frame.fx == "dots"
    assert mascot.frame.pose.gaze_y < -0.6  # mira hacia arriba al pensar
    lit = [glow for _, glow in mascot.frame.gems]
    clock.run(mascot, 0.3)
    assert lit != [glow for _, glow in mascot.frame.gems]  # la corona hace de indicador de carga

    mascot.react("happy", 1.0)
    assert mascot.expression == "happy"
    frames = clock.run(mascot, 0.5)
    assert min(f.bob for f in frames) < -5  # el saltito
    clock.run(mascot, 0.7)
    assert mascot.expression == "thinking"  # la reacción pasó y vuelve la base


def test_blinks_and_sleep_badge(qapp):
    mascot, clock = _mascot()
    frames = clock.run(mascot, 8.0)
    assert any(f.blink > 0.8 for f in frames)  # parpadea sola
    mascot.set_base("sleepy")
    clock.run(mascot, 2.0)
    assert mascot.frame.fx == "zzz" and mascot.frame.pose.eye_open < 0.3
    clock.run(mascot, 8.0)
    assert mascot.frame.fx_alpha < 0.05  # la «z» solo al quedarse dormida


def test_talking_moves_the_mouth(qapp):
    mascot, clock = _mascot()
    mascot.talk(1.5)
    mouths = [f.pose.mouth_open for f in clock.run(mascot, 1.4)]
    assert max(mouths) > 0.4 and min(mouths) < 0.25
    clock.run(mascot, 1.0)
    assert mascot.expression == "neutral" and mascot.frame.pose.mouth_open < 0.05


def test_reduced_motion_keeps_faces_but_no_gestures(qapp):
    mascot, clock = _mascot(reduced=True)
    mascot.react("happy", 1.0)
    frames = clock.run(mascot, 0.6)
    assert all(f.bob == 0 and f.squash == 0 for f in frames)
    assert frames[0].pose.smile == m.EXPRESSIONS["happy"].pose.smile  # cambia de cara al instante


def test_idle_mask_stops_repainting(qapp):
    mascot, clock = _mascot()
    clock.run(mascot, 1.5)
    mascot._next_blink = clock.now + 60
    mascot._next_glance = clock.now + 60
    clock.run(mascot, 0.5)
    clock.now += 0.03
    assert mascot.step() == 0  # en reposo no hay nada que redibujar


def test_reduced_motion_follows_the_system(monkeypatch):
    from labelbridge.system import accessibility

    monkeypatch.setenv("LABELBRIDGE_REDUCED_MOTION", "1")
    assert accessibility.reduced_motion() is True
    monkeypatch.delenv("LABELBRIDGE_REDUCED_MOTION")
    assert accessibility.reduced_motion() is False  # fuera de Windows, salvo que se pida


# --- la cara según la conversación --------------------------------------------------------------

class FakeChat(QObject):
    mood = Signal(str, object)
    changed = Signal()

    def __init__(self, connected: bool = True) -> None:
        super().__init__()
        self.connected = connected
        chat = self

        class Conversation:
            def has_key(self) -> bool:
                return chat.connected

        self.conversation = Conversation()


def test_director_turns_the_conversation_into_faces(qapp):
    from labelbridge.ui.assistant_dock import MoodDirector, is_question, talk_seconds

    mascot, clock = _mascot()
    chat = FakeChat()
    opened = {"value": True}
    director = MoodDirector(mascot, chat, lambda: opened["value"])
    unread: list[int] = []
    director.unread_changed.connect(unread.append)

    chat.mood.emit("typing", True)
    assert mascot.base == "listening"
    chat.mood.emit("sent", None)
    assert mascot.base == "thinking"
    chat.mood.emit("tool", "add_printer")
    assert mascot.base == "working"
    chat.mood.emit("tool_done", True)
    assert mascot.base == "thinking"
    chat.mood.emit("reply", "Listo, agregué la impresora. ¿Imprimo una etiqueta de prueba?")
    assert mascot.expression == "talking"
    chat.mood.emit("finished", None)
    director._after_reply()
    assert mascot.base == "curious"  # terminó con una pregunta
    chat.mood.emit("typing", True)
    assert mascot.base == "listening"
    chat.mood.emit("typing", False)
    chat.mood.emit("sent", None)
    chat.mood.emit("tool", "add_printer")
    chat.mood.emit("asking", True)
    assert mascot.base == "curious"  # ventana de permiso abierta, en pleno turno
    chat.mood.emit("asking", False)
    assert mascot.base == "working"
    chat.mood.emit("finished", None)

    chat.mood.emit("sent", None)
    chat.mood.emit("error", "Sin conexión")
    assert mascot.expression == "worried" and mascot.base == "neutral"
    chat.mood.emit("refusal", "No puedo ayudar con eso")
    assert mascot.expression == "no"
    chat.mood.emit("secret", None)
    assert mascot.expression == "wink"
    chat.mood.emit("sent", None)
    assert mascot.expression == "wink"  # el guiño del secreto no se corta al enviar
    chat.mood.emit("reply", "¿Seguimos?")
    assert director._after.isActive()
    chat.mood.emit("sent", None)
    assert not director._after.isActive() and not director._question  # nada viejo deja la cara en «Pregunta»
    chat.mood.emit("stopped", None)
    assert mascot.expression == "surprised"

    opened["value"] = False  # con el chat cerrado, una respuesta deja aviso y salta
    chat.mood.emit("reply", "Ya está.")
    assert unread == [1] and mascot._motion is not None
    director.opened()
    assert unread == [1, 0] and mascot.expression == "hello"
    director.hover(False)  # al abrir, el bubble se oculta y Qt avisa que el ratón «salió»
    assert mascot.expression == "hello"  # el saludo de bienvenida sigue

    chat.connected = False
    director.update_base()
    assert mascot.base == "sleepy"  # sin IA conectada todavía: descansa
    assert is_question("¿Quieres que siga?") and not is_question("Listo.")
    assert talk_seconds("x") == 0.8 and talk_seconds("x" * 1000) == 3.0


def test_director_sleeps_after_a_while_and_wakes_on_hover(qapp):
    from labelbridge.ui.assistant_dock import MoodDirector

    mascot, _clock = _mascot()
    director = MoodDirector(mascot, FakeChat(), lambda: False)
    director._fall_asleep()
    assert mascot.base == "sleepy"
    director.hover(True)
    assert mascot.base == "neutral" and mascot.expression == "hello"
    director.hover(False)
    assert mascot.expression == "neutral"


# --- el bubble en la ventana ------------------------------------------------------------------------

def test_floating_assistant_on_every_page(core):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtWidgets import QApplication

    from labelbridge.ui.main_window import NAV, MainWindow
    from labelbridge.ui.theme import STYLESHEET

    QApplication.instance().setStyleSheet(STYLESHEET)
    window = MainWindow(_context(core))
    window.resize(1180, 760)
    window.show()
    QApplication.processEvents()
    dock = window.assistant
    assert "assistant" not in window.pages and all(key != "assistant" for key, _, _ in NAV)
    for key, _, _ in NAV:  # el bubble está en todas las pantallas, abajo a la derecha
        window.navigate(key)
        QApplication.processEvents()
        assert dock.launcher.isVisible()
        launcher = dock.launcher.geometry()
        area = window.stack.geometry()
        assert area.contains(launcher.center()) and launcher.right() > area.right() - 120
        assert launcher.bottom() > area.bottom() - 120

    window.navigate("assistant")  # la guía y otras pantallas lo abren así
    assert dock.is_open and wait_for(lambda: (QApplication.processEvents(), dock.panel.isVisible())[1])
    assert not dock.launcher.isVisible()
    QApplication.processEvents()
    panel = dock.panel.card.geometry().translated(dock.panel.pos())
    assert window.stack.geometry().contains(panel)  # cabe dentro de la ventana
    dock.panel.minimize.emit()  # Esc o el botón de minimizar
    assert wait_for(lambda: (QApplication.processEvents(), dock.launcher.isVisible())[1])
    assert not dock.is_open and not dock.panel.isVisible()

    window.toggle_assistant()  # F1
    assert dock.is_open
    window.toggle_assistant()
    assert wait_for(lambda: (QApplication.processEvents(), not dock.panel.isVisible())[1])

    window.open_assistant(connect=True, provider="gemini")
    QApplication.processEvents()
    assert dock.page.key_card.isVisible() and dock.page.provider_combo.currentData() == "gemini"

    dock.director._set_unread(2)
    assert "2" in dock.launcher.toolTip()
    clicks: list[bool] = []
    dock.launcher.clicked.connect(lambda: clicks.append(True))
    QApplication.sendEvent(dock.launcher, QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
    assert clicks == [True]  # con el teclado también se abre
    window.quitting = True
    window.close()


def test_a_real_chat_turn_drives_the_mask(core):
    from PySide6.QtWidgets import QApplication, QWidget

    from labelbridge.secret_store import ANTHROPIC_API_KEY
    from labelbridge.ui.assistant_dock import AssistantDock
    from test_assistant import FakeAPI, _message

    core.secrets.set(ANTHROPIC_API_KEY, "sk-ant-prueba")
    api = FakeAPI([_message([{"type": "text", "text": "Listo, ya quedó conectada. ¿Imprimo una prueba?"}], "end_turn")])
    host = QWidget()
    host.resize(1000, 700)
    area = QWidget(host)
    area.setGeometry(220, 0, 780, 700)
    dock = AssistantDock(_context(core), host, area, connector=api.connector)
    host.show()
    seen: list[str] = []
    dock.page.mood.connect(lambda kind, _data: seen.append(kind))
    dock.open()
    dock.page.send_text("conecta mi tienda")
    assert dock.mascot.base == "thinking"  # nada más enviar
    assert wait_for(lambda: (QApplication.processEvents(), not dock.page.busy)[1])
    assert seen[:1] == ["sent"] and "reply" in seen and "finished" in seen
    assert dock.mascot.expression == "talking"  # «habla» al llegar la respuesta
    dock.director._after_reply()
    assert dock.mascot.base == "curious"  # y queda esperando la respuesta a su pregunta
    assert "Lista" in dock.page.status_text()
    host.close()


def test_long_waits_and_reduced_motion_cost_nothing(qapp):
    mascot, clock = _mascot()
    mascot.set_base("listening")  # p. ej. un borrador sin enviar
    clock.run(mascot, 2.0)
    assert mascot.step() >= 1  # las gemas laten un rato…
    clock.run(mascot, 25.0)
    mascot._next_blink = mascot._next_glance = clock.now + 60
    clock.run(mascot, 0.5)
    assert mascot.step() == 0  # …y después se quedan quietas

    calm, clock = _mascot(reduced=True)
    calm.set_base("thinking")
    clock.run(calm, 1.0)
    calm._next_blink = clock.now + 60
    assert calm.step() == 0 and calm.frame.gems == m.gem_lights_still("think")  # luces fijas, sin carga


def test_connect_card_comes_back_when_the_ai_changes(core):
    from labelbridge.secret_store import ANTHROPIC_API_KEY
    from labelbridge.ui.pages.assistant import AssistantPage

    core.secrets.set(ANTHROPIC_API_KEY, "sk-ant-prueba")
    page = AssistantPage(_context(core))
    assert page.key_box.isHidden()
    page.toggle_connect()
    assert not page.key_box.isHidden()
    page.toggle_connect()
    assert page.key_box.isHidden() and page.key_card.isHidden()  # sin recuadro vacío
    core.cfg.data.assistant.provider = "openai"  # cambia de IA en Ajustes (sin clave guardada)
    core.cfg.save("assistant")
    assert not page.key_card.isHidden() and not page.key_box.isHidden()
    assert "Falta conectar" in page.status_text()

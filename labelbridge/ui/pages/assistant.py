"""El chat del asistente: configura LabelBridge conversando. Funciona con la IA que elija el usuario (Claude, OpenAI,
Gemini o cualquier API compatible con OpenAI) y usa las herramientas de la app.

Vive dentro del panel flotante (ui/assistant_dock.py), que está disponible en todas las pantallas. La señal `mood`
cuenta lo que pasa (escribe, piensa, usa una herramienta, responde…) para que la máscara reaccione.
"""

from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFont, QKeyEvent, QPixmap, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...assistant.agent import Conversation, friendly_error
from ...assistant.providers import PROVIDERS, Endpoint, ProviderError, check_base_url, connect, resolve, valid_model
from ...assistant.secrets_guard import SecretsGuard
from ...assistant.tools import SECRET_KINDS, Toolbox
from ...events import CONFIG_EXTERNAL
from ...i18n import N_, language, tr
from ...secret_store import MQTT_PASSWORD, SHOPIFY_ACCESS_TOKEN, SHOPIFY_CLIENT_SECRET
from ...system.platform import pick
from .. import icons
from ..context import AppContext
from ..mascot import mascot_pixmap
from ..widgets import Card, SecretField, button, copy_to_clipboard, label, repolish, run_async

BUBBLE_MAX = 292  # ancho máximo de una burbuja dentro del panel
AVATAR = 28
WELCOME = N_(
    "¡Hola! Soy el asistente de LabelBridge. Escríbeme con tus palabras qué quieres configurar y lo hago por ti: la "
    "impresora de etiquetas, la conexión con tu tienda de Shopify y la compra e impresión automática de las **guías de "
    "envío de Shopify Shipping**. También reviso que todo funcione y te ayudo si algo no se imprimió.\n\n"
    "Puedes darme varios datos a la vez, por ejemplo: *«Tengo una Zebra por USB y mi tienda es {shop}»*.\n\n"
    "🔒 Antes de cambiar algo te pido permiso, y si hace falta un dato secreto (como el Client secret de Shopify) te "
    "abro una ventana segura: no hace falta escribirlo en el chat."
)
SUGGESTIONS = (  # (lo que dice el botón, lo que se envía)
    (N_("Configúralo todo conmigo"), N_("Configúralo todo conmigo paso a paso")),
    (N_("Imprimir solas las guías de Shopify"), N_("Quiero que se impriman solas las guías de Shopify Shipping")),
    (N_("Conectar mi impresora"), N_("Conecta mi impresora de etiquetas")),
    (N_("¿Por qué no se imprimió un pedido?"), N_("¿Por qué no se imprimió un pedido?")),
)
# Datos secretos que se pueden guardar con el botón del candado (la clave de la IA va en «Conectar IA»).
LOCK_KINDS = dict(SECRET_KINDS)
SECTION_OF_SECRET = {SHOPIFY_CLIENT_SECRET: "shopify", SHOPIFY_ACCESS_TOKEN: "shopify", MQTT_PASSWORD: "mqtt"}


# --- hilo de la interfaz ---------------------------------------------------------------------------

class _Invoker(QObject):
    """Ejecuta funciones en el hilo de la interfaz (las ventanas solo se pueden abrir ahí)."""

    request = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self.request.connect(self._run)

    @Slot(object)
    def _run(self, job: Callable[[], None]) -> None:
        job()

    def call(self, fn: Callable[[], Any]) -> Any:
        if threading.current_thread() is threading.main_thread():
            return fn()
        done = threading.Event()
        box: dict[str, Any] = {}

        def job() -> None:
            try:
                box["value"] = fn()
            except BaseException as exc:
                box["error"] = exc
            finally:
                done.set()

        self.request.emit(job)
        done.wait()
        if "error" in box:
            raise box["error"]
        return box.get("value")


def confirm_action(parent: QWidget, title: str, text: str, accept: str) -> bool:
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Warning)
    box.setWindowTitle(title)
    box.setText(text)
    yes = box.addButton(accept, QMessageBox.ButtonRole.AcceptRole)
    no = box.addButton(tr("Cancelar"), QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(no)  # lo seguro es lo predeterminado
    box.exec()
    return box.clickedButton() is yes


class ApprovalDialog(QDialog):
    """Ventana de permiso para un cambio del asistente. Resultado: "once", "session" o "deny"."""

    def __init__(self, parent: QWidget | None, title: str, text: str) -> None:
        super().__init__(parent)
        self.choice = "deny"  # cerrar la ventana = no permitir
        self.setWindowTitle(title)
        self.setMinimumWidth(540)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        head = QHBoxLayout()
        icon = QLabel()
        icon.setPixmap(mascot_pixmap("curious", 40))
        head.addWidget(icon, 0, Qt.AlignmentFlag.AlignTop)
        head.addWidget(label(tr("El asistente quiere hacer este cambio:"), "CardTitle", wrap=True), 1)
        layout.addLayout(head)
        detail = label(text, wrap=True, selectable=True)
        detail.setStyleSheet(
            "QLabel { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 10px 12px; }"
        )
        layout.addWidget(detail)
        layout.addWidget(label(tr("¿Le das permiso? Puedes permitir solo esto o todos los cambios de esta conversación."),
                               "Muted", wrap=True))
        row = QHBoxLayout()
        deny = button(tr("No permitir"))
        deny.clicked.connect(self.reject)
        session = button(tr("Permitir todo en esta conversación"), None, "ghost")
        session.clicked.connect(lambda: self._done("session"))
        once = button(tr("Permitir"), "check", "primary")
        once.clicked.connect(lambda: self._done("once"))
        row.addWidget(deny)
        row.addStretch(1)
        row.addWidget(session)
        row.addWidget(once)
        layout.addLayout(row)
        deny.setDefault(True)  # Enter no da permiso: hay que pulsar «Permitir»
        deny.setFocus()

    def _done(self, choice: str) -> None:
        self.choice = choice
        self.accept()


def approve_change(parent: QWidget, title: str, text: str) -> str:
    dialog = ApprovalDialog(parent, title, text)
    dialog.exec()
    return dialog.choice


class SecretDialog(QDialog):
    """Ventana segura para pegar un dato secreto: no pasa por el chat."""

    def __init__(self, parent: QWidget | None, title: str, hint: str, kinds: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("Dato secreto"))
        self.setMinimumWidth(480)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        head = QHBoxLayout()
        lock = QLabel()
        lock.setPixmap(icons.pixmap("lock", "#4F46E5", 22))
        head.addWidget(lock)
        self.title = label(title, "CardTitle")
        head.addWidget(self.title, 1)
        layout.addLayout(head)
        self.kind: QComboBox | None = None
        if kinds:
            self.kind = QComboBox()
            for key, (_name, kind_title, _hint) in kinds.items():
                self.kind.addItem(kind_title, key)
            self.kind.currentIndexChanged.connect(lambda _i: self._kind_changed(kinds))
            layout.addWidget(self.kind)
        self.hint = label(hint, "Muted", wrap=True)
        layout.addWidget(self.hint)
        self.field = SecretField(tr("Pega aquí el dato"), copyable=False)
        layout.addWidget(self.field)
        layout.addWidget(
            label(tr(pick(N_("Se guarda en el almacén seguro de Windows de este PC. El asistente no lo ve."),
                          N_("Se guarda en el Llavero de esta Mac. El asistente no lo ve."))), "Small", wrap=True)
        )
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button(tr("Cancelar"))
        cancel.clicked.connect(self.reject)
        self.save_btn = button(tr("Guardar"), "lock", "primary")
        self.save_btn.clicked.connect(self.accept)
        row.addWidget(cancel)
        row.addWidget(self.save_btn)
        layout.addLayout(row)
        self.field.edit.returnPressed.connect(self.accept)
        if kinds:
            self._kind_changed(kinds)

    def _kind_changed(self, kinds: dict) -> None:
        _name, title, hint = kinds[self.kind.currentData()]
        self.title.setText(title)
        self.hint.setText(hint)

    def value(self) -> str:
        return self.field.text().strip()

    @staticmethod
    def ask(parent: QWidget | None, title: str, hint: str) -> str | None:
        dialog = SecretDialog(parent, title, hint)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.value():
            return None
        return dialog.value()


class QtAssistantUI:
    """Lo que las herramientas del asistente piden a la interfaz, siempre en el hilo principal."""

    def __init__(self, page: "AssistantPage") -> None:
        self.page = page
        self.invoker = _Invoker()

    def _asking(self, show: Callable[[], Any]) -> Any:
        def run() -> Any:
            self.page.mood.emit("asking", True)
            try:
                return show()
            finally:
                self.page.mood.emit("asking", False)

        return self.invoker.call(run)

    def confirm(self, title: str, text: str, accept: str) -> bool:
        return bool(self._asking(lambda: confirm_action(self.page.window(), title, text, accept)))

    def approve(self, title: str, text: str) -> str:
        return str(self._asking(lambda: approve_change(self.page.window(), title, text)))

    def ask_secret(self, title: str, hint: str) -> str | None:
        return self._asking(lambda: SecretDialog.ask(self.page.window(), title, hint))

    def open_url(self, url: str) -> None:
        self.invoker.call(lambda: QDesktopServices.openUrl(QUrl(url)))

    def copy_text(self, text: str) -> None:
        self.invoker.call(lambda: copy_to_clipboard(text))

    def config_changed(self, section: str) -> None:
        self.page.ctx.events.emit(CONFIG_EXTERNAL, section)

    # --- control de la pantalla (assistant/screen.py) -------------------------------------------
    def screen_consent(self) -> bool:
        from ..screen_control import ScreenConsentDialog

        return bool(self._asking(lambda: ScreenConsentDialog().exec() == QDialog.DialogCode.Accepted))

    def screen_session(self, active: bool) -> None:
        self.invoker.call(lambda: self.page.screen_session(active))

    def capture_screen(self):
        from ..screen_control import capture_screen

        return self.invoker.call(capture_screen)

    def capture_region(self, rect: tuple[int, int, int, int]) -> bytes:
        from ..screen_control import capture_region

        return self.invoker.call(lambda: capture_region(rect))

    def blocked_areas(self) -> list[tuple[int, int, int, int]]:
        from ..screen_control import blocked_areas

        return self.invoker.call(blocked_areas)

    def approve_screen(self, what: str, reason: str, frame, points: list[tuple[int, int]]) -> str:
        from ..screen_control import ScreenActionDialog

        def ask() -> str:
            dialog = ScreenActionDialog(what, reason, frame, points)
            dialog.exec()
            if dialog.choice == "stop":
                self.page.stop()
            return dialog.choice

        return str(self._asking(ask))


# --- burbujas ----------------------------------------------------------------------------------------

def _document(text: str, markdown: bool, font: QFont) -> QTextDocument:
    """Texto del mensaje con espacio entre párrafos (el Markdown de Qt los deja pegados)."""
    doc = QTextDocument()
    doc.setDefaultFont(font)
    doc.setDocumentMargin(0)
    if not markdown:
        doc.setPlainText(text)
        return doc
    doc.setMarkdown(text)
    block = doc.begin()
    while block.isValid():
        following = block.next()
        fmt = block.blockFormat()
        fmt.setTopMargin(0)
        fmt.setBottomMargin(0 if not following.isValid() else (3 if block.textList() and following.textList() else 9))
        QTextCursor(block).setBlockFormat(fmt)
        block = following
    return doc


def open_safe_link(url: str) -> None:
    """Los enlaces del chat solo abren páginas web (nunca archivos ni programas de este PC)."""
    target = QUrl(url)
    if target.scheme() in ("https", "http"):
        QDesktopServices.openUrl(target)


def _narrow(combo: QComboBox) -> QComboBox:
    """Lista desplegable que cabe en el panel aunque algún texto sea largo."""
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
    combo.setMinimumContentsLength(12)
    return combo


def pick_model(provider, wanted: str, available: list[str]) -> str:
    """El modelo que se usará: el elegido si existe; si no, el primero recomendado que tenga la cuenta."""
    if not available or wanted in available:
        return wanted
    if wanted and wanted != provider.default_model:
        return wanted  # lo escribió el usuario: se respeta (el servidor dirá si no existe)
    for key, _text in provider.models:
        if key in available:
            return key
    return wanted or available[0]


_AVATARS: dict[str, QPixmap] = {}


def _avatar(expression: str = "neutral") -> QLabel:
    """La máscara pequeña junto a cada mensaje del asistente (imagen fija: la viva está en la cabecera)."""
    if expression not in _AVATARS:
        _AVATARS[expression] = mascot_pixmap(expression, AVATAR)
    avatar = QLabel()
    avatar.setFixedSize(AVATAR, AVATAR)
    avatar.setPixmap(_AVATARS[expression])
    return avatar


def bubble(text: str, role: str) -> QWidget:
    """role: user | assistant | error."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(10)
    body = QLabel()
    body.setWordWrap(True)
    body.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    body.setOpenExternalLinks(False)
    body.linkActivated.connect(open_safe_link)
    font = QFont(body.font())
    font.setPixelSize(14)
    body.setFont(font)
    markdown = role != "user"
    doc = _document(text, markdown, font)
    if markdown:
        body.setTextFormat(Qt.TextFormat.RichText)
        body.setText(doc.toHtml())
    else:
        body.setTextFormat(Qt.TextFormat.PlainText)
        body.setText(text)
    styles = {
        "user": "background: #4F46E5; color: white; border: none;",
        "assistant": "background: #F8FAFC; color: #0F172A; border: 1px solid #E2E8F0;",
        "error": "background: #FEF2F2; color: #991B1B; border: 1px solid #FECACA;",
    }
    body.setStyleSheet(f"QLabel {{ {styles[role]} border-radius: 14px; padding: 10px 14px; }}")
    # Tamaño justo para el texto: las burbujas cortas no ocupan toda la fila ni sobra alto.
    width = int(min(BUBBLE_MAX, doc.idealWidth() + 36))
    doc.setTextWidth(width - 30)
    body.setFixedSize(width, int(doc.size().height()) + 22)
    if role == "user":
        layout.addStretch(1)
        layout.addWidget(body)
    else:
        layout.addWidget(_avatar(), 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(body)
        layout.addStretch(1)
    row.body = body  # type: ignore[attr-defined]
    return row


class ActivityRow(QWidget):
    """Lo que el asistente está haciendo en la app (con ✓ o ✗ al terminar)."""

    def __init__(self, text: str, animated: bool = False) -> None:
        super().__init__()
        self.base = text
        layout = QHBoxLayout(self)
        layout.setContentsMargins(40, 0, 0, 0)
        layout.setSpacing(8)
        self.icon = QLabel()
        self.icon.setPixmap(icons.pixmap("refresh", "#6366F1", 14))
        self.text = label(f"{text}…", "Muted")
        self.text.setWordWrap(True)
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        layout.addWidget(self.text, 1)
        self._dots = 0
        self._timer: QTimer | None = None
        if animated:
            self._timer = QTimer(self)
            self._timer.setInterval(420)
            self._timer.timeout.connect(self._tick)
            self._timer.start()

    def _tick(self) -> None:
        self._dots = (self._dots + 1) % 4
        self.text.setText(self.base + "." * self._dots)

    def finish(self, ok: bool, detail: str = "") -> None:
        if self._timer:
            self._timer.stop()
        self.icon.setPixmap(icons.pixmap("check" if ok else "x", "#16A34A" if ok else "#DC2626", 14))
        detail = " ".join(detail.split())
        self.text.setText(self.base if ok else f"{self.base} — {detail[:220]}")


class Composer(QPlainTextEdit):
    """Caja de texto: Enter envía, Mayús+Enter hace un salto de línea."""

    submitted = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)


# --- pantalla -------------------------------------------------------------------------------------

class AssistantPage(QWidget):
    """El chat (sin cabecera: la pone el panel flotante, con la máscara viva)."""

    agent_event = Signal(str, object)
    mood = Signal(str, object)  # lo que pasa en la conversación, para la cara de la máscara
    changed = Signal()  # cambió el estado que muestra la cabecera (listo, trabajando, falta la IA…)

    def __init__(self, ctx: AppContext, connector=None) -> None:
        super().__init__()
        self._built = False
        self.setObjectName("AssistantChat")
        self.ctx = ctx
        self.ui = QtAssistantUI(self)
        self.guard = SecretsGuard(ctx.secrets)
        self.toolbox = Toolbox(ctx, self.ui, self.guard)
        self.connector = connector or connect  # en las pruebas: un servidor simulado
        self.conversation = Conversation(self.toolbox, ctx.secrets, self.connector)
        self.cancel = threading.Event()
        self.busy = False
        self.activity = ""  # lo que hace ahora (para la cabecera)
        self._worker: threading.Thread | None = None
        self._screen_bar = None  # barra de arriba mientras el asistente usa la pantalla
        self._screen_restore = False  # si la ventana estaba a la vista antes de minimizarse
        self.pending_notes: list[str] = []
        self._activities: dict[str, ActivityRow] = {}
        self._thinking: ActivityRow | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # «Conecta tu IA» arriba, con su propio desplazamiento si no cabe (ventanas pequeñas).
        self.key_card = self._key_card()
        holder = QWidget()
        holder.setObjectName("AssistantChat")
        card_layout = QVBoxLayout(holder)
        card_layout.setContentsMargins(12, 12, 12, 0)
        card_layout.addWidget(self.key_card)
        self.key_box = QScrollArea()
        self.key_box.setObjectName("KeyScroll")
        self.key_box.setWidget(holder)
        self.key_box.setWidgetResizable(True)
        self.key_box.setFrameShape(QFrame.Shape.NoFrame)
        self.key_box.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.key_box.setSizeAdjustPolicy(QScrollArea.SizeAdjustPolicy.AdjustToContents)
        outer.addWidget(self.key_box)
        self._card_wanted = False
        self._set_card(not self.conversation.has_key())

        self.scroll = QScrollArea()
        self.scroll.setObjectName("ChatScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        area = QWidget()
        area.setObjectName("ChatArea")
        self.column = QVBoxLayout(area)
        self.column.setContentsMargins(14, 14, 14, 14)
        self.column.setSpacing(10)
        self.column.addStretch(1)
        self.scroll.setWidget(area)
        bar = self.scroll.verticalScrollBar()
        bar.rangeChanged.connect(lambda _low, high: bar.setValue(high))  # siempre al último mensaje
        outer.addWidget(self.scroll, 1)

        composer = QFrame()
        self.composer = composer
        composer.setObjectName("Composer")
        row = QHBoxLayout(composer)
        row.setContentsMargins(10, 8, 10, 6)
        row.setSpacing(8)
        self.input = Composer()
        self.input.setPlaceholderText(tr("Escribe aquí… (Enter envía)"))
        self.input.setToolTip(tr("Enter envía · Mayús+Enter hace un salto de línea"))
        self.input.setFixedHeight(54)
        self.input.submitted.connect(self.send_current)
        self.input.textChanged.connect(lambda: self.mood.emit("typing", bool(self.input.toPlainText().strip())))
        row.addWidget(self.input, 1)
        self.secret_btn = button("", "lock", None, tr("Dato secreto: guardar una clave o contraseña sin escribirla en el chat"))
        self.secret_btn.setObjectName("IconButton")
        self.secret_btn.setAccessibleName(tr("Dato secreto"))
        self.secret_btn.setFixedSize(40, 40)
        self.secret_btn.clicked.connect(self._secret_from_button)
        row.addWidget(self.secret_btn, 0, Qt.AlignmentFlag.AlignBottom)
        self.send_btn = button(tr("Enviar"), "send", "primary")
        self.send_btn.setFixedHeight(40)
        self.send_btn.clicked.connect(self._send_or_stop)
        row.addWidget(self.send_btn, 0, Qt.AlignmentFlag.AlignBottom)
        outer.addWidget(composer)
        self.footnote = label("", "Small", wrap=True)
        self.footnote.setContentsMargins(14, 0, 14, 10)
        outer.addWidget(self.footnote)

        self.agent_event.connect(self._on_agent_event)
        ctx.bridge.event.connect(self._on_app_event)
        ctx.bridge.config_saved.connect(lambda section: self._on_app_event(CONFIG_EXTERNAL, section))
        self._built = True
        self._welcome()
        self.refresh()

    def _set_card(self, visible: bool) -> None:
        """Muestra u oculta «Conecta tu IA» (con su recuadro, para no dejar huecos)."""
        self._card_wanted = visible
        self.key_card.setVisible(visible)
        self.key_box.setVisible(visible)
        self._fit_card()

    def _fit_card(self) -> None:
        """Sin IA conectada aún no hay nada que conversar: la tarjeta usa todo el panel. Con IA, deja ver el chat."""
        if not self._built:
            return  # aún construyéndose
        first_time = self._card_wanted and not self.conversation.has_key()
        self.scroll.setVisible(not first_time)
        self.layout().setStretchFactor(self.key_box, 1 if first_time else 0)
        if first_time:
            self.key_box.setMaximumHeight(16777215)  # todo el alto disponible
        else:
            self.key_box.setMaximumHeight(max(180, self.height() - 240))  # y siempre se ve algo del chat

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._fit_card()

    def toggle_connect(self) -> None:
        if self._card_wanted and self.conversation.has_key():
            self._set_card(False)
        else:
            self.show_connect()

    def _on_app_event(self, kind: str, data) -> None:
        if kind == CONFIG_EXTERNAL and data in ("assistant", "all"):
            if not self._card_wanted:  # no se pisa lo que el usuario está escribiendo en la tarjeta
                self._provider_changed()
            self.refresh()

    def show_connect(self, provider_id: str | None = None) -> None:
        """Abre la tarjeta «Conecta tu IA» (desde Ajustes o la guía), con ese proveedor si se indica."""
        self._set_card(True)
        index = self.provider_combo.findData(provider_id) if provider_id else -1
        if index < 0:
            self._provider_changed()  # el proveedor guardado
            return
        self.provider_combo.blockSignals(True)
        self.provider_combo.setCurrentIndex(index)
        self.provider_combo.blockSignals(False)
        self._provider_changed(user=True)

    # --- conexión con la IA ------------------------------------------------------------------------
    def _key_card(self) -> Card:
        card = Card(
            tr("Conecta tu IA"),
            tr("Elige tu IA y usa tu propia clave: pagas por uso a tu proveedor. Los permisos y límites son los mismos "
               "con cualquiera."),
        )
        self.key_form = QFormLayout()
        self.key_form.setSpacing(10)
        self.provider_combo = _narrow(QComboBox())
        for provider in PROVIDERS.values():
            self.provider_combo.addItem(tr(provider.name), provider.id)
        self.provider_combo.setCurrentIndex(max(0, self.provider_combo.findData(resolve(self.ctx.cfg.data.assistant).provider.id)))
        self.provider_combo.currentIndexChanged.connect(lambda _i: self._provider_changed(user=True))
        self.key_form.addRow(tr("Proveedor"), self.provider_combo)
        self.url_field = QLineEdit()
        self.key_form.addRow(tr("Dirección"), self.url_field)
        self.model_field = _narrow(QComboBox())
        self.model_field.setEditable(True)
        self.key_form.addRow(tr("Modelo"), self.model_field)
        card.add(layout=self.key_form)
        self.key_steps = label("", wrap=True)
        self.key_steps.setStyleSheet("font-size: 12px; color: #334155;")
        card.add(self.key_steps)
        self.key_field = SecretField("", copyable=False)
        card.add(self.key_field)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.key_save = button(tr("Guardar y probar"), "check", "primary")
        self.key_save.clicked.connect(self._save_key)
        row.addWidget(self.key_save)
        self.open_keys = button(tr("Obtener clave"), "link")
        self.open_keys.clicked.connect(self._open_keys_page)
        row.addWidget(self.open_keys)
        row.addStretch(1)
        card.add(layout=row)
        self.key_result = label("", "Hint", wrap=True)
        self.key_result.setVisible(False)
        card.add(self.key_result)
        self._provider_changed()
        return card

    def _chosen_provider(self):
        return PROVIDERS.get(self.provider_combo.currentData()) or resolve(self.ctx.cfg.data.assistant).provider

    def _provider_changed(self, user: bool = False) -> None:
        """Ajusta la tarjeta al proveedor elegido (pasos, dirección, modelos y si ya hay clave guardada)."""
        if not hasattr(self, "provider_combo"):
            return
        settings = self.ctx.cfg.data.assistant
        current = resolve(settings)
        if not user:
            self.provider_combo.blockSignals(True)
            self.provider_combo.setCurrentIndex(max(0, self.provider_combo.findData(current.provider.id)))
            self.provider_combo.blockSignals(False)
        provider = self._chosen_provider()
        same = provider.id == current.provider.id
        custom_url = settings.base_url if same else ""
        self.url_field.setText(custom_url)
        self.url_field.setPlaceholderText(provider.base_url or "https://mi-servidor.com/v1")
        self.key_form.setRowVisible(self.url_field, provider.local or not provider.base_url or bool(custom_url))
        self.model_field.clear()
        for key, text in provider.models:
            self.model_field.addItem(tr(text), key)
        model = current.model if same else provider.default_model
        index = self.model_field.findData(model)
        if index >= 0:
            self.model_field.setCurrentIndex(index)
        else:
            self.model_field.setEditText(model)
        self.key_field.edit.setPlaceholderText(
            provider.key_hint or (tr("Clave de API (opcional)") if not provider.needs_key else tr("Clave de API"))
        )
        endpoint = Endpoint(provider, (custom_url or provider.base_url).rstrip("/"), model, current.effort)
        stored = bool(self.ctx.secrets.get(endpoint.key_name)) if endpoint.base_url else False
        if provider.local:
            steps = tr(
                "1. {0}<br>2. Pulsa <b>Guardar y probar</b>: no hace falta clave y el modelo funciona en tu equipo "
                "(gratis).",
                tr(provider.signup),
            )
        elif not provider.needs_key:
            steps = tr(
                pick(N_("1. {0}<br>2. Pulsa <b>Guardar y probar</b>. Si pones una clave, queda en el almacén seguro de "
                        "Windows de este PC."),
                     N_("1. {0}<br>2. Pulsa <b>Guardar y probar</b>. Si pones una clave, queda en el Llavero de esta Mac.")),
                tr(provider.signup),
            )
        else:
            starts = tr(" (empieza por <code>{0}</code>)", provider.key_hint.rstrip("…")) if provider.key_hint else ""
            steps = tr(
                pick(N_("1. {0}<br>2. Copia la clave{1}.<br>3. Pégala aquí y pulsa <b>Guardar y probar</b>. Queda en el "
                        "almacén seguro de Windows de este PC."),
                     N_("1. {0}<br>2. Copia la clave{1}.<br>3. Pégala aquí y pulsa <b>Guardar y probar</b>. Queda en el "
                        "Llavero de esta Mac.")),
                tr(provider.signup),
                starts,
            )
        if provider.note:
            steps += f"<br><i>{tr(provider.note)}</i>"
        if stored:
            steps += tr("<br>✓ Ya hay una clave guardada para este proveedor: deja el campo vacío para volver a probarla.")
        self.key_steps.setText(steps)
        self.open_keys.setVisible(bool(provider.keys_url))
        self.open_keys.setText(tr("Obtener clave") if provider.needs_key else tr("Descargar {0}", provider.short))
        self.open_keys.setToolTip(provider.keys_url)
        if user:
            self.key_result.setVisible(False)

    def _open_keys_page(self) -> None:
        url = self._chosen_provider().keys_url
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _wanted_model(self) -> str:
        index = self.model_field.currentIndex()
        text = self.model_field.currentText().strip()
        if index >= 0 and text == self.model_field.itemText(index):
            return str(self.model_field.itemData(index) or text)
        return text

    def _save_key(self) -> None:
        provider = self._chosen_provider()
        settings = self.ctx.cfg.data.assistant
        raw_url = self.url_field.text().strip() or provider.base_url
        if not raw_url:
            self._key_message(False, tr("Escribe la dirección del servidor (por ejemplo https://mi-servidor.com/v1)."))
            return
        try:
            base_url = check_base_url(raw_url)
        except ProviderError as exc:
            self._key_message(False, exc.detail)
            return
        model = self._wanted_model()
        if model and not valid_model(model):
            self._key_message(False, tr("El nombre del modelo no es válido."))
            return
        endpoint = Endpoint(provider, base_url, model, resolve(settings).effort)
        typed = self.key_field.text()
        key = typed or self.ctx.secrets.get(endpoint.key_name)
        if provider.needs_key and not key:
            self._key_message(False, tr("Pega la clave de API."))
            return
        self.key_save.setEnabled(False)
        self.key_save.setText(tr("Probando…"))
        connector = self.connector

        def check() -> tuple[str, str, list[str]]:
            try:
                available = connector(endpoint, key).models()
            except ProviderError as exc:
                if exc.kind in ("auth", "permission", "quota", "network", "timeout", "redirect", "policy"):
                    raise RuntimeError(friendly_error(exc, endpoint)) from None
                available = []  # el servidor no publica su lista de modelos
            chosen = pick_model(provider, endpoint.model, available)
            if not chosen:
                raise RuntimeError(tr("Escribe el nombre del modelo que quieres usar."))
            checked = Endpoint(provider, base_url, chosen, endpoint.effort)
            try:
                name = connector(checked, key).check()
            except ProviderError as exc:
                raise RuntimeError(friendly_error(exc, checked)) from None
            return chosen, name, available

        def ok(result: tuple[str, str, list[str]]) -> None:
            chosen, name, available = result
            if typed:
                self.ctx.secrets.set(endpoint.key_name, typed)
            settings.provider = provider.id
            settings.base_url = "" if base_url == provider.base_url.rstrip("/") else base_url
            settings.model = chosen
            self.ctx.cfg.save("assistant")
            self.ctx.events.emit(CONFIG_EXTERNAL, "assistant")
            self.key_field.setText("")
            note = ""
            if available and chosen not in available:
                note = tr(" Ojo: ese modelo no aparece en la lista de tu cuenta.")
            if self.conversation.started:
                note += tr(" Se usará desde la próxima conversación («Nueva conversación»).")
            label_name = provider.model_label(chosen) if provider.model_label(chosen) != chosen else (name or chosen)
            self._key_message(True, tr("✓ Conectado a {0} · {1}. Ya puedes escribirle al asistente.{2}", provider.short, label_name, note))
            self._provider_changed()
            self.refresh()
            self.mood.emit("connected", None)
            QTimer.singleShot(3000, self._hide_connected_card)

        run_async(check, ok, lambda message: self._key_message(False, f"✗ {message}"))

    def _hide_connected_card(self) -> None:
        if self.conversation.has_key():
            self._set_card(False)

    def _key_message(self, ok: bool, text: str) -> None:
        self.key_save.setEnabled(True)
        self.key_save.setText(tr("Guardar y probar"))
        self.key_result.setObjectName("Hint" if ok else "Warning")
        self.key_result.setText(text)
        self.key_result.setVisible(True)
        repolish(self.key_result)

    # --- conversación ------------------------------------------------------------------------------
    def status_text(self) -> str:
        """Lo que dice la cabecera bajo «Asistente»."""
        if self.busy:
            return f"{self.activity}…" if self.activity else tr("Pensando…")
        if not self.conversation.has_key():
            return tr("Falta conectar la IA")
        return tr("Lista · {0}", self.conversation.endpoint.label)

    def usage_text(self) -> str:
        settings = self.ctx.cfg.data.assistant
        today = self.conversation.meter.today()
        budget = float(settings.daily_budget_usd or 0)
        if budget > 0:
            return tr("Hoy ≈ {0:.2f} USD de {1:.2f}", today["cost"], budget)
        return tr("Hoy ≈ {0:.2f} USD", today["cost"])

    def refresh(self) -> None:
        has_key = self.conversation.has_key()
        if not has_key and not self._card_wanted:
            self._set_card(True)  # p. ej. se cambió a un proveedor sin clave guardada
        self._fit_card()
        self.input.setEnabled(has_key)
        self.send_btn.setEnabled(has_key or self.busy)
        for chip in self.findChildren(QPushButton, "Suggestion"):
            chip.setEnabled(has_key and not self.busy)
        endpoint = self.conversation.endpoint
        today = self.conversation.meter.today()
        self.footnote.setText(
            tr("🔒 Tus claves se quedan en este equipo y cada cambio pide permiso · {0}", self.usage_text())
        )
        self.footnote.setToolTip(
            tr("Funciona con {0}. Tokens hoy: {1:,} de entrada · {2:,} leídos de caché · {3:,} de salida. Es una estimación; la factura real está en tu cuenta del proveedor.", endpoint.label, int(today['input']), int(today['cache_read']), int(today['output']))
        )
        self.changed.emit()

    def _add(self, widget: QWidget) -> QWidget:
        self.column.insertWidget(self.column.count() - 1, widget)
        return widget

    def _welcome(self) -> None:
        shop = self.ctx.cfg.data.shopify.shop or tr("mi-tienda.myshopify.com")
        self._add(bubble(tr(WELCOME).format(shop=shop), "assistant"))
        chips = QWidget()
        grid = QVBoxLayout(chips)
        grid.setContentsMargins(AVATAR + 10, 0, 0, 0)
        grid.setSpacing(6)
        for text, message in SUGGESTIONS:
            chip = button(tr(text), None, "chip")
            chip.setObjectName("Suggestion")
            chip.clicked.connect(lambda _=False, m=tr(message): self.send_text(m))
            grid.addWidget(chip, 0, Qt.AlignmentFlag.AlignLeft)
        self._add(chips)

    def new_conversation(self) -> None:
        if self.busy:
            self.stop()
        while self.column.count() > 1:
            item = self.column.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # Conversación nueva (un turno detenido que aún esté terminando sigue con la anterior, sin mezclarse).
        self.conversation = Conversation(self.toolbox, self.ctx.secrets, self.connector)
        self.guard.vault.clear()
        self.pending_notes.clear()
        self._activities.clear()
        self._thinking = None
        self._welcome()
        self.refresh()
        self.mood.emit("new", None)

    def send_current(self) -> None:
        if self.busy:
            return
        text = self.input.toPlainText().strip()
        if text:
            self.input.clear()
            self.send_text(text)

    def _send_or_stop(self) -> None:
        if self.busy:
            self.stop()
        else:
            self.send_current()

    def send_text(self, text: str) -> None:
        if self.busy or not text.strip() or not self.conversation.has_key():
            return
        cleaned = self.guard.clean(text)
        self._add(bubble(cleaned.display_text, "user"))
        sections = set()
        for description in cleaned.saved:
            self._add(ActivityRow(tr("🔒 Guardé en este equipo: {0} (no se envió al asistente)", description), False)).finish(True)
        if any("Shopify" in d for d in cleaned.saved):
            sections.add("shopify")
        if cleaned.ai_keys:
            self._add(ActivityRow(tr("🔒 Quité una clave de IA del mensaje (no se envió). Configúrala en «Conectar IA»"),
                                  False)).finish(True)
        if cleaned.refs:
            count = len(cleaned.refs)
            self._add(ActivityRow(tr("🔒 Aparté {0} dato(s) secreto(s): no se envían al asistente", count), False)).finish(True)
        for section in sections:
            self.ctx.cfg.save(section)
            self.ctx.events.emit(CONFIG_EXTERNAL, section)
        if cleaned.saved or cleaned.refs:
            self.mood.emit("secret", None)
        message = cleaned.model_text
        if self.pending_notes:
            message = "\n".join(self.pending_notes) + "\n\n" + message
            self.pending_notes.clear()
        self._start(message)

    def _start(self, message: str) -> None:
        self.busy = True
        self.activity = ""
        self.mood.emit("sent", None)
        self.cancel = threading.Event()
        turn = self.cancel  # identifica el turno: los avisos de un turno ya detenido se ignoran
        self.send_btn.setText(tr("Detener"))
        self.send_btn.setIcon(icons.icon("stop", "#FFFFFF", 16))
        self._show_thinking()
        self.refresh()
        previous = self._worker
        conversation = self.conversation

        def emit(kind: str, data: dict) -> None:
            self.agent_event.emit(kind, {**data, "turn": turn})

        def work() -> None:
            if previous is not None:
                previous.join()  # un turno detenido termina de ordenar el historial antes del siguiente
            try:
                conversation.send(message, emit, turn)
            finally:
                emit("finished", {})

        self._worker = threading.Thread(target=work, name="asistente", daemon=True)
        self._worker.start()

    def screen_session(self, active: bool) -> None:
        """Control de la pantalla: LabelBridge se minimiza (el asistente ve lo demás) y arriba queda la barra con
        «Detener»; al terminar el turno, todo vuelve como estaba."""
        from ..screen_control import ScreenBar

        window = self.window()
        if active:
            self._screen_restore = window.isVisible() and not window.isMinimized()
            if self._screen_bar is None:
                self._screen_bar = ScreenBar(self.stop)
            self._screen_bar.place()
            self._screen_bar.show()
            if self._screen_restore:
                window.showMinimized()
            return
        if self._screen_bar is not None:
            self._screen_bar.hide()
        if self._screen_restore:
            window.showNormal()
            window.raise_()
            window.activateWindow()
        self._screen_restore = False

    def stop(self) -> None:
        self.cancel.set()
        self._finish_turn()
        self._add(ActivityRow(tr("Detenido"), False)).finish(False, tr("puedes seguir escribiendo"))
        self.mood.emit("stopped", None)

    def _finish_turn(self) -> None:
        self.busy = False
        self.activity = ""
        self.mood.emit("finished", None)
        self._hide_thinking()
        for row in self._activities.values():
            row.finish(False, tr("sin terminar"))
        self._activities.clear()
        self.send_btn.setText(tr("Enviar"))
        self.send_btn.setIcon(icons.icon("send", "#FFFFFF", 16))
        self.refresh()
        self.input.setFocus()

    def _show_thinking(self) -> None:
        self._hide_thinking()
        self._thinking = self._add(ActivityRow(tr("Pensando"), True))  # type: ignore[assignment]

    def _hide_thinking(self) -> None:
        if self._thinking is not None:
            self._thinking.deleteLater()
            self._thinking = None

    @Slot(str, object)
    def _on_agent_event(self, kind: str, data: Any) -> None:
        if data.get("turn") is not self.cancel or not self.busy or self.cancel.is_set():
            return  # aviso tardío de un turno que ya se detuvo
        if kind == "finished":
            self._finish_turn()
            return
        if kind == "tool":
            self._hide_thinking()
            self._activities[data["id"]] = self._add(ActivityRow(data["activity"], True))  # type: ignore[assignment]
            self.activity = data["activity"]
            self.mood.emit("tool", data["name"])
            self.changed.emit()
        elif kind == "tool_done":
            row = self._activities.pop(data["id"], None)
            if row is not None:
                detail = data.get("detail", "")  # lo que la herramienta le dice al modelo (en español)
                row.finish(data["ok"], detail if language() == "es" else tr("no se pudo hacer"))
                if detail and language() != "es":
                    row.setToolTip(detail)
            self._show_thinking()
            self.activity = ""
            self.mood.emit("tool_done", bool(data["ok"]))
            self.changed.emit()
        elif kind in ("reply", "note"):
            self._hide_thinking()
            self._add(bubble(data["text"], "assistant"))
            if kind == "note":
                self._show_thinking()
            self.mood.emit(kind, data["text"])
        elif kind in ("error", "refusal"):
            self._hide_thinking()
            self._add(bubble(data["message"], "error"))
            self.mood.emit(kind, data["message"])

    # --- datos secretos ----------------------------------------------------------------------------
    def _secret_from_button(self) -> None:
        dialog = SecretDialog(self.window(), "", "", LOCK_KINDS)
        if dialog.exec() != QDialog.DialogCode.Accepted or not dialog.value():
            return
        kind = dialog.kind.currentData()
        secret_name, title, _hint = LOCK_KINDS[kind]
        self.ctx.secrets.set(secret_name, dialog.value())
        section = SECTION_OF_SECRET.get(secret_name)
        if section:
            self.ctx.cfg.save(section)
            self.ctx.events.emit(CONFIG_EXTERNAL, section)
        self._add(ActivityRow(tr("🔒 Guardado en este equipo: {0}", title), False)).finish(True)
        self.pending_notes.append(f"[Nota de la app: el usuario guardó «{title}» con la ventana segura.]")
        self.mood.emit("secret", None)
        self.refresh()

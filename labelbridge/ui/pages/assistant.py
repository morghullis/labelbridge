"""Asistente: configura LabelBridge conversando. Usa Claude (Anthropic) con las herramientas de la app."""

from __future__ import annotations

import threading
from typing import Any, Callable

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QFont, QKeyEvent, QTextCursor, QTextDocument
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...assistant.agent import API_KEYS_URL, MODEL_NAME, Conversation, friendly_error, validate_key
from ...assistant.secrets_guard import SecretsGuard
from ...assistant.tools import SECRET_KINDS, Toolbox
from ...events import CONFIG_EXTERNAL
from ...secret_store import ANTHROPIC_API_KEY, MQTT_PASSWORD, SHOPIFY_ACCESS_TOKEN, SHOPIFY_CLIENT_SECRET
from .. import icons
from ..context import AppContext
from ..widgets import Card, Pill, SecretField, button, copy_to_clipboard, label, repolish, run_async

BUBBLE_MAX = 620
WELCOME = (
    "¡Hola! Soy el asistente de LabelBridge. Escríbeme con tus palabras qué quieres configurar y lo hago por ti: la "
    "impresora de etiquetas, la conexión con tu tienda de Shopify y la compra e impresión automática de las **guías de "
    "envío de Shopify Shipping**.\n\n"
    "Puedes darme varios datos a la vez, por ejemplo: *«Tengo una Zebra ZD421 por USB y mi tienda es "
    "pedroyorubajewelry»*.\n\n"
    "🔒 Si hace falta un dato secreto (como el Client secret de Shopify) te abro una ventana segura: no hace falta "
    "escribirlo en el chat."
)
SUGGESTIONS = (
    "Configúralo todo conmigo paso a paso",
    "Quiero que se impriman solas las guías de Shopify Shipping",
    "Conecta mi impresora de etiquetas",
    "¿Por qué no se imprimió un pedido?",
)
# Datos secretos que se pueden guardar con el botón del candado.
LOCK_KINDS = {
    **{kind: (name, title, hint) for kind, (name, title, hint) in SECRET_KINDS.items()},
    "anthropic_api_key": (ANTHROPIC_API_KEY, "Clave de API de Anthropic (asistente)", "platform.claude.com → API Keys."),
}
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
    no = box.addButton("Cancelar", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(no)  # lo seguro es lo predeterminado
    box.exec()
    return box.clickedButton() is yes


class SecretDialog(QDialog):
    """Ventana segura para pegar un dato secreto: no pasa por el chat."""

    def __init__(self, parent: QWidget | None, title: str, hint: str, kinds: dict | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Dato secreto")
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
        self.field = SecretField("Pega aquí el dato", copyable=False)
        layout.addWidget(self.field)
        layout.addWidget(
            label("Se guarda en el almacén seguro de Windows de este PC. El asistente no lo ve.", "Small", wrap=True)
        )
        row = QHBoxLayout()
        row.addStretch(1)
        cancel = button("Cancelar")
        cancel.clicked.connect(self.reject)
        self.save_btn = button("Guardar", "lock", "primary")
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

    def confirm(self, title: str, text: str, accept: str) -> bool:
        return bool(self.invoker.call(lambda: confirm_action(self.page.window(), title, text, accept)))

    def ask_secret(self, title: str, hint: str) -> str | None:
        return self.invoker.call(lambda: SecretDialog.ask(self.page.window(), title, hint))

    def open_url(self, url: str) -> None:
        self.invoker.call(lambda: QDesktopServices.openUrl(QUrl(url)))

    def copy_text(self, text: str) -> None:
        self.invoker.call(lambda: copy_to_clipboard(text))

    def config_changed(self, section: str) -> None:
        self.page.ctx.events.emit(CONFIG_EXTERNAL, section)


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


def _avatar() -> QLabel:
    avatar = QLabel()
    avatar.setFixedSize(30, 30)
    avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
    avatar.setPixmap(icons.pixmap("sparkle", "#FFFFFF", 16))
    avatar.setStyleSheet(
        "QLabel { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #818CF8, stop:1 #4338CA);"
        " border-radius: 15px; }"
    )
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
    body.setOpenExternalLinks(True)
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
    agent_event = Signal(str, object)

    def __init__(self, ctx: AppContext, client_factory=None) -> None:
        super().__init__()
        self.setObjectName("Content")
        self.ctx = ctx
        self.ui = QtAssistantUI(self)
        self.guard = SecretsGuard(ctx.secrets)
        self.toolbox = Toolbox(ctx, self.ui, self.guard)
        self._client_kwargs = {"client_factory": client_factory} if client_factory else {}
        self.conversation = Conversation(self.toolbox, ctx.secrets, **self._client_kwargs)
        self.cancel = threading.Event()
        self.busy = False
        self._worker: threading.Thread | None = None
        self.pending_notes: list[str] = []
        self._activities: dict[str, ActivityRow] = {}
        self._thinking: ActivityRow | None = None

        outer = QVBoxLayout(self)
        outer.setContentsMargins(32, 28, 32, 20)
        outer.setSpacing(14)

        header = QHBoxLayout()
        titles = QVBoxLayout()
        titles.setSpacing(4)
        titles.addWidget(label("Asistente", "PageTitle"))
        titles.addWidget(
            label(
                "Escríbele tus datos y él configura LabelBridge por ti: impresora, tienda y guías de Shopify Shipping.",
                "PageSubtitle",
                wrap=True,
            )
        )
        header.addLayout(titles, 1)
        self.status = Pill("off", "")
        header.addWidget(self.status, 0, Qt.AlignmentFlag.AlignTop)
        self.key_btn = button("Clave de API", "key", "ghost")
        self.key_btn.clicked.connect(lambda: self.key_card.setVisible(not self.key_card.isVisible()))
        header.addWidget(self.key_btn, 0, Qt.AlignmentFlag.AlignTop)
        self.new_btn = button("Nueva conversación", "refresh", "ghost")
        self.new_btn.clicked.connect(self.new_conversation)
        header.addWidget(self.new_btn, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(header)

        self.key_card = self._key_card()
        self.key_card.setVisible(not self.conversation.has_key())
        outer.addWidget(self.key_card)

        panel = QFrame()
        panel.setObjectName("ChatPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(1, 1, 1, 1)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        area = QWidget()
        area.setObjectName("ChatArea")
        self.column = QVBoxLayout(area)
        self.column.setContentsMargins(20, 18, 20, 18)
        self.column.setSpacing(12)
        self.column.addStretch(1)
        self.scroll.setWidget(area)
        bar = self.scroll.verticalScrollBar()
        bar.rangeChanged.connect(lambda _low, high: bar.setValue(high))  # siempre al último mensaje
        panel_layout.addWidget(self.scroll)
        outer.addWidget(panel, 1)

        composer = QFrame()
        self.composer = composer
        composer.setObjectName("Composer")
        row = QHBoxLayout(composer)
        row.setContentsMargins(12, 8, 8, 8)
        row.setSpacing(8)
        self.input = Composer()
        self.input.setPlaceholderText("Escribe aquí… (Enter envía, Mayús+Enter salto de línea)")
        self.input.setFixedHeight(64)
        self.input.submitted.connect(self.send_current)
        row.addWidget(self.input, 1)
        self.secret_btn = button("Dato secreto", "lock", None, "Guardar una clave o contraseña sin escribirla en el chat")
        self.secret_btn.clicked.connect(self._secret_from_button)
        row.addWidget(self.secret_btn, 0, Qt.AlignmentFlag.AlignBottom)
        self.send_btn = button("Enviar", "send", "primary")
        self.send_btn.clicked.connect(self._send_or_stop)
        row.addWidget(self.send_btn, 0, Qt.AlignmentFlag.AlignBottom)
        outer.addWidget(composer)
        outer.addWidget(
            label(
                "🔒 Las claves que escribas o pegues se guardan solo en este PC y no se envían al asistente. "
                f"Comprar guías siempre pide tu confirmación. Funciona con {MODEL_NAME} de Anthropic.",
                "Small",
                wrap=True,
            )
        )

        self.agent_event.connect(self._on_agent_event)
        self._welcome()
        self.refresh()

    # --- clave de API ------------------------------------------------------------------------------
    def _key_card(self) -> Card:
        card = Card(
            "Activa el asistente",
            "El asistente usa Claude, la IA de Anthropic, para entender lo que escribes y configurar la app por ti.",
        )
        steps = label(
            "1. Entra a <b>platform.claude.com</b> (o crea una cuenta) y agrega saldo en <b>Billing</b>. Se paga por "
            "uso: configurar todo suele costar menos de un dólar.<br>"
            "2. En <b>API Keys</b> pulsa <b>Create Key</b> y copia la clave (empieza por <code>sk-ant-</code>).<br>"
            "3. Pégala aquí y pulsa <b>Guardar y probar</b>. Queda en el almacén seguro de Windows de este PC.",
            wrap=True,
        )
        card.add(steps)
        row = QHBoxLayout()
        self.key_field = SecretField("sk-ant-…", copyable=False)
        row.addWidget(self.key_field, 1)
        self.key_save = button("Guardar y probar", "check", "primary")
        self.key_save.clicked.connect(self._save_key)
        row.addWidget(self.key_save)
        open_keys = button("Abrir API Keys", "link")
        open_keys.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(API_KEYS_URL)))
        row.addWidget(open_keys)
        card.add(layout=row)
        self.key_result = label("", "Hint", wrap=True)
        self.key_result.setVisible(False)
        card.add(self.key_result)
        return card

    def _save_key(self) -> None:
        key = self.key_field.text()
        if not key:
            self._key_message(False, "Pega la clave de API.")
            return
        self.key_save.setEnabled(False)
        self.key_save.setText("Probando…")

        def check() -> str:
            try:
                return validate_key(key, self.conversation.client_factory)
            except Exception as exc:
                raise RuntimeError(friendly_error(exc)) from None

        def ok(model_name: str) -> None:
            self.ctx.secrets.set(ANTHROPIC_API_KEY, key)
            self.key_field.setText("")
            self._key_message(True, f"✓ Clave válida · {model_name}. Ya puedes escribirle al asistente.")
            self.refresh()
            QTimer.singleShot(2500, lambda: self.key_card.setVisible(not self.conversation.has_key()))

        run_async(check, ok, lambda message: self._key_message(False, f"✗ {message}"))

    def _key_message(self, ok: bool, text: str) -> None:
        self.key_save.setEnabled(True)
        self.key_save.setText("Guardar y probar")
        self.key_result.setObjectName("Hint" if ok else "Warning")
        self.key_result.setText(text)
        self.key_result.setVisible(True)
        repolish(self.key_result)

    # --- conversación ------------------------------------------------------------------------------
    def refresh(self) -> None:
        has_key = self.conversation.has_key()
        if not has_key:
            self.key_card.setVisible(True)
        if self.busy:
            self.status.set_state("connecting", "Trabajando…")
        elif has_key:
            self.status.set_state("ok", "Listo")
        else:
            self.status.set_state("warn", "Falta la clave de API")
        self.input.setEnabled(has_key)
        self.send_btn.setEnabled(has_key or self.busy)
        for chip in self.findChildren(QPushButton, "Suggestion"):
            chip.setEnabled(has_key and not self.busy)

    def _add(self, widget: QWidget) -> QWidget:
        self.column.insertWidget(self.column.count() - 1, widget)
        return widget

    def _welcome(self) -> None:
        self._add(bubble(WELCOME, "assistant"))
        chips = QWidget()
        grid = QVBoxLayout(chips)
        grid.setContentsMargins(40, 0, 0, 0)
        grid.setSpacing(8)
        for start in range(0, len(SUGGESTIONS), 2):
            line = QHBoxLayout()
            line.setSpacing(8)
            for text in SUGGESTIONS[start : start + 2]:
                chip = button(text, None, "chip")
                chip.setObjectName("Suggestion")
                chip.clicked.connect(lambda _=False, t=text: self.send_text(t))
                line.addWidget(chip)
            line.addStretch(1)
            grid.addLayout(line)
        self._add(chips)

    def new_conversation(self) -> None:
        if self.busy:
            self.stop()
        while self.column.count() > 1:
            item = self.column.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        # Conversación nueva (un turno detenido que aún esté terminando sigue con la anterior, sin mezclarse).
        self.conversation = Conversation(self.toolbox, self.ctx.secrets, **self._client_kwargs)
        self.guard.vault.clear()
        self.pending_notes.clear()
        self._activities.clear()
        self._thinking = None
        self._welcome()
        self.refresh()

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
            self._add(ActivityRow(f"🔒 Guardé en este PC: {description} (no se envió al asistente)", False)).finish(True)
        if any("Shopify" in d for d in cleaned.saved):
            sections.add("shopify")
        if cleaned.refs:
            count = len(cleaned.refs)
            self._add(ActivityRow(f"🔒 Aparté {count} dato(s) secreto(s): no se envían al asistente", False)).finish(True)
        for section in sections:
            self.ctx.cfg.save(section)
            self.ctx.events.emit(CONFIG_EXTERNAL, section)
        message = cleaned.model_text
        if self.pending_notes:
            message = "\n".join(self.pending_notes) + "\n\n" + message
            self.pending_notes.clear()
        self._start(message)

    def _start(self, message: str) -> None:
        self.busy = True
        self.cancel = threading.Event()
        turn = self.cancel  # identifica el turno: los avisos de un turno ya detenido se ignoran
        self.send_btn.setText("Detener")
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

    def stop(self) -> None:
        self.cancel.set()
        self._finish_turn()
        self._add(ActivityRow("Detenido", False)).finish(False, "puedes seguir escribiendo")

    def _finish_turn(self) -> None:
        self.busy = False
        self._hide_thinking()
        for row in self._activities.values():
            row.finish(False, "sin terminar")
        self._activities.clear()
        self.send_btn.setText("Enviar")
        self.send_btn.setIcon(icons.icon("send", "#FFFFFF", 16))
        self.refresh()
        self.input.setFocus()

    def _show_thinking(self) -> None:
        self._hide_thinking()
        self._thinking = self._add(ActivityRow("Pensando", True))  # type: ignore[assignment]

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
        elif kind == "tool_done":
            row = self._activities.pop(data["id"], None)
            if row is not None:
                row.finish(data["ok"], data.get("detail", ""))
            self._show_thinking()
        elif kind in ("reply", "note"):
            self._hide_thinking()
            self._add(bubble(data["text"], "assistant"))
            if kind == "note":
                self._show_thinking()
        elif kind in ("error", "refusal"):
            self._hide_thinking()
            self._add(bubble(data["message"], "error"))

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
        self._add(ActivityRow(f"🔒 Guardado en este PC: {title}", False)).finish(True)
        if kind != "anthropic_api_key":
            self.pending_notes.append(f"[Nota de la app: el usuario guardó «{title}» con la ventana segura.]")
        self.refresh()

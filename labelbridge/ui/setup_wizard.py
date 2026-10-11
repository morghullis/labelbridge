"""Asistente de inicio: la primera vez, en pocos pasos y según lo que quieres hacer, deja lo esencial funcionando.

Pregunta primero el objetivo (comprar e imprimir guías de Shopify, solo imprimir los pedidos o imprimir lo que mande n8n
o tu web) y muestra solo los pasos que ese objetivo necesita, con lo que ya detecta elegido: las impresoras instaladas
(cuál parece de etiquetas y cuál de hojas), la tienda que dejó el instalador, lo habitual activado. Cada paso se puede
probar ahí mismo (imprimir prueba, conectar la tienda). Lo demás (remitente, empaques, reglas, avisos…) lo hace el
asistente de IA cuando se lo pidas, o la Guía, paso a paso.

Solo cambia la configuración al pulsar «Siguiente» en cada paso, y nunca activa la compra de guías sin que la persona lo
elija (la compra cuesta dinero).
"""

from __future__ import annotations

import logging
import re
import sys
from html import escape
from dataclasses import dataclass
from typing import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QRadioButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..config import PrinterProfile
from ..events import CONFIG_EXTERNAL
from ..connectors.shopify import DEV_DASHBOARD_URL, SCOPES_LABELS, ShopifyError, normalize_shop
from ..i18n import N_, tr
from ..printing import routing
from ..printing.hints import looks_like_label_printer, looks_like_zpl, suggest_alias
from ..secret_store import SHOPIFY_CLIENT_SECRET, SecretStoreError
from ..system.platform import pick
from .context import AppContext
from .widgets import (
    SecretField,
    SwitchRow,
    button,
    confirm,
    copy_to_clipboard,
    error,
    label,
    run_async,
    window_closed,
)

log = logging.getLogger(__name__)

BUY, PRINT, N8N = "buy", "print", "n8n"
GOALS = (
    (BUY, N_("Comprar e imprimir las guías de envío de Shopify"),
     N_("Cada pedido nuevo: LabelBridge compra la guía con Shopify Shipping y la imprime sola. Lo más común.")),
    (PRINT, N_("Solo imprimir los pedidos, sin comprar guías"),
     N_("La página de cada pedido (o una etiqueta para empacar) en cuanto llega. Las guías las compras tú.")),
    (N8N, N_("Imprimir lo que me mandan n8n, mi web u otro sistema"),
     N_("LabelBridge recibe etiquetas listas (PDF o ZPL) y las imprime. Para quien ya tiene esa automatización.")),
)
# Impresoras que nunca se eligen solas para la página del pedido: virtuales y de gran formato (plotters como la
# Canon imagePROGRAF PRO/TM, la HP DesignJet o la Epson SureColor T). Se mira el nombre, el modelo y el driver.
NOT_FOR_PAGES = re.compile(r"pdf|xps|onenote|fax|send to|imageprograf|designjet|plotter|surecolor|large format|"
                           r"\bpro-?\d{3,4}\b|\btm-?\d{3}\b|\bsc-?[tp]\d{3,5}\b|\bt\d{4}\b", re.I)
NONE = ""  # «Ninguna por ahora»


@dataclass
class Choice:
    """Una impresora para elegir: ya en LabelBridge (alias) o instalada en el equipo (se agrega al elegirla)."""

    key: str  # «lb:<alias>» | «sys:<nombre>»
    title: str
    label_like: bool
    alias: str = ""
    system_name: str = ""
    big: bool = False  # virtual o de gran formato: nunca se propone para la hoja (y se pregunta antes de probarla)


def printer_choices(ctx: AppContext, system: list[dict]) -> list[Choice]:
    """Las de LabelBridge primero y después las instaladas en el equipo que todavía no están."""
    installed = {str(info.get("name") or "").casefold(): info for info in system}

    def described(name: str) -> str:
        info = installed.get(name.casefold(), {})
        return f"{name} {info.get('model') or ''} {info.get('driver') or ''}"

    out = []
    for p in ctx.cfg.data.printers:
        if not p.enabled:
            continue
        text = f"{p.alias} {described(p.system_name) if p.system_name else ''}"
        out.append(Choice(f"lb:{p.alias}", p.display_name(),
                          routing.label_like(p) or looks_like_label_printer(text), alias=p.alias,
                          system_name=p.system_name, big=bool(NOT_FOR_PAGES.search(text))))
    known = {p.system_name.casefold() for p in ctx.cfg.data.printers if p.system_name}
    for name_key, info in installed.items():
        name = str(info.get("name") or "")
        if not name or name_key in known:
            continue
        text = described(name)
        out.append(Choice(f"sys:{name}", name, looks_like_label_printer(text), system_name=name,
                          big=bool(NOT_FOR_PAGES.search(text))))
    return out


def pick_label_printer(choices: list[Choice]) -> str:
    return next((c.key for c in choices if c.label_like), NONE)


def pick_page_printer(choices: list[Choice], default_name: str = "") -> str:
    """Para la hoja: la predeterminada del equipo si es de hojas, si no la primera de hojas. Nunca una virtual o un
    plotter (la persona puede elegirla igual)."""
    sheets = [c for c in choices if not c.label_like and not c.big]
    preferred = next((c for c in sheets if c.system_name and c.system_name == default_name), None)
    return (preferred or (sheets[0] if sheets else None)).key if (preferred or sheets) else NONE


class SetupWizard(QDialog):
    """La ventana del asistente de inicio. `open_assistant`: abre el chat del asistente de IA al terminar."""

    def __init__(self, parent: QWidget | None, ctx: AppContext, *,
                 open_assistant: Callable[[str], None] | None = None,
                 navigate: Callable[[str], None] | None = None, system_printers: list[dict] | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.open_assistant = open_assistant
        self.navigate = navigate
        self.goal = BUY
        self.connected = ""  # el nombre de la tienda, cuando la conexión salió bien
        self.buy_proposed = False  # con el objetivo «comprar guías», el interruptor ya sale propuesto una vez
        self.done_steps: list[str] = []
        self.setWindowTitle(tr("Empezar con LabelBridge"))
        self.setMinimumSize(760, 560)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 22)
        outer.setSpacing(14)
        self.step_title = label("", "Small")
        outer.addWidget(self.step_title)
        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        outer.addWidget(self.progress)
        self.stack = QStackedWidget()
        outer.addWidget(self.stack, 1)
        if system_printers is None:
            system_printers = _installed_printers()
        self.system_printers = system_printers
        self.pages = {
            "goal": self._goal_page(),
            "printers": self._printers_page(),
            "store": self._store_page(),
            "orders": self._orders_page(),
            "always": self._always_page(),
            "done": self._done_page(),
        }
        for page in self.pages.values():
            self.stack.addWidget(page)
        buttons = QHBoxLayout()
        self.later_btn = button(tr("Lo hago después"))
        self.later_btn.setToolTip(tr("Cierra este asistente: puedes volver a abrirlo desde la Guía."))
        self.later_btn.clicked.connect(self.reject)
        buttons.addWidget(self.later_btn)
        buttons.addStretch(1)
        self.back_btn = button(tr("Atrás"))
        self.back_btn.clicked.connect(self.back)
        buttons.addWidget(self.back_btn)
        self.next_btn = button(tr("Siguiente"), None, "primary")
        self.next_btn.clicked.connect(self.next)
        buttons.addWidget(self.next_btn)
        outer.addLayout(buttons)
        self.current = "goal"
        self._show("goal")

    # --- navegación -----------------------------------------------------------------------------------
    def steps(self) -> list[str]:
        """Los pasos que necesita el objetivo elegido (n8n no necesita la tienda ni qué hacer con cada pedido)."""
        if self.goal == N8N:
            return ["goal", "printers", "always", "done"]
        return ["goal", "printers", "store", "orders", "always", "done"]

    def _show(self, key: str) -> None:
        self.current = key
        steps = self.steps()
        index = steps.index(key)
        self.stack.setCurrentWidget(self.pages[key])
        titles = {"goal": tr("Tu objetivo"), "printers": tr("Impresoras"), "store": tr("Tu tienda"),
                  "orders": tr("Cada pedido"), "always": tr("Siempre funcionando"), "done": tr("Listo")}
        self.step_title.setText(tr("Paso {0} de {1} · {2}", index + 1, len(steps), titles[key]))
        self.progress.setRange(0, len(steps) - 1)
        self.progress.setValue(index)
        self.back_btn.setVisible(0 < index < len(steps) - 1)
        last = key == "done"
        self.next_btn.setText(tr("Terminar") if last else tr("Siguiente"))
        self.later_btn.setVisible(not last)
        if key == "orders":
            self._refresh_orders()
        if key == "done":
            self._refresh_done()

    def back(self) -> None:
        steps = self.steps()
        index = steps.index(self.current)
        if index > 0:
            self._show(steps[index - 1])

    def next(self) -> None:
        """Guarda lo del paso y pasa al siguiente (si algo falta, lo dice y se queda)."""
        apply = {"goal": self._apply_goal, "printers": self._apply_printers, "store": self._apply_store,
                 "orders": self._apply_orders, "always": self._apply_always}.get(self.current)
        if apply is not None:
            problem = apply()
            if problem:
                error(self, tr("Empezar con LabelBridge"), problem)
                return
        if self.current == "done":
            self.accept()
            return
        steps = self.steps()
        self._show(steps[steps.index(self.current) + 1])

    def done(self, result: int) -> None:  # noqa: A003  al cerrarse (Terminar, Lo hago después o la X)
        g = self.ctx.cfg.data.general
        if not g.setup_done:
            g.setup_done = True  # no vuelve a abrirse solo; se abre otra vez desde la Guía
            self._save("general")
        super().done(result)

    # --- 1. objetivo ----------------------------------------------------------------------------------
    def _goal_page(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setSpacing(10)
        column.addWidget(label(tr("¿Qué quieres que haga LabelBridge?"), "CardTitle"))
        column.addWidget(label(tr("Te muestro solo lo que necesitas para eso. Lo demás lo puedes configurar después, "
                                  "o pedírselo al asistente."), "Muted", wrap=True))
        self.goal_group = QButtonGroup(self)
        for index, (key, title, text) in enumerate(GOALS):
            radio = QRadioButton(tr(title))
            radio.setStyleSheet("font-weight: 600;")
            radio.setChecked(index == 0)
            self.goal_group.addButton(radio, index)
            column.addWidget(radio)
            hint = label(tr(text), "Muted", wrap=True)
            hint.setContentsMargins(26, 0, 0, 6)
            column.addWidget(hint)
        column.addStretch(1)
        return page

    def _apply_goal(self) -> str:
        self.goal = GOALS[max(0, self.goal_group.checkedId())][0]
        return ""

    # --- 2. impresoras --------------------------------------------------------------------------------
    def _printers_page(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setSpacing(10)
        column.addWidget(label(tr("¿Por qué impresora sale cada cosa?"), "CardTitle"))
        column.addWidget(label(tr("Te propongo las que encontré en este equipo. Cámbialas si no son y prueba cada una: "
                                  "las nuevas se agregan a LabelBridge al seguir."), "Muted", wrap=True))
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(10)
        self.label_combo, self.page_combo = QComboBox(), QComboBox()
        rows = (
            (self.label_combo, tr("Guías y etiquetas (rollo 4×6)"), "label"),
            (self.page_combo, tr("Página del pedido (hoja carta)"), "page"),
        )
        self.test_buttons = {}
        for row, (combo, text, kind) in enumerate(rows):
            combo.setMinimumWidth(330)
            grid.addWidget(label(text), row, 0)
            grid.addWidget(combo, row, 1)
            test = button(tr("Imprimir prueba"), "printer")
            test.clicked.connect(lambda _=False, k=kind: self._test_printer(k))
            grid.addWidget(test, row, 2)
            self.test_buttons[kind] = test
        grid.setColumnStretch(1, 1)
        column.addLayout(grid)
        self.printer_note = label("", "Hint", wrap=True)
        self.printer_note.setVisible(False)
        column.addWidget(self.printer_note)
        more = QHBoxLayout()
        other = button(tr("¿No aparece? Agregarla en Impresoras"), "plus", "ghost")
        other.clicked.connect(lambda: self._go("printers"))
        more.addWidget(other)
        more.addStretch(1)
        column.addLayout(more)
        column.addStretch(1)
        self._fill_printers()
        return page

    def _fill_printers(self) -> None:
        """Las listas de impresoras. Lo que ya está elegido se respeta (también si se acaba de agregar a LabelBridge);
        si no, lo guardado o, la primera vez, lo que se propone."""
        previous = [str(combo.currentData() or "") if combo.count() else None
                    for combo in (self.label_combo, self.page_combo)]
        self.choices = printer_choices(self.ctx, self.system_printers)
        default_name = next((str(p.get("name")) for p in self.system_printers if p.get("is_default")), "")
        s = self.ctx.cfg.data.shopify
        proposed = (f"lb:{s.shipping_label_printer}" if s.shipping_label_printer else pick_label_printer(self.choices),
                    f"lb:{s.order_page_printer}" if s.order_page_printer
                    else pick_page_printer(self.choices, default_name))
        empties = (tr("Ninguna por ahora"), tr("Ninguna (no imprimir la página)"))
        for combo, before, fallback, empty in zip((self.label_combo, self.page_combo), previous, proposed, empties):
            chosen = fallback if before is None else self._current_key(before)
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(empty, NONE)
            for choice in self.choices:
                tag = tr("etiquetas") if choice.label_like else tr("hojas")
                combo.addItem(f"{choice.title} ({tag})", choice.key)
            combo.setCurrentIndex(max(0, combo.findData(chosen)))
            combo.blockSignals(False)
        if not self.choices:
            self.printer_note.setText(tr("No encontré impresoras instaladas. Agrégala en Impresoras (USB, red o Wi‑Fi) "
                                         "y vuelve aquí."))
            self.printer_note.setVisible(True)

    def _current_key(self, key: str) -> str:
        """Una instalada que ya se agregó a LabelBridge pasa a elegirse por su alias."""
        if key.startswith("sys:"):
            name = key[4:].casefold()
            profile = next((p for p in self.ctx.cfg.data.printers if p.system_name.casefold() == name), None)
            if profile is not None:
                return f"lb:{profile.alias}"
        return key

    def _choice(self, key: str) -> Choice | None:
        return next((c for c in self.choices if c.key == key), None)

    def _profile_for(self, key: str) -> PrinterProfile | None:
        """La impresora de LabelBridge para lo elegido: la que ya está o, si es una instalada, se agrega ahora."""
        choice = self._choice(key)
        if choice is None:
            return None
        if choice.alias:
            return self.ctx.cfg.find_printer(choice.alias)
        aliases = [p.alias for p in self.ctx.cfg.data.printers]
        text = choice.system_name
        profile = PrinterProfile(
            alias=suggest_alias(tr("etiquetas") if choice.label_like and "etiquetas" not in aliases else text, aliases),
            kind="system", system_name=choice.system_name, language="zpl" if looks_like_zpl(text) else "driver",
            media="4x6" if choice.label_like else "auto", dpi=300 if "300" in text else 203,
        )
        self.ctx.cfg.data.printers.append(profile)
        self._save("printers")
        log.info("Asistente de inicio: impresora %s agregada (%s)", profile.alias, profile.system_name)
        # Desde ahora es de LabelBridge: la lista la muestra por su alias.
        choice.key, choice.alias = f"lb:{profile.alias}", profile.alias
        return profile

    def _selected_profile(self, kind: str) -> PrinterProfile | None:
        combo = self.label_combo if kind == "label" else self.page_combo
        key = str(combo.currentData() or "")
        profile = self._profile_for(key) if key else None
        if profile is not None and not key.startswith("lb:"):  # recién agregada: las listas pasan a su alias
            self._fill_printers()
        return profile

    def _test_printer(self, kind: str) -> None:
        if kind == "page":  # la hoja: nunca por una de etiquetas; un plotter, solo si se confirma
            choice = self._choice(str(self.page_combo.currentData() or ""))
            problem = self._page_choice_problem(choice)
            if problem:
                error(self, tr("Imprimir prueba"), problem)
                return
            if choice is not None and choice.big and not confirm(self, tr("Imprimir prueba"), tr(
                    "«{0}» parece una impresora de gran formato o virtual. ¿Imprimir igual una hoja de prueba ahí?",
                    choice.title)):
                return
        profile = self._selected_profile(kind)
        if profile is None:
            error(self, tr("Imprimir prueba"), tr("Primero elige la impresora."))
            return
        test = self.test_buttons[kind]
        test.setEnabled(False)
        engine = self.ctx.engine

        def work() -> str:
            (engine.print_test if kind == "label" else engine.print_test_page)(profile)
            return profile.display_name()

        def finished(name: str) -> None:
            try:
                test.setEnabled(True)
                self.printer_note.setText(tr("Prueba enviada a «{0}». ¿Salió completa? Si no, cámbiala aquí o "
                                             "ajústala en Impresoras.", name))
                self.printer_note.setVisible(True)
            except RuntimeError as exc:
                if not window_closed(exc):
                    raise

        def failed(message: str) -> None:
            try:
                test.setEnabled(True)
                error(self, tr("No se pudo imprimir la prueba"), message)
            except RuntimeError as exc:
                if not window_closed(exc):
                    raise

        run_async(work, finished, failed, what=tr("Imprimir prueba"))

    def _page_choice_problem(self, choice: Choice | None) -> str:
        label_choice = self._choice(str(self.label_combo.currentData() or ""))
        if choice is None:
            return ""
        if label_choice is not None and choice.key == label_choice.key:
            return tr("La página del pedido va en hoja: elige otra impresora que la de las guías, o «Ninguna».")
        if choice.label_like:
            return tr("Esa impresora es de etiquetas y la página del pedido es una hoja carta: elige una de hojas, o "
                      "«Ninguna».")
        return ""

    def _apply_printers(self) -> str:
        problem = self._page_choice_problem(self._choice(str(self.page_combo.currentData() or "")))
        if problem:
            return problem
        labels = self._selected_profile("label")
        page = self._selected_profile("page")
        if page is not None and routing.label_like(page):
            return tr("Esa impresora es de etiquetas y la página del pedido es una hoja carta: elige una de hojas, o "
                      "«Ninguna».")
        s, g = self.ctx.cfg.data.shopify, self.ctx.cfg.data.general
        if labels is not None:
            s.shipping_label_printer = labels.alias
            if self.ctx.cfg.printer_by_id(g.default_printer_id) is None:
                g.default_printer_id = labels.id  # sin predeterminada: lo que llegue sin impresora va a la de etiquetas
        if page is not None:
            if page.alias != s.order_page_printer:  # una hoja recién elegida: se quiere la página (si no, como estaba)
                s.print_order_page = True
            s.order_page_printer = page.alias
        else:
            s.print_order_page = False  # sin hoja: no hay página (elegirla después la vuelve a activar)
        self._save("general")
        self._save("shopify_package")  # solo impresoras: no hace falta reiniciar la conexión con Shopify
        self._mark("printers")
        return ""

    # --- 3. tienda ------------------------------------------------------------------------------------
    def _store_page(self) -> QWidget:
        s = self.ctx.cfg.data.shopify
        page = QWidget()
        column = QVBoxLayout(page)
        column.setSpacing(10)
        column.addWidget(label(tr("Conecta tu tienda de Shopify"), "CardTitle"))
        column.addWidget(label(tr("Con tres datos de la app «LabelBridge» de tu tienda. Si todavía no la creaste, son "
                                  "3 minutos: abre el Dev Dashboard, crea la app, pega los permisos e instálala."),
                               "Muted", wrap=True))
        help_row = QHBoxLayout()
        dashboard = button(tr("Abrir Dev Dashboard"), "link", "ghost")
        dashboard.clicked.connect(lambda: self._open_url(DEV_DASHBOARD_URL))
        scopes = button(tr("Copiar permisos"), "copy", "ghost")
        scopes.clicked.connect(lambda: copy_to_clipboard(SCOPES_LABELS))
        guide = button(tr("Ver los pasos con fotos"), "book", "ghost")
        guide.clicked.connect(lambda: self._go("guide"))
        for widget in (dashboard, scopes, guide):
            help_row.addWidget(widget)
        help_row.addStretch(1)
        column.addLayout(help_row)
        form = QGridLayout()
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)
        self.shop_edit = QLineEdit(s.shop)
        self.shop_edit.setPlaceholderText("tu-tienda.myshopify.com")
        self.client_id_edit = QLineEdit(s.client_id)
        self.client_id_edit.setPlaceholderText(tr("Client ID de la app"))
        self.secret_edit = SecretField(tr("Client secret de la app"), copyable=False)
        self.secret_edit.setText(self.ctx.secrets.get(SHOPIFY_CLIENT_SECRET))
        for row, (text, widget) in enumerate(((tr("Tienda"), self.shop_edit), (tr("Client ID"), self.client_id_edit),
                                              (tr("Client secret"), self.secret_edit))):
            form.addWidget(label(text), row, 0)
            form.addWidget(widget, row, 1)
        form.setColumnStretch(1, 1)
        column.addLayout(form)
        connect_row = QHBoxLayout()
        self.connect_btn = button(tr("Conectar y probar"), "refresh")
        self.connect_btn.clicked.connect(self._connect)
        connect_row.addWidget(self.connect_btn)
        connect_row.addStretch(1)
        column.addLayout(connect_row)
        self.store_result = label("", "Hint", wrap=True)
        self.store_result.setVisible(False)
        column.addWidget(self.store_result)
        column.addStretch(1)
        return page

    def _store_unchanged(self) -> bool:
        """La tienda ya conectada y nada cambiado en los campos (también con token, que aquí no se muestra)."""
        s = self.ctx.cfg.data.shopify
        if not s.enabled or not s.shop:
            return False
        if s.auth_mode == "token":
            return self.shop_edit.text().strip() in ("", s.shop)
        return (self.shop_edit.text().strip() == s.shop and self.client_id_edit.text().strip() == s.client_id
                and self.secret_edit.text() == self.ctx.secrets.get(SHOPIFY_CLIENT_SECRET))

    def _check_store(self) -> tuple[str, str, str]:
        """(problema, tienda, client id) de lo escrito, sin guardar nada."""
        shop = self.shop_edit.text().strip()
        if not shop:
            return tr("Escribe el dominio de tu tienda (tu-tienda.myshopify.com)."), "", ""
        try:
            shop = normalize_shop(shop)
        except ShopifyError as exc:
            return str(exc), "", ""
        if not self.client_id_edit.text().strip() or not self.secret_edit.text().strip():
            return tr("Pega el Client ID y el Client secret de la app «LabelBridge»."), "", ""
        return "", shop, self.client_id_edit.text().strip()

    def _connect(self) -> None:
        """Prueba lo escrito. Si la tienda ya funcionaba y la prueba falla, vuelve a lo de antes (nunca se queda con
        datos sin probar)."""
        if self._store_unchanged():
            self._connected_already()
            return
        problem, shop, client_id = self._check_store()
        if problem:
            self._store_message(problem, "Warning")
            return
        s = self.ctx.cfg.data.shopify
        before = (s.shop, s.auth_mode, s.client_id, self.ctx.secrets.get(SHOPIFY_CLIENT_SECRET))
        try:  # la clave, en el almacén seguro del equipo (nunca en la configuración)
            self.ctx.secrets.update(SHOPIFY_CLIENT_SECRET, self.secret_edit.text())
        except SecretStoreError as exc:
            self._store_message(str(exc), "Warning")
            return
        s.shop, s.auth_mode, s.client_id = shop, "client_credentials", client_id
        self.shop_edit.setText(shop)
        self._save("shopify")
        self.connect_btn.setEnabled(False)
        self.connect_btn.setText(tr("Conectando…"))

        def failed(message: str) -> None:
            if before[0]:  # ya había una tienda: se vuelve a la que funcionaba (lo nuevo queda escrito para corregirlo)
                s.shop, s.auth_mode, s.client_id = before[0], before[1], before[2]
                try:
                    self.ctx.secrets.update(SHOPIFY_CLIENT_SECRET, before[3])
                except SecretStoreError as exc:
                    log.error("Asistente de inicio: no se pudo volver a la clave anterior: %s", exc)
                self._save("shopify")
            self._connect_failed(message)

        run_async(self.ctx.services.shopify.test_connection, self._connected, failed, what=tr("Conectar la tienda"))

    def _connected_already(self) -> None:
        s = self.ctx.cfg.data.shopify
        self.connected = s.shop
        self._store_message(tr("✓ Tu tienda {0} ya está conectada.", s.shop), "Hint")
        self._mark("store")

    def _connected(self, data: dict) -> None:
        try:
            self.connect_btn.setEnabled(True)
            self.connect_btn.setText(tr("Conectar y probar"))
        except RuntimeError as exc:
            if window_closed(exc):
                return
            raise
        s = self.ctx.cfg.data.shopify
        if not s.enabled and not s.activated_once:  # la primera conexión que funciona activa la tienda
            s.enabled = s.activated_once = True
            self._save("shopify")
        if data.get("name") and not self.ctx.cfg.data.labels.store_name.strip():
            self.ctx.cfg.data.labels.store_name = str(data["name"])
            self._save("general")
        self.connected = str(data.get("name") or s.shop)
        text = (tr("✓ Conectado a «{0}». LabelBridge ya revisa sus pedidos nuevos.", self.connected) if s.enabled
                else tr("✓ Conectado a «{0}», pero la tienda está apagada en LabelBridge: actívala en Shopify → "
                        "Conexión.", self.connected))
        if data.get("missing"):
            text += " " + tr("Faltan permisos en la app: {0}. Pega los permisos con «Copiar permisos» en el Dev "
                             "Dashboard y publica la versión nueva.", ", ".join(data["missing"]))
        self._store_message(text, "Warning" if data.get("missing") else "Hint")
        self._mark("store")

    def _connect_failed(self, message: str) -> None:
        try:
            self.connect_btn.setEnabled(True)
            self.connect_btn.setText(tr("Conectar y probar"))
            self._store_message(message, "Warning")
        except RuntimeError as exc:
            if not window_closed(exc):
                raise

    def _store_message(self, text: str, style: str) -> None:
        self.store_result.setObjectName(style)
        self.store_result.setText(text)
        self.store_result.setVisible(True)
        self.store_result.style().unpolish(self.store_result)
        self.store_result.style().polish(self.store_result)

    def _apply_store(self) -> str:
        if self.connected:
            return ""
        if self._store_unchanged():  # ya estaba conectada y no se tocó nada: se sigue
            self.connected = self.ctx.cfg.data.shopify.shop
            return ""
        filled = any(w.text().strip() for w in (self.shop_edit, self.client_id_edit, self.secret_edit))
        if not filled:
            return ""  # sin tienda por ahora: se sigue (el asistente o la Guía lo retoman)
        problem, _shop, _client = self._check_store()
        if problem:
            return problem
        return tr("Pulsa «Conectar y probar» para comprobar la tienda (nada se guarda sin probarlo), o borra los datos "
                  "para hacerlo después.")

    # --- 4. cada pedido --------------------------------------------------------------------------------
    def _orders_page(self) -> QWidget:
        s = self.ctx.cfg.data.shopify
        page = QWidget()
        column = QVBoxLayout(page)
        column.setSpacing(12)
        column.addWidget(label(tr("¿Qué hace LabelBridge con cada pedido nuevo?"), "CardTitle"))
        self.buy_switch = SwitchRow(
            tr("Comprar la guía de envío sola y imprimirla"),
            tr("Con Shopify Shipping, en USPS Priority Mail (sirve en cualquier empaque). Shopify cobra cada guía en tu "
               "factura. FedEx no se puede comprar desde una app: la compras en Shopify y LabelBridge la imprime."),
            s.buy_shipping_label)
        column.addWidget(self.buy_switch)
        self.notify_switch = SwitchRow(tr("Avisar al cliente con el número de seguimiento"),
                                       tr("Shopify le manda el correo de envío al comprar la guía."), s.notify_customer)
        column.addWidget(self.notify_switch)
        self.page_switch = SwitchRow(
            tr("Imprimir la página del pedido"),
            tr("Como «Imprimir página de pedido» de Shopify, con fotos y precios, en la impresora de hojas. También "
               "para los retiros en tienda."), s.print_order_page)
        column.addWidget(self.page_switch)
        self.orders_note = label("", "Warning", wrap=True)
        self.orders_note.setVisible(False)
        column.addWidget(self.orders_note)
        column.addStretch(1)
        self.buy_switch.switch.toggled.connect(lambda _on: self._refresh_orders())
        return page

    def _refresh_orders(self) -> None:
        buying = self.goal == BUY
        self.buy_switch.setVisible(buying)
        self.notify_switch.setVisible(buying)
        if buying and not self.buy_proposed:
            self.buy_proposed = True
            self.buy_switch.setChecked(True)  # lo que se eligió como objetivo, ya propuesto (se confirma al seguir)
        has_page_printer = bool(self.ctx.cfg.data.shopify.order_page_printer)
        self.page_switch.setEnabled(has_page_printer)
        self.page_switch.setChecked(self.ctx.cfg.data.shopify.print_order_page and has_page_printer)
        note = "" if has_page_printer else tr("Para la página del pedido elige antes una impresora de hojas (paso "
                                              "Impresoras).")
        if buying and not self.connected and not self.ctx.cfg.data.shopify.enabled:
            note = (note + " " if note else "") + tr("La tienda todavía no está conectada: lo elegido empieza a "
                                                     "funcionar cuando la conectes.")
        self.orders_note.setText(note)
        self.orders_note.setVisible(bool(note))

    def _apply_orders(self) -> str:
        s = self.ctx.cfg.data.shopify
        s.print_order_page = self.page_switch.isChecked() and bool(s.order_page_printer)
        if self.goal != BUY:  # «Solo imprimir»: la compra de guías queda como estaba (no se apaga sin preguntar)
            self._save("shopify")
            self._mark("orders")
            return ""
        buy = self.buy_switch.isChecked()
        if buy and not s.buy_shipping_label:
            if not confirm(self, tr("Compra automática de guías"), tr(
                    "LabelBridge comprará una guía de Shopify Shipping (USPS Priority Mail) por cada pedido nuevo pagado "
                    "y pendiente de envío, con cargo a tu cuenta de Shopify.\n\n¿Activar la compra automática?")):
                self.buy_switch.setChecked(False)
                buy = False
        s.buy_shipping_label = buy
        s.notify_customer = buy and self.notify_switch.isChecked()
        self._save("shopify")
        self._mark("orders")
        return ""

    # --- 5. siempre funcionando -------------------------------------------------------------------------
    def _always_page(self) -> QWidget:
        g = self.ctx.cfg.data.general
        page = QWidget()
        column = QVBoxLayout(page)
        column.setSpacing(12)
        column.addWidget(label(tr("Que trabaje solo, todos los días"), "CardTitle"))
        column.addWidget(label(tr("LabelBridge imprime en segundo plano: cerrar la ventana no la detiene."), "Muted",
                               wrap=True))
        self.start_switch = SwitchRow(pick(N_("Abrir LabelBridge al encender Windows"),
                                           N_("Abrir LabelBridge al iniciar sesión en la Mac")),
                                      tr("Si el equipo se reinicia, vuelve a imprimir solo."), g.start_with_windows)
        self.awake_switch = SwitchRow(tr("No dejar que el equipo se duerma"),
                                      tr("Mientras LabelBridge está abierta (la pantalla sí se apaga). Dormido, no "
                                         "imprime."), g.keep_awake)
        self.updates_switch = SwitchRow(tr("Actualizarse sola"),
                                        tr("Cuando no está imprimiendo ni comprando guías."), g.auto_install_updates)
        for widget in (self.start_switch, self.awake_switch, self.updates_switch):
            column.addWidget(widget)
        if sys.platform == "win32":
            column.addWidget(label(tr("¿Y con la PC suspendida o sin sesión iniciada? Ajustes → General → «Funcionar "
                                      "siempre»."), "Hint", wrap=True))
        column.addStretch(1)
        return page

    def _apply_always(self) -> str:
        g = self.ctx.cfg.data.general
        g.start_with_windows = self.start_switch.isChecked()
        g.keep_awake = self.awake_switch.isChecked()
        g.auto_install_updates = self.updates_switch.isChecked()
        try:
            from ..system import autostart

            autostart.apply(g.start_with_windows)
        except OSError as exc:
            return tr("No se pudo configurar el inicio automático: {0}", exc)
        self._save("general")
        self._mark("always")
        return ""

    # --- 6. listo --------------------------------------------------------------------------------------
    def _done_page(self) -> QWidget:
        page = QWidget()
        column = QVBoxLayout(page)
        column.setSpacing(10)
        column.addWidget(label(tr("¡Listo para empezar!"), "CardTitle"))
        self.summary = label("", wrap=True)
        self.summary.setTextFormat(Qt.TextFormat.RichText)
        column.addWidget(self.summary)
        column.addWidget(label(tr("El asistente de IA puede seguir por ti, poco a poco: el remitente, tus empaques, las "
                                  "reglas por tipo de envío, los avisos por email… Pídeselo con tus palabras."),
                               "Muted", wrap=True))
        actions = QHBoxLayout()
        assistant = button(tr("Seguir con el asistente"), "chat", "primary")
        assistant.clicked.connect(self._continue_with_assistant)
        actions.addWidget(assistant)
        guide = button(tr("Ver la Guía"), "book", "ghost")
        guide.clicked.connect(lambda: self._go("guide"))
        actions.addWidget(guide)
        actions.addStretch(1)
        column.addLayout(actions)
        column.addStretch(1)
        return page

    def _refresh_done(self) -> None:
        s, g = self.ctx.cfg.data.shopify, self.ctx.cfg.data.general
        done, pending = [], []
        (done if s.shipping_label_printer else pending).append(
            tr("Impresora de guías: {0}", s.shipping_label_printer) if s.shipping_label_printer
            else tr("Elegir la impresora de guías"))
        if s.print_order_page and s.order_page_printer:
            done.append(tr("Página del pedido por: {0}", s.order_page_printer))
        if self.goal != N8N:
            (done if s.enabled else pending).append(
                tr("Tienda conectada: {0}", self.connected or s.shop) if s.enabled else tr("Conectar la tienda"))
        if self.goal == BUY:
            (done if s.buy_shipping_label else pending).append(
                tr("Compra automática de guías (USPS Priority Mail)") if s.buy_shipping_label
                else tr("Activar la compra automática de guías"))
        if self.goal == N8N:
            pending.append(tr("Conectar n8n (pestaña n8n o el asistente)"))
        if g.start_with_windows:
            done.append(tr("Se abre sola al encender el equipo"))
        pending.append(tr("Hacer un pedido de prueba (la Guía te dice cómo)"))
        html = "".join(f"<p>✓ {escape(item)}</p>" for item in done)
        if pending:
            html += "<p><b>" + escape(tr("Para después:")) + "</b></p>" + "".join(
                f"<p>• {escape(item)}</p>" for item in pending)
        self.summary.setText(html)

    def _continue_with_assistant(self) -> None:
        self.accept()
        if self.open_assistant is not None:
            self.open_assistant(tr("Ya terminé el asistente de inicio. ¿Qué me falta configurar? Hazlo conmigo paso a "
                                   "paso."))

    # --- utilidades ------------------------------------------------------------------------------------
    def _save(self, section: str) -> None:
        """Guarda y avisa a las pantallas abiertas (Shopify, Ajustes) que recarguen lo guardado: si no, al pulsar
        «Guardar» en ellas escribirían lo que mostraban antes y desharían lo de este asistente."""
        self.ctx.cfg.save(section)
        self.ctx.events.emit(CONFIG_EXTERNAL, "shopify" if section.startswith("shopify") else section)

    def _mark(self, step: str) -> None:
        if step not in self.done_steps:
            self.done_steps.append(step)

    def _go(self, target: str) -> None:
        """Ir a otra pantalla de la app (Impresoras, la Guía): el asistente queda para retomarlo desde la Guía."""
        if self.navigate is not None:
            self.reject()
            self.navigate(target)

    @staticmethod
    def _open_url(url: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl(url))


def _installed_printers() -> list[dict]:
    try:
        from ..printing.outputs import list_system_printers

        return list_system_printers()
    except Exception as exc:  # noqa: BLE001  sin la lista se sigue: se agregan en Impresoras
        log.warning("Asistente de inicio: no se pudo leer las impresoras instaladas: %s", exc)
        return []


def should_open(ctx: AppContext) -> bool:
    """Se abre solo la primera vez: sin hacerlo antes y sin nada configurado todavía (quien ya usaba LabelBridge no
    lo ve; lo abre desde la Guía si quiere)."""
    g, s = ctx.cfg.data.general, ctx.cfg.data.shopify
    real_printers = [p for p in ctx.cfg.data.printers if p.kind != "file"]
    return not g.setup_done and not real_printers and not s.enabled

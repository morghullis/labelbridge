"""Guía paso a paso: deja LabelBridge funcionando después de instalarlo.

Cada paso se marca como listo solo cuando la app comprueba que de verdad está hecho.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...assistant.providers import resolve
from ...connectors.shopify import DEV_DASHBOARD_URL, SCOPES_BASIC, SCOPES_LABELS, ShopifyError, admin_url
from ...secret_store import SHOPIFY_ACCESS_TOKEN, SHOPIFY_CLIENT_SECRET
from ...updater import UPDATES_PAGE
from ..actions import enqueue_test_label
from ..context import AppContext
from ..widgets import Card, Page, Pill, button, copy_to_clipboard, label

@dataclass
class Step:
    key: str
    title: str
    summary: str
    details: str
    check: Callable[[AppContext], bool]
    actions: list[tuple[str, str, str]] = field(default_factory=list)  # (texto, icono, acción)
    optional: bool = False
    images: list[tuple[str, str]] = field(default_factory=list)  # (archivo en assets/guide, pie de foto)


# --- comprobaciones ------------------------------------------------------------------------

def _acknowledged(ctx: AppContext, key: str) -> bool:
    return key in ctx.cfg.data.general.guide_acknowledged.split(",")


def _printer_ready(ctx: AppContext) -> bool:
    return any(p.enabled for p in ctx.cfg.data.printers)


def _test_printed(ctx: AppContext) -> bool:
    return ctx.store.count_done() > 0 or ctx.store.kv_get("guide:test_printed") == "1"


def _sender_ready(ctx: AppContext) -> bool:
    return bool(ctx.cfg.data.labels.store_name.strip())


def _shopify_credentials(ctx: AppContext) -> bool:
    s = ctx.cfg.data.shopify
    if not s.shop.strip():
        return False
    if s.auth_mode == "token":
        return bool(ctx.secrets.get(SHOPIFY_ACCESS_TOKEN))
    return bool(s.client_id.strip() and ctx.secrets.get(SHOPIFY_CLIENT_SECRET))


def _shopify_connected(ctx: AppContext) -> bool:
    s = ctx.cfg.data.shopify
    if not (s.enabled and _shopify_credentials(ctx)):
        return False
    service = ctx.services.shopify
    if service.state == "ok":
        return True
    try:  # ya completó al menos una revisión de pedidos en el pasado
        return ctx.store.kv_get(service._key("cursor")) is not None
    except ShopifyError:
        return False


def _what_to_print(ctx: AppContext) -> bool:
    s = ctx.cfg.data.shopify
    chosen = s.print_order_label or s.buy_shipping_label or s.metafield_label
    return s.enabled and chosen and (_acknowledged(ctx, "what") or s.buy_shipping_label or s.metafield_label)


def _test_order(ctx: AppContext) -> bool:
    return ctx.store.count_done("shopify") > 0


def _daily(ctx: AppContext) -> bool:
    return ctx.cfg.data.general.start_with_windows and _acknowledged(ctx, "daily")


def _n8n(ctx: AppContext) -> bool:
    return ctx.cfg.data.mqtt.enabled and ctx.services.mqtt.state == "ok"


def admin_draft_order_url(ctx: AppContext) -> str:
    return admin_url(ctx.cfg.data.shopify.shop, "draft_orders/new")


def _assistant_ready(ctx: AppContext) -> bool:
    endpoint = resolve(ctx.cfg.data.assistant)
    return not endpoint.needs_key or bool(ctx.secrets.get(endpoint.key_name))


STEPS: list[Step] = [
    Step(
        "assistant",
        "¿Prefieres conversar? El asistente lo configura por ti",
        "Escríbele tus datos (impresora, tienda…) y él hace los pasos de esta guía. Opcional.",
        "<ol>"
        "<li>Ábrelo con ① el <b>botón dorado de la máscara</b>, abajo a la derecha: está en todas las pantallas "
        "(también con la pestaña <b>Asistente</b> o con <b>F1</b>).</li>"
        "<li>La primera vez, en <b>Conecta tu IA</b>: ② elige tu IA (<b>Claude, ChatGPT de OpenAI, Gemini</b>, otra "
        "compatible u Ollama en tu equipo), ③ pega tu <b>clave de API</b> y ④ pulsa <b>Guardar y probar</b>. Se paga "
        "por uso a tu proveedor: configurar todo suele costar menos de un dólar.</li>"
        "<li>⑤ Escríbele con tus palabras, por ejemplo: <i>«Tengo una Zebra por USB y quiero imprimir las guías de "
        "Shopify Shipping de mi tienda»</i>. Él agrega la impresora, conecta la tienda y te dice qué falta; la máscara "
        "te muestra si piensa, trabaja o te pregunta algo.</li>"
        "<li>Antes de cada cambio te pide permiso. Las claves se piden en una ventana segura (o con ⑥ el "
        "<b>candado</b>) y se quedan en este PC. Comprar guías siempre pide confirmación.</li>"
        "</ol>",
        _assistant_ready,
        [("Abrir el asistente", "chat", "nav:assistant")],
        optional=True,
        images=[
            ("assistant-bubble.png", "El botón del asistente (en todas las pantallas)"),
            ("assistant-connect.png", "Conecta tu IA"),
            ("assistant.png", "El chat del asistente"),
        ],
    ),
    Step(
        "printer",
        "Conecta tu impresora de etiquetas",
        "Sirve cualquier impresora: USB o instalada en Windows, o por Wi‑Fi/red sin driver.",
        "<ol>"
        "<li>Pulsa <b>Ir a Impresoras</b> (botón de abajo).</li>"
        "<li><b>Si la impresora ya funciona en este PC</b> (USB o instalada): en ② <i>Instaladas en este equipo</i> "
        "selecciónala y pulsa ③ <b>Agregar a LabelBridge</b>.</li>"
        "<li><b>Si está en la red o Wi‑Fi</b>: en ② abre <i>En la red</i>, pulsa <b>Buscar en la red</b>, elige la "
        "tuya y pulsa <b>Agregar a LabelBridge</b>. No necesita driver. Si conoces la IP, también puedes usar "
        "① <b>Agregar</b>.</li>"
        "<li>En la ventana de la impresora (segunda imagen): ① <b>Alias</b> <code>etiquetas</code>, ② <b>Lenguaje</b> "
        "<i>ZPL</i> si es Zebra o compatible (si no, <i>Driver de Windows</i>), ③ <b>Tamaño</b> 4 × 6 in y "
        "④ <b>Guardar</b>.</li>"
        "</ol>",
        _printer_ready,
        [("Ir a Impresoras", "printer", "nav:printers")],
        images=[("printers.png", "Pantalla Impresoras"), ("printer-dialog.png", "Ventana de la impresora")],
    ),
    Step(
        "test",
        "Imprime una etiqueta de prueba",
        "Comprueba que la etiqueta sale completa y del tamaño correcto.",
        "<ol>"
        "<li>Pulsa ① <b>Imprimir prueba</b> (aquí abajo o en Inicio): sale una etiqueta de ejemplo (pedido #1001).</li>"
        "<li>En ② <i>Últimos trabajos</i> debe aparecer <b>Impreso</b>. Revisa que no salga cortada ni girada; si sale "
        "mal, ajusta <i>Tamaño</i>, <i>Rotación</i> o <i>Resolución</i> en <b>Impresoras → Editar</b>.</li>"
        "<li>Si dice <b>Error</b> o <b>Reintentando</b>, el detalle en <b>Historial</b> explica la causa (impresora "
        "apagada, sin papel, IP incorrecta…).</li>"
        "</ol>",
        _test_printed,
        [("Imprimir prueba", "printer", "test"), ("Ver historial", "clock", "nav:history")],
        images=[("test-print.png", "Inicio"), ("test-result.png", "Últimos trabajos")],
    ),
    Step(
        "sender",
        "Pon los datos de tu tienda en la etiqueta",
        "El remitente que aparece arriba en cada etiqueta de pedido.",
        "<ol>"
        "<li>Ve a <b>Ajustes</b>.</li>"
        "<li>① Escribe el nombre de la tienda y ② la dirección desde la que envías.</li>"
        "<li>Elige si quieres mostrar los productos, el QR del pedido y el total, y pulsa ③ <b>Guardar ajustes</b>.</li>"
        "</ol>",
        _sender_ready,
        [("Ir a Ajustes", "sliders", "nav:settings")],
        images=[("sender.png", "Ajustes → Etiqueta de pedido generada")],
    ),
    Step(
        "shopify_app",
        "Crea la app «LabelBridge» en Shopify (una sola vez)",
        "Da permiso a LabelBridge para leer tus pedidos. Sirve para todos tus PCs.",
        "<ol>"
        "<li>Pulsa <b>Abrir Dev Dashboard</b> e inicia sesión con la cuenta dueña de la tienda.</li>"
        "<li>② <b>Apps → Create app → Start from Dev Dashboard</b>. Nombre: <code>LabelBridge</code>.</li>"
        "<li>③ En <b>Access scopes</b> pega los permisos con <b>Copiar permisos</b>: incluyen los de comprar las guías "
        "de Shopify Shipping. (Si solo vas a imprimir la etiqueta interna basta <b>Solo lectura</b>: "
        "<code>read_orders</code>).</li>"
        "<li>④ En <b>Protected customer data</b> marca <b>Name</b>, <b>Address</b> y <b>Phone</b>: la etiqueta "
        "necesita nombre, dirección y teléfono.</li>"
        "<li>⑤ <b>Release</b> (publica la versión) e <b>Install app</b> en tu tienda.</li>"
        "<li>⑥ En <b>App settings</b> copia el <b>Client ID</b> y el <b>Client secret</b>: los pegas en el paso "
        "siguiente.</li>"
        "</ol>"
        "<p>¿Aparece <i>shop_not_permitted</i>? La app y la tienda deben estar en la misma organización del Dev "
        "Dashboard.</p>",
        _shopify_credentials,
        [
            ("Abrir Dev Dashboard", "link", f"url:{DEV_DASHBOARD_URL}"),
            ("Copiar permisos", "copy", f"copy:{SCOPES_LABELS}"),
            ("Solo lectura", "copy", f"copy:{SCOPES_BASIC}"),
            ("Ir a Shopify", "shop", "nav:shopify"),
        ],
        images=[("devdashboard.png", "Dev Dashboard de Shopify (ilustración)")],
    ),
    Step(
        "shopify_on",
        "Conecta tu tienda",
        "LabelBridge empieza a revisar los pedidos nuevos.",
        "<ol>"
        "<li>Ve a <b>Shopify</b> y ① activa <b>Conector Shopify activo</b>.</li>"
        "<li>② Escribe tu tienda: <code>tu-tienda.myshopify.com</code>.</li>"
        "<li>③ Pega el <b>Client ID</b> y el <b>Client secret</b> del paso anterior.</li>"
        "<li>④ Pulsa <b>Guardar y probar conexión</b>: debe decir <i>✓ Conectado</i> y mostrar los permisos (si falta "
        "alguno, te lo indica).</li>"
        "<li>Se imprimen los pedidos <b>nuevos</b> desde ese momento. Para pedidos de horas anteriores usa "
        "<i>Reprocesar las últimas X h</i>.</li>"
        "</ol>",
        _shopify_connected,
        [("Ir a Shopify", "shop", "nav:shopify")],
        images=[("shopify-connect.png", "Pantalla Shopify → Conexión")],
    ),
    Step(
        "what",
        "Activa la guía de envío de Shopify Shipping",
        "La etiqueta con franqueo que va en el paquete: LabelBridge la compra y la imprime sola.",
        "<ol>"
        "<li>En Shopify deja listo Shopify Shipping (<b>Configuración → Envío y entrega</b>): un <b>paquete "
        "predeterminado</b>, la dirección de envío completa y un método de pago para las guías.</li>"
        "<li>En la pantalla <b>Shopify</b> de LabelBridge activa ① <b>Guía de envío de Shopify Shipping</b> y elige a "
        "la derecha ② la impresora de etiquetas. <b>Shopify cobra cada guía</b>; actívala en un solo PC.</li>"
        "<li>③ La <b>Etiqueta de pedido de LabelBridge</b> es opcional (sin franqueo, para empacar). Déjala apagada si "
        "solo quieres la guía, así no gastas dos etiquetas por pedido.</li>"
        "<li>Pulsa <b>Guardar cambios</b>.</li>"
        "</ol>"
        "<p>¿Envías con otra paquetería? Usa la etiqueta desde un metafield o n8n, o la etiqueta de pedido, y pulsa "
        "<b>Entendido</b>.</p>",
        _what_to_print,
        [("Ir a Shopify", "shop", "nav:shopify"), ("Entendido", "check", "ack:what")],
        images=[("shopify-what.png", "Pantalla Shopify → Qué imprimir en cada pedido")],
    ),
    Step(
        "order",
        "Haz un pedido de prueba",
        "Comprueba todo de punta a punta con un pedido de prueba.",
        "<ol>"
        "<li>Pulsa <b>Crear pedido de prueba</b>: se abre ① <i>Pedidos preliminares</i> en Shopify.</li>"
        "<li>② Agrega un producto y un cliente con dirección de envío.</li>"
        "<li>③ Pulsa <b>Cobrar → Marcar como pagado</b> (no se le cobra a nadie).</li>"
        "<li>En menos de un minuto sale la etiqueta y aparece en <b>Historial</b> con origen <i>Shopify</i>. Con la "
        "compra de guías activa, ese pedido compra una guía real: si no la usas, anúlala desde el pedido en Shopify. "
        "Después cancela o archiva el pedido.</li>"
        "</ol>",
        _test_order,
        [("Crear pedido de prueba", "link", "url:draft"), ("Ver historial", "clock", "nav:history")],
        images=[("draft-order.png", "Shopify → Pedidos preliminares (ilustración)")],
    ),
    Step(
        "daily",
        "Déjalo funcionando para el día a día",
        "LabelBridge trabaja solo, en segundo plano.",
        "<ul>"
        "<li>① <b>Iniciar LabelBridge al encender Windows</b> debe estar activado: si el PC se reinicia, vuelve a "
        "imprimir solo.</li>"
        "<li>Al cerrar la ventana la app <b>sigue funcionando</b> junto al reloj (① en la segunda imagen). Clic derecho "
        "→ ② <i>Pausar impresión automática</i> si necesitas detenerla.</li>"
        "<li>Deja el PC <b>encendido</b> y la impresora conectada. Si algo falla verás un aviso y la etiqueta se "
        "reintenta sola.</li>"
        "<li><b>¿Otro PC?</b> Descarga el instalador desde la <b>página de descargas</b>, instálalo y haz los pasos 1 a "
        "3. Deja el conector Shopify activo <b>solo en un PC</b> (o reparte pedidos con el filtro por tag).</li>"
        "<li>Si Windows dice <i>«Windows protegió su PC»</i> al instalar: <b>Más información → Ejecutar de todas "
        "formas</b>.</li>"
        "<li>Las <b>actualizaciones</b> llegan solas: la app te avisa y se actualiza con un clic.</li>"
        "</ul>",
        _daily,
        [
            ("Ir a Ajustes", "sliders", "nav:settings"),
            ("Página de descargas", "link", f"url:{UPDATES_PAGE}"),
            ("Entendido", "check", "ack:daily"),
        ],
        images=[("daily.png", "Ajustes → General"), ("tray.png", "Bandeja de Windows (ilustración)")],
    ),
    Step(
        "n8n",
        "n8n en tiempo real",
        "Solo si usas n8n para guías de otra paquetería o flujos propios.",
        "<ol>"
        "<li>Crea un broker MQTT gratuito (EMQX Serverless) con dos usuarios: uno para n8n y otro para este PC.</li>"
        "<li>En la pantalla <b>n8n</b> escribe servidor, usuario, contraseña y canal, y pulsa <b>Guardar y "
        "probar</b>.</li>"
        "<li>Importa las plantillas (botón <i>Abrir carpeta de plantillas</i>) y sigue su guía.</li>"
        "</ol>",
        _n8n,
        [("Ir a n8n", "flow", "nav:n8n")],
        optional=True,
    ),
]


def _safe(check: Callable[[AppContext], bool], ctx: AppContext) -> bool:
    try:
        return bool(check(ctx))
    except Exception:
        return False


def progress(ctx: AppContext) -> tuple[int, int]:
    """(pasos obligatorios listos, total de pasos obligatorios)."""
    required = [step for step in STEPS if not step.optional]
    return sum(1 for step in required if _safe(step.check, ctx)), len(required)


def is_complete(ctx: AppContext) -> bool:
    done, total = progress(ctx)
    return done == total


# --- interfaz ------------------------------------------------------------------------------

CODE_STYLE = "font-family: Consolas, Menlo, monospace; color: #3730A3; background-color: #EEF2FF;"


BADGE_STYLE = "background-color: #F59E0B; color: #1E1B4B; font-weight: 700;"
CIRCLED = "①②③④⑤⑥⑦⑧⑨"


def _highlight_code(html: str) -> str:
    """Resalta lo que hay que escribir o pegar y convierte ①②③ en las insignias doradas de las capturas."""
    html = html.replace("<code>", f'<span style="{CODE_STYLE}">&nbsp;').replace("</code>", "&nbsp;</span>")
    for number, symbol in enumerate(CIRCLED, start=1):
        html = html.replace(symbol, f'<span style="{BADGE_STYLE}">&nbsp;{number}&nbsp;</span>')
    return html


BADGE_STYLES = {
    "done": "background: #16A34A; color: white;",
    "current": "background: #4F46E5; color: white;",
    "pending": "background: #E2E8F0; color: #475569;",
    "optional": "background: #F1F5F9; color: #64748B; border: 1px dashed #94A3B8;",
}


def guide_image_path(name: str):
    from ...paths import resource_path

    return resource_path("assets", "guide", name)


class ImageViewer(QDialog):
    """Captura a tamaño completo (clic en una imagen de la guía)."""

    def __init__(self, parent: QWidget | None, pixmap: QPixmap, caption: str) -> None:
        super().__init__(parent)
        self.setWindowTitle(caption)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        view = QLabel()
        view.setPixmap(pixmap)
        view.setAlignment(Qt.AlignmentFlag.AlignCenter)
        scroll = QScrollArea()
        scroll.setWidget(view)
        scroll.setWidgetResizable(True)
        layout.addWidget(scroll)
        screen = self.screen().availableGeometry() if self.screen() else None
        logical = pixmap.size() / pixmap.devicePixelRatio()
        width, height = logical.width() + 48, logical.height() + 48
        if screen is not None:
            width, height = min(width, int(screen.width() * 0.9)), min(height, int(screen.height() * 0.9))
        self.resize(width, height)


class GuideImage(QWidget):
    """Captura señalada (2x) ajustada al ancho de la tarjeta, con pie de foto; clic para ampliar."""

    MAX_WIDTH = 760

    def __init__(self, file_name: str, caption: str) -> None:
        super().__init__()
        self.caption = caption
        self.pixmap = QPixmap(str(guide_image_path(file_name)))
        self.available = not self.pixmap.isNull()
        if not self.available:
            return
        self.pixmap.setDevicePixelRatio(2.0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        logical = self.pixmap.size() / 2.0
        scale = min(1.0, self.MAX_WIDTH / max(1, logical.width()))
        shown = self.pixmap.scaled(
            int(self.pixmap.width() * scale),
            int(self.pixmap.height() * scale),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        shown.setDevicePixelRatio(2.0)
        picture = QLabel()
        picture.setPixmap(shown)
        picture.setCursor(Qt.CursorShape.PointingHandCursor)
        picture.setToolTip("Clic para ver en grande")
        picture.setStyleSheet("QLabel { border: 1px solid #E2E8F0; border-radius: 8px; background: white; }")
        picture.mousePressEvent = lambda _event: ImageViewer(self.window(), self.pixmap, caption).exec()
        layout.addWidget(picture, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(label(f"▲ {caption} · clic para ampliar", "Small"))


class StepCard(QFrame):
    def __init__(self, number: int, step: Step, run_action: Callable[[str, QPushButton], None]) -> None:
        super().__init__()
        self.setObjectName("Card")
        self.step = step
        self.number = number
        row = QHBoxLayout(self)
        row.setContentsMargins(20, 16, 20, 16)
        row.setSpacing(16)
        self.badge = QLabel(str(number))
        self.badge.setFixedSize(34, 34)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(6)
        head = QHBoxLayout()
        title = label(step.title, "CardTitle", wrap=True)
        head.addWidget(title, 1)
        self.status = Pill("off", "")
        head.addWidget(self.status, 0, Qt.AlignmentFlag.AlignTop)
        body.addLayout(head)
        body.addWidget(label(step.summary, "Muted", wrap=True))

        self.toggle = button("Ver instrucciones", None, "ghost")
        self.toggle.setStyleSheet("color: #4F46E5; font-weight: 600; text-align: left; padding-left: 0;")
        self.toggle.clicked.connect(lambda: self.set_expanded(not self.details.isVisible()))
        body.addWidget(self.toggle, 0, Qt.AlignmentFlag.AlignLeft)

        self.details = QWidget()
        details_layout = QVBoxLayout(self.details)
        details_layout.setContentsMargins(0, 0, 0, 4)
        details_layout.setSpacing(12)
        instructions = QLabel(_highlight_code(step.details))
        instructions.setWordWrap(True)
        instructions.setTextFormat(Qt.TextFormat.RichText)
        instructions.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        instructions.setStyleSheet(
            "QLabel { background: #F8FAFC; border: 1px solid #E2E8F0; border-radius: 8px; padding: 10px 14px;"
            " color: #0F172A; line-height: 150%; }"
        )
        details_layout.addWidget(instructions)
        for file_name, caption in step.images:
            shot = GuideImage(file_name, caption)
            if shot.available:
                details_layout.addWidget(shot)
        body.addWidget(self.details)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        for index, (text, icon_name, action) in enumerate(step.actions):
            btn = button(text, icon_name, "primary" if index == 0 else None)
            btn.clicked.connect(lambda _=False, a=action, b=btn: run_action(a, b))
            actions.addWidget(btn)
        actions.addStretch(1)
        body.addLayout(actions)
        row.addLayout(body, 1)
        self.set_expanded(False)

    def set_expanded(self, expanded: bool) -> None:
        self.details.setVisible(expanded)
        self.toggle.setText("Ocultar instrucciones" if expanded else "Ver instrucciones paso a paso")

    def set_state(self, state: str) -> None:
        self.badge.setText("✓" if state == "done" else str(self.number))
        self.badge.setStyleSheet(
            f"QLabel {{ {BADGE_STYLES[state]} border-radius: 17px; font-weight: 700; font-size: 14px; }}"
        )
        texts = {"done": ("ok", "Listo"), "current": ("info", "Siguiente paso"), "pending": ("off", "Pendiente"),
                 "optional": ("off", "Opcional")}
        pill_state, text = texts[state]
        self.status.set_state(pill_state, text)


class GuidePage(Page):
    def __init__(self, ctx: AppContext, navigate: Callable[[str], None]) -> None:
        super().__init__(
            "Guía paso a paso",
            "Sigue estos pasos una vez después de instalar. Cada uno se marca con ✓ cuando la app comprueba que "
            "quedó listo.",
        )
        self.ctx = ctx
        self.navigate = navigate
        self._current_key: str | None = None

        summary = Card()
        row = QHBoxLayout()
        texts = QVBoxLayout()
        self.summary_title = label("", "CardTitle")
        self.summary_text = label("", "Muted", wrap=True)
        texts.addWidget(self.summary_title)
        texts.addWidget(self.summary_text)
        row.addLayout(texts, 1)
        summary.add(layout=row)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        summary.add(self.bar)
        self.add(summary)

        self.cards: list[StepCard] = []
        required_number = 0
        for step in STEPS:
            if not step.optional:
                required_number += 1
            card = StepCard(required_number if not step.optional else 0, step, self._run_action)
            if step.optional:
                card.badge.setText("+")
            self.cards.append(card)
            self.add(card)
        self.finish()

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(400)
        self._timer.timeout.connect(self.refresh)
        ctx.bridge.event.connect(lambda *_: self._timer.start())
        ctx.bridge.config_saved.connect(lambda *_: self._timer.start())
        self.refresh()

    def refresh(self) -> None:
        current = None
        for card in self.cards:
            done = _safe(card.step.check, self.ctx)
            if card.step.optional:
                card.set_state("done" if done else "optional")
                if not done:
                    card.badge.setText("+")
                continue
            if done:
                card.set_state("done")
            elif current is None:
                current = card
                card.set_state("current")
            else:
                card.set_state("pending")
        # Al cambiar de paso, se abren las instrucciones del siguiente.
        current_key = current.step.key if current else None
        if current_key != self._current_key:
            self._current_key = current_key
            for card in self.cards:
                card.set_expanded(card is current)

        done, total = progress(self.ctx)
        self.bar.setRange(0, total)
        self.bar.setValue(done)
        if done == total:
            self.summary_title.setText("✓ ¡Todo listo! LabelBridge ya imprime solo")
            self.summary_text.setText(
                "Los pedidos nuevos de tu tienda se imprimen automáticamente. Puedes volver a esta guía cuando "
                "quieras, por ejemplo al instalar en otro PC."
            )
        else:
            self.summary_title.setText(f"Configuración: {done} de {total} pasos listos")
            self.summary_text.setText(
                f"Siguiente: {current.step.title}." if current else "Revisa los pasos pendientes."
            )

    def _run_action(self, action: str, origin: QPushButton) -> None:
        kind, _, value = action.partition(":")
        if kind == "nav":
            self.navigate(value)
        elif kind == "url":
            url = admin_draft_order_url(self.ctx) if value == "draft" else value
            QDesktopServices.openUrl(QUrl(url))
        elif kind == "copy":
            copy_to_clipboard(value)
            previous = origin.text()
            origin.setText("¡Copiado!")
            QTimer.singleShot(1500, lambda: origin.setText(previous))
        elif kind == "test":
            enqueue_test_label(self.ctx)
        elif kind == "ack":
            general = self.ctx.cfg.data.general
            keys = {k for k in general.guide_acknowledged.split(",") if k}
            keys.add(value)
            general.guide_acknowledged = ",".join(sorted(keys))
            self.ctx.cfg.save("general")
        self._timer.start()


def guide_banner(ctx: AppContext, navigate: Callable[[str], None]) -> QWidget:
    """Aviso para Inicio mientras falten pasos (se actualiza con update_guide_banner)."""
    card = Card()
    row = QHBoxLayout()
    texts = QVBoxLayout()
    card.banner_title = label("", "CardTitle")  # type: ignore[attr-defined]
    card.banner_text = label("Sigue la guía para dejar todo funcionando.", "Muted")  # type: ignore[attr-defined]
    texts.addWidget(card.banner_title)  # type: ignore[attr-defined]
    texts.addWidget(card.banner_text)  # type: ignore[attr-defined]
    row.addLayout(texts, 1)
    go = button("Continuar la guía", "list", "primary")
    go.clicked.connect(lambda: navigate("guide"))
    row.addWidget(go)
    card.add(layout=row)
    return card


def update_guide_banner(card: QWidget, ctx: AppContext) -> None:
    done, total = progress(ctx)
    card.setVisible(done < total)
    card.banner_title.setText(f"Configuración: {done} de {total} pasos listos")  # type: ignore[attr-defined]

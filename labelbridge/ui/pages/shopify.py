"""Shopify: conexión directa (sin n8n), qué imprimir y filtros."""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...config import LABEL_SIZES
from ...connectors.shopify import SCOPES_LABELS, ShopifyError, normalize_shop
from ...events import CONFIG_EXTERNAL
from ...i18n import date_text, tr
from ...paths import resource_path
from ...printing import routing
from ...secret_store import SHOPIFY_ACCESS_TOKEN, SHOPIFY_CLIENT_SECRET, SecretStoreError
from ..context import AppContext
from ..print_routes import PrintRoutesCard
from ..widgets import (
    Card,
    FlowLayout,
    Page,
    Pill,
    SecretField,
    SwitchRow,
    button,
    confirm,
    copy_to_clipboard,
    error,
    form_layout,
    inline,
    label,
    run_async,
)


class ShopifyPage(Page):
    def __init__(self, ctx: AppContext, navigate: Callable[[str], None] | None = None) -> None:
        super().__init__(
            tr("Shopify"),
            tr("Tu tienda conectada directo: LabelBridge revisa los pedidos nuevos, compra las guías e imprime solo."),
        )
        self.ctx = ctx
        s = ctx.cfg.data.shopify
        self.status = Pill("off", tr("Desactivado"))
        self.header_actions.addWidget(self.status)

        # --- conexión ---------------------------------------------------------------------
        conn = Card(tr("Conexión"))
        self.enabled = SwitchRow(tr("Conector Shopify activo"), tr("Revisa pedidos cada pocos segundos."), s.enabled)
        conn.add(self.enabled)
        form = form_layout()
        form.setSpacing(10)
        self.shop = QLineEdit(s.shop)
        self.shop.setPlaceholderText(tr("mi-tienda.myshopify.com"))
        form.addRow(tr("Tienda"), self.shop)

        mode_row = QVBoxLayout()
        self.mode_cc = QRadioButton(tr("App del Dev Dashboard: Client ID + Client secret (recomendado, desde 2026)"))
        self.mode_token = QRadioButton(tr("Token de acceso fijo (shpat_…) de una app antigua creada en el admin"))
        group = QButtonGroup(self)
        group.addButton(self.mode_cc)
        group.addButton(self.mode_token)
        (self.mode_token if s.auth_mode == "token" else self.mode_cc).setChecked(True)
        mode_row.addWidget(self.mode_cc)
        mode_row.addWidget(self.mode_token)
        holder = QWidget()
        holder.setLayout(mode_row)
        mode_row.setContentsMargins(0, 0, 0, 0)
        form.addRow(tr("Autenticación"), holder)

        self.client_id = QLineEdit(s.client_id)
        self.client_id.setPlaceholderText(tr("Client ID"))
        form.addRow(tr("Client ID"), self.client_id)
        self.client_secret = SecretField(tr("Client secret"), copyable=False)
        self.client_secret.setText(ctx.secrets.get(SHOPIFY_CLIENT_SECRET))
        form.addRow(tr("Client secret"), self.client_secret)
        self.token = SecretField(tr("shpat_…"), copyable=False)
        self.token.setText(ctx.secrets.get(SHOPIFY_ACCESS_TOKEN))
        form.addRow(tr("Token de acceso"), self.token)
        self.api_version = QComboBox()
        self.api_version.setEditable(True)
        self.api_version.addItems(["2026-10", "2026-07"])
        self.api_version.setCurrentText(s.api_version)
        form.addRow(tr("Versión de API"), self.api_version)
        conn.add(layout=form)
        self.result = label("", "Hint", wrap=True)
        self.result.setVisible(False)
        conn.add(self.result)
        row = QHBoxLayout()
        self.test_btn = button(tr("Guardar y probar conexión"), "zap", "primary")
        self.test_btn.clicked.connect(self._test)
        guide = button(tr("Cómo crear la app en Shopify"), "file", "ghost")
        guide.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(resource_path("docs", "SHOPIFY.md")))))
        # Permisos que le faltan a la app: se copian para pegarlos en el Dev Dashboard.
        self.scopes_btn = button(tr("Copiar permisos"), "copy", "ghost",
                                 tr("Copia la lista de permisos para pegarla en la app del Dev Dashboard"))
        self.scopes_btn.clicked.connect(lambda: copy_to_clipboard(SCOPES_LABELS))
        self.scopes_btn.setVisible(False)
        row.addWidget(self.test_btn)
        row.addWidget(guide)
        row.addWidget(self.scopes_btn)
        row.addStretch(1)
        conn.add(layout=row)
        self.add(conn)
        self.mode_cc.toggled.connect(self._mode_changed)
        self._mode_changed()

        # --- qué imprimir ---------------------------------------------------------------------
        what = Card(tr("Qué imprimir en cada pedido"))
        self.buy_label = SwitchRow(
            tr("Guía de envío de Shopify Shipping (recomendado)"),
            tr("La etiqueta con franqueo que va en el paquete: LabelBridge la compra con el paquete y la tarifa "
               "predeterminados de tu tienda y la imprime. Shopify cobra cada guía en tu factura."),
            s.buy_shipping_label,
        )
        what.add(self.buy_label)
        # Las opciones de la compra, debajo del interruptor; si no caben en una línea, siguen en la de abajo.
        buy_opts = FlowLayout(spacing=20, line_spacing=8)
        buy_opts.setContentsMargins(56, 0, 0, 0)
        self.notify_customer = QCheckBox(tr("Avisar al cliente por correo"))
        self.notify_customer.setChecked(s.notify_customer)
        buy_opts.addWidget(self.notify_customer)
        self.max_per_day = QSpinBox()
        self.max_per_day.setRange(1, 5000)
        self.max_per_day.setValue(s.max_labels_per_day)
        buy_opts.addWidget(inline(label(tr("Máximo por día:")), self.max_per_day))
        self.label_size = QComboBox()
        for key, text in LABEL_SIZES.items():
            self.label_size.addItem(tr(text), key)
        self.label_size.setCurrentIndex(max(0, self.label_size.findData(s.label_size)))
        self.label_size.setToolTip(
            tr("La guía sale a este tamaño real en cualquier papel: en un rollo de etiquetas 4×6 lo llena, y en una hoja "
               "carta o A4 va arriba y centrada para recortarla. «Ajustar a la hoja» la agranda hasta llenar el papel.")
        )
        buy_opts.addWidget(inline(label(tr("Tamaño:")), self.label_size))
        what.add(layout=buy_opts)
        self.buy_warning = label(
            tr("Actívala solo en un equipo y no la combines con la plantilla 02 de n8n (comprarías dos guías por "
               "pedido). Requiere los permisos write_orders y write_merchant_managed_fulfillment_orders, Shopify "
               "Shipping activo y un paquete predeterminado en Shopify. No disponible para FedEx."),
            "Warning",
            wrap=True,
        )
        what.add(self.buy_warning)

        self.print_order = SwitchRow(
            tr("Etiqueta del pedido (opcional)"),
            tr("Sin franqueo: número de pedido, productos y dirección, para empacar. Sale cuando llega el pedido, antes "
               "de la guía; abajo eliges por qué impresora."),
            s.print_order_label,
        )
        what.add(self.print_order)

        self.print_page = SwitchRow(
            tr("Página del pedido"),
            tr("Como «Imprimir página de pedido» de Shopify, en hoja carta: productos con precio, totales, el pago y si "
               "es envío o retiro en tienda. Sale con cada pedido nuevo pagado, también los que no llevan guía, por la "
               "impresora que elijas abajo (nunca por la de las guías)."),
            s.print_order_page,
        )
        what.add(self.print_page)

        self.metafield = SwitchRow(
            tr("Etiqueta desde un metafield del pedido (URL de PDF/ZPL)"),
            tr("Para guías de otra paquetería que n8n (plantilla 03), tu web o un ERP guardan en el pedido."),
            s.metafield_label,
        )
        what.add(self.metafield)
        mf_row = QHBoxLayout()
        mf_row.setContentsMargins(56, 0, 0, 0)
        mf_row.addWidget(label(tr("Metafield:")))
        self.mf_namespace = QLineEdit(s.metafield_namespace)
        self.mf_namespace.setMaximumWidth(140)
        self.mf_key = QLineEdit(s.metafield_key)
        self.mf_key.setMaximumWidth(160)
        mf_row.addWidget(self.mf_namespace)
        mf_row.addWidget(label("."))
        mf_row.addWidget(self.mf_key)
        mf_row.addStretch(1)
        what.add(layout=mf_row)
        self.buy_label.switch.toggled.connect(self._buy_toggled)
        self._buy_toggled(s.buy_shipping_label)
        self.add(what)

        # --- por dónde sale cada cosa --------------------------------------------------------------
        self.routes = PrintRoutesCard(ctx, navigate)
        self.print_order.switch.toggled.connect(self.routes.set_order_label_active)
        self.print_page.switch.toggled.connect(self.routes.set_page_active)
        self.add(self.routes)

        # --- filtros -----------------------------------------------------------------------
        filters = Card(tr("Filtros y frecuencia"))
        form = form_layout()
        form.setSpacing(10)
        self.only_paid = QCheckBox(tr("Solo pedidos pagados (o con pago autorizado)"))
        self.only_paid.setChecked(s.only_paid)
        form.addRow(self.only_paid)
        self.only_unfulfilled = QCheckBox(tr("Solo pedidos pendientes de envío"))
        self.only_unfulfilled.setChecked(s.only_unfulfilled)
        form.addRow(self.only_unfulfilled)
        self.required_tag = QLineEdit(s.required_tag)
        self.required_tag.setPlaceholderText(tr("(opcional) p. ej. imprimir"))
        form.addRow(tr("Solo pedidos con el tag"), self.required_tag)
        self.tag_after = QLineEdit(s.tag_after_print)
        self.tag_after.setPlaceholderText(tr("(opcional) p. ej. etiqueta-impresa — requiere write_orders"))
        form.addRow(tr("Agregar tag al imprimir"), self.tag_after)
        self.poll = QSpinBox()
        self.poll.setRange(15, 3600)
        self.poll.setSuffix(" s")
        self.poll.setValue(s.poll_seconds)
        form.addRow(tr("Revisar cada"), self.poll)
        filters.add(layout=form)

        since_row = QHBoxLayout()
        self.since_label = label("", "Muted", wrap=True)  # se parte en líneas si la ventana es angosta
        since_row.addWidget(self.since_label, 1)
        since_row.addWidget(label(tr("Reprocesar las últimas")))
        self.back_hours = QSpinBox()
        self.back_hours.setRange(0, 720)
        self.back_hours.setSuffix(" h")
        since_row.addWidget(self.back_hours)
        reset = button(tr("Aplicar"), "retry")
        reset.clicked.connect(self._reset_since)
        since_row.addWidget(reset)
        filters.add(layout=since_row)
        self.add(filters)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save = button(tr("Guardar cambios"), "check", "primary")
        save.clicked.connect(self._save)
        save_row.addWidget(save)
        self.add(layout=save_row)
        self.finish()

        ctx.bridge.event.connect(self._on_event)
        ctx.bridge.config_saved.connect(self._on_config)
        self.refresh()

    # --- comportamiento -------------------------------------------------------------------------
    def _mode_changed(self) -> None:
        cc = self.mode_cc.isChecked()
        self.client_id.setEnabled(cc)
        self.client_secret.setEnabled(cc)
        self.token.setEnabled(not cc)

    def _buy_toggled(self, on: bool) -> None:
        self.notify_customer.setEnabled(on)
        self.max_per_day.setEnabled(on)
        self.label_size.setEnabled(on)

    def _on_event(self, kind: str, data) -> None:
        if kind == "connector" and isinstance(data, dict) and data.get("name") == "shopify":
            self.refresh()
        elif kind == CONFIG_EXTERNAL and data in ("shopify", "all"):
            self.load_from_config()

    def load_from_config(self) -> None:
        """Muestra lo guardado (p. ej. después de que el asistente cambió la configuración)."""
        s = self.ctx.cfg.data.shopify
        self.enabled.setChecked(s.enabled)
        self.shop.setText(s.shop)
        (self.mode_token if s.auth_mode == "token" else self.mode_cc).setChecked(True)
        self.client_id.setText(s.client_id)
        self.client_secret.setText(self.ctx.secrets.get(SHOPIFY_CLIENT_SECRET))
        self.token.setText(self.ctx.secrets.get(SHOPIFY_ACCESS_TOKEN))
        self.api_version.setCurrentText(s.api_version)
        self.buy_label.setChecked(s.buy_shipping_label)
        self.notify_customer.setChecked(s.notify_customer)
        self.max_per_day.setValue(s.max_labels_per_day)
        self.label_size.setCurrentIndex(max(0, self.label_size.findData(s.label_size)))
        self.print_order.setChecked(s.print_order_label)
        self.print_page.setChecked(s.print_order_page)
        self.metafield.setChecked(s.metafield_label)
        self.mf_namespace.setText(s.metafield_namespace)
        self.mf_key.setText(s.metafield_key)
        self.routes.load()
        self.only_paid.setChecked(s.only_paid)
        self.only_unfulfilled.setChecked(s.only_unfulfilled)
        self.required_tag.setText(s.required_tag)
        self.tag_after.setText(s.tag_after_print)
        self.poll.setValue(s.poll_seconds)
        self.refresh()

    def _on_config(self, section: str) -> None:
        if section in ("printers", "general", "all"):  # la predeterminada se guarda en «general»
            self.routes.reload_printers()

    def refresh(self) -> None:
        svc = self.ctx.services.shopify
        texts = {"ok": tr("Conectado"), "connecting": tr("Conectando…"), "error": tr("Error"), "off": tr("Desactivado"), "warn": tr("Aviso")}
        self.status.set_state(svc.state, texts.get(svc.state, svc.state))
        self.status.setToolTip(svc.message)
        if self.ctx.cfg.data.shopify.shop.strip():
            try:
                normalize_shop(self.ctx.cfg.data.shopify.shop)
                since = self.ctx.store.kv_get(svc._key("since"))
            except ShopifyError:
                since = None
            if since:
                when = date_text(float(since))
                self.since_label.setText(tr("Se procesan pedidos creados desde el {0}.", when))
            else:
                self.since_label.setText(tr("Al activarse procesará los pedidos creados desde ese momento."))
        else:
            self.since_label.setText("")

    def _collect(self) -> str | None:
        s = self.ctx.cfg.data.shopify
        shop = self.shop.text().strip()
        if shop:
            try:
                shop = normalize_shop(shop)
            except ShopifyError as exc:
                return str(exc)
        if self.enabled.isChecked() and not shop:
            return tr("Escribe el dominio de la tienda.")
        if self.buy_label.isChecked() and not s.buy_shipping_label:
            if not confirm(
                self,
                tr("Compra automática de etiquetas"),
                tr("LabelBridge comprará una etiqueta de Shopify Shipping por cada pedido nuevo pagado y pendiente "
                   "de envío, con cargo a tu cuenta de Shopify.\n\n¿Activar la compra automática?"),
            ):
                self.buy_label.setChecked(False)
        try:  # las claves primero: si el almacén falla, la configuración no queda a medio guardar
            self.ctx.secrets.update(SHOPIFY_CLIENT_SECRET, self.client_secret.text())
            self.ctx.secrets.update(SHOPIFY_ACCESS_TOKEN, self.token.text())
        except SecretStoreError as exc:
            return str(exc)
        s.enabled = self.enabled.isChecked()
        s.activated_once = s.activated_once or s.enabled  # ya decidió la persona: no se vuelve a activar solo
        s.shop = shop
        s.auth_mode = "token" if self.mode_token.isChecked() else "client_credentials"
        s.client_id = self.client_id.text().strip()
        s.api_version = self.api_version.currentText().strip() or "2026-10"
        s.print_order_label = self.print_order.isChecked()
        s.order_label_printer = self.routes.alias(routing.ORDER)
        s.print_order_page = self.print_page.isChecked()
        s.order_page_printer = self.routes.alias(routing.PAGE)
        s.buy_shipping_label = self.buy_label.isChecked()
        s.shipping_label_printer = self.routes.alias(routing.SHIPPING)
        s.notify_customer = self.notify_customer.isChecked()
        s.max_labels_per_day = self.max_per_day.value()
        s.label_size = self.label_size.currentData() or "4x6"
        s.metafield_label = self.metafield.isChecked()
        s.metafield_printer = self.routes.alias(routing.METAFIELD)
        s.metafield_namespace = self.mf_namespace.text().strip() or "custom"
        s.metafield_key = self.mf_key.text().strip() or "label_url"
        s.only_paid = self.only_paid.isChecked()
        s.only_unfulfilled = self.only_unfulfilled.isChecked()
        s.required_tag = self.required_tag.text().strip()
        s.tag_after_print = self.tag_after.text().strip()
        s.poll_seconds = self.poll.value()
        return None

    def _save(self) -> bool:
        problem = self._collect()
        if problem:
            error(self, tr("Shopify"), problem)
            return False
        self.ctx.cfg.save("shopify")
        self.refresh()
        return True

    def test_now(self) -> None:
        """Probar la conexión ya (la tienda del instalador, con su clave): si funciona, la tienda queda activa."""
        self._test()

    def _test(self) -> None:
        if not self._save():
            return
        if not self.ctx.cfg.data.shopify.shop:
            error(self, tr("Shopify"), tr("Escribe el dominio de la tienda."))
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText(tr("Probando…"))
        run_async(self.ctx.services.shopify.test_connection, self._tested, self._test_failed)

    def _tested(self, data: dict) -> None:
        self.test_btn.setEnabled(True)
        self.test_btn.setText(tr("Guardar y probar conexión"))
        if data.get("name") and not self.ctx.cfg.data.labels.store_name.strip():
            self.ctx.cfg.data.labels.store_name = str(data["name"])  # remitente de la etiqueta
            self.ctx.cfg.save("general")
        scopes = ", ".join(sorted(data.get("scopes") or [])) or "—"
        text = tr("Conectado a «{0}» ({1}).\nPermisos: {2}", data.get('name'), data.get('myshopifyDomain'), scopes)
        if self._activate_first_time():
            text += tr("\nLa tienda quedó activa: LabelBridge ya revisa sus pedidos.")
        if data.get("missing"):
            text += tr("\nFaltan permisos: ") + ", ".join(data["missing"])
        if data.get("optional_missing"):
            text += tr("\nOpcionales que aún no tiene la app (lo demás funciona igual): ") + ", ".join(
                data["optional_missing"]) + tr(". Agrégalos con «Copiar permisos» en el Dev Dashboard y aprueba la "
                                               "versión nueva de la app.")
        self.scopes_btn.setVisible(bool(data.get("missing") or data.get("optional_missing")))
        self.result.setObjectName("Warning" if data.get("missing") else "Hint")
        self.result.setStyleSheet("")
        self.result.setText(text)
        self.result.setVisible(True)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)

    def _activate_first_time(self) -> bool:
        """La primera conexión que funciona activa el conector (instalador, guía o Mac). Si la persona ya lo activó o
        lo apagó alguna vez, se respeta lo que eligió."""
        s = self.ctx.cfg.data.shopify
        if s.enabled or s.activated_once:
            return False
        s.enabled = s.activated_once = True
        self.ctx.cfg.save("shopify")
        self.enabled.setChecked(True)
        self.ctx.events.notify(tr("Tienda conectada"), tr("LabelBridge ya revisa los pedidos de {0}.", s.shop))
        self.refresh()
        return True

    def _test_failed(self, message: str) -> None:
        self.test_btn.setEnabled(True)
        self.test_btn.setText(tr("Guardar y probar conexión"))
        self.result.setObjectName("Warning")
        self.result.setText(message)
        self.result.setVisible(True)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)

    def _reset_since(self) -> None:
        if not self.ctx.cfg.data.shopify.shop.strip():
            error(self, tr("Shopify"), tr("Primero guarda el dominio de la tienda."))
            return
        hours = self.back_hours.value()
        if self.ctx.cfg.data.shopify.buy_shipping_label and hours and not confirm(
            self,
            tr("Reprocesar pedidos"),
            tr("La compra automática está activa: LabelBridge comprará (y Shopify cobrará) la guía de cada pedido pagado y sin enviar de las últimas {0} horas.\n\n¿Reprocesar?", hours),
        ):
            return
        try:
            self.ctx.services.shopify.reset_since(hours)
        except ShopifyError as exc:
            error(self, tr("Shopify"), str(exc))
            return
        self.refresh()

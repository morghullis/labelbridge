"""Shopify: conexión directa (sin n8n), qué imprimir y filtros."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ...config import LABEL_SIZES
from ...connectors.shopify import ShopifyError, normalize_shop
from ...events import CONFIG_EXTERNAL
from ...paths import resource_path
from ...secret_store import SHOPIFY_ACCESS_TOKEN, SHOPIFY_CLIENT_SECRET
from ..context import AppContext
from ..widgets import Card, Page, Pill, SecretField, SwitchRow, button, confirm, error, label, run_async


def printer_combo(ctx: AppContext, current: str) -> QComboBox:
    combo = QComboBox()
    combo.addItem("Impresora predeterminada", "")
    for p in ctx.cfg.data.printers:
        combo.addItem(p.alias, p.alias)
    if current and combo.findData(current) < 0:
        combo.addItem(current, current)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    return combo


class ShopifyPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            "Shopify",
            "Conexión directa con tu tienda: LabelBridge revisa los pedidos nuevos e imprime sin pasar por n8n.",
        )
        self.ctx = ctx
        s = ctx.cfg.data.shopify
        self.status = Pill("off", "Desactivado")
        self.header_actions.addWidget(self.status)

        # --- conexión ---------------------------------------------------------------------
        conn = Card("Conexión")
        self.enabled = SwitchRow("Conector Shopify activo", "Revisa pedidos cada pocos segundos.", s.enabled)
        conn.add(self.enabled)
        form = QFormLayout()
        form.setSpacing(10)
        self.shop = QLineEdit(s.shop)
        self.shop.setPlaceholderText("mi-tienda.myshopify.com")
        form.addRow("Tienda", self.shop)

        mode_row = QVBoxLayout()
        self.mode_cc = QRadioButton("App del Dev Dashboard: Client ID + Client secret (recomendado, desde 2026)")
        self.mode_token = QRadioButton("Token de acceso fijo (shpat_…) de una app antigua creada en el admin")
        group = QButtonGroup(self)
        group.addButton(self.mode_cc)
        group.addButton(self.mode_token)
        (self.mode_token if s.auth_mode == "token" else self.mode_cc).setChecked(True)
        mode_row.addWidget(self.mode_cc)
        mode_row.addWidget(self.mode_token)
        holder = QWidget()
        holder.setLayout(mode_row)
        mode_row.setContentsMargins(0, 0, 0, 0)
        form.addRow("Autenticación", holder)

        self.client_id = QLineEdit(s.client_id)
        self.client_id.setPlaceholderText("Client ID")
        form.addRow("Client ID", self.client_id)
        self.client_secret = SecretField("Client secret", copyable=False)
        self.client_secret.setText(ctx.secrets.get(SHOPIFY_CLIENT_SECRET))
        form.addRow("Client secret", self.client_secret)
        self.token = SecretField("shpat_…", copyable=False)
        self.token.setText(ctx.secrets.get(SHOPIFY_ACCESS_TOKEN))
        form.addRow("Token de acceso", self.token)
        self.api_version = QComboBox()
        self.api_version.setEditable(True)
        self.api_version.addItems(["2026-10", "2026-07"])
        self.api_version.setCurrentText(s.api_version)
        form.addRow("Versión de API", self.api_version)
        conn.add(layout=form)
        self.result = label("", "Hint", wrap=True)
        self.result.setVisible(False)
        conn.add(self.result)
        row = QHBoxLayout()
        self.test_btn = button("Guardar y probar conexión", "zap", "primary")
        self.test_btn.clicked.connect(self._test)
        guide = button("Cómo crear la app en Shopify", "file", "ghost")
        guide.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(resource_path("docs", "SHOPIFY.md")))))
        row.addWidget(self.test_btn)
        row.addWidget(guide)
        row.addStretch(1)
        conn.add(layout=row)
        self.add(conn)
        self.mode_cc.toggled.connect(self._mode_changed)
        self._mode_changed()

        # --- qué imprimir --------------------------------------------------------------------
        what = Card("Qué imprimir en cada pedido")
        grid = QGridLayout()
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(14)

        self.buy_label = SwitchRow(
            "Guía de envío de Shopify Shipping (recomendado)",
            "La etiqueta con franqueo que va en el paquete: LabelBridge la compra con el paquete y la tarifa "
            "predeterminados de tu tienda y la imprime. Shopify cobra cada guía en tu factura.",
            s.buy_shipping_label,
        )
        self.label_printer = printer_combo(ctx, s.shipping_label_printer)
        grid.addWidget(self.buy_label, 0, 0)
        grid.addWidget(self.label_printer, 0, 1)

        buy_opts = QHBoxLayout()
        buy_opts.setContentsMargins(56, 0, 0, 0)
        self.notify_customer = QCheckBox("Avisar al cliente por correo")
        self.notify_customer.setChecked(s.notify_customer)
        buy_opts.addWidget(self.notify_customer)
        buy_opts.addSpacing(16)
        buy_opts.addWidget(label("Máximo por día:"))
        self.max_per_day = QSpinBox()
        self.max_per_day.setRange(1, 5000)
        self.max_per_day.setValue(s.max_labels_per_day)
        buy_opts.addWidget(self.max_per_day)
        buy_opts.addSpacing(16)
        buy_opts.addWidget(label("Tamaño:"))
        self.label_size = QComboBox()
        for key, text in LABEL_SIZES.items():
            self.label_size.addItem(text, key)
        self.label_size.setCurrentIndex(max(0, self.label_size.findData(s.label_size)))
        self.label_size.setToolTip(
            "La guía sale a este tamaño real en cualquier papel: en un rollo de etiquetas 4×6 lo llena, y en una hoja "
            "carta o A4 va arriba y centrada para recortarla. «Ajustar a la hoja» la agranda hasta llenar el papel."
        )
        buy_opts.addWidget(self.label_size)
        buy_opts.addStretch(1)
        grid.addLayout(buy_opts, 1, 0, 1, 2)
        self.buy_warning = label(
            "⚠ Actívala solo en un equipo y no la combines con la plantilla 02 de n8n (comprarías dos "
            "guías por pedido). Requiere los permisos write_orders y write_merchant_managed_fulfillment_orders, "
            "Shopify Shipping activo y un paquete predeterminado en Shopify. No disponible para FedEx.",
            "Warning",
            wrap=True,
        )
        grid.addWidget(self.buy_warning, 2, 0, 1, 2)

        self.print_order = SwitchRow(
            "Etiqueta de pedido de LabelBridge (opcional)",
            "Etiqueta interna sin franqueo: dirección, productos y código del pedido. Útil para empacar.",
            s.print_order_label,
        )
        self.order_printer = printer_combo(ctx, s.order_label_printer)
        grid.addWidget(self.print_order, 3, 0)
        grid.addWidget(self.order_printer, 3, 1)

        self.metafield = SwitchRow(
            "Etiqueta desde un metafield del pedido (URL de PDF/ZPL)",
            "Para guías de otra paquetería que n8n (plantilla 03), tu web o un ERP guardan en el pedido.",
            s.metafield_label,
        )
        self.metafield_printer = printer_combo(ctx, s.metafield_printer)
        grid.addWidget(self.metafield, 4, 0)
        grid.addWidget(self.metafield_printer, 4, 1)
        mf_row = QHBoxLayout()
        mf_row.setContentsMargins(56, 0, 0, 0)
        mf_row.addWidget(label("Metafield:"))
        self.mf_namespace = QLineEdit(s.metafield_namespace)
        self.mf_namespace.setMaximumWidth(140)
        self.mf_key = QLineEdit(s.metafield_key)
        self.mf_key.setMaximumWidth(160)
        mf_row.addWidget(self.mf_namespace)
        mf_row.addWidget(label("."))
        mf_row.addWidget(self.mf_key)
        mf_row.addStretch(1)
        grid.addLayout(mf_row, 5, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        what.add(layout=grid)
        self.buy_label.switch.toggled.connect(self._buy_toggled)
        self._buy_toggled(s.buy_shipping_label)
        self.add(what)

        # --- filtros -----------------------------------------------------------------------
        filters = Card("Filtros y frecuencia")
        form = QFormLayout()
        form.setSpacing(10)
        self.only_paid = QCheckBox("Solo pedidos pagados (o con pago autorizado)")
        self.only_paid.setChecked(s.only_paid)
        form.addRow("", self.only_paid)
        self.only_unfulfilled = QCheckBox("Solo pedidos pendientes de envío")
        self.only_unfulfilled.setChecked(s.only_unfulfilled)
        form.addRow("", self.only_unfulfilled)
        self.required_tag = QLineEdit(s.required_tag)
        self.required_tag.setPlaceholderText("(opcional) p. ej. imprimir")
        form.addRow("Solo pedidos con el tag", self.required_tag)
        self.tag_after = QLineEdit(s.tag_after_print)
        self.tag_after.setPlaceholderText("(opcional) p. ej. etiqueta-impresa — requiere write_orders")
        form.addRow("Agregar tag al imprimir", self.tag_after)
        self.poll = QSpinBox()
        self.poll.setRange(15, 3600)
        self.poll.setSuffix(" s")
        self.poll.setValue(s.poll_seconds)
        form.addRow("Revisar cada", self.poll)
        filters.add(layout=form)

        since_row = QHBoxLayout()
        self.since_label = label("", "Muted")
        since_row.addWidget(self.since_label, 1)
        since_row.addWidget(label("Reprocesar las últimas"))
        self.back_hours = QSpinBox()
        self.back_hours.setRange(0, 720)
        self.back_hours.setSuffix(" h")
        since_row.addWidget(self.back_hours)
        reset = button("Aplicar", "retry")
        reset.clicked.connect(self._reset_since)
        since_row.addWidget(reset)
        filters.add(layout=since_row)
        self.add(filters)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save = button("Guardar cambios", "check", "primary")
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
        self.label_printer.setEnabled(on)
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
        self.metafield.setChecked(s.metafield_label)
        self.mf_namespace.setText(s.metafield_namespace)
        self.mf_key.setText(s.metafield_key)
        for combo, alias in (
            (self.label_printer, s.shipping_label_printer),
            (self.order_printer, s.order_label_printer),
            (self.metafield_printer, s.metafield_printer),
        ):
            if alias and combo.findData(alias) < 0:
                combo.addItem(alias, alias)
            combo.setCurrentIndex(max(0, combo.findData(alias)))
        self.only_paid.setChecked(s.only_paid)
        self.only_unfulfilled.setChecked(s.only_unfulfilled)
        self.required_tag.setText(s.required_tag)
        self.tag_after.setText(s.tag_after_print)
        self.poll.setValue(s.poll_seconds)
        self.refresh()

    def _on_config(self, section: str) -> None:
        if section in ("printers", "all"):
            for combo, current in (
                (self.order_printer, self.order_printer.currentData()),
                (self.label_printer, self.label_printer.currentData()),
                (self.metafield_printer, self.metafield_printer.currentData()),
            ):
                fresh = printer_combo(self.ctx, current)
                combo.clear()
                for i in range(fresh.count()):
                    combo.addItem(fresh.itemText(i), fresh.itemData(i))
                combo.setCurrentIndex(max(0, combo.findData(current)))

    def refresh(self) -> None:
        svc = self.ctx.services.shopify
        texts = {"ok": "Conectado", "connecting": "Conectando…", "error": "Error", "off": "Desactivado", "warn": "Aviso"}
        self.status.set_state(svc.state, texts.get(svc.state, svc.state))
        self.status.setToolTip(svc.message)
        if self.ctx.cfg.data.shopify.shop.strip():
            try:
                normalize_shop(self.ctx.cfg.data.shopify.shop)
                since = self.ctx.store.kv_get(svc._key("since"))
            except ShopifyError:
                since = None
            if since:
                when = datetime.fromtimestamp(float(since)).strftime("%d/%m/%Y %H:%M")
                self.since_label.setText(f"Se procesan pedidos creados desde el {when}.")
            else:
                self.since_label.setText("Al activarse procesará los pedidos creados desde ese momento.")
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
            return "Escribe el dominio de la tienda."
        if self.buy_label.isChecked() and not s.buy_shipping_label:
            if not confirm(
                self,
                "Compra automática de etiquetas",
                "LabelBridge comprará una etiqueta de Shopify Shipping por cada pedido nuevo pagado y pendiente "
                "de envío, con cargo a tu cuenta de Shopify.\n\n¿Activar la compra automática?",
            ):
                self.buy_label.setChecked(False)
        s.enabled = self.enabled.isChecked()
        s.shop = shop
        s.auth_mode = "token" if self.mode_token.isChecked() else "client_credentials"
        s.client_id = self.client_id.text().strip()
        s.api_version = self.api_version.currentText().strip() or "2026-10"
        s.print_order_label = self.print_order.isChecked()
        s.order_label_printer = self.order_printer.currentData() or ""
        s.buy_shipping_label = self.buy_label.isChecked()
        s.shipping_label_printer = self.label_printer.currentData() or ""
        s.notify_customer = self.notify_customer.isChecked()
        s.max_labels_per_day = self.max_per_day.value()
        s.label_size = self.label_size.currentData() or "4x6"
        s.metafield_label = self.metafield.isChecked()
        s.metafield_printer = self.metafield_printer.currentData() or ""
        s.metafield_namespace = self.mf_namespace.text().strip() or "custom"
        s.metafield_key = self.mf_key.text().strip() or "label_url"
        s.only_paid = self.only_paid.isChecked()
        s.only_unfulfilled = self.only_unfulfilled.isChecked()
        s.required_tag = self.required_tag.text().strip()
        s.tag_after_print = self.tag_after.text().strip()
        s.poll_seconds = self.poll.value()
        self.ctx.secrets.set(SHOPIFY_CLIENT_SECRET, self.client_secret.text())
        self.ctx.secrets.set(SHOPIFY_ACCESS_TOKEN, self.token.text())
        return None

    def _save(self) -> bool:
        problem = self._collect()
        if problem:
            error(self, "Shopify", problem)
            return False
        self.ctx.cfg.save("shopify")
        self.refresh()
        return True

    def _test(self) -> None:
        if not self._save():
            return
        if not self.ctx.cfg.data.shopify.shop:
            error(self, "Shopify", "Escribe el dominio de la tienda.")
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText("Probando…")
        run_async(self.ctx.services.shopify.test_connection, self._tested, self._test_failed)

    def _tested(self, data: dict) -> None:
        self.test_btn.setEnabled(True)
        self.test_btn.setText("Guardar y probar conexión")
        if data.get("name") and not self.ctx.cfg.data.labels.store_name.strip():
            self.ctx.cfg.data.labels.store_name = str(data["name"])  # remitente de la etiqueta
            self.ctx.cfg.save("general")
        scopes = ", ".join(sorted(data.get("scopes") or [])) or "—"
        text = f"✓ Conectado a «{data.get('name')}» ({data.get('myshopifyDomain')}).\nPermisos: {scopes}"
        if data.get("missing"):
            text += "\n⚠ Faltan permisos: " + ", ".join(data["missing"])
        self.result.setObjectName("Warning" if data.get("missing") else "Hint")
        self.result.setStyleSheet("")
        self.result.setText(text)
        self.result.setVisible(True)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)

    def _test_failed(self, message: str) -> None:
        self.test_btn.setEnabled(True)
        self.test_btn.setText("Guardar y probar conexión")
        self.result.setObjectName("Warning")
        self.result.setText(f"✗ {message}")
        self.result.setVisible(True)
        self.result.style().unpolish(self.result)
        self.result.style().polish(self.result)

    def _reset_since(self) -> None:
        if not self.ctx.cfg.data.shopify.shop.strip():
            error(self, "Shopify", "Primero guarda el dominio de la tienda.")
            return
        try:
            self.ctx.services.shopify.reset_since(self.back_hours.value())
        except ShopifyError as exc:
            error(self, "Shopify", str(exc))
            return
        self.refresh()

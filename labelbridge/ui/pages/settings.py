"""Ajustes generales, etiqueta generada y datos."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QSpinBox

from ... import APP_NAME, __version__
from ...paths import data_dir, logs_dir, output_dir
from ...system import autostart
from ...updater import UPDATES_PAGE
from ..context import AppContext
from ..widgets import Card, Page, button, label


class SettingsPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__("Ajustes", "Comportamiento del agente en este equipo.")
        self.ctx = ctx
        g = ctx.cfg.data.general
        lb = ctx.cfg.data.labels

        general = Card("General")
        form = QFormLayout()
        form.setSpacing(10)
        self.agent = QLineEdit(g.agent_name)
        form.addRow("Nombre de este equipo", self.agent)
        self.autostart = QCheckBox("Iniciar LabelBridge al encender Windows")
        self.autostart.setChecked(g.start_with_windows)
        if not autostart.supported():
            self.autostart.setToolTip("Se aplica en la versión instalada para Windows")
        form.addRow("", self.autostart)
        self.minimized = QCheckBox("Iniciar minimizado en la bandeja del sistema")
        self.minimized.setChecked(g.start_minimized)
        form.addRow("", self.minimized)
        self.auto_discover = QCheckBox("Buscar impresoras automáticamente al conectarse a una red (Wi‑Fi / LAN)")
        self.auto_discover.setChecked(g.auto_discover)
        form.addRow("", self.auto_discover)
        self.notify_errors = QCheckBox("Avisar cuando una etiqueta no se pueda imprimir")
        self.notify_errors.setChecked(g.notify_errors)
        form.addRow("", self.notify_errors)
        self.notify_printed = QCheckBox("Avisar cada vez que se imprime una etiqueta")
        self.notify_printed.setChecked(g.notify_printed)
        form.addRow("", self.notify_printed)
        self.retries = QSpinBox()
        self.retries.setRange(0, 20)
        self.retries.setValue(g.max_retries)
        form.addRow("Reintentos si la impresora falla", self.retries)
        self.retention = QSpinBox()
        self.retention.setRange(1, 365)
        self.retention.setSuffix(" días")
        self.retention.setValue(g.retention_days)
        form.addRow("Guardar historial", self.retention)
        general.add(layout=form)
        self.add(general)

        label_card = Card(
            "Etiqueta de pedido generada",
            "Datos del remitente y contenido de la etiqueta que LabelBridge genera a partir del pedido.",
        )
        form = QFormLayout()
        form.setSpacing(10)
        self.store_name = QLineEdit(lb.store_name)
        self.store_name.setPlaceholderText("Nombre de tu tienda")
        form.addRow("Remitente", self.store_name)
        self.sender = QPlainTextEdit(lb.sender_address)
        self.sender.setPlaceholderText("Calle y número\nCiudad, Estado CP\nTeléfono")
        self.sender.setFixedHeight(84)
        form.addRow("Dirección", self.sender)
        self.show_items = QCheckBox("Incluir la lista de productos")
        self.show_items.setChecked(lb.show_items)
        form.addRow("", self.show_items)
        self.show_qr = QCheckBox("Incluir QR al pedido en Shopify (para escanear en bodega)")
        self.show_qr.setChecked(lb.show_qr)
        form.addRow("", self.show_qr)
        self.show_price = QCheckBox("Incluir el total del pedido")
        self.show_price.setChecked(lb.show_price)
        form.addRow("", self.show_price)
        label_card.add(layout=form)
        self.add(label_card)

        row = QHBoxLayout()
        row.addStretch(1)
        save = button("Guardar ajustes", "check", "primary")
        self.save_btn = save
        save.clicked.connect(self._save)
        row.addWidget(save)
        self.add(layout=row)

        updates = Card("Actualizaciones", "LabelBridge avisa cuando hay una versión nueva y se actualiza con un clic.")
        self.auto_update = QCheckBox("Avisarme cuando haya una nueva versión (recomendado)")
        self.auto_update.setChecked(g.auto_update_check)
        updates.add(self.auto_update)
        row = QHBoxLayout()
        row.addWidget(label(f"Versión instalada: <b>{__version__}</b>"))
        row.addStretch(1)
        check = button("Buscar actualizaciones ahora", "download", "primary")
        check.clicked.connect(lambda: self.window().check_updates_now())
        page_btn = button("Página de descargas", "link")
        page_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(UPDATES_PAGE)))
        row.addWidget(page_btn)
        row.addWidget(check)
        updates.add(layout=row)
        self.add(updates)

        data = Card("Datos y soporte")
        data.add(
            label(
                f"{APP_NAME} {__version__} · Datos en: {data_dir()}\n"
                f"Secretos guardados en: {ctx.secrets.backend_name}",
                "Muted",
                wrap=True,
                selectable=True,
            )
        )
        row = QHBoxLayout()
        for text, path in (("Carpeta de datos", data_dir()), ("Registros", logs_dir()), ("Salida de pruebas", output_dir())):
            btn = button(text, "folder")
            btn.clicked.connect(lambda _=False, p=path: QDesktopServices.openUrl(QUrl.fromLocalFile(str(p))))
            row.addWidget(btn)
        row.addStretch(1)
        data.add(layout=row)
        self.add(data)
        self.finish()

    def _save(self) -> None:
        g = self.ctx.cfg.data.general
        lb = self.ctx.cfg.data.labels
        g.agent_name = self.agent.text().strip() or g.agent_name
        g.start_with_windows = self.autostart.isChecked()
        g.start_minimized = self.minimized.isChecked()
        g.auto_discover = self.auto_discover.isChecked()
        g.auto_update_check = self.auto_update.isChecked()
        g.notify_errors = self.notify_errors.isChecked()
        g.notify_printed = self.notify_printed.isChecked()
        g.max_retries = self.retries.value()
        g.retention_days = self.retention.value()
        lb.store_name = self.store_name.text().strip()
        lb.sender_address = self.sender.toPlainText().strip()
        lb.show_items = self.show_items.isChecked()
        lb.show_qr = self.show_qr.isChecked()
        lb.show_price = self.show_price.isChecked()
        self.ctx.cfg.save("general")
        try:
            autostart.apply(g.start_with_windows)
        except OSError:
            pass

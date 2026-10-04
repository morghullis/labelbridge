"""Ajustes generales, etiqueta generada y datos."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
)

from ... import APP_NAME, __version__
from ...config import ASSISTANT_EFFORTS, ASSISTANT_MODELS
from ...events import CONFIG_EXTERNAL
from ...paths import data_dir, logs_dir, output_dir
from ...system import autostart
from ...updater import UPDATES_PAGE
from ..context import AppContext
from ..widgets import Card, Page, button, label


def _combo(options: dict[str, str]) -> QComboBox:
    combo = QComboBox()
    for key, text in options.items():
        combo.addItem(text, key)
    return combo


def _select(combo: QComboBox, value: str) -> None:
    combo.setCurrentIndex(max(0, combo.findData(value)))


def _spin(low: int, high: int, suffix: str) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(low, high)
    spin.setSuffix(suffix)
    return spin


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

        assistant = Card(
            "Asistente",
            "Usa Claude de Anthropic con tu clave de API (se paga por uso). El modelo y el nivel se aplican a las "
            "conversaciones nuevas.",
        )
        form = QFormLayout()
        form.setSpacing(10)
        self.assistant_on = QCheckBox("Asistente activado")
        form.addRow("", self.assistant_on)
        self.assistant_model = _combo(ASSISTANT_MODELS)
        form.addRow("Modelo", self.assistant_model)
        self.assistant_effort = _combo(ASSISTANT_EFFORTS)
        form.addRow("Nivel de razonamiento", self.assistant_effort)
        self.assistant_approval = _combo(
            {"changes": "Pedir permiso para cada cambio (recomendado)", "critical": "Solo para compras y borrados"}
        )
        form.addRow("Permisos", self.assistant_approval)
        self.assistant_budget = QDoubleSpinBox()
        self.assistant_budget.setRange(0, 500)
        self.assistant_budget.setDecimals(2)
        self.assistant_budget.setSingleStep(0.5)
        self.assistant_budget.setSuffix(" USD")
        self.assistant_budget.setSpecialValueText("Sin límite")
        form.addRow("Límite de gasto por día", self.assistant_budget)
        self.assistant_rate = _spin(1, 1000, " mensajes")
        form.addRow("Máximo por hora", self.assistant_rate)
        assistant.add(layout=form)
        self.add(assistant)

        security = Card(
            "Seguridad",
            "Los pedidos, etiquetas y la configuración se guardan cifrados (AES-256) y las claves en el "
            "Administrador de credenciales de Windows. Estos límites protegen la API local, n8n y las descargas.",
        )
        form = QFormLayout()
        form.setSpacing(10)
        self.api_rate = _spin(10, 10000, " por minuto")
        form.addRow("Peticiones a la API local (por equipo)", self.api_rate)
        self.api_failures = _spin(3, 100, " intentos")
        form.addRow("Claves incorrectas antes de bloquear", self.api_failures)
        self.api_block = _spin(1, 1440, " min")
        form.addRow("Duración del bloqueo", self.api_block)
        self.jobs_rate = _spin(1, 10000, " por minuto")
        form.addRow("Trabajos nuevos (API local y n8n)", self.jobs_rate)
        self.max_copies = _spin(1, 50, " copias")
        form.addRow("Copias máximas por trabajo", self.max_copies)
        self.private_urls = _combo(
            {
                "local_api": "Solo trabajos de la API local de este equipo (recomendado)",
                "never": "Nunca",
                "always": "Siempre (también n8n y Shopify)",
            }
        )
        form.addRow("Descargas desde la red interna", self.private_urls)
        self.require_https = QCheckBox("Descargas desde internet solo por https (recomendado)")
        form.addRow("", self.require_https)
        security.add(layout=form)
        row = QHBoxLayout()
        audit_btn = button("Ver registro de seguridad", "file")
        audit_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir() / "seguridad.log"))))
        row.addWidget(audit_btn)
        row.addStretch(1)
        security.add(layout=row)
        self.add(security)

        row = QHBoxLayout()
        row.addStretch(1)
        save_all = button("Guardar ajustes", "check", "primary")
        save_all.clicked.connect(self._save)
        row.addWidget(save_all)
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
        ctx.bridge.event.connect(self._on_event)
        self._load_protection()

    def _on_event(self, kind: str, data) -> None:
        if kind == CONFIG_EXTERNAL and data in ("general", "all"):
            self.load_from_config()

    def load_from_config(self) -> None:
        """Muestra lo guardado (p. ej. después de que el asistente cambió la configuración)."""
        g = self.ctx.cfg.data.general
        lb = self.ctx.cfg.data.labels
        self.agent.setText(g.agent_name)
        self.autostart.setChecked(g.start_with_windows)
        self.minimized.setChecked(g.start_minimized)
        self.auto_discover.setChecked(g.auto_discover)
        self.notify_errors.setChecked(g.notify_errors)
        self.notify_printed.setChecked(g.notify_printed)
        self.retries.setValue(g.max_retries)
        self.retention.setValue(g.retention_days)
        self.auto_update.setChecked(g.auto_update_check)
        self.store_name.setText(lb.store_name)
        self.sender.setPlainText(lb.sender_address)
        self.show_items.setChecked(lb.show_items)
        self.show_qr.setChecked(lb.show_qr)
        self.show_price.setChecked(lb.show_price)

    def _load_protection(self) -> None:
        a = self.ctx.cfg.data.assistant
        s = self.ctx.cfg.data.security
        self.assistant_on.setChecked(a.enabled)
        _select(self.assistant_model, a.model)
        _select(self.assistant_effort, a.effort)
        _select(self.assistant_approval, a.approval)
        self.assistant_budget.setValue(float(a.daily_budget_usd))
        self.assistant_rate.setValue(a.messages_per_hour)
        self.api_rate.setValue(s.api_requests_per_minute)
        self.api_failures.setValue(s.api_max_auth_failures)
        self.api_block.setValue(s.api_block_minutes)
        self.jobs_rate.setValue(s.jobs_per_minute)
        self.max_copies.setValue(s.max_copies)
        _select(self.private_urls, s.private_urls)
        self.require_https.setChecked(s.require_https)

    def _save_protection(self) -> None:
        a = self.ctx.cfg.data.assistant
        s = self.ctx.cfg.data.security
        before = (vars(a).copy(), vars(s).copy())
        a.enabled = self.assistant_on.isChecked()
        a.model = self.assistant_model.currentData()
        a.effort = self.assistant_effort.currentData()
        a.approval = self.assistant_approval.currentData()
        a.daily_budget_usd = round(self.assistant_budget.value(), 2)
        a.messages_per_hour = self.assistant_rate.value()
        s.api_requests_per_minute = self.api_rate.value()
        s.api_max_auth_failures = self.api_failures.value()
        s.api_block_minutes = self.api_block.value()
        s.jobs_per_minute = self.jobs_rate.value()
        s.max_copies = self.max_copies.value()
        s.private_urls = self.private_urls.currentData()
        s.require_https = self.require_https.isChecked()
        if vars(a) != before[0]:
            self.ctx.cfg.save("assistant")
        if vars(s) != before[1]:
            self.ctx.cfg.save("security")

    def _save(self) -> None:
        self._save_protection()
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

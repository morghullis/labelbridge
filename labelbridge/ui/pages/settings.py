"""Ajustes generales, etiqueta generada y datos."""

from __future__ import annotations

from types import SimpleNamespace

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
from ...assistant.pricing import price_for
from ...assistant.providers import (
    PROVIDERS,
    Endpoint,
    ProviderError,
    check_base_url,
    connect,
    is_plain_http,
    resolve,
    valid_model,
)
from ...config import ASSISTANT_EFFORTS
from ...events import CONFIG_EXTERNAL
from ...paths import data_dir, logs_dir, output_dir
from ...system import autostart
from ...updater import UPDATES_PAGE
from ..context import AppContext
from ..widgets import Card, Page, button, label, run_async


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


def _price_spin() -> QDoubleSpinBox:
    """USD por millón de tokens; el mínimo (-1) significa «automático» (precio público del modelo)."""
    spin = QDoubleSpinBox()
    spin.setRange(-1, 1000)
    spin.setDecimals(3)
    spin.setSingleStep(0.25)
    spin.setSuffix(" USD")
    spin.setSpecialValueText("Automático")
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
            "Funciona con Claude, OpenAI (ChatGPT), Gemini o cualquier API compatible con OpenAI, con tu propia clave "
            "(pagas por uso a tu proveedor). Los permisos y límites valen igual para cualquier modelo. Proveedor, modelo "
            "y nivel se aplican a las conversaciones nuevas.",
        )
        form = QFormLayout()
        form.setSpacing(10)
        self.assistant_form = form
        self.assistant_on = QCheckBox("Asistente activado")
        form.addRow("", self.assistant_on)
        self.assistant_provider = _combo({p.id: p.name for p in PROVIDERS.values()})
        self.assistant_provider.currentIndexChanged.connect(lambda _i: self._provider_changed(user=True))
        form.addRow("Proveedor de IA", self.assistant_provider)
        model_row = QHBoxLayout()
        self.assistant_model = QComboBox()
        self.assistant_model.setEditable(True)
        self.assistant_model.currentTextChanged.connect(lambda _t: self._show_price())
        model_row.addWidget(self.assistant_model, 1)
        self.load_models = button("Cargar modelos de mi cuenta", "refresh", "ghost")
        self.load_models.clicked.connect(self._load_models)
        model_row.addWidget(self.load_models)
        form.addRow("Modelo", model_row)
        self.assistant_url = QLineEdit()
        self.assistant_url.textChanged.connect(lambda _t: self._check_url())
        form.addRow("Dirección del servidor", self.assistant_url)
        self.url_hint = label("", "Small", wrap=True)
        form.addRow("", self.url_hint)
        self.assistant_effort = _combo(ASSISTANT_EFFORTS)
        form.addRow("Nivel de razonamiento", self.assistant_effort)
        prices = QHBoxLayout()
        self.price_in = _price_spin()
        self.price_in.valueChanged.connect(lambda _v: self._show_price())
        self.price_out = _price_spin()
        self.price_out.valueChanged.connect(lambda _v: self._show_price())
        prices.addWidget(label("entrada"))
        prices.addWidget(self.price_in, 1)
        prices.addWidget(label("salida"))
        prices.addWidget(self.price_out, 1)
        form.addRow("Precio por millón de tokens", prices)
        self.price_hint = label("", "Small", wrap=True)
        form.addRow("", self.price_hint)
        self.key_status = label("", "Small", wrap=True)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_status, 1)
        connect_btn = button("Conectar IA", "key", "ghost")
        connect_btn.clicked.connect(self._open_connect)
        key_row.addWidget(connect_btn)
        form.addRow("Clave de API", key_row)
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
        if kind == CONFIG_EXTERNAL and data in ("assistant", "all"):
            self._load_protection()

    # --- asistente ---------------------------------------------------------------------------------
    def _form_endpoint(self) -> Endpoint:
        """Lo que hay escrito en la tarjeta del asistente (aún sin guardar)."""
        provider = PROVIDERS.get(self.assistant_provider.currentData()) or resolve(self.ctx.cfg.data.assistant).provider
        base_url = (self.assistant_url.text().strip() or provider.base_url).rstrip("/")
        return Endpoint(provider, base_url, self._model_text(), self.assistant_effort.currentData() or "low")

    def _model_text(self) -> str:
        index = self.assistant_model.currentIndex()
        text = self.assistant_model.currentText().strip()
        if index >= 0 and text == self.assistant_model.itemText(index):
            return str(self.assistant_model.itemData(index) or text)
        return text

    def _set_models(self, provider, current: str, extra: list[str] = ()) -> None:
        self.assistant_model.blockSignals(True)
        self.assistant_model.clear()
        known = {key for key, _ in provider.models}
        for key, text in provider.models:
            self.assistant_model.addItem(text, key)
        for name in extra:
            if name not in known:
                self.assistant_model.addItem(name, name)
        index = self.assistant_model.findData(current)
        if index >= 0:
            self.assistant_model.setCurrentIndex(index)
        else:
            self.assistant_model.setEditText(current)
        self.assistant_model.blockSignals(False)

    def _provider_changed(self, user: bool = False) -> None:
        provider = PROVIDERS.get(self.assistant_provider.currentData())
        if provider is None:
            return
        settings = self.ctx.cfg.data.assistant
        same = provider.id == resolve(settings).provider.id
        if user:
            self.assistant_url.setText(settings.base_url if same else "")
            self._set_models(provider, resolve(settings).model if same else provider.default_model)
        self.assistant_url.setPlaceholderText(provider.base_url or "https://mi-servidor.com/v1 (obligatoria)")
        self.assistant_url.setToolTip("Déjala vacía para usar la dirección oficial del proveedor.")
        self._check_url()
        self._show_price()

    def _check_url(self) -> bool:
        """Avisa al momento si la dirección no se puede usar (o si va sin cifrar). True si se puede guardar."""
        url = self.assistant_url.text().strip()
        if not url:
            provider = PROVIDERS.get(self.assistant_provider.currentData())
            missing = provider is not None and not provider.base_url
            self._url_hint("✗ Escribe la dirección de tu servidor." if missing else "")
            return not missing
        try:
            check_base_url(url)
        except ProviderError as exc:
            self._url_hint(f"✗ {exc.detail}")
            return False
        self._url_hint("⚠ Conexión sin cifrar (http): úsala solo en tu red local." if is_plain_http(url) else "")
        return True

    def _url_hint(self, text: str) -> None:
        self.url_hint.setText(text)
        self.assistant_form.setRowVisible(self.url_hint, bool(text))

    def _show_price(self) -> None:
        if not hasattr(self, "price_hint"):
            return
        endpoint = self._form_endpoint()
        prices = SimpleNamespace(price_input=self.price_in.value(), price_output=self.price_out.value())
        self.price_hint.setText(price_for(endpoint, prices).text())
        if not endpoint.base_url:
            self.key_status.setText("Falta la dirección del servidor.")
        elif not endpoint.needs_key:
            self.key_status.setText("No hace falta clave para este proveedor.")
        elif self.ctx.secrets.get(endpoint.key_name):
            self.key_status.setText(f"✓ Guardada para {endpoint.provider.short} (en el almacén seguro de Windows).")
        else:
            self.key_status.setText(f"Falta la clave de {endpoint.provider.short}: pégala en Asistente → Conectar IA.")

    def _open_connect(self) -> None:
        """Abre «Conecta tu IA» con el proveedor elegido aquí (sin guardar nada de esta pantalla)."""
        window = self.window()
        window.navigate("assistant")
        page = getattr(window, "pages", {}).get("assistant")
        if hasattr(page, "show_connect"):
            page.show_connect(self.assistant_provider.currentData())

    def _load_models(self) -> None:
        endpoint = self._form_endpoint()
        key = self.ctx.secrets.get(endpoint.key_name) if endpoint.base_url else ""
        if endpoint.needs_key and not key:
            self.price_hint.setText("Primero guarda la clave en Asistente → Conectar IA.")
            return
        self.load_models.setEnabled(False)
        self.load_models.setText("Cargando…")

        def fetch() -> list[str]:
            try:
                check_base_url(endpoint.base_url)
                return connect(endpoint, key).models()
            except ProviderError as exc:
                raise RuntimeError(exc.detail or exc.kind) from None

        def done(names: list[str]) -> None:
            self.load_models.setEnabled(True)
            self.load_models.setText("Cargar modelos de mi cuenta")
            self._set_models(endpoint.provider, self._model_text(), names)
            self.price_hint.setText(f"{len(names)} modelos disponibles en tu cuenta. Elige uno y pulsa «Guardar ajustes».")

        def failed(message: str) -> None:
            self.load_models.setEnabled(True)
            self.load_models.setText("Cargar modelos de mi cuenta")
            self.price_hint.setText(f"No se pudo cargar la lista: {message}")

        run_async(fetch, done, failed)

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
        endpoint = resolve(a)
        self.assistant_on.setChecked(a.enabled)
        self.assistant_provider.blockSignals(True)
        _select(self.assistant_provider, endpoint.provider.id)
        self.assistant_provider.blockSignals(False)
        self._set_models(endpoint.provider, endpoint.model)
        self.assistant_url.setText(a.base_url)
        self.price_in.setValue(a.price_input if a.price_input >= 0 else -1)
        self.price_out.setValue(a.price_output if a.price_output >= 0 else -1)
        self._provider_changed()
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
        endpoint = self._form_endpoint()
        url = self.assistant_url.text().strip()
        # Proveedor, modelo y dirección van juntos: con una dirección no permitida no se cambia ninguno (queda
        # escrita con el motivo debajo para corregirla) y la clave nunca se manda a un servidor a medio configurar.
        if self._check_url():
            url = check_base_url(url) if url else ""
            a.provider = endpoint.provider.id
            model = self._model_text()
            a.model = model if valid_model(model) else endpoint.provider.default_model
            a.base_url = "" if url == endpoint.provider.base_url.rstrip("/") else url
        a.price_input = round(self.price_in.value(), 3) if self.price_in.value() >= 0 else -1.0
        a.price_output = round(self.price_out.value(), 3) if self.price_out.value() >= 0 else -1.0
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

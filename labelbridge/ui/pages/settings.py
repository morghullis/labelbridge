"""Ajustes generales, etiqueta generada y datos."""

from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtCore import Qt, QTime, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QSlider,
    QSpinBox,
    QTimeEdit,
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
from ...config import ASSISTANT_EFFORTS, NOTIFY_EVENTS
from ...events import CONFIG_EXTERNAL
from ...i18n import LANGUAGES, N_, tr
from ...paths import data_dir, logs_dir, output_dir
from ...system import autostart, sounds
from ...system.platform import pick
from ...updater import UPDATES_PAGE
from ..alerts import play_async
from ..context import AppContext
from ..widgets import Card, Page, button, confirm, info, label, run_async

REMIND_OPTIONS = {0: N_("No recordar"), 15: N_("Cada 15 minutos"), 30: N_("Cada 30 minutos"), 60: N_("Cada hora"),
                  120: N_("Cada 2 horas")}


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
    spin.setSuffix(tr(" USD"))
    spin.setSpecialValueText(tr("Automático"))
    return spin


class SettingsPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(tr("Ajustes"), tr("Comportamiento del agente en este equipo."))
        self.ctx = ctx
        g = ctx.cfg.data.general
        lb = ctx.cfg.data.labels

        general = Card(tr("General"))
        form = QFormLayout()
        form.setSpacing(10)
        self.agent = QLineEdit(g.agent_name)
        form.addRow(tr("Nombre de este equipo"), self.agent)
        self.autostart = QCheckBox(tr(pick(N_("Iniciar LabelBridge al encender Windows"),
                                           N_("Abrir LabelBridge al iniciar sesión en la Mac"))))
        self.autostart.setChecked(g.start_with_windows)
        if not autostart.supported():
            self.autostart.setToolTip(tr("Se aplica en la app instalada"))
        form.addRow("", self.autostart)
        self.minimized = QCheckBox(tr(pick(N_("Iniciar minimizado en la bandeja del sistema"),
                                           N_("Iniciar oculto (solo el icono en la barra de menús)"))))
        self.minimized.setChecked(g.start_minimized)
        form.addRow("", self.minimized)
        self.auto_discover = QCheckBox(tr("Buscar impresoras automáticamente al conectarse a una red (Wi‑Fi / LAN)"))
        self.auto_discover.setChecked(g.auto_discover)
        form.addRow("", self.auto_discover)
        self.retries = QSpinBox()
        self.retries.setRange(0, 20)
        self.retries.setValue(g.max_retries)
        form.addRow(tr("Reintentos si la impresora falla"), self.retries)
        self.retention = QSpinBox()
        self.retention.setRange(1, 365)
        self.retention.setSuffix(tr(" días"))
        self.retention.setValue(g.retention_days)
        form.addRow(tr("Guardar historial"), self.retention)
        self.language = QComboBox()
        self.language.addItem(tr(pick(N_("Automático (el del instalador o el de Windows)"), N_("Automático (el de la Mac)"))), "")
        for code, name in LANGUAGES.items():
            self.language.addItem(name, code)  # cada idioma con su propio nombre
        self.language.setCurrentIndex(max(0, self.language.findData(g.language)))
        form.addRow(tr("Idioma / Language"), self.language)
        general.add(layout=form)
        self.add(general)
        self.add(self._notifications_card())

        label_card = Card(
            tr("Etiqueta de pedido generada"),
            tr("Datos del remitente y contenido de la etiqueta que LabelBridge genera a partir del pedido."),
        )
        form = QFormLayout()
        form.setSpacing(10)
        self.store_name = QLineEdit(lb.store_name)
        self.store_name.setPlaceholderText(tr("Nombre de tu tienda"))
        form.addRow(tr("Remitente"), self.store_name)
        self.sender = QPlainTextEdit(lb.sender_address)
        self.sender.setPlaceholderText(tr("Calle y número\nCiudad, Estado CP\nTeléfono"))
        self.sender.setFixedHeight(84)
        form.addRow(tr("Dirección"), self.sender)
        self.show_items = QCheckBox(tr("Incluir la lista de productos"))
        self.show_items.setChecked(lb.show_items)
        form.addRow("", self.show_items)
        self.show_qr = QCheckBox(tr("Incluir QR al pedido en Shopify (para escanear en bodega)"))
        self.show_qr.setChecked(lb.show_qr)
        form.addRow("", self.show_qr)
        self.show_price = QCheckBox(tr("Incluir el total del pedido"))
        self.show_price.setChecked(lb.show_price)
        form.addRow("", self.show_price)
        label_card.add(layout=form)
        self.add(label_card)

        row = QHBoxLayout()
        row.addStretch(1)
        save = button(tr("Guardar ajustes"), "check", "primary")
        self.save_btn = save
        save.clicked.connect(self._save)
        row.addWidget(save)
        self.add(layout=row)

        assistant = Card(
            tr("Asistente"),
            tr("Funciona con Claude, OpenAI (ChatGPT), Gemini o cualquier API compatible con OpenAI, con tu propia clave "
               "(pagas por uso a tu proveedor). Los permisos y límites valen igual para cualquier modelo. Proveedor, modelo "
               "y nivel se aplican a las conversaciones nuevas."),
        )
        form = QFormLayout()
        form.setSpacing(10)
        self.assistant_form = form
        self.assistant_on = QCheckBox(tr("Asistente activado"))
        form.addRow("", self.assistant_on)
        self.assistant_provider = _combo({p.id: tr(p.name) for p in PROVIDERS.values()})
        self.assistant_provider.currentIndexChanged.connect(lambda _i: self._provider_changed(user=True))
        form.addRow(tr("Proveedor de IA"), self.assistant_provider)
        model_row = QHBoxLayout()
        self.assistant_model = QComboBox()
        self.assistant_model.setEditable(True)
        self.assistant_model.currentTextChanged.connect(lambda _t: self._show_price())
        model_row.addWidget(self.assistant_model, 1)
        self.load_models = button(tr("Cargar modelos de mi cuenta"), "refresh", "ghost")
        self.load_models.clicked.connect(self._load_models)
        model_row.addWidget(self.load_models)
        form.addRow(tr("Modelo"), model_row)
        self.assistant_url = QLineEdit()
        self.assistant_url.textChanged.connect(lambda _t: self._check_url())
        form.addRow(tr("Dirección del servidor"), self.assistant_url)
        self.url_hint = label("", "Small", wrap=True)
        form.addRow("", self.url_hint)
        self.assistant_effort = _combo({key: tr(text) for key, text in ASSISTANT_EFFORTS.items()})
        form.addRow(tr("Nivel de razonamiento"), self.assistant_effort)
        prices = QHBoxLayout()
        self.price_in = _price_spin()
        self.price_in.valueChanged.connect(lambda _v: self._show_price())
        self.price_out = _price_spin()
        self.price_out.valueChanged.connect(lambda _v: self._show_price())
        prices.addWidget(label(tr("entrada")))
        prices.addWidget(self.price_in, 1)
        prices.addWidget(label(tr("salida")))
        prices.addWidget(self.price_out, 1)
        form.addRow(tr("Precio por millón de tokens"), prices)
        self.price_hint = label("", "Small", wrap=True)
        form.addRow("", self.price_hint)
        self.key_status = label("", "Small", wrap=True)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_status, 1)
        connect_btn = button(tr("Conectar IA"), "key", "ghost")
        connect_btn.clicked.connect(self._open_connect)
        key_row.addWidget(connect_btn)
        form.addRow(tr("Clave de API"), key_row)
        self.assistant_approval = _combo(
            {"changes": tr("Pedir permiso para cada cambio (recomendado)"), "critical": tr("Solo para compras y borrados")}
        )
        form.addRow(tr("Permisos"), self.assistant_approval)
        self.assistant_budget = QDoubleSpinBox()
        self.assistant_budget.setRange(0, 500)
        self.assistant_budget.setDecimals(2)
        self.assistant_budget.setSingleStep(0.5)
        self.assistant_budget.setSuffix(tr(" USD"))
        self.assistant_budget.setSpecialValueText(tr("Sin límite"))
        form.addRow(tr("Límite de gasto por día"), self.assistant_budget)
        self.assistant_rate = _spin(1, 1000, tr(" mensajes"))
        form.addRow(tr("Máximo por hora"), self.assistant_rate)
        assistant.add(layout=form)
        self.add(assistant)

        security = Card(
            tr("Seguridad"),
            tr("Los pedidos, etiquetas y la configuración se guardan cifrados (AES-256) y las claves en el "
               "Administrador de credenciales de Windows. Estos límites protegen la API local, n8n y las descargas."),
        )
        form = QFormLayout()
        form.setSpacing(10)
        self.api_rate = _spin(10, 10000, tr(" por minuto"))
        form.addRow(tr("Peticiones a la API local (por equipo)"), self.api_rate)
        self.api_failures = _spin(3, 100, tr(" intentos"))
        form.addRow(tr("Claves incorrectas antes de bloquear"), self.api_failures)
        self.api_block = _spin(1, 1440, " min")
        form.addRow(tr("Duración del bloqueo"), self.api_block)
        self.jobs_rate = _spin(1, 10000, tr(" por minuto"))
        form.addRow(tr("Trabajos nuevos (API local y n8n)"), self.jobs_rate)
        self.max_copies = _spin(1, 50, tr(" copias"))
        form.addRow(tr("Copias máximas por trabajo"), self.max_copies)
        self.private_urls = _combo(
            {
                "local_api": tr("Solo trabajos de la API local de este equipo (recomendado)"),
                "never": tr("Nunca"),
                "always": tr("Siempre (también n8n y Shopify)"),
            }
        )
        form.addRow(tr("Descargas desde la red interna"), self.private_urls)
        self.require_https = QCheckBox(tr("Descargas desde internet solo por https (recomendado)"))
        form.addRow("", self.require_https)
        security.add(layout=form)
        row = QHBoxLayout()
        audit_btn = button(tr("Ver registro de seguridad"), "file")
        audit_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(logs_dir() / "seguridad.log"))))
        row.addWidget(audit_btn)
        row.addStretch(1)
        security.add(layout=row)
        self.add(security)

        row = QHBoxLayout()
        row.addStretch(1)
        save_all = button(tr("Guardar ajustes"), "check", "primary")
        save_all.clicked.connect(self._save)
        row.addWidget(save_all)
        self.add(layout=row)

        updates = Card(tr("Actualizaciones"), tr("LabelBridge avisa cuando hay una versión nueva y se actualiza con un clic."))
        self.auto_update = QCheckBox(tr("Avisarme cuando haya una nueva versión (recomendado)"))
        self.auto_update.setChecked(g.auto_update_check)
        updates.add(self.auto_update)
        row = QHBoxLayout()
        row.addWidget(label(tr("Versión instalada: <b>{0}</b>", __version__)))
        row.addStretch(1)
        check = button(tr("Buscar actualizaciones ahora"), "download", "primary")
        check.clicked.connect(lambda: self.window().check_updates_now())
        page_btn = button(tr("Página de descargas"), "link")
        page_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(UPDATES_PAGE)))
        row.addWidget(page_btn)
        row.addWidget(check)
        updates.add(layout=row)
        self.add(updates)

        data = Card(tr("Datos y soporte"))
        data.add(
            label(
                tr("{0} {1} · Datos en: {2}\nSecretos guardados en: {3}", APP_NAME, __version__, data_dir(), ctx.secrets.backend_name),
                "Muted",
                wrap=True,
                selectable=True,
            )
        )
        row = QHBoxLayout()
        for text, path in ((tr("Carpeta de datos"), data_dir()), (tr("Registros"), logs_dir()), (tr("Salida de pruebas"), output_dir())):
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
            self.assistant_model.addItem(tr(text), key)
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
        self.assistant_url.setPlaceholderText(provider.base_url or tr("https://mi-servidor.com/v1 (obligatoria)"))
        self.assistant_url.setToolTip(tr("Déjala vacía para usar la dirección oficial del proveedor."))
        self._check_url()
        self._show_price()

    def _check_url(self) -> bool:
        """Avisa al momento si la dirección no se puede usar (o si va sin cifrar). True si se puede guardar."""
        url = self.assistant_url.text().strip()
        if not url:
            provider = PROVIDERS.get(self.assistant_provider.currentData())
            missing = provider is not None and not provider.base_url
            self._url_hint(tr("✗ Escribe la dirección de tu servidor.") if missing else "")
            return not missing
        try:
            check_base_url(url)
        except ProviderError as exc:
            self._url_hint(f"✗ {exc.detail}")
            return False
        self._url_hint(tr("⚠ Conexión sin cifrar (http): úsala solo en tu red local.") if is_plain_http(url) else "")
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
            self.key_status.setText(tr("Falta la dirección del servidor."))
        elif not endpoint.needs_key:
            self.key_status.setText(tr("No hace falta clave para este proveedor."))
        elif self.ctx.secrets.get(endpoint.key_name):
            self.key_status.setText(tr(pick(N_("✓ Guardada para {0} (en el almacén seguro de Windows)."),
                                            N_("✓ Guardada para {0} (en el Llavero de la Mac).")), endpoint.provider.short))
        else:
            self.key_status.setText(tr("Falta la clave de {0}: pégala en Asistente → Conectar IA.", endpoint.provider.short))

    def _open_connect(self) -> None:
        """Abre «Conecta tu IA» con el proveedor elegido aquí (sin guardar nada de esta pantalla)."""
        window = self.window()
        if hasattr(window, "open_assistant"):
            window.open_assistant(connect=True, provider=self.assistant_provider.currentData())

    def _load_models(self) -> None:
        endpoint = self._form_endpoint()
        key = self.ctx.secrets.get(endpoint.key_name) if endpoint.base_url else ""
        if endpoint.needs_key and not key:
            self.price_hint.setText(tr("Primero guarda la clave en Asistente → Conectar IA."))
            return
        self.load_models.setEnabled(False)
        self.load_models.setText(tr("Cargando…"))

        def fetch() -> list[str]:
            try:
                check_base_url(endpoint.base_url)
                return connect(endpoint, key).models()
            except ProviderError as exc:
                raise RuntimeError(exc.detail or exc.kind) from None

        def done(names: list[str]) -> None:
            self.load_models.setEnabled(True)
            self.load_models.setText(tr("Cargar modelos de mi cuenta"))
            self._set_models(endpoint.provider, self._model_text(), names)
            self.price_hint.setText(tr("{0} modelos disponibles en tu cuenta. Elige uno y pulsa «Guardar ajustes».", len(names)))

        def failed(message: str) -> None:
            self.load_models.setEnabled(True)
            self.load_models.setText(tr("Cargar modelos de mi cuenta"))
            self.price_hint.setText(tr("No se pudo cargar la lista: {0}", message))

        run_async(fetch, done, failed)

    # --- notificaciones y sonidos --------------------------------------------------------------
    def _notifications_card(self) -> Card:
        card = Card(
            tr("Notificaciones y sonidos"),
            tr(pick(N_("Qué te avisa LabelBridge y cómo suena en este PC. Los avisos salen junto al reloj de Windows."),
                    N_("Qué te avisa LabelBridge y cómo suena en esta Mac. Los avisos salen en el Centro de notificaciones."))),
        )
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        grid.addWidget(label(tr("Avisar"), "Small"), 0, 1)
        grid.addWidget(label(tr("Sonido"), "Small"), 0, 2)
        self.alert_toasts: dict[str, QCheckBox] = {}
        self.alert_sounds: dict[str, QComboBox] = {}
        for row, (event, text) in enumerate(NOTIFY_EVENTS.items(), start=1):
            grid.addWidget(label(tr(text)), row, 0)
            toast = QCheckBox()
            toast.setToolTip(tr("Mostrar el aviso junto al reloj"))
            grid.addWidget(toast, row, 1, alignment=Qt.AlignmentFlag.AlignCenter)
            sound = QComboBox()
            for key, name in sounds.SOUNDS.items():
                sound.addItem(tr(name), key)
            sound.setMinimumWidth(190)
            grid.addWidget(sound, row, 2)
            test = button("", "play", "ghost", tr("Probar el sonido"))
            test.clicked.connect(lambda _checked=False, combo=sound: self._test_sound(combo.currentData()))
            grid.addWidget(test, row, 3)
            self.alert_toasts[event], self.alert_sounds[event] = toast, sound
        grid.setColumnStretch(4, 1)
        # Los de imprimir son los ajustes de siempre (el asistente también los cambia).
        self.notify_printed, self.notify_errors = self.alert_toasts["printed"], self.alert_toasts["print_error"]
        card.add(layout=grid)

        form = QFormLayout()
        form.setSpacing(10)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setMaximumWidth(260)
        self.volume_text = label("", "Muted")
        self.volume.valueChanged.connect(lambda value: self.volume_text.setText(f"{value} %"))
        volume_row = QHBoxLayout()
        volume_row.addWidget(self.volume)
        volume_row.addWidget(self.volume_text)
        volume_row.addStretch(1)
        form.addRow(tr("Volumen"), volume_row)
        self.custom_sound = QLineEdit()
        self.custom_sound.setPlaceholderText(tr("Un archivo .wav para «Mi archivo (.wav)»"))
        choose = button(tr("Elegir…"), "folder")
        choose.clicked.connect(self._choose_sound)
        custom_row = QHBoxLayout()
        custom_row.addWidget(self.custom_sound, 1)
        custom_row.addWidget(choose)
        form.addRow(tr("Mi sonido"), custom_row)
        self.quiet = QCheckBox(tr("Sin sonido de"))
        self.quiet_from, self.quiet_to = QTimeEdit(), QTimeEdit()
        for edit in (self.quiet_from, self.quiet_to):
            edit.setDisplayFormat("HH:mm")
        quiet_row = QHBoxLayout()
        quiet_row.addWidget(self.quiet)
        quiet_row.addWidget(self.quiet_from)
        quiet_row.addWidget(label(tr("a")))
        quiet_row.addWidget(self.quiet_to)
        quiet_row.addWidget(label(tr("(los avisos llegan igual, en silencio)"), "Muted"))
        quiet_row.addStretch(1)
        form.addRow(tr("Horario de silencio"), quiet_row)
        self.remind = QComboBox()
        for minutes, text in REMIND_OPTIONS.items():
            self.remind.addItem(tr(text), minutes)
        self.remind.setToolTip(tr("Vuelve a avisar mientras haya pedidos esperando en «Necesitan acción»"))
        self.remind.setMaximumWidth(260)
        form.addRow(tr("Recordar pedidos que necesitan acción"), self.remind)
        card.add(layout=form)

        card.add(label(tr("Alertas de entrega"), "SectionTitle"))
        delays = QFormLayout()
        delays.setSpacing(10)
        self.no_scan_days = _spin(1, 30, tr(" días"))
        delays.addRow(tr("La paquetería no recibe el paquete después de"), self.no_scan_days)
        self.transit_days = _spin(2, 60, tr(" días"))
        delays.addRow(tr("En camino sin fecha de entrega más de"), self.transit_days)
        self.unshipped_days = _spin(1, 30, tr(" días"))
        for spin in (self.no_scan_days, self.transit_days, self.unshipped_days):
            spin.setMaximumWidth(140)
        delays.addRow(tr("Marcar los pedidos pagados sin enviar después de"), self.unshipped_days)
        delays.addRow("", label(tr("También avisa si se pasa la fecha estimada de entrega o la paquetería reporta un problema "
                                   "(intento fallido, retraso, no entregado)."), "Muted", wrap=True))
        card.add(layout=delays)
        self._load_notifications()
        return card

    def _load_notifications(self) -> None:
        g, n = self.ctx.cfg.data.general, self.ctx.cfg.data.notifications
        toasts = {"new_order": n.new_order, "action": n.action, "delivery": n.delivery,
                  "printed": g.notify_printed, "print_error": g.notify_errors}
        for event in NOTIFY_EVENTS:
            self.alert_toasts[event].setChecked(bool(toasts[event]))
            _select(self.alert_sounds[event], n.sound(event))
        self.volume.setValue(max(0, min(100, n.volume)))
        self.volume_text.setText(f"{self.volume.value()} %")
        self.custom_sound.setText(n.custom_sound)
        self.quiet.setChecked(n.quiet)
        self.quiet_from.setTime(QTime.fromString(n.quiet_from, "HH:mm"))
        self.quiet_to.setTime(QTime.fromString(n.quiet_to, "HH:mm"))
        self.remind.setCurrentIndex(max(0, self.remind.findData(n.remind_minutes)))
        self.no_scan_days.setValue(n.no_scan_days)
        self.transit_days.setValue(n.transit_days)
        self.unshipped_days.setValue(n.unshipped_days)

    def _save_notifications(self) -> None:
        g, n = self.ctx.cfg.data.general, self.ctx.cfg.data.notifications
        n.new_order = self.alert_toasts["new_order"].isChecked()
        n.action = self.alert_toasts["action"].isChecked()
        n.delivery = self.alert_toasts["delivery"].isChecked()
        g.notify_printed = self.alert_toasts["printed"].isChecked()
        g.notify_errors = self.alert_toasts["print_error"].isChecked()
        n.sounds = {event: self.alert_sounds[event].currentData() or "" for event in NOTIFY_EVENTS}
        n.volume = self.volume.value()
        n.custom_sound = self.custom_sound.text().strip()
        n.quiet = self.quiet.isChecked()
        n.quiet_from = self.quiet_from.time().toString("HH:mm")
        n.quiet_to = self.quiet_to.time().toString("HH:mm")
        n.remind_minutes = int(self.remind.currentData() or 0)
        n.no_scan_days = self.no_scan_days.value()
        n.transit_days = self.transit_days.value()
        n.unshipped_days = self.unshipped_days.value()
        self.ctx.cfg.save("notifications")

    def _test_sound(self, key: str) -> None:
        if key == "custom" and not self.custom_sound.text().strip():
            info(self, tr("Mi sonido"), tr("Elige primero un archivo .wav en «Mi sonido»."))
            return
        play_async(key, self.volume.value(), self.custom_sound.text().strip())

    def _choose_sound(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Elegir sonido"), "", tr("Sonido WAV (*.wav)"))
        if path:
            self.custom_sound.setText(path)

    def load_from_config(self) -> None:
        """Muestra lo guardado (p. ej. después de que el asistente cambió la configuración)."""
        g = self.ctx.cfg.data.general
        lb = self.ctx.cfg.data.labels
        self.agent.setText(g.agent_name)
        self.autostart.setChecked(g.start_with_windows)
        self.minimized.setChecked(g.start_minimized)
        self.auto_discover.setChecked(g.auto_discover)
        self._load_notifications()
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
        g.max_retries = self.retries.value()
        g.retention_days = self.retention.value()
        language_changed = (self.language.currentData() or "") != g.language
        g.language = self.language.currentData() or ""
        lb.store_name = self.store_name.text().strip()
        lb.sender_address = self.sender.toPlainText().strip()
        lb.show_items = self.show_items.isChecked()
        lb.show_qr = self.show_qr.isChecked()
        lb.show_price = self.show_price.isChecked()
        self.ctx.cfg.save("general")
        self._save_notifications()
        try:
            autostart.apply(g.start_with_windows)
        except OSError:
            pass
        if language_changed:
            window = self.window()
            if confirm(self, tr("Idioma"), tr("El idioma cambia al reiniciar LabelBridge. ¿Reiniciar ahora?")) and hasattr(
                window, "restart_app"
            ):
                window.restart_app()

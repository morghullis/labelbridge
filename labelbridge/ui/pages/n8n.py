"""n8n en tiempo real: n8n se ejecuta solo cuando ocurre un evento y envía el trabajo por MQTT."""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLineEdit, QSpinBox

from ...config import slugify
from ...connectors.mqtt import topics
from ...events import CONFIG_EXTERNAL
from ...i18n import tr
from ...paths import resource_path
from ...secret_store import MQTT_PASSWORD
from ..context import AppContext
from ..widgets import Card, Page, Pill, SecretField, SwitchRow, button, error, form_layout, label, run_async


class N8nPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            tr("n8n · tiempo real"),
            tr("n8n se ejecuta solo cuando pasa algo en Shopify y envía la etiqueta a este equipo al instante."),
        )
        self.ctx = ctx
        s = ctx.cfg.data.mqtt
        self.status = Pill("off", tr("Desactivado"))
        self.header_actions.addWidget(self.status)

        how = Card(tr("Cómo funciona"))
        how.add(
            label(
                tr("1. Shopify avisa a n8n (por ejemplo, <b>pedido pagado</b>): el flujo se ejecuta <b>una vez por pedido</b>, "
                   "sin consultas periódicas.<br>"
                   "2. n8n publica la etiqueta en el <b>canal de este equipo</b> (MQTT cifrado).<br>"
                   "3. LabelBridge la recibe en el acto y la imprime. Si el equipo está apagado, la etiqueta "
                   "<b>espera en el canal</b> y sale al encenderlo."),
                wrap=True,
            )
        )
        self.add(how)

        conn = Card(
            tr("Canal en tiempo real (MQTT)"),
            tr("Usa un broker MQTT con TLS, por ejemplo EMQX Serverless (cuota gratuita mensual). La guía explica cómo crearlo."),
        )
        self.enabled = SwitchRow(tr("Recibir trabajos de n8n en tiempo real"), tr("Conexión saliente: no hay que abrir puertos."), s.enabled)
        conn.add(self.enabled)
        form = form_layout()
        form.setSpacing(10)
        self.host = QLineEdit(s.host)
        self.host.setPlaceholderText("xxxxxxxx.ala.us-east-1.emqxsl.com")
        form.addRow(tr("Servidor (broker)"), self.host)
        self.port = QSpinBox()
        self.port.setRange(1, 65535)
        self.port.setValue(s.port)
        form.addRow(tr("Puerto"), self.port)
        self.tls = QCheckBox(tr("Conexión cifrada (TLS) — recomendado"))
        self.tls.setChecked(s.tls)
        form.addRow("", self.tls)
        self.username = QLineEdit(s.username)
        self.username.setPlaceholderText("labelbridge-pc")
        form.addRow(tr("Usuario"), self.username)
        self.password = SecretField(tr("Contraseña del broker"), copyable=False)
        self.password.setText(ctx.secrets.get(MQTT_PASSWORD))
        form.addRow(tr("Contraseña"), self.password)
        self.channel = QLineEdit(s.channel)
        self.channel.setPlaceholderText(tr("pc-bodega"))
        self.channel.textChanged.connect(self._update_topic)
        form.addRow(tr("Canal de este equipo"), self.channel)
        conn.add(layout=form)
        self.topic = label("", "Hint", wrap=True, selectable=True)
        conn.add(self.topic)
        self.result = label("", "Hint", wrap=True)
        self.result.setVisible(False)
        conn.add(self.result)
        row = QHBoxLayout()
        self.test_btn = button(tr("Guardar y probar"), "zap", "primary")
        self.test_btn.clicked.connect(self._test)
        save = button(tr("Guardar"), "check")
        save.clicked.connect(self._save)
        row.addWidget(self.test_btn)
        row.addWidget(save)
        row.addStretch(1)
        conn.add(layout=row)
        self.add(conn)

        templates = Card(
            tr("Plantillas de n8n"),
            tr("Importa estos archivos en n8n (Workflows → Import from file). Todos se activan por eventos."),
        )
        for name, desc in (
            ("01-pedido-a-labelbridge.json", tr("Pedido pagado → etiqueta de pedido al instante en este equipo.")),
            ("02-comprar-etiqueta-shopify-shipping.json", tr("Compra la guía con Shopify Shipping y la envía a imprimir.")),
            ("03-etiqueta-a-metafield.json", tr("Guía de cualquier paquetería → metafield del pedido (sin broker).")),
            ("04-avisar-errores.json", tr("Opcional: te avisa solo cuando una etiqueta falla.")),
        ):
            templates.add(label(f"<b>{name}</b> — {desc}", wrap=True))
        row = QHBoxLayout()
        folder = button(tr("Abrir carpeta de plantillas"), "folder")
        folder.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(resource_path("n8n")))))
        guide = button(tr("Ver guía paso a paso"), "file", "ghost")
        guide.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(resource_path("n8n", "README.md")))))
        row.addWidget(folder)
        row.addWidget(guide)
        row.addStretch(1)
        templates.add(layout=row)
        self.add(templates)

        others = Card(tr("Sin broker MQTT"))
        others.add(
            label(
                tr("• <b>Metafield</b>: n8n guarda la URL de la guía en el pedido y LabelBridge la lee desde Shopify "
                   "(pantalla Shopify → «Etiqueta desde un metafield»).<br>"
                   "• <b>n8n en tu misma red</b>: envía directo a la API local de este equipo (pantalla «API local»)."),
                wrap=True,
            )
        )
        self.add(others)
        self.finish()

        ctx.bridge.event.connect(self._on_event)
        self._update_topic()
        self.refresh()

    def _update_topic(self) -> None:
        channel = slugify(self.channel.text() or "pc")
        self.topic.setText(
            tr("En el nodo MQTT de n8n publica en <b>{0}/&lt;id del trabajo&gt;</b> con QoS 1 y Retain activado (así espera si el equipo está apagado).", topics(channel)['jobs'])
        )

    def _on_event(self, kind: str, data) -> None:
        if kind == "connector" and isinstance(data, dict) and data.get("name") == "mqtt":
            self.refresh()
        elif kind == CONFIG_EXTERNAL and data in ("mqtt", "all"):
            self.load_from_config()

    def load_from_config(self) -> None:
        """Muestra lo guardado (p. ej. después de que el asistente cambió la configuración)."""
        s = self.ctx.cfg.data.mqtt
        self.enabled.setChecked(s.enabled)
        self.host.setText(s.host)
        self.port.setValue(s.port)
        self.tls.setChecked(s.tls)
        self.username.setText(s.username)
        self.password.setText(self.ctx.secrets.get(MQTT_PASSWORD))
        self.channel.setText(s.channel)
        self.refresh()

    def refresh(self) -> None:
        svc = self.ctx.services.mqtt
        texts = {"ok": tr("Conectado"), "connecting": tr("Conectando…"), "error": tr("Error"), "off": tr("Desactivado"), "warn": tr("Aviso")}
        self.status.set_state(svc.state, texts.get(svc.state, svc.state))
        self.status.setToolTip(svc.message)

    def _save(self) -> bool:
        s = self.ctx.cfg.data.mqtt
        host = self.host.text().strip().removeprefix("mqtts://").removeprefix("mqtt://").split("/")[0]
        if self.enabled.isChecked() and not host:
            error(self, "n8n", tr("Escribe el servidor (broker) MQTT."))
            return False
        s.enabled = self.enabled.isChecked()
        s.host = host
        s.port = self.port.value()
        s.tls = self.tls.isChecked()
        s.username = self.username.text().strip()
        s.channel = slugify(self.channel.text() or s.channel)
        self.channel.setText(s.channel)
        self.ctx.secrets.set(MQTT_PASSWORD, self.password.text())
        self.ctx.cfg.save("mqtt")
        return True

    def _test(self) -> None:
        if not self._save():
            return
        self.test_btn.setEnabled(False)

        def show(ok: bool, message: str) -> None:
            self.test_btn.setEnabled(True)
            self.result.setObjectName("Hint" if ok else "Warning")
            self.result.setText(("✓ " if ok else "✗ ") + message)
            self.result.setVisible(True)
            self.result.style().unpolish(self.result)
            self.result.style().polish(self.result)

        run_async(self.ctx.services.mqtt.test_connection, lambda m: show(True, m), lambda m: show(False, m))

"""API local: para n8n en tu red, tu web o scripts."""

from __future__ import annotations

import json

from PySide6.QtWidgets import QCheckBox, QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QSpinBox

from ...discovery import local_ipv4_addresses
from ...i18n import N_, tr
from ...secret_store import LOCAL_API_KEY
from ...services import generate_key
from ...system.platform import pick
from ..context import AppContext
from ..widgets import Card, Page, Pill, SecretField, SwitchRow, button, confirm, copy_to_clipboard


class LocalApiPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            tr("API local"),
            tr("Envía trabajos por HTTP a este equipo: n8n self-hosted en tu red, tu web, scripts o un ERP."),
        )
        self.ctx = ctx
        s = ctx.cfg.data.local_api
        self.status = Pill("off", tr("Desactivada"))
        self.header_actions.addWidget(self.status)

        card = Card(tr("Servidor"))
        self.enabled = SwitchRow(tr("API local activa"), tr("Escucha en este PC. Requiere la clave en cada petición."), s.enabled)
        card.add(self.enabled)
        form = QFormLayout()
        form.setSpacing(10)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(s.port)
        form.addRow(tr("Puerto"), self.port)
        self.allow_lan = QCheckBox(tr(pick(N_("Permitir conexiones desde otros equipos de la red (Windows pedirá permiso de firewall)"),
                                           N_("Permitir conexiones desde otros equipos de la red (la Mac puede pedir permiso)"))))
        self.allow_lan.setChecked(s.allow_lan)
        form.addRow("", self.allow_lan)
        key_row = QHBoxLayout()
        self.key = SecretField()
        self.key.setText(ctx.secrets.get(LOCAL_API_KEY))
        key_row.addWidget(self.key, 1)
        regen = button(tr("Regenerar"), "refresh")
        regen.clicked.connect(self._regenerate)
        key_row.addWidget(regen)
        form.addRow(tr("Clave de API"), key_row)
        self.cors = QLineEdit(s.cors_origins)
        self.cors.setPlaceholderText(tr("(opcional) https://mi-web.com — para llamar desde el navegador"))
        form.addRow(tr("Orígenes web (CORS)"), self.cors)
        card.add(layout=form)
        row = QHBoxLayout()
        row.addStretch(1)
        save = button(tr("Guardar"), "check", "primary")
        save.clicked.connect(self._save)
        row.addWidget(save)
        card.add(layout=row)
        self.add(card)

        examples = Card(tr("Ejemplos"), tr("Copia y pega. Cambia el alias de la impresora si no usas «etiquetas»."))
        self.code = QPlainTextEdit()
        self.code.setObjectName("Code")
        self.code.setReadOnly(True)
        self.code.setMinimumHeight(330)
        examples.add(self.code)
        row = QHBoxLayout()
        copy = button(tr("Copiar ejemplos"), "copy")
        copy.clicked.connect(lambda: copy_to_clipboard(self.code.toPlainText()))
        row.addWidget(copy)
        row.addStretch(1)
        examples.add(layout=row)
        self.add(examples)
        self.finish()

        ctx.bridge.event.connect(self._on_event)
        self.refresh()

    def _on_event(self, kind: str, data) -> None:
        if kind == "connector" and isinstance(data, dict) and data.get("name") == "local_api":
            self.refresh()

    def refresh(self) -> None:
        svc = self.ctx.services.local_api
        texts = {"ok": tr("Activa"), "error": tr("Error"), "off": tr("Desactivada")}
        self.status.set_state(svc.state, texts.get(svc.state, svc.state))
        self.status.setToolTip(svc.message)
        s = self.ctx.cfg.data.local_api
        host = "127.0.0.1"
        if s.allow_lan:
            ips = local_ipv4_addresses()
            host = ips[0] if ips else host
        base = f"http://{host}:{s.port}"
        key = tr("TU_CLAVE")
        alias = tr("etiquetas")
        zpl_id, zpl = tr("zpl-prueba-1"), "^XA^FO50,50^A0N,50,50^FD" + tr("Hola") + "^FS^XZ"
        pdf_job = {"id": tr("pedido-1001-envio"), "title": tr("Pedido #1001"), "printer": alias,
                   "url": tr("https://ejemplo.com/etiqueta.pdf")}
        zpl_job = {"id": zpl_id, "printer": alias, "format": "zpl", "content": zpl}
        headers = f'-H "Authorization: Bearer {key}" -H "Content-Type: application/json"'
        lines = [
            "# PowerShell (Windows)",
            f'$h = @{{ Authorization = "Bearer {key}" }}',
            f'$job = @{{ id = "{zpl_id}"; printer = "{alias}"; format = "zpl"; content = "{zpl}" }} | ConvertTo-Json',
            f'Invoke-RestMethod -Method Post -Uri {base}/api/v1/jobs -Headers $h -ContentType "application/json" -Body $job',
            "",
            "# curl (macOS / Linux / Git Bash)",
            "# " + tr("Estado (sin clave)"),
            f"curl {base}/api/v1/health",
            "",
            "# " + tr("Imprimir un PDF desde una URL"),
            f"curl -X POST {base}/api/v1/jobs \\",
            f"  {headers} \\",
            f"  -d '{json.dumps(pdf_job, ensure_ascii=False)}'",
            "",
            "# " + tr("Enviar ZPL"),
            f"curl -X POST {base}/api/v1/jobs \\",
            f"  {headers} \\",
            f"  -d '{json.dumps(zpl_job)}'",
            "",
            "# " + tr("Etiqueta de pedido a partir del JSON de Shopify (webhook orders/paid)"),
            f'#   {{"id": "{tr("pedido-1001")}", "printer": "{alias}", "order": {{ {tr("...pedido de Shopify...")} }}}}',
            "",
            "# " + tr("Ver trabajos recientes"),
            f'curl -H "Authorization: Bearer {key}" {base}/api/v1/jobs',
            "",
            "# " + tr("n8n (nodo HTTP Request): POST {0}/api/v1/jobs", base),
            "#   " + tr("Header: Authorization = Bearer <clave>   Body (JSON): el trabajo"),
        ]
        self.code.setPlainText("\n".join(lines))

    def _regenerate(self) -> None:
        if confirm(self, tr("Regenerar clave"), tr("Las integraciones que usen la clave actual dejarán de funcionar. ¿Continuar?")):
            self.key.setText(generate_key())
            self._save()

    def _save(self) -> None:
        s = self.ctx.cfg.data.local_api
        s.enabled = self.enabled.isChecked()
        s.port = self.port.value()
        s.allow_lan = self.allow_lan.isChecked()
        s.cors_origins = self.cors.text().strip()
        if self.key.text():
            self.ctx.secrets.set(LOCAL_API_KEY, self.key.text())
        self.ctx.cfg.save("local_api")
        self.refresh()

"""API local: para n8n en tu red, tu web o scripts."""

from __future__ import annotations

import json

from PySide6.QtWidgets import QCheckBox, QFormLayout, QHBoxLayout, QLineEdit, QPlainTextEdit, QSpinBox

from ...discovery import local_ipv4_addresses
from ...secret_store import LOCAL_API_KEY
from ...services import generate_key
from ..context import AppContext
from ..widgets import Card, Page, Pill, SecretField, SwitchRow, button, confirm, copy_to_clipboard


class LocalApiPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            "API local",
            "Envía trabajos por HTTP a este equipo: n8n self-hosted en tu red, tu web, scripts o un ERP.",
        )
        self.ctx = ctx
        s = ctx.cfg.data.local_api
        self.status = Pill("off", "Desactivada")
        self.header_actions.addWidget(self.status)

        card = Card("Servidor")
        self.enabled = SwitchRow("API local activa", "Escucha en este PC. Requiere la clave en cada petición.", s.enabled)
        card.add(self.enabled)
        form = QFormLayout()
        form.setSpacing(10)
        self.port = QSpinBox()
        self.port.setRange(1024, 65535)
        self.port.setValue(s.port)
        form.addRow("Puerto", self.port)
        self.allow_lan = QCheckBox("Permitir conexiones desde otros equipos de la red (Windows pedirá permiso de firewall)")
        self.allow_lan.setChecked(s.allow_lan)
        form.addRow("", self.allow_lan)
        key_row = QHBoxLayout()
        self.key = SecretField()
        self.key.setText(ctx.secrets.get(LOCAL_API_KEY))
        key_row.addWidget(self.key, 1)
        regen = button("Regenerar", "refresh")
        regen.clicked.connect(self._regenerate)
        key_row.addWidget(regen)
        form.addRow("Clave de API", key_row)
        self.cors = QLineEdit(s.cors_origins)
        self.cors.setPlaceholderText("(opcional) https://mi-web.com — para llamar desde el navegador")
        form.addRow("Orígenes web (CORS)", self.cors)
        card.add(layout=form)
        row = QHBoxLayout()
        row.addStretch(1)
        save = button("Guardar", "check", "primary")
        save.clicked.connect(self._save)
        row.addWidget(save)
        card.add(layout=row)
        self.add(card)

        examples = Card("Ejemplos", "Copia y pega. Cambia el alias de la impresora si no usas «etiquetas».")
        self.code = QPlainTextEdit()
        self.code.setObjectName("Code")
        self.code.setReadOnly(True)
        self.code.setMinimumHeight(330)
        examples.add(self.code)
        row = QHBoxLayout()
        copy = button("Copiar ejemplos", "copy")
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
        texts = {"ok": "Activa", "error": "Error", "off": "Desactivada"}
        self.status.set_state(svc.state, texts.get(svc.state, svc.state))
        self.status.setToolTip(svc.message)
        s = self.ctx.cfg.data.local_api
        host = "127.0.0.1"
        if s.allow_lan:
            ips = local_ipv4_addresses()
            host = ips[0] if ips else host
        base = f"http://{host}:{s.port}"
        key = "TU_CLAVE"
        pdf_job = {"id": "pedido-1001-envio", "title": "Pedido #1001", "printer": "etiquetas", "url": "https://ejemplo.com/etiqueta.pdf"}
        zpl_job = {"id": "zpl-prueba-1", "printer": "etiquetas", "format": "zpl", "content": "^XA^FO50,50^A0N,50,50^FDHola^FS^XZ"}
        self.code.setPlainText(
            "# PowerShell (Windows)\n"
            f"$h = @{{ Authorization = \"Bearer {key}\" }}\n"
            "$job = @{ id = \"zpl-prueba-1\"; printer = \"etiquetas\"; format = \"zpl\"; "
            "content = \"^XA^FO50,50^A0N,50,50^FDHola^FS^XZ\" } | ConvertTo-Json\n"
            f"Invoke-RestMethod -Method Post -Uri {base}/api/v1/jobs -Headers $h "
            "-ContentType \"application/json\" -Body $job\n\n"
            "# curl (macOS / Linux / Git Bash)\n"
            f"# Estado (sin clave)\ncurl {base}/api/v1/health\n\n"
            f"# Imprimir un PDF desde una URL\ncurl -X POST {base}/api/v1/jobs \\\n"
            f"  -H \"Authorization: Bearer {key}\" -H \"Content-Type: application/json\" \\\n"
            f"  -d '{json.dumps(pdf_job, ensure_ascii=False)}'\n\n"
            f"# Enviar ZPL\ncurl -X POST {base}/api/v1/jobs \\\n"
            f"  -H \"Authorization: Bearer {key}\" -H \"Content-Type: application/json\" \\\n"
            f"  -d '{json.dumps(zpl_job)}'\n\n"
            f"# Etiqueta de pedido a partir del JSON de Shopify (webhook orders/paid)\n"
            f"#   {{\"id\": \"pedido-1001\", \"printer\": \"etiquetas\", \"order\": {{ ...pedido de Shopify... }}}}\n\n"
            f"# Ver trabajos recientes\ncurl -H \"Authorization: Bearer {key}\" {base}/api/v1/jobs\n\n"
            f"# n8n (nodo HTTP Request): POST {base}/api/v1/jobs\n"
            f"#   Header: Authorization = Bearer <clave>   Body (JSON): el trabajo"
        )

    def _regenerate(self) -> None:
        if confirm(self, "Regenerar clave", "Las integraciones que usen la clave actual dejarán de funcionar. ¿Continuar?"):
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

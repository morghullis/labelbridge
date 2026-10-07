"""Ventanas de los documentos legales: leer uno (Acerca de) y aceptar los términos o el aviso del asistente de IA.

Aceptar pide marcar la casilla «He leído y acepto…» (como los instaladores y las apps profesionales): el botón no se
activa antes. Lo aceptado se guarda con su versión (legal.TERMS_VERSION, legal.AI_NOTICE_VERSION).
"""

from __future__ import annotations

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QTextBrowser, QVBoxLayout, QWidget

from .. import legal
from ..i18n import tr
from .widgets import button, label


def _browser(markdown: str) -> QTextBrowser:
    view = QTextBrowser()
    view.setObjectName("LegalText")
    view.setOpenLinks(False)  # los enlaces se abren en el navegador, no dentro de la ventana
    view.anchorClicked.connect(lambda url: QDesktopServices.openUrl(url))
    view.setMarkdown(markdown)
    return view


class DocumentDialog(QDialog):
    """Un documento para leer (términos, privacidad, IA, licencias)."""

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        text = legal.document(name)
        self.setWindowTitle(text.splitlines()[0].lstrip("# ").strip() or "LabelBridge")
        self.resize(720, 640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(14)
        layout.addWidget(_browser(text), 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = button(tr("Cerrar"), None, "primary")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)


class AcceptDialog(QDialog):
    """Leer y aceptar: la casilla habilita «Aceptar y continuar». Sin aceptar, `exec()` devuelve Rejected."""

    def __init__(self, documents: tuple[str, ...], intro: str, checkbox: str, decline: str,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("LabelBridge")
        self.resize(760, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 22)
        layout.setSpacing(14)
        layout.addWidget(label(intro, "Muted", wrap=True))
        layout.addWidget(_browser("\n\n---\n\n".join(legal.document(name) for name in documents)), 1)
        contact = label(tr("¿Dudas? Escríbenos a <a href=\"mailto:{0}\">{0}</a>.", legal.CONTACT_EMAIL), "Small")
        contact.setOpenExternalLinks(True)
        layout.addWidget(contact)
        self.agree = QCheckBox(checkbox)
        layout.addWidget(self.agree)
        row = QHBoxLayout()
        row.addStretch(1)
        self.decline_btn = button(decline)
        self.decline_btn.clicked.connect(self.reject)
        self.accept_btn = button(tr("Aceptar y continuar"), None, "primary")
        self.accept_btn.setEnabled(False)
        self.accept_btn.clicked.connect(self.accept)
        self.agree.toggled.connect(self.accept_btn.setEnabled)
        row.addWidget(self.decline_btn)
        row.addWidget(self.accept_btn)
        layout.addLayout(row)


def terms_dialog(parent: QWidget | None = None) -> AcceptDialog:
    return AcceptDialog(
        (legal.TERMS, legal.PRIVACY),
        tr("Antes de empezar, revisa cómo funciona LabelBridge y qué hace con tus datos. Solo se pide una vez (y otra "
           "vez si estos documentos cambian)."),
        tr("He leído y acepto los Términos de uso y la Política de privacidad"),
        tr("No acepto (cerrar LabelBridge)"),
        parent,
    )


def ai_notice_dialog(parent: QWidget | None = None) -> AcceptDialog:
    return AcceptDialog(
        (legal.AI_NOTICE,),
        tr("El asistente envía datos de tu tienda al proveedor de inteligencia artificial que elegiste."),
        tr("Entiendo qué datos se envían y quiero usar el asistente"),
        tr("Ahora no"),
        parent,
    )


def ensure_ai_notice(cfg, parent: QWidget | None = None) -> bool:
    """True si ya se aceptó el aviso del asistente (o se acepta ahora). Se pide antes del primer mensaje."""
    if cfg.data.assistant.ai_notice_accepted >= legal.AI_NOTICE_VERSION:
        return True
    if ai_notice_dialog(parent).exec() != QDialog.DialogCode.Accepted:
        return False
    cfg.data.assistant.ai_notice_accepted = legal.AI_NOTICE_VERSION
    cfg.save("assistant")
    return True


def open_link(url: str) -> None:
    QDesktopServices.openUrl(QUrl(url))

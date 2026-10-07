"""Ajustes → Acerca de: qué es LabelBridge, su versión y el sistema, quién la desarrolla y cómo contactarlo, y los
documentos legales (términos, privacidad, asistente de IA y licencias de terceros). Como el «Acerca de» de las apps
profesionales: con «Copiar información del sistema» para pedir ayuda sin escribirla a mano.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ... import __version__, legal
from ...i18n import tr
from ..icons import app_icon_pixmap
from ..legal_dialog import DocumentDialog
from ..widgets import Card, FlowLayout, button, form_layout, label, link_button


class AboutPanel(QWidget):
    def __init__(self, check_updates=None) -> None:
        super().__init__()
        column = QVBoxLayout(self)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(18)

        # La app: logo, nombre, versión y para qué sirve.
        product = Card()
        head = QHBoxLayout()
        head.setSpacing(18)
        logo = QLabel()
        pixmap = app_icon_pixmap(128)
        pixmap.setDevicePixelRatio(2.0)
        logo.setPixmap(pixmap)
        logo.setFixedSize(64, 64)
        logo.setScaledContents(True)
        logo.setAccessibleName("LabelBridge")
        head.addWidget(logo, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(4)
        text.addWidget(label("LabelBridge", "AboutTitle"))
        text.addWidget(label(tr("Versión {0}", __version__), "Muted", selectable=True))
        text.addWidget(label(tr("Imprime las guías de envío y las etiquetas de los pedidos de tu tienda, compra las "
                                "guías en Shopify y te dice cómo va tu negocio cada semana."), wrap=True))
        head.addLayout(text, 1)
        product.add(layout=head)
        actions = FlowLayout(spacing=8)
        if check_updates is not None:
            updates = button(tr("Buscar actualizaciones"), "download", "primary")
            updates.clicked.connect(check_updates)
            actions.addWidget(updates)
        self.copy_btn = button(tr("Copiar información del sistema"), "copy",
                               tooltip=tr("Para pedir ayuda: versión, sistema y equipo (sin datos de tu tienda ni claves)"))
        self.copy_btn.clicked.connect(self._copy_info)
        actions.addWidget(self.copy_btn)
        product.add(layout=actions)
        column.addWidget(product)

        # Quién la hace y cómo contactarlo.
        developer = Card(tr("Desarrollador y soporte"))
        form = form_layout()
        form.addRow(tr("Desarrollado por"), label(legal.DEVELOPER, selectable=True))
        email = label(f'<a href="mailto:{legal.CONTACT_EMAIL}">{legal.CONTACT_EMAIL}</a>')
        email.setOpenExternalLinks(True)
        email.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
        form.addRow(tr("Contacto y soporte"), email)
        site = label(f'<a href="{legal.WEBSITE}">{legal.WEBSITE.split("//", 1)[-1].rstrip("/")}</a>')
        site.setOpenExternalLinks(True)
        form.addRow(tr("Sitio y descargas"), site)
        developer.add(layout=form)
        developer.add(label(tr("Al escribir a soporte, pega la información del sistema (botón de arriba): así te "
                               "ayudamos más rápido."), "Hint", wrap=True))
        column.addWidget(developer)

        # Los documentos.
        documents = Card(tr("Legal y privacidad"),
                         tr("Tus datos se quedan en este equipo: solo salen hacia los servicios que tú conectas."))
        links = FlowLayout(spacing=18)
        for name, title in ((legal.TERMS, tr("Términos de uso")), (legal.PRIVACY, tr("Política de privacidad")),
                            (legal.AI_NOTICE, tr("Uso del asistente de IA")),
                            (legal.THIRD_PARTY, tr("Licencias de terceros"))):
            link = link_button(title)
            link.clicked.connect(lambda _=False, n=name: DocumentDialog(n, self.window()).exec())
            links.addWidget(link)
        documents.add(layout=links)
        column.addWidget(documents)

        footer = label(tr("{0}. Todos los derechos reservados.", legal.COPYRIGHT), "Small")
        footer.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        column.addWidget(footer)
        column.addStretch(1)

    def _copy_info(self) -> None:
        QApplication.clipboard().setText(legal.system_info())
        self.copy_btn.setText(tr("Copiada ✓"))

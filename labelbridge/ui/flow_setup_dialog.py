"""«Shopify Flow compra la FedEx»: cómo armar el flujo de la tienda que compra la guía FedEx sola.

Shopify no deja que una app compre FedEx (shippingLabelPurchase no la admite), pero su app gratis Shopify Flow sí: la
acción «Purchase shipping label» compra la guía del servicio elegido en cuanto un pedido está listo para preparar. Flow
no tiene API para crear flujos, así que esta ventana lo explica paso a paso con los valores de las Reglas de paquetería
de LabelBridge (la regla de FedEx, el servicio). LabelBridge hace el resto: espera la guía, la imprime y avisa si no
llega.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ..connectors.orders_overview import FLOW_WAIT_SECONDS, rule_text
from ..connectors.shopify import admin_url
from ..connectors.shopify_orders import MANUAL_TAG
from ..i18n import tr
from .widgets import button, copy_to_clipboard, fit_height, label, step_title


def fedex_rule(rules) -> object | None:
    """La regla de paquetería que manda FedEx (la que el flujo tiene que copiar), si hay."""
    return next((rule for rule in rules or [] if "fedex" in str(getattr(rule, "carrier", "")).lower()
                 and not getattr(rule, "auto", True)), None)


def flow_conditions(rule) -> list[tuple[str, str, str]]:
    """Las condiciones del flujo, (campo de Flow, comparación, valor): un envío pagado que cumple la regla de FedEx de
    LabelBridge y que no es un pedido manual de LabelBridge (esos los compra quien los crea, con confirmación)."""
    items = [
        ("fulfillmentOrder / deliveryMethod / methodType", tr("es igual a"), "SHIPPING"),
        ("fulfillmentOrder / order / displayFinancialStatus", tr("es igual a"), "PAID"),
    ]
    method = (getattr(rule, "method", "") or "").strip()
    over = float(getattr(rule, "over", 0) or 0)
    if method:
        items.append(("fulfillmentOrder / order / shippingLine / title", tr("contiene"), method))
    if over > 0:
        amount = f"{over:.2f}".rstrip("0").rstrip(".")
        items.append(("fulfillmentOrder / order / currentSubtotalPriceSet / shopMoney / amount", tr("es mayor que"),
                      amount))
    items.append(("fulfillmentOrder / order / tags", tr("no incluye"), MANUAL_TAG))
    return items


class FlowSetupDialog(QDialog):
    """Los pasos para que Shopify Flow compre la FedEx, con botones para abrir Flow y copiar cada valor."""

    def __init__(self, parent: QWidget | None, shop: str, rule, service: str) -> None:
        super().__init__(parent)
        self.shop = shop
        self.setWindowTitle(tr("Shopify Flow compra la FedEx"))
        self.setMinimumWidth(640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(10)
        layout.addWidget(label(tr("Que Shopify Flow compre la FedEx automáticamente"), "CardTitle"))
        which = rule_text(rule) if rule is not None else ""
        layout.addWidget(label(
            tr("Shopify Flow es una app gratis de Shopify. Con este flujo, la guía FedEx de cada pedido{0} se compra "
               "sola en cuanto entra. LabelBridge la imprime (si Shopify no le da el PDF, abre el pedido para que "
               "pulses «Imprimir etiqueta») y te avisa si en {1} min no aparece.",
               tr(" con {0}", which) if which else "", FLOW_WAIT_SECONDS // 60),
            "Muted", wrap=True,
        ))

        # 1 · Flow
        layout.addWidget(step_title(1, tr("En Shopify, abre Flow y pulsa «Crear flujo de trabajo»")))
        open_row = QHBoxLayout()
        open_row.setContentsMargins(34, 0, 0, 0)
        open_flow = button(tr("Abrir Shopify Flow"), "link", "primary")
        open_flow.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(admin_url(self.shop, "apps/flow"))))
        open_row.addWidget(open_flow)
        open_row.addStretch(1)
        layout.addLayout(open_row)

        # 2 · Disparador
        layout.addWidget(step_title(2, tr("Disparador: «Fulfillment order ready to fulfill»")))
        layout.addWidget(self._hint(tr("Se dispara cuando un pedido queda listo para preparar (también al liberarlo "
                                       "si estaba retenido).")))

        # 3 · Condición
        layout.addWidget(step_title(3, tr("Condición: agrega estas, unidas con «Y»")))
        layout.addLayout(self._conditions(flow_conditions(rule)))

        # 4 · Acción
        layout.addWidget(step_title(4, tr("Entonces: acción «Purchase shipping label»")))
        layout.addLayout(self._card([
            (tr("Servicio de envío"), service, ""),
            (tr("Paquete y peso"), tr("Tu paquete guardado y el peso de los artículos"), ""),
            (tr("Confirmación de envío"), tr("El mismo día"), ""),
        ]))

        # 5 · Activar
        layout.addWidget(step_title(5, tr("Pulsa «Activar flujo»")))
        layout.addWidget(self._hint(tr("Si tienes el flujo viejo que llamaba a tu web (/api/etiquetas), desactívalo: "
                                       "ya no hace falta.")))
        note = label(tr("Flow no puede pedir firma ni seguro adicional. Si quieres FedEx con firma y seguro, en Reglas "
                        "de paquetería elige comprarla tú con la ventana «Guía FedEx»."), "Hint", wrap=True)
        layout.addWidget(note)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        done = button(tr("Listo"), "check", "primary")
        done.clicked.connect(self.accept)
        buttons.addWidget(done)
        layout.addLayout(buttons)

    def fit(self) -> None:
        """El alto justo para el contenido (los textos largos ocupan varias líneas, p. ej. en inglés)."""
        fit_height(self)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self.fit)

    @staticmethod
    def _conditions(items: list[tuple[str, str, str]]) -> QHBoxLayout:
        """Cada condición en dos líneas: el campo de Flow entero y debajo la comparación y el valor, con «Copiar»."""
        card = QFrame()
        card.setObjectName("Card")
        grid = QGridLayout(card)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        for index, (field, how, value) in enumerate(items):
            line = index * 2
            if index:
                grid.setRowMinimumHeight(line - 1, 0)
            grid.addWidget(label(field, "Muted", wrap=True, selectable=True), line, 0, 1, 3)
            grid.addWidget(label(how, "Small"), line + 1, 0, alignment=Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(label(value, "ItemTitle", selectable=True), line + 1, 1,
                           alignment=Qt.AlignmentFlag.AlignVCenter)
            copy_btn = button(tr("Copiar"), "copy", None, tr("Copia el valor para pegarlo en Flow"))
            copy_btn.setMinimumWidth(copy_btn.sizeHint().width())
            copy_btn.clicked.connect(lambda _=False, text=value: copy_to_clipboard(text))
            grid.addWidget(copy_btn, line + 1, 2, alignment=Qt.AlignmentFlag.AlignVCenter)
            if index < len(items) - 1:
                grid.setRowMinimumHeight(line + 1, copy_btn.sizeHint().height() + 8)
        grid.setColumnStretch(1, 1)
        box = QHBoxLayout()
        box.setContentsMargins(34, 0, 0, 0)
        box.addWidget(card, 1)
        return box

    @staticmethod
    def _hint(text: str) -> QWidget:
        hint = label(text, "Small", wrap=True)
        hint.setContentsMargins(34, 0, 0, 0)
        return hint

    @staticmethod
    def _card(lines: list[tuple[str, str, str]]) -> QHBoxLayout:
        """Una tarjeta con (qué, cuál) por línea, sangrada bajo el paso."""
        card = QFrame()
        card.setObjectName("Card")
        grid = QGridLayout(card)
        grid.setContentsMargins(14, 10, 14, 10)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        for line, (what, value, _copy) in enumerate(lines):
            grid.addWidget(label(what, "Muted"), line, 0, alignment=Qt.AlignmentFlag.AlignTop)
            grid.addWidget(label(value, "ItemTitle", wrap=True, selectable=True), line, 1,
                           alignment=Qt.AlignmentFlag.AlignTop)
        grid.setColumnStretch(1, 1)
        box = QHBoxLayout()
        box.setContentsMargins(34, 0, 0, 0)
        box.addWidget(card, 1)
        return box

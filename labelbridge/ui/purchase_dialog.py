"""«Comprar guía»: la guía de un pedido que necesita una persona se compra desde LabelBridge con Shopify Shipping.

Se llenan los datos que hagan falta (caja, peso, aviso al cliente), se confirma y LabelBridge compra la guía, la
imprime y Shopify deja el pedido preparado con su número de seguimiento. FedEx no se puede comprar por la API de
Shopify: se compra en Shopify con un clic (LabelBridge la imprime sola) o aquí con la paquetería que elija Shopify.
"""

from __future__ import annotations

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QProgressBar,
    QRadioButton,
    QVBoxLayout,
    QWidget,
)

from ..config import PACKAGE_TYPES
from ..connectors.orders_overview import OrderRow, friendly_reason
from ..connectors.shopify import admin_url
from ..i18n import tr
from .context import AppContext
from .widgets import button, label, run_async

DIM_UNITS = {"INCHES": "in", "CENTIMETERS": "cm"}
WEIGHT_UNITS = {"OUNCES": "oz", "POUNDS": "lb", "GRAMS": "g", "KILOGRAMS": "kg"}


def _spin(value: float, maximum: float = 999.0, decimals: int = 2) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, maximum)
    spin.setDecimals(decimals)
    spin.setValue(float(value))
    spin.setMinimumWidth(78)
    return spin


def _units(options: dict[str, str], current: str) -> QComboBox:
    combo = QComboBox()
    for code, text in options.items():
        combo.addItem(text, code)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    return combo


class PurchaseDialog(QDialog):
    """Compra confirmada de la guía de un pedido (Pedidos → «Comprar guía»)."""

    def __init__(self, parent: QWidget, ctx: AppContext, row: OrderRow) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.row = row
        self.bought = False
        self.busy = False  # comprando: la ventana no se cierra hasta saber el resultado
        s = ctx.cfg.data.shopify
        p = s.package
        self.setWindowTitle(tr("Comprar guía · {0}", row.name))
        self.setMinimumWidth(600)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(12)

        layout.addWidget(label(tr("Comprar la guía de {0}", row.name), "CardTitle"))
        who = " · ".join(part for part in (row.customer, row.destination, row.method) if part)
        if row.subtotal:
            who += " · " + tr("{0} en piezas", f"US${row.subtotal:,.2f}")
        layout.addWidget(label(who, "Muted", wrap=True))
        self.fedex = (row.carrier or row.rule_carrier) == "FedEx"
        self.problem_label = label(tr("Shopify no la compró: {0}.", row.problem), "Warning", wrap=True)
        self.problem_label.setVisible(bool(row.problem))
        layout.addWidget(self.problem_label)
        if row.status and not row.problem and not self.fedex:
            layout.addWidget(label(row.status, "Small", wrap=True))

        # FedEx: Shopify no deja comprarla por API. Un clic en Shopify (se imprime sola) u otra paquetería aquí.
        self.other_carrier = QCheckBox(tr("Comprarla aquí con la paquetería que elija Shopify (no FedEx)"))
        if self.fedex:
            layout.addWidget(label(
                tr("Shopify no deja que ninguna app compre guías de FedEx. Cómprala en el pedido en Shopify (1 clic) "
                   "y LabelBridge la imprime sola."),
                "Warning", wrap=True,
            ))
            shopify_row = QHBoxLayout()
            open_btn = button(tr("Comprar FedEx en Shopify"), "link", "primary")
            open_btn.clicked.connect(self._open_in_shopify)
            shopify_row.addWidget(open_btn)
            shopify_row.addStretch(1)
            layout.addLayout(shopify_row)
            self.other_carrier.toggled.connect(lambda _on: self._update())
            layout.addWidget(self.other_carrier)

        self.form_box = QWidget()
        form = QFormLayout(self.form_box)
        form.setContentsMargins(0, 6, 0, 0)
        form.setSpacing(10)
        form.addRow(tr("Paquetería"), label(tr("La que elija Shopify: el servicio que pagó el cliente o el recomendado"), wrap=True))

        # Paquete: el de Shopify o una caja propia (se puede recordar para la próxima).
        self.package_default = QRadioButton(tr("El paquete predeterminado de Shopify"))
        self.package_custom = QRadioButton(tr("Mi caja:"))
        group = QButtonGroup(self)
        group.addButton(self.package_default)
        group.addButton(self.package_custom)
        (self.package_custom if p.use_custom else self.package_default).setChecked(True)
        self.package_type = QComboBox()
        for code, text in PACKAGE_TYPES.items():
            self.package_type.addItem(tr(text), code)
        self.package_type.setCurrentIndex(max(0, self.package_type.findData(p.type)))
        self.box_length, self.box_width, self.box_height = _spin(p.length), _spin(p.width), _spin(p.height)
        self.dim_unit = _units(DIM_UNITS, p.dim_unit)
        self.box_weight = _spin(p.weight)
        self.box_weight_unit = _units(WEIGHT_UNITS, p.weight_unit)
        dims = QHBoxLayout()
        dims.setSpacing(6)
        dims.addWidget(self.package_type)
        for index, spin in enumerate((self.box_length, self.box_width, self.box_height)):
            if index:
                dims.addWidget(label("×"))
            dims.addWidget(spin)
        dims.addWidget(self.dim_unit)
        dims.addStretch(1)
        box_weight = QHBoxLayout()
        box_weight.setSpacing(6)
        box_weight.addWidget(label(tr("Caja vacía")))
        box_weight.addWidget(self.box_weight)
        box_weight.addWidget(self.box_weight_unit)
        box_weight.addStretch(1)
        self.remember = QCheckBox(tr("Recordar esta caja para las próximas guías"))
        self.remember.setChecked(p.use_custom)
        package_box = QVBoxLayout()
        package_box.setSpacing(6)
        package_box.addWidget(self.package_default)
        package_box.addWidget(self.package_custom)
        self.custom_box = QWidget()
        custom_layout = QVBoxLayout(self.custom_box)
        custom_layout.setContentsMargins(24, 0, 0, 0)
        custom_layout.setSpacing(6)
        custom_layout.addLayout(dims)
        custom_layout.addLayout(box_weight)
        custom_layout.addWidget(self.remember)
        package_box.addWidget(self.custom_box)
        form.addRow(tr("Paquete"), package_box)

        # Peso total: lo calcula Shopify (productos + paquete) o se indica.
        self.weight_auto = QRadioButton(tr("Que lo calcule Shopify (productos + paquete)"))
        self.weight_manual = QRadioButton(tr("Indicarlo:"))
        weights = QButtonGroup(self)
        weights.addButton(self.weight_auto)
        weights.addButton(self.weight_manual)
        self.weight_auto.setChecked(True)
        self.total_weight = _spin(1.0)
        self.total_weight_unit = _units(WEIGHT_UNITS, "POUNDS")
        manual_weight = QHBoxLayout()
        manual_weight.setSpacing(6)
        manual_weight.addWidget(self.weight_manual)
        manual_weight.addWidget(self.total_weight)
        manual_weight.addWidget(self.total_weight_unit)
        manual_weight.addStretch(1)
        weight_box = QVBoxLayout()
        weight_box.setSpacing(6)
        weight_box.addWidget(self.weight_auto)
        weight_box.addLayout(manual_weight)
        form.addRow(tr("Peso total"), weight_box)

        self.notify = QCheckBox(tr("Avisar al cliente por correo con el número de seguimiento"))
        self.notify.setChecked(s.notify_customer)
        form.addRow("", self.notify)
        layout.addWidget(self.form_box)

        printer = s.shipping_label_printer or tr("la impresora predeterminada")
        self.money_note = label(
            tr("Shopify cobra la guía en tu factura. Al confirmar, LabelBridge la compra, la imprime en «{0}» y el pedido "
               "queda preparado en Shopify con su número de seguimiento.", printer),
            "Hint", wrap=True,
        )
        layout.addWidget(self.money_note)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)  # «ocupado» mientras Shopify compra
        self.progress.setFixedHeight(8)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        # El resultado: en curso, listo o lo que Shopify contestó (cada uno con su estilo).
        self.statuses = {style: label("", style or None, wrap=True) for style in ("", "Hint", "Warning")}
        for status in self.statuses.values():
            status.setVisible(False)
            layout.addWidget(status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.track_btn = button(tr("Rastrear"), "truck")
        self.track_btn.setVisible(False)
        self.track_btn.clicked.connect(self._track)
        buttons.addWidget(self.track_btn)
        self.cancel_btn = button(tr("Cancelar"))
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)
        self.buy_btn = button(tr("Confirmar y comprar"), "check", "primary")
        self.buy_btn.clicked.connect(self._buy)
        buttons.addWidget(self.buy_btn)
        layout.addLayout(buttons)

        for widget in (self.package_default, self.package_custom, self.weight_auto, self.weight_manual):
            widget.toggled.connect(lambda _on: self._update())
        self.tracking_url = ""
        self._update()

    # --- estado --------------------------------------------------------------------------------
    def reject(self) -> None:
        if not self.busy:  # Esc o la X durante la compra: se espera el resultado
            super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.busy:
            event.ignore()
            return
        super().closeEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self._fit)  # con los estilos ya aplicados: el alto justo para el texto

    def _fit(self) -> None:
        """El alto justo para el contenido al ancho actual (con textos que ocupan dos líneas, p. ej. en inglés)."""
        layout = self.layout()
        layout.activate()
        width = max(self.minimumWidth(), self.size().width())
        needed = layout.heightForWidth(width) if layout.hasHeightForWidth() else layout.sizeHint().height()
        self.resize(width, max(needed, layout.minimumSize().height()))

    def _update(self) -> None:
        usable = (not self.fedex or self.other_carrier.isChecked()) and not self.bought
        self.form_box.setVisible(usable)
        self.money_note.setVisible(usable)
        self.buy_btn.setVisible(not self.bought)
        self.buy_btn.setEnabled(usable)
        self.custom_box.setEnabled(self.package_custom.isChecked())
        self.total_weight.setEnabled(self.weight_manual.isChecked())
        self.total_weight_unit.setEnabled(self.weight_manual.isChecked())
        if self.isVisible():
            QTimer.singleShot(0, self._fit)

    def options(self) -> dict:
        """Lo elegido, como lo pide Shopify."""
        package = None
        if self.package_custom.isChecked():
            package = {
                "type": self.package_type.currentData(),
                "dimensions": {"length": self.box_length.value(), "width": self.box_width.value(),
                               "height": self.box_height.value(),
                               "unit": self.dim_unit.currentData()},
                "weight": {"value": self.box_weight.value(), "unit": self.box_weight_unit.currentData()},
            }
        weight = None
        if self.weight_manual.isChecked():
            weight = {"value": self.total_weight.value(), "unit": self.total_weight_unit.currentData()}
        return {"package": package, "total_weight": weight, "notify": self.notify.isChecked()}

    def _problem(self) -> str:
        if self.package_custom.isChecked() and min(self.box_length.value(), self.box_width.value(), self.box_height.value()) <= 0:
            return tr("Escribe las medidas de la caja.")
        if self.weight_manual.isChecked() and self.total_weight.value() <= 0:
            return tr("Escribe el peso total del paquete.")
        return ""

    def _remember_box(self) -> None:
        s = self.ctx.cfg.data.shopify
        p = s.package
        p.use_custom = self.package_custom.isChecked() and self.remember.isChecked()
        if self.package_custom.isChecked():
            p.type = self.package_type.currentData()
            p.length, p.width, p.height = self.box_length.value(), self.box_width.value(), self.box_height.value()
            p.dim_unit = self.dim_unit.currentData()
            p.weight, p.weight_unit = self.box_weight.value(), self.box_weight_unit.currentData()
        self.ctx.cfg.save("shopify_package")  # solo la caja: no hace falta reiniciar la conexión con Shopify

    # --- acciones ------------------------------------------------------------------------------
    def _buy(self) -> None:
        problem = self._problem()
        if problem:
            self._show(problem, "Warning")
            return
        self._remember_box()
        chosen = self.options()
        self.busy = True
        self.buy_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.form_box.setEnabled(False)
        self.progress.setVisible(True)
        self._show(tr("Comprando la guía con Shopify… (tarda unos segundos)"), "")
        service = self.ctx.services.shopify
        run_async(
            lambda: service.buy_label_now(self.row.legacy, package=chosen["package"],
                                          total_weight=chosen["total_weight"], notify=chosen["notify"]),
            self._done,
            self._failed,
        )

    def _done(self, result: dict) -> None:
        self.busy = False
        self.progress.setVisible(False)
        self.cancel_btn.setEnabled(True)
        state = result.get("state")
        if state == "purchased":
            self.bought = True
            carrier, tracking = result.get("carrier") or "", result.get("tracking") or ""
            self._show(tr("✓ Guía comprada: {0} {1}. Se está imprimiendo y el pedido quedó preparado en Shopify.",
                          carrier, tracking).replace("  ", " "), "Hint")
            self.cancel_btn.setText(tr("Cerrar"))
            self.problem_label.setVisible(False)
            self.tracking_url = result.get("tracking_url") or ""
            self.track_btn.setVisible(bool(tracking))
            self._update()
            return
        self.form_box.setEnabled(True)
        if state == "uncertain":
            self.bought = True  # no se puede volver a comprar desde aquí: hay que mirarlo en Shopify
            self._show(tr("No se pudo confirmar si Shopify compró la guía: revisa el pedido en Shopify antes de comprar otra."),
                       "Warning")
            self.cancel_btn.setText(tr("Cerrar"))
            self._update()
        elif state == "failed":
            self._show(tr("Shopify no compró la guía: {0}. Corrige los datos y vuelve a confirmar.",
                          friendly_reason(result.get("reason") or "") or tr("sin detalle")), "Warning")
            self.buy_btn.setEnabled(True)
        else:  # sigue en curso: el ciclo de Shopify la imprime cuando termine
            self.bought = True
            self._show(tr("Shopify sigue procesando la compra. LabelBridge la imprimirá sola en cuanto termine."), "Hint")
            self.cancel_btn.setText(tr("Cerrar"))
            self._update()

    def _failed(self, message: str) -> None:
        self.busy = False
        self.progress.setVisible(False)
        self.cancel_btn.setEnabled(True)
        self.form_box.setEnabled(True)
        self.buy_btn.setEnabled(True)
        self._show(message, "Warning")

    def _show(self, text: str, style: str) -> None:
        for name, status in self.statuses.items():
            status.setText(text if name == style else "")
            status.setVisible(bool(text) and name == style)
        if self.isVisible():
            QTimer.singleShot(0, self._fit)

    def _open_in_shopify(self) -> None:
        QDesktopServices.openUrl(QUrl(admin_url(self.ctx.cfg.data.shopify.shop, f"orders/{self.row.legacy}")))

    def _track(self) -> None:
        url = self.tracking_url or next((u for _c, _n, u in self.row.tracking if u), "")
        if url:
            QDesktopServices.openUrl(QUrl(url))
        else:
            self._open_in_shopify()


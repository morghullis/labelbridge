"""«Empaques»: los sobres, bolsas y cajas con que salen tus pedidos, para que cada guía sirva para su empaque.

Los propios se escriben aquí (nombre, tipo, medidas de fuera y peso vacío). Los oficiales de USPS con tarifa plana (Flat
Rate) ya están en todas las listas y no hace falta agregarlos. Una caja o sobre de USPS Priority sin tarifa plana se
agrega como propio, marcado «Oficial de USPS Priority»: así su guía sale con Priority Mail, que es la única que USPS
acepta en ese empaque. Qué empaque lleva cada pedido lo dicen las reglas de paquetería.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from ..config import LEGACY_BOX_ID, PACKAGE_TYPES, ShippingPackage
from ..connectors import packaging as pk
from ..i18n import tr
from .context import AppContext
from .widgets import button, error, fit_height, label


def _spin(value: float, maximum: float = 999.0) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0.0, maximum)
    spin.setDecimals(2)
    spin.setValue(float(value))
    spin.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)  # se escribe: sin flechas, cabe en la fila
    spin.setMinimumWidth(62)
    return spin


def _combo(options: dict[str, str], current: str, translate: bool = False) -> QComboBox:
    combo = QComboBox()
    for code, text in options.items():
        combo.addItem(tr(text) if translate else text, code)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    return combo


Use = tuple[str, str, str]  # (paquetería, empaque, servicio) de una regla o de «Todo lo demás»


def saved_uses(settings) -> list[Use]:
    """Dónde se usan los empaques según las reglas guardadas."""
    uses = [(rule.carrier, rule.package, rule.service) for rule in getattr(settings, "shipping_rules", None) or []]
    return uses + [(getattr(settings, "default_carrier", "USPS"), getattr(settings, "default_package", ""),
                    getattr(settings, "default_service", ""))]


def in_use(uses: list[Use], key: str) -> bool:
    """¿Lo usa alguna regla de paquetería o «Todo lo demás»?"""
    return any(package == key for _carrier, package, _service in uses)


def priority_conflict(uses: list[Use], key: str) -> bool:
    """Marcado «Oficial de USPS Priority» pero una regla lo usa con otra paquetería o con Ground Advantage."""
    return any(package == key and (carrier != "USPS" or service not in ("", pk.PRIORITY, pk.EXPRESS))
               for carrier, package, service in uses)


class _Row:
    """Un empaque propio en la lista: sus campos y su botón para quitarlo."""

    def __init__(self, package: ShippingPackage) -> None:
        self.id = package.id or pk.new_id()
        self.name = QLineEdit(package.name.strip() or (pk.own_name(package) if package.id == LEGACY_BOX_ID else ""))
        self.name.setPlaceholderText(tr("p. ej. Bolsa de nailon"))
        self.name.setMinimumWidth(170)
        self.type = _combo(PACKAGE_TYPES, package.type, translate=True)
        self.type.setMinimumWidth(200)
        self.length, self.width, self.height = _spin(package.length), _spin(package.width), _spin(package.height)
        self.dim_unit = _combo(pk.DIM_UNITS, package.dim_unit)
        self.weight = _spin(package.weight)
        self.weight_unit = _combo(pk.WEIGHT_UNITS, package.weight_unit)
        self.priority = QCheckBox(tr("Oficial de USPS Priority"))
        self.priority.setChecked(package.usps_priority)
        self.priority.setToolTip(tr("Una caja o sobre de USPS Priority Mail sin tarifa plana: su guía sale con Priority "
                                    "Mail, la única que USPS acepta en ese empaque."))
        self.remove = button("", "trash", "ghost", tr("Quitar este empaque"))
        self.dims = QWidget()
        dims = QHBoxLayout(self.dims)
        dims.setContentsMargins(0, 0, 0, 0)
        dims.setSpacing(4)
        for index, spin in enumerate((self.length, self.width, self.height)):
            if index:
                dims.addWidget(label("×"))
            dims.addWidget(spin)
        dims.addWidget(self.dim_unit)
        self.empty = QWidget()
        empty = QHBoxLayout(self.empty)
        empty.setContentsMargins(0, 0, 0, 0)
        empty.setSpacing(4)
        empty.addWidget(self.weight)
        empty.addWidget(self.weight_unit)

    def widgets(self) -> tuple[QWidget, ...]:
        return self.name, self.type, self.dims, self.empty, self.priority, self.remove

    def package(self) -> ShippingPackage:
        return ShippingPackage(
            self.id, self.name.text().strip(), self.type.currentData(), round(self.length.value(), 2),
            round(self.width.value(), 2), round(self.height.value(), 2), self.dim_unit.currentData(),
            round(self.weight.value(), 2), self.weight_unit.currentData(), self.priority.isChecked())


class PackagesDialog(QDialog):
    """Tus empaques: los propios (editables) y los oficiales de USPS con tarifa plana (siempre disponibles)."""

    def __init__(self, parent: QWidget | None, ctx: AppContext, uses: list[Use] | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        # Dónde se usa cada empaque: las reglas que se están editando (si se abrió desde ahí) o las guardadas.
        self.uses = saved_uses(ctx.cfg.data.shopify) if uses is None else uses
        self.setWindowTitle(tr("Empaques"))
        self.setMinimumWidth(1080)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(22, 20, 22, 18)
        outer.setSpacing(12)
        outer.addWidget(label(tr("Con qué empacas tus pedidos"), "CardTitle"))
        outer.addWidget(label(
            tr("La guía se compra para el empaque con que sale el paquete. Agrega los tuyos (sobre blanco, bolsa de "
               "nailon, cajas) con sus medidas de fuera y lo que pesan vacíos. En Reglas de paquetería eliges cuál "
               "lleva cada pedido según el envío que pagó."),
            "Muted", wrap=True,
        ))
        self.grid = QGridLayout()
        self.grid.setHorizontalSpacing(8)
        self.grid.setVerticalSpacing(8)
        headers = (tr("Nombre"), tr("Tipo"), tr("Medidas de fuera (largo × ancho × alto)"), tr("Vacío pesa"), "", "")
        for col, text in enumerate(headers):
            self.grid.addWidget(label(text, "Small"), 0, col)
        self.grid.setColumnStretch(0, 1)
        outer.addLayout(self.grid)
        self.rows: list[_Row] = []
        self.empty_note = label(tr("Todavía no agregaste empaques propios."), "Muted")
        outer.addWidget(self.empty_note)
        for package in ctx.cfg.data.shopify.packages:
            self._add_row(package)
        add = button(tr("Agregar empaque"), "plus", "ghost")
        add.clicked.connect(lambda: self._add_row(ShippingPackage(pk.new_id(), "")))
        outer.addWidget(add, 0, Qt.AlignmentFlag.AlignLeft)

        outer.addWidget(label(tr("Oficiales de USPS con tarifa plana (ya están en las listas)"), "Small", wrap=True))
        flat = "  ·  ".join(f"{tr(package.label)} {package.size}" for package in pk.USPS_FLAT_RATE)
        outer.addWidget(label(flat, "Muted", wrap=True))
        outer.addWidget(label(
            tr("USPS solo acepta sus empaques oficiales (Flat Rate y Priority) con guía de Priority Mail o Priority Mail "
               "Express: con ellos LabelBridge la compra así, nunca con Ground Advantage. FedEx: su sobre, Pak y "
               "cajas solo valen con FedEx Express (2Day, Overnight, Express Saver), nunca con FedEx Ground."),
            "Hint", wrap=True,
        ))
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = button(tr("Cancelar"))
        cancel.clicked.connect(self.reject)
        save = button(tr("Guardar"), "check", "primary")
        save.clicked.connect(self.accept)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        outer.addLayout(buttons)
        self._update()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, lambda: fit_height(self))

    def _add_row(self, package: ShippingPackage) -> None:
        row = _Row(package)
        row.remove.clicked.connect(lambda: self._remove_row(row))
        line = len(self.rows) + 1
        for col, widget in enumerate(row.widgets()):
            self.grid.addWidget(widget, line, col)
        self.rows.append(row)
        self._update()
        if not package.name and self.isVisible():
            row.name.setFocus()

    def _remove_row(self, row: _Row) -> None:
        if in_use(self.uses, row.id):
            error(self, tr("Empaques"), tr("«{0}» se usa en tus reglas de paquetería: cámbialas antes de quitarlo.",
                                           row.name.text().strip() or tr("Este empaque")))
            return
        if row in self.rows:
            self.rows.remove(row)
            for widget in row.widgets():
                widget.hide()
                widget.deleteLater()
        self._update()

    def _update(self) -> None:
        self.empty_note.setVisible(not self.rows)
        if self.isVisible():
            QTimer.singleShot(0, lambda: fit_height(self))

    def packages(self) -> list[ShippingPackage]:
        return [row.package() for row in self.rows]

    def problem(self) -> str:
        for row in self.rows:
            package = row.package()
            if not package.name:
                return tr("Ponle nombre a cada empaque.")
            if min(package.length, package.width, package.height) <= 0:
                return tr("Escribe las medidas de «{0}».", package.name)
            if package.usps_priority and priority_conflict(self.uses, package.id):
                return tr("«{0}» es oficial de USPS Priority, pero una regla lo usa con otra paquetería o con Ground "
                          "Advantage: cambia esa regla primero.", package.name)
        return ""

    def accept(self) -> None:
        problem = self.problem()
        if problem:
            error(self, tr("Empaques"), problem)
            return
        super().accept()

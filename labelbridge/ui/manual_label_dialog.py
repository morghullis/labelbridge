"""«Nueva guía manual» (Pedidos): una guía para un envío que no vino de la tienda en línea (un reemplazo, una venta por
mensaje, una muestra…).

Shopify Shipping solo vende guías de pedidos: aquí se escribe a quién va y qué lleva, y al confirmar se crea en
Shopify un pedido marcado «Manual · LabelBridge», pagado y sin cobrar al cliente. Después se abre «Imprimir guía» para
ese pedido (caja, peso y compra, como siempre). La compra automática no toca estos pedidos.
"""

from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..connectors.orders_overview import OrderRow
from ..connectors.shopify import SCOPES_LABELS
from ..connectors.shopify_orders import ManualItem, ProductChoice, Recipient, manual_problem
from ..i18n import tr
from .context import AppContext
from .purchase_dialog import WEIGHT_UNITS, _units
from .widgets import button, confirm, copy_to_clipboard, form_layout, label, run_async

COUNTRIES = {"US": "Estados Unidos", "PR": "Puerto Rico", "MX": "México", "CA": "Canadá", "ES": "España",
             "CO": "Colombia", "DO": "República Dominicana"}


class ManualLabelDialog(QDialog):
    """Paso 1 de la guía manual: destinatario y contenido → pedido manual en Shopify. `created` queda con el pedido."""

    def __init__(self, parent: QWidget, ctx: AppContext) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.created: dict | None = None
        self.recipient: Recipient | None = None
        self.busy = False
        self.choices: list[ProductChoice] = []
        self.chosen: list[tuple[ProductChoice, QSpinBox]] = []
        self.setWindowTitle(tr("Nueva guía manual"))
        self.setMinimumWidth(680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)
        layout.addWidget(label(tr("Nueva guía manual"), "CardTitle"))
        layout.addWidget(label(
            tr("Para un envío que no vino de la tienda en línea. Se crea en Shopify un pedido marcado «Manual · "
               "LabelBridge», pagado y sin cobrar al cliente; después eliges la caja y se compra su guía."),
            "Muted", wrap=True,
        ))

        # --- destinatario ---------------------------------------------------------------------
        layout.addWidget(label(tr("Destinatario"), "SectionTitle"))
        form = form_layout()
        form.setSpacing(8)
        self.name = QLineEdit()
        form.addRow(tr("Nombre"), self.name)
        self.company = QLineEdit()
        self.company.setPlaceholderText(tr("Opcional"))
        form.addRow(tr("Empresa"), self.company)
        self.address1 = QLineEdit()
        form.addRow(tr("Dirección"), self.address1)
        self.address2 = QLineEdit()
        self.address2.setPlaceholderText(tr("Apartamento, suite… (opcional)"))
        form.addRow("", self.address2)
        place = QHBoxLayout()
        place.setSpacing(6)
        self.city = QLineEdit()
        self.city.setPlaceholderText(tr("Ciudad"))
        self.province = QLineEdit()
        self.province.setPlaceholderText(tr("Estado"))
        self.province.setMaximumWidth(90)
        self.zip = QLineEdit()
        self.zip.setPlaceholderText(tr("Código postal"))
        self.zip.setMaximumWidth(130)
        place.addWidget(self.city, 1)
        place.addWidget(self.province)
        place.addWidget(self.zip)
        form.addRow(tr("Ciudad"), place)
        self.country = QComboBox()
        self.country.setEditable(True)
        for code, name in COUNTRIES.items():
            self.country.addItem(f"{code} · {tr(name)}", code)
        form.addRow(tr("País"), self.country)
        self.phone = QLineEdit()
        self.phone.setPlaceholderText(tr("Con código de país, p. ej. +1 305 555 0100"))
        form.addRow(tr("Teléfono"), self.phone)
        self.email = QLineEdit()
        self.email.setPlaceholderText(tr("Opcional: para avisarle con el número de seguimiento"))
        form.addRow(tr("Email"), self.email)
        layout.addLayout(form)

        # --- contenido -------------------------------------------------------------------------
        layout.addWidget(label(tr("Qué lleva"), "SectionTitle"))
        kinds = QHBoxLayout()
        self.use_products = QRadioButton(tr("Productos de la tienda"))
        self.use_free = QRadioButton(tr("Un artículo libre"))
        group = QButtonGroup(self)
        group.addButton(self.use_products)
        group.addButton(self.use_free)
        self.use_products.setChecked(True)
        kinds.addWidget(self.use_products)
        kinds.addWidget(self.use_free)
        kinds.addStretch(1)
        layout.addLayout(kinds)

        self.products_box = QWidget()
        products = QVBoxLayout(self.products_box)
        products.setContentsMargins(0, 0, 0, 0)
        products.setSpacing(6)
        search = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar por nombre o SKU…"))
        self.search.returnPressed.connect(self._search)
        search.addWidget(self.search, 1)
        search_btn = button(tr("Buscar"), "search")
        search_btn.clicked.connect(self._search)
        search.addWidget(search_btn)
        products.addLayout(search)
        self.results = QListWidget()
        self.results.setFixedHeight(110)
        self.results.itemDoubleClicked.connect(lambda _item: self._add_selected())
        products.addWidget(self.results)
        add_row = QHBoxLayout()
        self.search_note = label("", "Small", wrap=True)
        add_row.addWidget(self.search_note, 1)
        self.scopes_btn = button(tr("Copiar permisos"), "copy", "ghost")
        self.scopes_btn.clicked.connect(lambda: copy_to_clipboard(SCOPES_LABELS))
        self.scopes_btn.setVisible(False)
        add_row.addWidget(self.scopes_btn)
        add = button(tr("Agregar"), "plus")
        add.clicked.connect(self._add_selected)
        add_row.addWidget(add)
        products.addLayout(add_row)
        self.items = QTableWidget(0, 3)
        self.items.setHorizontalHeaderLabels([tr("Producto"), tr("Cantidad"), ""])
        self.items.verticalHeader().setVisible(False)
        self.items.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.items.setFixedHeight(120)
        products.addWidget(self.items)
        self.decrement = QCheckBox(tr("Descontar del inventario"))
        self.decrement.setChecked(True)
        products.addWidget(self.decrement)
        layout.addWidget(self.products_box)

        self.free_box = QWidget()
        free = QHBoxLayout(self.free_box)
        free.setContentsMargins(0, 0, 0, 0)
        free.setSpacing(6)
        self.free_title = QLineEdit()
        self.free_title.setPlaceholderText(tr("Qué es, p. ej. «Reemplazo de anillo»"))
        free.addWidget(self.free_title, 1)
        free.addWidget(label(tr("Cantidad")))
        self.free_quantity = QSpinBox()
        self.free_quantity.setRange(1, 999)
        free.addWidget(self.free_quantity)
        free.addWidget(label(tr("Peso c/u")))
        self.free_weight = QDoubleSpinBox()
        self.free_weight.setRange(0, 999)
        self.free_weight.setDecimals(2)
        self.free_weight.setValue(1.0)
        free.addWidget(self.free_weight)
        self.free_weight_unit = _units(WEIGHT_UNITS, "OUNCES")
        free.addWidget(self.free_weight_unit)
        layout.addWidget(self.free_box)

        layout.addWidget(label(tr("Instrucciones (opcional)"), "SectionTitle"))
        self.note = QPlainTextEdit()
        self.note.setPlaceholderText(tr("Por ejemplo: reemplazo del pedido #1023, enviar con tarjeta"))
        self.note.setFixedHeight(64)
        layout.addWidget(self.note)

        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setFixedHeight(8)
        self.progress.setTextVisible(False)
        self.progress.setVisible(False)
        layout.addWidget(self.progress)
        self.status = label("", "Warning", wrap=True)
        self.status.setVisible(False)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.cancel_btn = button(tr("Cancelar"))
        self.cancel_btn.clicked.connect(self.reject)
        buttons.addWidget(self.cancel_btn)
        self.create_btn = button(tr("Crear pedido y seguir con la guía"), "check", "primary")
        self.create_btn.clicked.connect(self._create)
        buttons.addWidget(self.create_btn)
        layout.addLayout(buttons)

        for radio in (self.use_products, self.use_free):
            radio.toggled.connect(lambda _on: self._update())
        self._update()

    # --- estado --------------------------------------------------------------------------------
    def reject(self) -> None:
        if not self.busy:
            super().reject()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.busy:
            event.ignore()
            return
        super().closeEvent(event)

    def _update(self) -> None:
        self.products_box.setVisible(self.use_products.isChecked())
        self.free_box.setVisible(self.use_free.isChecked())
        if self.isVisible():
            QTimer.singleShot(0, self.adjustSize)

    def _show(self, text: str) -> None:
        self.status.setText(text)
        self.status.setVisible(bool(text))

    # --- productos -----------------------------------------------------------------------------
    def _search(self) -> None:
        text = self.search.text().strip()
        if not text:
            return
        self.search_note.setText(tr("Buscando…"))

        def found(choices: list[ProductChoice]) -> None:
            self.choices = choices
            self.results.clear()
            for choice in choices:
                text = choice.title + (f"  ({tr('SKU {0}', choice.sku)})" if choice.sku else "")
                if choice.stock_text:  # con read_inventory
                    text += f"  · {choice.stock_text}"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, choice.variant_id)
                if choice.stock is not None and choice.stock <= 0 and not choice.oversell:
                    item.setForeground(QColor("#BE123C"))
                self.results.addItem(item)
            self.search_note.setText(tr("Doble clic o «Agregar» para sumarlo al envío.") if choices
                                     else tr("No se encontró nada con «{0}».", text))
            self.scopes_btn.setVisible(False)

        def failed(message: str) -> None:
            if "read_products" in message or "access denied" in message.lower() or "permisos" in message:
                self.search_note.setText(tr("Para buscar productos, agrega el permiso read_products a tu app en el Dev "
                                            "Dashboard de Shopify (Copiar permisos) y vuelve a instalarla. Mientras "
                                            "tanto, usa «Un artículo libre»."))
                self.scopes_btn.setVisible(True)
            else:
                self.search_note.setText(message)

        run_async(lambda: self.ctx.services.shopify.search_products(text), found, failed)

    def _add_selected(self) -> None:
        item = self.results.currentItem()
        if item is None:
            return
        variant_id = item.data(Qt.ItemDataRole.UserRole)
        choice = next((c for c in self.choices if c.variant_id == variant_id), None)
        if choice is None:
            return
        if choice.stock is not None and choice.stock <= 0 and not choice.oversell:
            self.search_note.setText(tr("Ojo: «{0}» no tiene existencias en Shopify.", choice.title))
        for existing, spin in self.chosen:
            if existing.variant_id == variant_id:
                spin.setValue(spin.value() + 1)
                return
        row = self.items.rowCount()
        self.items.insertRow(row)
        self.items.setItem(row, 0, QTableWidgetItem(choice.title))
        spin = QSpinBox()
        spin.setRange(1, 999)
        self.items.setCellWidget(row, 1, spin)
        remove = button("", "x", "ghost", tr("Quitar"))
        remove.clicked.connect(lambda _=False, v=variant_id: self._remove(v))
        self.items.setCellWidget(row, 2, remove)
        self.chosen.append((choice, spin))

    def _remove(self, variant_id: str) -> None:
        for index, (choice, _spin) in enumerate(self.chosen):
            if choice.variant_id == variant_id:
                self.items.removeRow(index)
                del self.chosen[index]
                return

    # --- crear ---------------------------------------------------------------------------------
    def _collect(self) -> tuple[Recipient, list[ManualItem]]:
        text = self.country.currentText().strip()
        index = self.country.findText(text)
        country = self.country.itemData(index) if index >= 0 else text.split("·")[0]
        recipient = Recipient(
            name=self.name.text().strip(), address1=self.address1.text().strip(), city=self.city.text().strip(),
            zip=self.zip.text().strip(), country=str(country or "US").strip().upper()[:2],
            province=self.province.text().strip(), address2=self.address2.text().strip(),
            company=self.company.text().strip(), phone=self.phone.text().strip(), email=self.email.text().strip(),
        )
        if self.use_products.isChecked():
            items = [ManualItem(quantity=spin.value(), variant_id=choice.variant_id) for choice, spin in self.chosen]
        else:
            items = [ManualItem(quantity=self.free_quantity.value(), title=self.free_title.text().strip(),
                                weight=self.free_weight.value(), weight_unit=self.free_weight_unit.currentData())]
        return recipient, items

    def _create(self) -> None:
        recipient, items = self._collect()
        problem = manual_problem(recipient, items)
        if problem:
            self._show(problem)
            return
        if self.use_products.isChecked():
            what = ", ".join(f"{spin.value()} × {choice.title}" for choice, spin in self.chosen)
        else:
            what = f"{items[0].quantity} × {items[0].title}"
        where = ", ".join(part for part in (recipient.city, recipient.province, recipient.country) if part)
        if not confirm(self, tr("Nueva guía manual"), tr(
            "Se crea en Shopify un pedido manual para {0} ({1}) con: {2}.\n\nNo se le cobra nada al cliente. Después "
            "eliges la caja y se compra la guía (Shopify la cobra en tu factura).\n\n¿Crear el pedido?",
            recipient.name, where, what,
        )):
            return
        self.recipient = recipient
        self.busy = True
        self.create_btn.setEnabled(False)
        self.cancel_btn.setEnabled(False)
        self.progress.setVisible(True)
        self._show("")
        decrement = self.decrement.isChecked() and self.use_products.isChecked()
        note = self.note.toPlainText().strip()
        run_async(
            lambda: self.ctx.services.shopify.create_manual_order(recipient, items, note, decrement=decrement),
            self._created,
            self._failed,
        )

    def _created(self, created: dict) -> None:
        self.busy = False
        self.created = created
        self.accept()

    def _failed(self, message: str) -> None:
        self.busy = False
        self.progress.setVisible(False)
        self.create_btn.setEnabled(True)
        self.cancel_btn.setEnabled(True)
        self._show(message)

    def order_row(self) -> OrderRow | None:
        """El pedido creado, listo para «Imprimir guía»."""
        if not self.created or self.recipient is None:
            return None
        r = self.recipient
        return OrderRow(
            gid=self.created["gid"], legacy=self.created["legacy"], name=self.created["name"], created=time.time(),
            customer=r.name, destination=", ".join(part for part in (r.city, r.province) if part),
            method=tr("Envío manual (LabelBridge)"), state="action", action="retry",
        )


def run_manual_label(parent: QWidget, ctx: AppContext) -> bool:
    """Los dos pasos: el pedido manual y, si se creó, «Imprimir guía» para ese pedido. True si se creó el pedido."""
    from .purchase_dialog import PurchaseDialog
    from .widgets import run_dialog

    dialog = ManualLabelDialog(parent, ctx)
    run_dialog(dialog)
    row = dialog.order_row()
    if row is None:
        return False
    purchase = PurchaseDialog(parent, ctx, row)
    purchase.notify.setChecked(bool(dialog.recipient and dialog.recipient.email))
    run_dialog(purchase)
    if purchase.fedex_requested:  # FedEx: se compra en Shopify con la ventana que dice qué elegir
        from .fedex_dialog import open_fedex

        open_fedex(parent, ctx, row)
    return True


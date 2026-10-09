"""«Imprimir guía» de un pedido sin guía: se elige el servicio (o «Automático»), el empaque y el peso, y con un clic
LabelBridge la compra con Shopify Shipping, la imprime y Shopify deja el pedido preparado con su seguimiento. Sale
elegido lo que dicen las reglas de paquetería para ese pedido; con un empaque oficial de USPS, solo Priority Mail.

FedEx no se puede comprar por la API de Shopify: al elegirla, el pedido se abre en Shopify (1 clic para comprarla) y
LabelBridge la imprime sola cuando la ve. Si Shopify no ofrece el servicio elegido para ese envío, no cobra nada y se
elige otro aquí mismo.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ..config import PACKAGE_TYPES, ShippingPackage
from ..connectors import packaging as pk
from ..connectors import shipping_services as ss
from ..connectors.orders_overview import OrderRow
from ..connectors.shopify import admin_url
from ..i18n import tr
from . import icons
from .context import AppContext
from .theme import Color
from .widgets import button, label, run_async, run_dialog, window_closed

DIM_UNITS = pk.DIM_UNITS
WEIGHT_UNITS = pk.WEIGHT_UNITS
CUSTOM = "custom"  # «Otro empaque solo para esta guía…»


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


def fill_packages(combo: QComboBox, settings, current: str = pk.SHOPIFY, *, rules: bool = False,
                  sizes: bool = True) -> None:
    """La lista de empaques: (con `rules`, «Según tus reglas»,) el de Shopify, los propios y los oficiales de USPS,
    separados por grupo (con sus medidas si `sizes`), con `current` elegido (o el primero, si ya no existe)."""
    text = pk.describe if sizes else pk.name
    combo.blockSignals(True)
    combo.clear()
    if rules:
        combo.addItem(tr("Según tus reglas (cada pedido el suyo)"), pk.RULES)
    combo.addItem(pk.name(settings, pk.SHOPIFY), pk.SHOPIFY)
    own = list(getattr(settings, "packages", None) or [])
    if own:
        combo.insertSeparator(combo.count())
        for package in own:
            combo.addItem(text(settings, package.id), package.id)
            combo.setItemData(combo.count() - 1, pk.describe(settings, package.id), Qt.ItemDataRole.ToolTipRole)
    combo.insertSeparator(combo.count())
    for package in pk.USPS_FLAT_RATE:
        combo.addItem(text(settings, package.key), package.key)
        combo.setItemData(combo.count() - 1, pk.describe(settings, package.key), Qt.ItemDataRole.ToolTipRole)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    combo.blockSignals(False)


def open_packages(parent: QWidget, ctx: AppContext, uses: list[tuple[str, str, str]] | None = None) -> bool:
    """«Empaques…»: True si se guardaron cambios. `uses`: las reglas en pantalla (si se abre desde ellas)."""
    from .packages_dialog import PackagesDialog

    dialog = PackagesDialog(parent, ctx, uses)
    if run_dialog(dialog) != QDialog.DialogCode.Accepted:
        return False
    ctx.cfg.data.shopify.packages = dialog.packages()
    ctx.cfg.save("shopify_package")  # solo los empaques: no hace falta reiniciar la conexión con Shopify
    return True


class PackageForm(QObject):
    """El empaque y el peso de una guía, en dos listas: el paquete de Shopify, uno de «Empaques» (propios u oficiales
    de USPS) u otro solo para esta guía; y el peso que calcula Shopify o uno escrito."""

    changed = Signal()

    def __init__(self, parent: QWidget, ctx: AppContext, current: str = pk.SHOPIFY) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.parent_widget = parent
        s = ctx.cfg.data.shopify
        self.choice = QComboBox()
        self.choice.setMinimumWidth(320)
        self._fill(current)
        self.manage = button(tr("Empaques…"), "package", "ghost", tr("Tus empaques: sobres, bolsas y cajas"))
        self.manage.clicked.connect(self._manage)

        # «Otro empaque…»: tipo, medidas de fuera y peso vacío (de partida, los del primer empaque propio).
        p = next(iter(s.packages), None) or ShippingPackage(type="BOX", length=6, width=4, height=2, weight=3)
        self.package_type = QComboBox()
        for code, text in PACKAGE_TYPES.items():
            self.package_type.addItem(tr(text), code)
        self.package_type.setCurrentIndex(max(0, self.package_type.findData(p.type)))
        self.box_length, self.box_width, self.box_height = _spin(p.length), _spin(p.width), _spin(p.height)
        self.dim_unit = _units(DIM_UNITS, p.dim_unit)
        self.box_weight = _spin(p.weight)
        self.box_weight_unit = _units(WEIGHT_UNITS, p.weight_unit)
        self.remember = QCheckBox(tr("Guardarlo en Empaques como"))
        self.remember.setChecked(False)
        self.remember_name = QLineEdit()
        self.remember_name.setPlaceholderText(tr("p. ej. Bolsa de nailon"))
        self.remember_name.setEnabled(False)
        self.remember.toggled.connect(self.remember_name.setEnabled)
        self.custom_box = QWidget()
        custom = QVBoxLayout(self.custom_box)
        custom.setContentsMargins(0, 2, 0, 0)
        custom.setSpacing(6)
        dims = QHBoxLayout()
        dims.setSpacing(6)
        dims.addWidget(self.package_type)
        for index, spin in enumerate((self.box_length, self.box_width, self.box_height)):
            if index:
                dims.addWidget(label("×"))
            dims.addWidget(spin)
        dims.addWidget(self.dim_unit)
        dims.addStretch(1)
        custom.addLayout(dims)
        empty = QHBoxLayout()
        empty.setSpacing(6)
        empty.addWidget(label(tr("Vacío pesa")))
        empty.addWidget(self.box_weight)
        empty.addWidget(self.box_weight_unit)
        empty.addStretch(1)
        custom.addLayout(empty)
        keep = QHBoxLayout()
        keep.setSpacing(6)
        keep.addWidget(self.remember)
        keep.addWidget(self.remember_name, 1)
        custom.addLayout(keep)
        self.package_box = QVBoxLayout()
        self.package_box.setSpacing(6)
        pick = QHBoxLayout()
        pick.setSpacing(6)
        pick.addWidget(self.choice, 1)
        pick.addWidget(self.manage)
        self.package_box.addLayout(pick)
        self.package_box.addWidget(self.custom_box)

        # Peso total: lo calcula Shopify (productos + caja) o se escribe.
        self.weight_choice = QComboBox()
        self.weight_choice.addItem(tr("Que lo calcule Shopify (productos + caja)"), "auto")
        self.weight_choice.addItem(tr("Escribir el peso total"), "manual")
        self.total_weight = _spin(1.0)
        self.total_weight_unit = _units(WEIGHT_UNITS, "POUNDS")
        self.weight_box = QHBoxLayout()
        self.weight_box.setSpacing(6)
        self.weight_box.addWidget(self.weight_choice, 1)
        self.weight_box.addWidget(self.total_weight)
        self.weight_box.addWidget(self.total_weight_unit)
        self.choice.currentIndexChanged.connect(lambda _i: self.update())
        self.weight_choice.currentIndexChanged.connect(lambda _i: self.update())
        self.update()

    def add_rows(self, form: QFormLayout) -> None:
        form.addRow(tr("Empaque"), self.package_box)
        form.addRow(tr("Peso"), self.weight_box)

    def _fill(self, current: str) -> None:
        fill_packages(self.choice, self.ctx.cfg.data.shopify, current)
        self.choice.insertSeparator(self.choice.count())
        self.choice.addItem(tr("Otro empaque solo para esta guía…"), CUSTOM)
        if current == CUSTOM:
            self.choice.setCurrentIndex(self.choice.count() - 1)

    def _manage(self) -> None:
        current = self.key()
        if open_packages(self.parent_widget, self.ctx):
            self.choice.blockSignals(True)
            self._fill(current)
            self.choice.blockSignals(False)
            self.update()

    def key(self) -> str:
        """El empaque elegido: pk.SHOPIFY, la clave de uno de «Empaques» o CUSTOM."""
        value = self.choice.currentData()
        return pk.SHOPIFY if value is None else str(value)

    def allowed(self) -> tuple[str, ...] | None:
        """Con qué servicios vale el empaque elegido (None: con cualquiera)."""
        key = self.key()
        return None if key == CUSTOM else pk.allowed_services(self.ctx.cfg.data.shopify, key)

    def update(self) -> None:
        self.custom_box.setVisible(self.key() == CUSTOM)
        manual = self.weight_choice.currentData() == "manual"
        self.total_weight.setVisible(manual)
        self.total_weight_unit.setVisible(manual)
        self.changed.emit()

    def _custom(self) -> ShippingPackage:
        return ShippingPackage(
            pk.new_id(), self.remember_name.text().strip(), self.package_type.currentData(), self.box_length.value(),
            self.box_width.value(), self.box_height.value(), self.dim_unit.currentData(), self.box_weight.value(),
            self.box_weight_unit.currentData())

    def options(self) -> tuple[str | dict, dict | None]:
        """(empaque, peso total) para buy_label_now: la clave del empaque o, con «Otro empaque…», el empaque como lo
        pide Shopify; el peso, None = que lo calcule Shopify."""
        key = self.key()
        package: str | dict = pk.custom_input(self._custom()) if key == CUSTOM else key
        weight = None
        if self.weight_choice.currentData() == "manual":
            weight = {"value": self.total_weight.value(), "unit": self.total_weight_unit.currentData()}
        return package, weight

    def problem(self) -> str:
        custom = self.key() == CUSTOM
        if custom and min(self.box_length.value(), self.box_width.value(), self.box_height.value()) <= 0:
            return tr("Escribe las medidas del empaque.")
        if custom and self.remember.isChecked() and not self.remember_name.text().strip():
            return tr("Escribe un nombre para guardar el empaque.")
        if self.weight_choice.currentData() == "manual" and self.total_weight.value() <= 0:
            return tr("Escribe el peso total del paquete.")
        return ""

    def remember_box(self) -> None:
        """«Guardarlo en Empaques»: el empaque escrito para esta guía queda en la lista para las próximas, y queda
        elegido (así reintentar la compra no lo guarda dos veces)."""
        if self.key() != CUSTOM or not self.remember.isChecked():
            return
        package = self._custom()
        self.ctx.cfg.data.shopify.packages.append(package)
        self.ctx.cfg.save("shopify_package")  # solo los empaques: no hace falta reiniciar la conexión con Shopify
        self.remember.setChecked(False)
        self.remember_name.clear()
        self.choice.blockSignals(True)
        self._fill(package.id)
        self.choice.blockSignals(False)
        self.update()

    def set_enabled(self, enabled: bool) -> None:
        for widget in (self.choice, self.manage, self.custom_box, self.weight_choice, self.total_weight,
                       self.total_weight_unit):
            widget.setEnabled(enabled)


class ServicePicker(QObject):
    """La lista de paqueterías y servicios: «Automático» y los que Shopify ofrece para el envío (todos los del tipo
    de envío si la app no puede saberlo), más FedEx para comprarla en Shopify."""

    changed = Signal()

    def __init__(self, parent: QWidget, *, fedex: bool = True) -> None:
        super().__init__(parent)
        self.combo = QComboBox()
        self.combo.setMinimumWidth(320)
        self.hint = label("", "Small", wrap=True)
        self.route: ss.Route | None = None
        self.loading = False
        self.problem = ""  # por qué no se pudo preguntar a Shopify qué ofrece (se usa la lista completa)
        self.with_fedex = fedex
        self.keep_fedex = False  # tus reglas mandan este pedido por FedEx: sigue en la lista aunque Shopify no la liste
        self.allowed: tuple[str, ...] | None = None  # empaque oficial de USPS: solo estos servicios
        self.package_name = ""
        self.combo.currentIndexChanged.connect(lambda _i: self._changed())
        self.fill(ss.Route())

    def key(self) -> str:
        return str(self.combo.currentData() or ss.AUTO)

    def set_allowed(self, allowed: tuple[str, ...] | None, package_name: str = "") -> None:
        """Con un empaque oficial de USPS solo se puede elegir entre sus servicios (Priority Mail…); sin él, todos."""
        if allowed == self.allowed:
            if package_name != self.package_name:  # otro empaque con los mismos servicios: el texto lo nombra
                self.package_name = package_name
                self._changed()
            return
        self.allowed, self.package_name = allowed, package_name
        current = self.key()
        self.fill(self.route, prefer=current if not allowed or current in allowed else allowed[0])

    def fill(self, route: ss.Route | None, prefer: str = "") -> None:
        """Rehace la lista para esa ruta y deja elegido `prefer` (o lo que estaba, si sigue en la lista)."""
        current = prefer or self.key()
        if self.allowed and current not in self.allowed:
            current = self.allowed[0]
        self.route = route
        self.combo.blockSignals(True)
        self.combo.clear()
        if not self.allowed:
            self.combo.addItem(tr("Automático · el que elija Shopify"), ss.AUTO)
        carrier = ""
        for service in ss.choices(route):
            if self.allowed and service.key not in self.allowed:
                continue
            if service.carrier != carrier:
                if self.combo.count():
                    self.combo.insertSeparator(self.combo.count())
                carrier = service.carrier
            self.combo.addItem(service.label, service.key)
        if self.with_fedex and not self.allowed and (route is None or route.fedex or self.keep_fedex
                                                     or current == ss.FEDEX):
            self.combo.insertSeparator(self.combo.count())
            self.combo.addItem(tr("FedEx · se compra en Shopify"), ss.FEDEX)
        chosen = ss.by_key(current)
        if self.combo.findData(current) < 0 and chosen is not None:
            # Lo que eligió la persona nunca cambia solo: se queda, avisando que Shopify no lo ofrece aquí.
            self.combo.insertSeparator(self.combo.count())
            self.combo.addItem(tr("{0} · no disponible para este envío", chosen.name), current)
        index = self.combo.findData(current)
        self.combo.setCurrentIndex(max(index, 0))
        self.combo.blockSignals(False)
        self._changed()

    def available(self) -> bool:
        """Si se puede comprar con lo elegido: Automático y FedEx siempre; un servicio, si Shopify lo ofrece aquí."""
        service = ss.by_key(self.key())
        return service is None or self.route is None or self.route.allows(service)

    def set_loading(self, loading: bool) -> None:
        self.loading = loading
        if loading:
            self.problem = ""
        self._changed()

    def set_problem(self, message: str) -> None:
        """Shopify no contestó qué servicios ofrece: se sigue con la lista completa y se dice por qué."""
        self.loading = False
        self.problem = message
        self._changed()

    def _changed(self) -> None:
        key = self.key()
        service = ss.by_key(key)
        if key == ss.FEDEX:
            text = tr("Shopify no deja que ninguna app compre FedEx: se compra en el pedido en Shopify (1 clic) y "
                      "LabelBridge la imprime sola en cuanto la ve.")
        elif self.allowed and service is not None and self.available():
            text = tr("{0} es un empaque oficial de USPS: solo vale con {1}. Si Shopify no lo ofrece para este envío, "
                      "avisa y no cobra nada.", self.package_name,
                      tr(" o ").join(ss.service_name(k) for k in self.allowed))
        elif service is not None and not self.available():
            text = tr("Shopify no ofrece {0} para este envío: elige otro servicio o «Automático».", service.name)
        elif service is not None:
            text = tr("Entrega habitual: {0}. Si Shopify no ofrece este servicio para el envío, avisa y no cobra nada.",
                      tr(service.days))
        else:
            text = tr("Shopify usa el servicio que pagó el cliente o tu paquetería preferida en Shopify.")
        if self.loading:
            text += " " + tr("Consultando qué ofrece Shopify para este envío…")
        elif self.problem:
            text += " " + tr("No se pudo preguntar a Shopify qué ofrece para este envío ({0}): se muestran todos.",
                             self.problem)
        elif self.route is not None and self.route.offered is None and self.route.destination:
            text += " " + tr("(Con el permiso read_shipping verías solo lo que Shopify ofrece para este envío.)")
        self.hint.setText(text)
        self.changed.emit()


class PurchaseDialog(QDialog):
    """«Imprimir guía» de un pedido sin guía (también el de una guía manual): servicio, caja, peso y aviso, y un
    clic para comprarla e imprimirla."""

    def __init__(self, parent: QWidget, ctx: AppContext, row: OrderRow, prefer: str | None = None) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.row = row
        self.bought = False
        self.fedex_requested = False  # eligió FedEx: quien abrió esta ventana abre la de «Guía FedEx»
        self.busy = False  # comprando: la ventana no se cierra hasta saber el resultado
        s = ctx.cfg.data.shopify
        self.setWindowTitle(tr("Imprimir guía · {0}", row.name))
        self.setMinimumWidth(620)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 22, 24, 20)
        layout.setSpacing(10)

        layout.addWidget(label(tr("Guía de envío de {0}", row.name), "CardTitle"))
        who = " · ".join(part for part in (row.customer, row.destination, row.method) if part)
        if row.subtotal:
            who += " · " + tr("{0} en piezas", f"US${row.subtotal:,.2f}")
        layout.addWidget(label(who, "Muted", wrap=True))
        self.problem_label = label(tr("Shopify no la compró: {0}.", row.problem), "Warning", wrap=True)
        self.problem_label.setVisible(bool(row.problem))
        layout.addWidget(self.problem_label)
        if not row.paid:
            layout.addWidget(label(tr("Ojo: este pedido todavía no está pagado."), "Warning", wrap=True))
        in_shopify = row.action == "buy"  # tus reglas: se compra en Shopify (FedEx u otra marcada así)
        fedex_rule = in_shopify and (row.carrier or row.rule_carrier) == "FedEx" and prefer is None
        if in_shopify:
            layout.addWidget(label(tr("Según tus reglas, este pedido sale por {0}.", row.carrier or row.rule_carrier),
                                   "Small", wrap=True))

        self.form_box = QWidget()
        form = QFormLayout(self.form_box)
        form.setContentsMargins(0, 6, 0, 0)
        form.setSpacing(10)
        self.services = ServicePicker(self)
        service_box = QVBoxLayout()
        service_box.setSpacing(4)
        service_box.addWidget(self.services.combo)
        service_box.addWidget(self.services.hint)
        form.addRow(tr("Servicio"), service_box)
        self.details_box = QWidget()
        details = QFormLayout(self.details_box)
        details.setContentsMargins(0, 0, 0, 0)
        details.setSpacing(10)
        try:  # lo que dicen tus reglas para este pedido (por el envío que pagó y el valor de las piezas)
            ruled = pk.for_order(s, row.method, row.subtotal, _domestic(row))
            rules_problem = ""
        except pk.PackagingError as exc:  # se dice arriba; se empieza por el paquete de Shopify
            ruled, rules_problem = pk.Packing(), str(exc)
        self.package = PackageForm(self, ctx, ruled.package)
        self.package.add_rows(details)
        self.notify = QCheckBox(tr("Avisar al cliente por correo con el número de seguimiento"))
        self.notify.setChecked(s.notify_customer)
        details.addRow(self.notify)
        form.addRow(self.details_box)
        layout.addWidget(self.form_box)
        self.services.changed.connect(self._update)
        self.package.changed.connect(self._update)

        printer = s.shipping_label_printer or tr("la impresora predeterminada")
        self.money_note = label(
            tr("Shopify cobra la guía en tu factura. Se imprime en «{0}» y el pedido queda preparado en Shopify con su "
               "número de seguimiento.", printer),
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
        self.buy_btn = button(tr("Comprar e imprimir"), "printer", "primary")
        self.buy_btn.clicked.connect(self._go)
        buttons.addWidget(self.buy_btn)
        layout.addLayout(buttons)

        if rules_problem:
            layout.insertWidget(2, label(tr("Tus reglas: {0}", rules_problem), "Warning", wrap=True))
        self.bought_service = ss.AUTO
        self.services.keep_fedex = fedex_rule
        self.services.fill(ss.Route(domestic=_domestic(row)),
                           prefer=ss.FEDEX if fedex_rule else (prefer or ruled.service or ss.AUTO))
        self._package_changed()
        self.package.changed.connect(self._package_changed)
        self.services.changed.connect(self._service_changed)
        self._update()
        self._load_route()

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

    def _load_route(self) -> None:
        """Qué servicios ofrece Shopify para este envío (sin esperar: la lista completa se puede usar mientras)."""
        if not self.ctx.cfg.data.shopify.shop.strip():
            return
        self.services.set_loading(True)

        def loaded(route: ss.Route) -> None:
            try:
                self.services.loading = False
                self.services.fill(route)
            except RuntimeError as exc:  # la ventana se cerró antes
                if not window_closed(exc):
                    raise
                return

        def failed(message: str) -> None:
            try:
                self.services.set_problem(message)
            except RuntimeError as exc:  # la ventana se cerró antes
                if not window_closed(exc):
                    raise
                return

        run_async(lambda: self.ctx.services.shopify.shipping_route(self.row.legacy), loaded, failed)

    def _package_changed(self) -> None:
        """Un empaque oficial de USPS deja elegir solo sus servicios (Priority Mail…)."""
        if self.services.key() == ss.FEDEX:
            return  # FedEx se compra en Shopify: el empaque se elige allá
        self.services.set_allowed(self.package.allowed(),
                                  pk.name(self.ctx.cfg.data.shopify, self.package.key()))

    def _service_changed(self) -> None:
        """Al dejar FedEx (que no limita nada), el empaque elegido vuelve a limitar los servicios."""
        if self.services.key() != ss.FEDEX and self.services.allowed != self.package.allowed():
            self._package_changed()

    def _update(self) -> None:
        fedex = self.services.key() == ss.FEDEX
        usable = not self.bought
        self.form_box.setVisible(usable)
        self.details_box.setVisible(usable and not fedex)
        self.money_note.setVisible(usable and not fedex)
        self.buy_btn.setVisible(usable)
        self.buy_btn.setEnabled(usable and not self.busy and self.services.available())
        self.buy_btn.setText(tr("Comprar en Shopify") if fedex else tr("Comprar e imprimir"))
        self.buy_btn.setIcon(icons.icon("link" if fedex else "printer", Color.ON_PRIMARY, 16))
        if self.isVisible():
            QTimer.singleShot(0, self._fit)

    def options(self) -> dict:
        """Lo elegido, como lo pide Shopify."""
        package, weight = self.package.options()
        return {"package": package, "total_weight": weight, "notify": self.notify.isChecked(),
                "service": self.services.key()}

    # --- acciones ------------------------------------------------------------------------------
    def _go(self) -> None:
        if self.services.key() == ss.FEDEX:  # se compra en Shopify, guiado por la ventana «Guía FedEx»
            self.fedex_requested = True
            self.accept()
        else:
            self._buy()

    def _buy(self) -> None:
        problem = self.package.problem()
        if problem:
            self._show(problem, "Warning")
            return
        self.package.remember_box()
        chosen = self.options()
        self.bought_service = chosen["service"]  # para explicar el resultado con lo que se compró de verdad
        self.busy = True
        self.cancel_btn.setEnabled(False)
        self.form_box.setEnabled(False)
        self.progress.setVisible(True)
        self._update()
        self._show(tr("Comprando la guía con Shopify… (tarda unos segundos)"), "")
        service = self.ctx.services.shopify
        run_async(
            lambda: service.buy_label_now(self.row.legacy, package=chosen["package"],
                                          total_weight=chosen["total_weight"], notify=chosen["notify"],
                                          service=chosen["service"]),
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
            what = " ".join(part for part in (result.get("carrier"), result.get("tracking")) if part)
            if result.get("cost"):
                what += " · " + tr("US${0:,.2f}", float(result["cost"]))
            self._show(tr("✓ Guía comprada: {0}. Se está imprimiendo y el pedido quedó preparado en Shopify.",
                          what or tr("lista")), "Hint")
            self.cancel_btn.setText(tr("Cerrar"))
            self.problem_label.setVisible(False)
            self.track_btn.setVisible(bool(result.get("tracking")))
            self._update()
            return
        self.form_box.setEnabled(True)
        if state == "uncertain":
            self.bought = True  # no se puede volver a comprar desde aquí: hay que mirarlo en Shopify
            self._show(tr("No se pudo confirmar si Shopify compró la guía: revisa el pedido en Shopify antes de comprar otra."),
                       "Warning")
            self.cancel_btn.setText(tr("Cerrar"))
        elif state == "failed":
            self._show(tr("Shopify no compró la guía: {0}.",
                          ss.rejection(result.get("reason") or "", self.bought_service).rstrip(".")), "Warning")
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
        self._show(message, "Warning")
        self._update()

    def _show(self, text: str, style: str) -> None:
        for name, status in self.statuses.items():
            status.setText(text if name == style else "")
            status.setVisible(bool(text) and name == style)
        if self.isVisible():
            QTimer.singleShot(0, self._fit)

    def _open_in_shopify(self) -> None:
        QDesktopServices.openUrl(QUrl(admin_url(self.ctx.cfg.data.shopify.shop, f"orders/{self.row.legacy}")))

    def _track(self) -> None:
        """El seguimiento de la guía recién comprada, en una ventana de LabelBridge (no en la web)."""
        from .tracking_dialog import open_tracking

        open_tracking(self, self.ctx, self.row)


def _domestic(row: OrderRow) -> bool:
    """Mientras Shopify contesta de dónde sale: como si saliera de EE. UU."""
    return ss.is_domestic("US", row.country)

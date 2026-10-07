"""«Por dónde sale cada cosa» (Shopify): la impresora de la guía, la de la etiqueta del pedido y la de las guías de un
metafield, una prueba para cada una y un aviso si la etiqueta del pedido sale por la misma impresora que la guía."""

from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtWidgets import QComboBox, QGridLayout, QHBoxLayout

from ..i18n import tr
from ..printing import routing
from ..printing.engine import resolve_printer
from .context import AppContext
from .widgets import Card, button, error, label, run_async

log = logging.getLogger(__name__)


def printer_combo(ctx: AppContext, current: str) -> QComboBox:
    """Las impresoras de LabelBridge, con la predeterminada por su nombre (así se ve por cuál sale de verdad)."""
    combo = QComboBox()
    fill_printers(combo, ctx, current)
    return combo


def fill_printers(combo: QComboBox, ctx: AppContext, current: str) -> None:
    default = ctx.cfg.default_printer()
    combo.blockSignals(True)
    combo.clear()
    combo.addItem(tr("Predeterminada ({0})", default.display_name()) if default
                  else tr("Predeterminada (no hay ninguna)"), "")
    for profile in ctx.cfg.data.printers:
        combo.addItem(profile.alias, profile.alias)
    if current and combo.findData(current) < 0:  # un alias que ya no está en la lista: se muestra tal cual
        combo.addItem(current, current)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    combo.blockSignals(False)


class PrintRoutesCard(Card):
    """Una fila por documento: qué es, por qué impresora sale y «Imprimir prueba» (por esa misma impresora)."""

    def __init__(self, ctx: AppContext, navigate: Callable[[str], None] | None = None) -> None:
        super().__init__(
            tr("Por dónde sale cada cosa"),
            tr("Cada documento puede salir por una impresora distinta: por ejemplo, la guía en la impresora de "
               "etiquetas y la etiqueta del pedido en la de la oficina."),
        )
        self.ctx = ctx
        self.order_label_on = ctx.cfg.data.shopify.print_order_label
        self.combos: dict[str, QComboBox] = {}
        self.tests = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        for row, route in enumerate(routing.routes(ctx.cfg)):
            grid.addWidget(label(tr(route.title)), row, 0)
            combo = printer_combo(ctx, route.alias)
            combo.currentIndexChanged.connect(lambda _i: self.update_notes())
            grid.addWidget(combo, row, 1)
            test = button(tr("Imprimir prueba"), "printer", tooltip=tr("Imprime la etiqueta de ejemplo por esta impresora"))
            test.clicked.connect(lambda _=False, key=route.key: self.test(key))
            grid.addWidget(test, row, 2)
            self.combos[route.key] = combo
            self.tests[route.key] = test
        grid.setColumnStretch(1, 1)
        self.add(layout=grid)

        # Avisos: misma impresora para la guía y la etiqueta del pedido; un alias que no está en Impresoras.
        self.clash = label("", "Warning", wrap=True)
        self.clash.setVisible(False)
        self.add(self.clash)
        self.add_printer = button(tr("Agregar impresora"), "plus")
        self.add_printer.setVisible(False)
        if navigate is not None:
            self.add_printer.clicked.connect(lambda: navigate("printers"))
        actions = QHBoxLayout()
        actions.addWidget(self.add_printer)
        actions.addStretch(1)
        self.add(layout=actions)
        self.unknown = label("", "Hint", wrap=True)
        self.unknown.setVisible(False)
        self.add(self.unknown)
        self.sent = label("", "Small", wrap=True)
        self.sent.setVisible(False)
        self.add(self.sent)
        self.update_notes()

    # --- lo elegido ---------------------------------------------------------------------------------
    def alias(self, key: str) -> str:
        return self.combos[key].currentData() or ""

    def set_order_label_active(self, on: bool) -> None:
        self.order_label_on = on
        self.update_notes()

    def load(self) -> None:
        """Lo guardado (después de que el asistente cambió la configuración) y la lista de impresoras al día."""
        for route in routing.routes(self.ctx.cfg):
            fill_printers(self.combos[route.key], self.ctx, route.alias)
        self.order_label_on = self.ctx.cfg.data.shopify.print_order_label
        self.update_notes()

    def reload_printers(self) -> None:
        """Cambió la lista de impresoras: las mismas elecciones, con la lista nueva."""
        for combo in self.combos.values():
            fill_printers(combo, self.ctx, combo.currentData() or "")
        self.update_notes()

    # --- avisos ---------------------------------------------------------------------------------------
    def update_notes(self) -> None:
        cfg = self.ctx.cfg
        shared = (routing.shared_printer(self.alias(routing.ORDER), self.alias(routing.SHIPPING), cfg)
                  if self.order_label_on else None)
        if shared is not None:
            others = [p for p in cfg.data.printers
                      if p.enabled and routing.device(p) != routing.device(shared)]
            text = tr("La etiqueta del pedido y la guía salen por la misma impresora («{0}»).", shared.display_name())
            text += " " + (tr("Elige otra para la etiqueta del pedido.") if others else
                           tr("Para que salgan por separado, agrega la otra impresora en Impresoras y elígela aquí."))
            self.clash.setText(text)
        self.clash.setVisible(shared is not None)
        self.add_printer.setVisible(shared is not None and not any(
            p.enabled and routing.device(p) != routing.device(shared) for p in cfg.data.printers))
        missing = sorted({alias for alias in (self.alias(key) for key in self.combos)
                          if alias and routing.printer_for(alias, cfg) is None})
        if missing:
            self.unknown.setText(tr("«{0}» no está en Impresoras (o está desactivada): LabelBridge la buscará entre "
                                    "las instaladas en el equipo al imprimir.", "», «".join(missing)))
        self.unknown.setVisible(bool(missing))

    # --- prueba ---------------------------------------------------------------------------------------
    def test(self, key: str) -> None:
        """La etiqueta de ejemplo, directo por la impresora de esa fila (sin pasar por la cola ni la pausa)."""
        alias = self.alias(key)
        button_ = self.tests[key]
        button_.setEnabled(False)

        def work() -> str:
            profile = resolve_printer(alias, self.ctx.cfg)
            self.ctx.engine.print_test(profile)
            return profile.display_name()

        def done(name: str) -> None:
            button_.setEnabled(True)
            self.sent.setText(tr("Prueba enviada a «{0}».", name))
            self.sent.setVisible(True)

        def failed(message: str) -> None:
            button_.setEnabled(True)
            self.sent.setVisible(False)
            error(self, tr("No se pudo imprimir la prueba"), message)

        run_async(work, done, failed)

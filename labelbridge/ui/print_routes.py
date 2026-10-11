"""«Por dónde sale cada cosa» (Shopify): la impresora de la guía, la de la etiqueta del pedido, la de la página del
pedido y la de las guías de un metafield, una prueba para cada una y avisos si la etiqueta o la página del pedido salen
por la misma impresora que la guía (la página, además, solo sale por una impresora elegida)."""

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


def printer_combo(ctx: AppContext, current: str, choose: bool = False) -> QComboBox:
    """Las impresoras de LabelBridge, con la predeterminada por su nombre (así se ve por cuál sale de verdad).
    `choose`: sin «Predeterminada» (la página del pedido sale solo por una impresora elegida)."""
    combo = QComboBox()
    combo.setProperty("choose", choose)
    fill_printers(combo, ctx, current)
    return combo


def fill_printers(combo: QComboBox, ctx: AppContext, current: str) -> None:
    default = ctx.cfg.default_printer()
    combo.blockSignals(True)
    combo.clear()
    if combo.property("choose"):
        combo.addItem(tr("Elige una impresora…"), "")
    else:
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
        self.page_on = ctx.cfg.data.shopify.print_order_page
        self.combos: dict[str, QComboBox] = {}
        self.tests = {}
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(10)
        for row, route in enumerate(routing.routes(ctx.cfg)):
            grid.addWidget(label(tr(route.title)), row, 0)
            combo = printer_combo(ctx, route.alias, choose=route.key == routing.PAGE)
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
        self.page_note = label("", "Warning", wrap=True)  # la página del pedido sin impresora o por la de las guías
        self.page_note.setVisible(False)
        self.add(self.page_note)
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

    def set_page_active(self, on: bool) -> None:
        self.page_on = on
        self.update_notes()

    def load(self) -> None:
        """Lo guardado (después de que el asistente cambió la configuración) y la lista de impresoras al día."""
        for route in routing.routes(self.ctx.cfg):
            fill_printers(self.combos[route.key], self.ctx, route.alias)
        self.order_label_on = self.ctx.cfg.data.shopify.print_order_label
        self.page_on = self.ctx.cfg.data.shopify.print_order_page
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
        # La página del pedido, con lo que está elegido en pantalla (la guía, también la de pantalla).
        page_alias = self.alias(routing.PAGE)
        page = ""
        profile = routing.printer_for(page_alias, cfg) if page_alias else None
        if self.page_on and not page_alias:
            page = tr("Elige la impresora de la página del pedido: no sale por la predeterminada a ciegas.")
        elif self.page_on and routing.shared_printer(page_alias, self.alias(routing.SHIPPING), cfg) is not None:
            page = tr("La página del pedido saldría por la impresora de las guías y no se imprime: elige otra.")
        elif self.page_on and profile is not None and routing.label_like(profile):
            page = tr("Esa impresora es de etiquetas y la página del pedido es una hoja carta: elige una de hojas.")
        self.page_note.setText(page)
        self.page_note.setVisible(bool(page))

    # --- prueba ---------------------------------------------------------------------------------------
    def test(self, key: str) -> None:
        """La etiqueta de ejemplo, directo por la impresora de esa fila (sin pasar por la cola ni la pausa)."""
        alias = self.alias(key)
        if key == routing.PAGE:  # la página: nunca a ciegas, ni por la de las guías, ni por una de etiquetas
            from ..connectors.shopify import page_problem_text

            problem = routing.page_problem(self.ctx.cfg, alias=alias, on=True)
            if not problem and routing.shared_printer(alias, self.alias(routing.SHIPPING), self.ctx.cfg) is not None:
                problem = "shipping"  # también contra la de la guía elegida en pantalla (sin guardar todavía)
            if problem:
                error(self, tr("Imprimir prueba"), page_problem_text(problem))
                return
        button_ = self.tests[key]
        button_.setEnabled(False)

        def work() -> str:
            profile = resolve_printer(alias, self.ctx.cfg)
            if key == routing.PAGE:
                self.ctx.engine.print_test_page(profile)
            else:
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

"""Impresoras: las configuradas en LabelBridge, las instaladas en el PC y las encontradas en la red."""

from __future__ import annotations

import threading

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QMenu,
    QProgressBar,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ...config import LANGUAGES, MEDIA_PRESETS, PRINTER_KINDS, PrinterProfile
from ...discovery import DiscoveredPrinter, discover
from ...i18n import tr
from ...printing.hints import looks_like_label_printer, looks_like_zpl, suggest_alias
from ...printing.outputs import list_system_printers
from ...system import windows_printers
from .. import icons
from ..context import AppContext
from ..printer_dialog import PrinterDialog
from ..widgets import Card, Page, button, confirm, error, info, label, run_async


def _table(headers: list[str], stretch_col: int) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
    table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
    table.setShowGrid(False)
    table.setAlternatingRowColors(True)
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(stretch_col, QHeaderView.ResizeMode.Stretch)
    table.setMinimumHeight(200)
    return table


class PrintersPage(Page):
    def __init__(self, ctx: AppContext) -> None:
        super().__init__(
            tr("Impresoras"),
            tr("Conecta impresoras instaladas en Windows o directamente por IP (Wi‑Fi / red local), sin driver."),
        )
        self.ctx = ctx
        self._discovered: list[DiscoveredPrinter] = []
        self._system: list[dict] = []
        self._scan_cancel = threading.Event()

        # --- configuradas ------------------------------------------------------------------
        mine = Card(
            tr("Impresoras de LabelBridge"),
            tr("Los trabajos indican la impresora por su alias (p. ej. «etiquetas»). Sin alias se usa la predeterminada ★."),
        )
        self.table = _table(["", tr("Alias"), tr("Tipo"), tr("Destino"), tr("Lenguaje"), tr("Tamaño"), tr("Estado")], 3)
        self.table.doubleClicked.connect(lambda _i: self._edit())
        mine.add(self.table)
        actions = QHBoxLayout()
        add = button(tr("Agregar"), "plus", "primary")
        self.add_btn = add
        menu = QMenu(add)
        menu.addAction(icons.icon("printer"), tr("Impresora instalada en Windows…"), lambda: self._new("system"))
        menu.addAction(icons.icon("wifi"), tr("Impresora de red por IP…"), lambda: self._new("network"))
        menu.addAction(icons.icon("folder"), tr("Guardar como archivo (pruebas)…"), lambda: self._new("file"))
        add.setMenu(menu)
        actions.addWidget(add)
        for text, icon_name, slot, variant in (
            (tr("Editar"), "edit", self._edit, None),
            (tr("Predeterminada"), "star", self._make_default, None),
            (tr("Imprimir prueba"), "printer", self._test, None),
            (tr("Eliminar"), "trash", self._delete, "danger"),
        ):
            btn = button(text, icon_name, variant)
            btn.clicked.connect(slot)
            actions.addWidget(btn)
        actions.addStretch(1)
        mine.add(layout=actions)
        self.add(mine)

        # --- detección -----------------------------------------------------------------------
        detect = Card(tr("Detectar impresoras"))
        tabs = QTabWidget()
        self.tabs = tabs
        detect.add(tabs)

        installed = QWidget()
        installed_layout = QVBoxLayout(installed)
        installed_layout.setContentsMargins(0, 10, 0, 0)
        self.sys_table = _table([tr("Nombre"), tr("Modelo / driver"), tr("Puerto"), tr("Predeterminada")], 0)
        installed_layout.addWidget(self.sys_table)
        row = QHBoxLayout()
        refresh = button(tr("Actualizar"), "refresh")
        refresh.clicked.connect(self._load_system)
        add_sys = button(tr("Agregar a LabelBridge"), "plus", "primary")
        self.add_sys_btn = add_sys
        add_sys.clicked.connect(self._add_system)
        row.addWidget(refresh)
        row.addWidget(add_sys)
        row.addStretch(1)
        installed_layout.addLayout(row)
        tabs.addTab(installed, icons.icon("printer"), tr("Instaladas en este equipo"))

        network = QWidget()
        net_layout = QVBoxLayout(network)
        net_layout.setContentsMargins(0, 10, 0, 0)
        net_layout.addWidget(
            label(
                tr("Busca impresoras en tu Wi‑Fi o red local (puertos 9100, 631 y 515, y Bonjour/mDNS). "
                   "No envía nada a las impresoras durante la búsqueda."),
                "Muted",
                wrap=True,
            )
        )
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        net_layout.addWidget(self.progress)
        self.net_table = _table(["IP", tr("Nombre / modelo"), tr("Protocolos"), tr("Sugerido")], 1)
        net_layout.addWidget(self.net_table)
        row = QHBoxLayout()
        self.scan_btn = button(tr("Buscar en la red"), "search", "primary")
        self.scan_btn.clicked.connect(self._scan)
        add_net = button(tr("Agregar a LabelBridge"), "plus")
        add_net.clicked.connect(self._add_network)
        row.addWidget(self.scan_btn)
        row.addWidget(add_net)
        if windows_printers.supported():
            install = button(tr("Instalar en Windows…"), "download", tooltip=tr("Crea el puerto TCP/IP y la impresora en Windows"))
            install.clicked.connect(self._install_windows)
            row.addWidget(install)
        row.addStretch(1)
        net_layout.addLayout(row)
        tabs.addTab(network, icons.icon("wifi"), tr("En la red (Wi‑Fi / LAN)"))
        self.add(detect)
        self.finish()

        ctx.bridge.config_saved.connect(lambda section: section in ("printers", "general", "all") and self.refresh())
        ctx.bridge.event.connect(self._on_event)
        self.refresh()
        self._load_system()
        if ctx.services.network.results:
            self._show_network(ctx.services.network.results, silent=True)

    # --- lista configurada ----------------------------------------------------------------
    def refresh(self) -> None:
        cfg = self.ctx.cfg
        default = cfg.default_printer()
        self.table.setRowCount(len(cfg.data.printers))
        for row, p in enumerate(cfg.data.printers):
            star = QTableWidgetItem()
            if default and p.id == default.id:
                star.setIcon(icons.icon("star", "#F59E0B", 16))
                star.setToolTip(tr("Predeterminada"))
            self.table.setItem(row, 0, star)
            values = [
                p.alias,
                tr(PRINTER_KINDS.get(p.kind, p.kind)),
                p.target_text(),
                ("ZPL" if p.language == "zpl" else "PDF") if p.kind == "file" else tr(LANGUAGES.get(p.language, p.language)).split(" (")[0],
                tr(MEDIA_PRESETS.get(p.media, ("?",))[0]).split(" (")[0].split(" ·")[0],
                tr("Activa") if p.enabled else tr("Desactivada"),
            ]
            for col, value in enumerate(values, start=1):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, p.id)
                self.table.setItem(row, col, item)
            self.table.setRowHeight(row, 36)

    def _selected(self) -> PrinterProfile | None:
        row = self.table.currentRow()
        if row < 0 or row >= len(self.ctx.cfg.data.printers):
            return None
        return self.ctx.cfg.data.printers[row]

    def _open_dialog(self, profile: PrinterProfile, is_new: bool) -> None:
        dialog = PrinterDialog(self, self.ctx, profile, is_new)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        result: PrinterProfile = dialog.result_profile
        printers = self.ctx.cfg.data.printers
        if is_new:
            printers.append(result)
            if len([p for p in printers if p.enabled]) == 1 or not self.ctx.cfg.data.general.default_printer_id:
                self.ctx.cfg.data.general.default_printer_id = result.id
        else:
            for index, existing in enumerate(printers):
                if existing.id == result.id:
                    printers[index] = result
        self.ctx.cfg.save("printers")
        self.refresh()
        self.ctx.engine.wake()

    def _new(self, kind: str, **values) -> None:
        aliases = [p.alias for p in self.ctx.cfg.data.printers]
        defaults = {"system": "driver", "network": "zpl", "file": "driver"}
        profile = PrinterProfile(kind=kind, language=defaults[kind], media="4x6")
        if kind == "file":
            profile.alias = suggest_alias("archivo-pdf", aliases)
        for key, value in values.items():
            setattr(profile, key, value)
        if not profile.alias:
            profile.alias = suggest_alias(tr("etiquetas") if not aliases else (profile.system_name or profile.host or kind), aliases)
        self._open_dialog(profile, True)

    def _edit(self) -> None:
        profile = self._selected()
        if profile is None:
            info(self, tr("Impresoras"), tr("Selecciona una impresora de la lista."))
            return
        self._open_dialog(profile, False)

    def _make_default(self) -> None:
        profile = self._selected()
        if profile is None:
            return
        self.ctx.cfg.data.general.default_printer_id = profile.id
        self.ctx.cfg.save("general")
        self.refresh()

    def _delete(self) -> None:
        profile = self._selected()
        if profile is None:
            return
        if not confirm(self, tr("Eliminar impresora"), tr("¿Eliminar «{0}» de LabelBridge?", profile.alias)):
            return
        self.ctx.cfg.data.printers = [p for p in self.ctx.cfg.data.printers if p.id != profile.id]
        if self.ctx.cfg.data.general.default_printer_id == profile.id:
            remaining = self.ctx.cfg.data.printers
            self.ctx.cfg.data.general.default_printer_id = remaining[0].id if remaining else ""
        self.ctx.cfg.save("printers")
        self.refresh()

    def _test(self) -> None:
        profile = self._selected()
        if profile is None:
            info(self, tr("Impresoras"), tr("Selecciona una impresora de la lista."))
            return
        run_async(
            lambda: self.ctx.engine.print_test(profile),
            lambda detail: info(self, tr("Prueba enviada"), tr("Etiqueta de prueba enviada a «{0}» ({1}).", profile.alias, detail)),
            lambda message: error(self, tr("No se pudo imprimir"), message),
        )

    # --- instaladas en el sistema ---------------------------------------------------------------
    def _load_system(self) -> None:
        run_async(list_system_printers, self._show_system, lambda m: error(self, tr("Impresoras"), m))

    def _show_system(self, printers: list[dict]) -> None:
        self._system = printers
        self.sys_table.setRowCount(len(printers))
        for row, p in enumerate(printers):
            for col, value in enumerate(
                [p["name"], p["model"] or p["description"], p["port"] or p["location"], tr("Sí") if p["is_default"] else ""]
            ):
                self.sys_table.setItem(row, col, QTableWidgetItem(value or ""))
            self.sys_table.setRowHeight(row, 34)

    def _add_system(self) -> None:
        row = self.sys_table.currentRow()
        if row < 0 or row >= len(self._system):
            info(self, tr("Impresoras"), tr("Selecciona una impresora instalada."))
            return
        p = self._system[row]
        text = f"{p['name']} {p['model']} {p['driver']}"
        self._new(
            "system",
            system_name=p["name"],
            language="zpl" if looks_like_zpl(text) else "driver",
            media="4x6" if looks_like_label_printer(text) else "auto",
            dpi=300 if "300" in text else 203,
        )

    # --- red ---------------------------------------------------------------------------------------
    def _on_event(self, kind: str, data) -> None:
        if kind == "discovery" and isinstance(data, list) and self.scan_btn.isEnabled():
            self._show_network(data, silent=True)

    def _scan(self) -> None:
        self.scan_btn.setEnabled(False)
        self.scan_btn.setText(tr("Buscando…"))
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self._scan_cancel = threading.Event()
        cancel = self._scan_cancel
        run_async(lambda: discover(cancel), self._scanned, self._scan_failed)

    def _scanned(self, printers: list[DiscoveredPrinter]) -> None:
        self.ctx.services.network.results = printers
        self._show_network(printers)

    def _scan_failed(self, message: str) -> None:
        self._scan_done()
        error(self, tr("Búsqueda en la red"), message)

    def _scan_done(self) -> None:
        self.scan_btn.setEnabled(True)
        self.scan_btn.setText(tr("Buscar en la red"))
        self.progress.setVisible(False)

    def _show_network(self, printers: list[DiscoveredPrinter], silent: bool = False) -> None:
        if not silent:
            self._scan_done()
        self._discovered = printers
        self.net_table.setRowCount(len(printers))
        for row, p in enumerate(printers):
            s = p.suggested()
            suggestion = f"{'IPP' if s['protocol'] == 'ipp' else 'RAW 9100'} · {s['language'].upper()}"
            for col, value in enumerate([p.ip, p.title if p.model in p.title else f"{p.title} · {p.model}".strip(" ·"), ", ".join(p.protocols), suggestion]):
                self.net_table.setItem(row, col, QTableWidgetItem(value))
            self.net_table.setRowHeight(row, 34)
        if not printers and not silent:
            info(
                self,
                tr("Búsqueda en la red"),
                tr("No se encontraron impresoras. Verifica que estén encendidas y en la misma red que este PC."),
            )

    def _selected_network(self) -> DiscoveredPrinter | None:
        row = self.net_table.currentRow()
        if row < 0 or row >= len(self._discovered):
            info(self, tr("Impresoras"), tr("Selecciona una impresora encontrada."))
            return None
        return self._discovered[row]

    def _add_network(self) -> None:
        p = self._selected_network()
        if p is None:
            return
        s = p.suggested()
        aliases = [x.alias for x in self.ctx.cfg.data.printers]
        self._new(
            "network",
            alias=suggest_alias(p.name or p.model or p.ip, aliases) if aliases else tr("etiquetas"),
            host=p.ip,
            port=s["port"],
            protocol=s["protocol"],
            language=s["language"],
            ipp_path=s.get("ipp_path", "/ipp/print"),
            media="4x6" if p.is_zpl or looks_like_label_printer(p.title + p.model) else "auto",
        )

    def _install_windows(self) -> None:
        p = self._selected_network()
        if p is None:
            return
        drivers = windows_printers.list_drivers()
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Instalar en Windows"))
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(tr("Impresora en {0}. Elige el driver (debe estar instalado en Windows):", p.ip), wrap=True))
        combo = QComboBox()
        combo.addItems(drivers)
        preferred = next((d for d in drivers if "zdesigner" in d.lower()), None) if p.is_zpl else None
        preferred = preferred or ("Generic / Text Only" if p.is_zpl else "Microsoft IPP Class Driver")
        combo.setCurrentText(preferred)
        layout.addWidget(combo)
        layout.addWidget(
            label(tr("Windows pedirá permiso de administrador. Para Zebra usa el driver ZDesigner (o «Generic / Text Only» solo para ZPL)."), "Muted", wrap=True)
        )
        row = QHBoxLayout()
        row.addStretch(1)
        ok = button(tr("Instalar"), "download", "primary")
        ok.clicked.connect(dialog.accept)
        cancel = button(tr("Cancelar"))
        cancel.clicked.connect(dialog.reject)
        row.addWidget(cancel)
        row.addWidget(ok)
        layout.addLayout(row)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name = p.name or p.model or tr("Impresora {0}", p.ip)
        driver = combo.currentText()
        port = 9100 if 9100 in p.ports else 9100
        run_async(
            lambda: windows_printers.install_network_printer(name, p.ip, driver, port),
            lambda _r: (info(self, tr("Listo"), tr("«{0}» quedó instalada en Windows.", name)), self._load_system()),
            lambda m: error(self, tr("No se pudo instalar"), m),
        )

"""Diálogo para crear/editar una impresora de LabelBridge."""

from __future__ import annotations

import copy

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..config import MEDIA_PRESETS, PRINTER_KINDS, PrinterProfile
from ..i18n import tr
from .context import AppContext
from .widgets import button, error, info, label, run_async


class PrinterDialog(QDialog):
    def __init__(self, parent: QWidget, ctx: AppContext, profile: PrinterProfile, is_new: bool) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.original = profile
        self.is_new = is_new
        self.setWindowTitle(tr("Nueva impresora") if is_new else tr("Editar «{0}»", profile.alias))
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 20)
        layout.setSpacing(14)
        layout.addWidget(label(tr("Tipo: {0}", tr(PRINTER_KINDS.get(profile.kind, profile.kind))), "SectionTitle"))

        form = QFormLayout()
        form.setSpacing(10)
        self.alias = QLineEdit(profile.alias)
        self.alias.setPlaceholderText(tr("etiquetas"))
        form.addRow(tr("Alias (nombre corto)"), self.alias)

        self.system_name = QComboBox()
        self.host = QLineEdit(profile.host)
        self.port = QSpinBox()
        self.protocol = QComboBox()
        self.ipp_path = QLineEdit(profile.ipp_path)
        self.output_dir = QLineEdit(profile.output_dir)

        if profile.kind == "system":
            self.system_name.setEditable(True)
            names = []
            try:
                from ..printing.outputs import list_system_printers

                names = [p["name"] for p in list_system_printers()]
            except Exception:
                pass
            if profile.system_name and profile.system_name not in names:
                names.insert(0, profile.system_name)
            self.system_name.addItems(names)
            self.system_name.setCurrentText(profile.system_name)
            form.addRow(tr("Impresora de Windows"), self.system_name)
        elif profile.kind == "network":
            self.host.setPlaceholderText("192.168.1.50")
            form.addRow(tr("IP o nombre"), self.host)
            self.protocol.addItem(tr("RAW / JetDirect (puerto 9100)"), "raw")
            self.protocol.addItem(tr("IPP (puerto 631)"), "ipp")
            self.protocol.addItem(tr("IPP seguro (IPPS)"), "ipps")
            self.protocol.setCurrentIndex(max(0, self.protocol.findData(profile.protocol)))
            form.addRow(tr("Conexión"), self.protocol)
            self.port.setRange(1, 65535)
            self.port.setValue(profile.port)
            form.addRow(tr("Puerto"), self.port)
            form.addRow(tr("Ruta IPP"), self.ipp_path)
            self.protocol.currentIndexChanged.connect(self._protocol_changed)
        else:
            row = QHBoxLayout()
            row.addWidget(self.output_dir, 1)
            browse = button(tr("Elegir…"), "folder")
            browse.clicked.connect(self._browse)
            row.addWidget(browse)
            holder = QWidget()
            holder.setLayout(row)
            row.setContentsMargins(0, 0, 0, 0)
            self.output_dir.setPlaceholderText(tr("Carpeta «salida» de LabelBridge"))
            form.addRow(tr("Carpeta"), holder)

        self.language = QComboBox()
        form.addRow(tr("Lenguaje"), self.language)
        self.media = QComboBox()
        for key, (name, _, _) in MEDIA_PRESETS.items():
            self.media.addItem(tr(name), key)
        self.media.setCurrentIndex(max(0, self.media.findData(profile.media)))
        form.addRow(tr("Tamaño de etiqueta"), self.media)
        self.dpi = QComboBox()
        for dpi in (203, 300, 600):
            self.dpi.addItem(f"{dpi} dpi", dpi)
        self.dpi.setCurrentIndex(max(0, self.dpi.findData(profile.dpi)))
        form.addRow(tr("Resolución"), self.dpi)
        self.rotate = QComboBox()
        for value, text in (("auto", tr("Automática")), ("0", "0°"), ("90", "90°"), ("180", "180°"), ("270", "270°")):
            self.rotate.addItem(text, value)
        self.rotate.setCurrentIndex(max(0, self.rotate.findData(profile.rotate)))
        form.addRow(tr("Rotación"), self.rotate)
        self.crop = QCheckBox(tr("Recortar márgenes blancos de PDFs tamaño carta/A4"))
        self.crop.setChecked(profile.crop == "auto")
        form.addRow("", self.crop)
        self.enabled = QCheckBox(tr("Activa"))
        self.enabled.setChecked(profile.enabled)
        form.addRow("", self.enabled)
        self.retrust = QCheckBox(tr("Volver a confiar en el certificado de la impresora (si lo cambiaste)"))
        self.retrust.setVisible(profile.kind == "network" and bool(profile.tls_fingerprint))
        form.addRow("", self.retrust)
        layout.addLayout(form)

        self.hint = label("", "Hint", wrap=True)
        layout.addWidget(self.hint)
        self._fill_languages()
        self.language.currentIndexChanged.connect(self._update_hint)
        self._update_hint()

        buttons = QHBoxLayout()
        self.test_btn = button(tr("Imprimir prueba"), "printer")
        self.test_btn.clicked.connect(self._test)
        buttons.addWidget(self.test_btn)
        buttons.addStretch(1)
        cancel = button(tr("Cancelar"))
        cancel.clicked.connect(self.reject)
        save = button(tr("Guardar"), "check", "primary")
        self.save_btn = save
        save.clicked.connect(self._save)
        buttons.addWidget(cancel)
        buttons.addWidget(save)
        layout.addLayout(buttons)

    # --- formulario -----------------------------------------------------------------------
    def _fill_languages(self) -> None:
        kind = self.original.kind
        protocol = self.protocol.currentData() if kind == "network" else ""
        self.language.blockSignals(True)
        self.language.clear()
        if kind == "system":
            self.language.addItem(tr("Driver de Windows (PDF, imágenes, etiquetas)"), "driver")
            self.language.addItem(tr("ZPL directo (Zebra y compatibles, sin pasar por el driver)"), "zpl")
        elif kind == "network" and protocol in ("ipp", "ipps"):
            self.language.addItem(tr("PDF (IPP Everywhere / AirPrint)"), "pdf")
        elif kind == "network":
            self.language.addItem(tr("ZPL (Zebra y compatibles)"), "zpl")
            self.language.addItem(tr("PDF directo (impresoras que aceptan PDF en el 9100)"), "pdf")
        else:
            self.language.addItem(tr("PDF"), "driver")
            self.language.addItem(tr("ZPL"), "zpl")
        index = self.language.findData(self.original.language)
        self.language.setCurrentIndex(max(0, index))
        self.language.blockSignals(False)

    def _protocol_changed(self) -> None:
        protocol = self.protocol.currentData()
        if protocol == "raw" and self.port.value() in (631, 443):
            self.port.setValue(9100)
        elif protocol in ("ipp", "ipps") and self.port.value() == 9100:
            self.port.setValue(631)
        self.ipp_path.setEnabled(protocol != "raw")
        self._fill_languages()
        self._update_hint()

    def _update_hint(self) -> None:
        lang = self.language.currentData()
        kind = self.original.kind
        if kind == "network" and lang == "zpl":
            text = tr("Los PDF e imágenes se convierten a ZPL en este PC: no necesitas instalar driver.")
        elif kind == "network":
            text = tr("Se envía PDF directamente a la impresora (sin driver).")
        elif kind == "system" and lang == "zpl":
            text = tr("Ideal para Zebra con driver ZDesigner: los PDF se convierten a ZPL y se envían en modo RAW.")
        elif kind == "system":
            text = tr("Imprime a través del driver de Windows. Ajusta el tamaño de etiqueta igual que en el driver.")
        else:
            text = tr("Modo de pruebas: guarda cada etiqueta como archivo en lugar de imprimirla.")
        self.hint.setText(text)
        if kind == "network":
            self.ipp_path.setEnabled(self.protocol.currentData() != "raw")

    def _browse(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Carpeta de salida"), self.output_dir.text())
        if folder:
            self.output_dir.setText(folder)

    def profile(self) -> PrinterProfile:
        p = copy.deepcopy(self.original)
        p.alias = self.alias.text().strip()
        if p.kind == "system":
            p.system_name = self.system_name.currentText().strip()
        elif p.kind == "network":
            new_host = self.host.text().strip()
            if new_host != p.host or self.protocol.currentData() != p.protocol or self.retrust.isChecked():
                p.tls_fingerprint = ""
            p.host = new_host
            p.protocol = self.protocol.currentData()
            p.port = self.port.value()
            p.ipp_path = self.ipp_path.text().strip() or "/ipp/print"
        else:
            p.output_dir = self.output_dir.text().strip()
        p.language = self.language.currentData()
        p.media = self.media.currentData()
        p.dpi = int(self.dpi.currentData())
        p.rotate = self.rotate.currentData()
        p.crop = "auto" if self.crop.isChecked() else "none"
        p.enabled = self.enabled.isChecked()
        return p

    def _validate(self, p: PrinterProfile) -> str:
        if not p.alias:
            return tr("Escribe un alias (por ejemplo «etiquetas»).")
        others = [x.alias.casefold() for x in self.ctx.cfg.data.printers if x.id != p.id]
        if p.alias.casefold() in others:
            return tr("Ya existe una impresora con el alias «{0}».", p.alias)
        if p.kind == "system" and not p.system_name:
            return tr("Elige la impresora de Windows.")
        if p.kind == "network" and not p.host:
            return tr("Escribe la IP de la impresora.")
        return ""

    def _test(self) -> None:
        p = self.profile()
        problem = self._validate(p) if p.alias else ""
        if problem and "alias" not in problem:
            error(self, tr("Revisa los datos"), problem)
            return
        self.test_btn.setEnabled(False)
        self.test_btn.setText(tr("Imprimiendo…"))

        def done(detail: str) -> None:
            self._restore_test()
            info(self, tr("Prueba enviada"), tr("Se envió la etiqueta de prueba ({0}).", detail))

        def failed(message: str) -> None:
            self._restore_test()
            error(self, tr("No se pudo imprimir"), message)

        run_async(lambda: self.ctx.engine.print_test(p), done, failed)

    def _restore_test(self) -> None:
        self.test_btn.setEnabled(True)
        self.test_btn.setText(tr("Imprimir prueba"))

    def _save(self) -> None:
        p = self.profile()
        problem = self._validate(p)
        if problem:
            error(self, tr("Revisa los datos"), problem)
            return
        self.result_profile = p
        self.accept()

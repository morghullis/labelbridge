"""Ventanas de actualización: «Nueva versión disponible» (descarga + instalación) y «Novedades»."""

from __future__ import annotations

import threading
import time
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QPainter
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import APP_NAME, __version__
from .. import updater
from ..i18n import date_text, decimal, tr
from ..updater import UpdateInfo
from . import icons
from .context import AppContext
from .widgets import button, label


def _size_text(size: int) -> str:
    return f"{decimal(size / 1024 / 1024)} MB" if size else ""


def _date_text(value: str) -> str:
    try:
        return date_text(datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(), clock="")
    except ValueError:
        return ""


class _Banner(QFrame):
    """Cabecera con degradado índigo, icono de la app y títulos."""

    def __init__(self, title: str, subtitle: str) -> None:
        super().__init__()
        self.setFixedHeight(112)
        row = QHBoxLayout(self)
        row.setContentsMargins(26, 18, 26, 18)
        row.setSpacing(18)
        logo = QLabel()
        logo.setPixmap(icons.app_icon_pixmap(64))
        row.addWidget(logo)
        texts = QVBoxLayout()
        texts.setSpacing(4)
        heading = QLabel(title)
        heading.setStyleSheet("color: white; font-size: 20px; font-weight: 700; background: transparent;")
        sub = QLabel(subtitle)
        sub.setStyleSheet("color: #D4D4D4; font-size: 13px; background: transparent;")
        texts.addStretch(1)
        texts.addWidget(heading)
        texts.addWidget(sub)
        texts.addStretch(1)
        row.addLayout(texts, 1)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)  # sobrio: el negro de la marca con una línea dorada abajo
        painter.fillRect(self.rect(), QColor("#1F1F1F"))
        painter.fillRect(0, self.height() - 3, self.width(), 3, QColor("#B98A2C"))
        painter.end()


def _notes_box(notes: str) -> QTextBrowser:
    box = QTextBrowser()
    box.setOpenExternalLinks(True)
    box.setHtml(updater.notes_to_html(notes))
    box.setStyleSheet(
        "QTextBrowser { background: #FFFFFF; border: none; border-radius: 10px; padding: 8px 12px;"
        " font-size: 13px; color: #303030; }"
    )
    box.setMinimumHeight(150)
    return box


class _DownloadSignals(QObject):
    progress = Signal(int, int)
    finished = Signal(str)
    failed = Signal(str)


class UpdateDialog(QDialog):
    def __init__(self, parent: QWidget | None, ctx: AppContext, info: UpdateInfo, quit_app) -> None:
        super().__init__(parent)
        self.ctx = ctx
        self.info = info
        self.quit_app = quit_app
        self._cancel = threading.Event()
        self._started = 0.0
        self.setWindowTitle(tr("Actualizar {0}", APP_NAME))
        self.setMinimumWidth(560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(_Banner(tr("Nueva versión disponible"), tr("{0} {1} · tienes la {2}", APP_NAME, info.version, __version__)))

        body = QVBoxLayout()
        body.setContentsMargins(26, 20, 26, 22)
        body.setSpacing(12)
        meta = [part for part in (_date_text(info.published_at), _size_text(info.size)) if part]
        if meta:
            body.addWidget(label(tr("Publicada el ") + " · ".join(meta), "Muted"))
        body.addWidget(label(tr("Novedades"), "SectionTitle"))
        body.addWidget(_notes_box(info.localized_notes()), 1)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(10)
        body.addWidget(self.progress)
        self.status = label("", "Muted", wrap=True)
        self.status.setVisible(False)
        body.addWidget(self.status)

        hint = (
            tr("LabelBridge se cerrará un momento, se instalará la nueva versión y se abrirá sola. "
               "Tu configuración y tu historial se conservan.")
            if updater.can_install()
            else tr("En este equipo se descargará el instalador para que lo ejecutes tú.")
        )
        self.hint = label(hint, "Hint", wrap=True)
        body.addWidget(self.hint)

        buttons = QHBoxLayout()
        self.skip_btn = button(tr("Omitir esta versión"), None, "ghost")
        self.skip_btn.clicked.connect(self._skip)
        buttons.addWidget(self.skip_btn)
        buttons.addStretch(1)
        self.later_btn = button(tr("Más tarde"))
        self.later_btn.clicked.connect(self._later)
        self.update_btn = button(tr("Actualizar ahora"), "download", "primary")
        self.update_btn.clicked.connect(self._start)
        buttons.addWidget(self.later_btn)
        buttons.addWidget(self.update_btn)
        body.addLayout(buttons)
        layout.addLayout(body)

    # --- acciones ------------------------------------------------------------------------
    def _skip(self) -> None:
        self.ctx.cfg.data.general.skipped_version = self.info.version
        self.ctx.cfg.save("general")
        self.reject()

    def _later(self) -> None:
        self._cancel.set()
        self.reject()

    def _start(self) -> None:
        self.update_btn.setEnabled(False)
        self.skip_btn.setVisible(False)
        self.later_btn.setText(tr("Cancelar"))
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status.setVisible(True)
        self.status.setText(tr("Conectando…"))
        self._started = time.time()
        signals = _DownloadSignals(self)
        signals.progress.connect(self._on_progress)
        signals.finished.connect(self._on_downloaded)
        signals.failed.connect(self._on_failed)
        self._signals = signals

        def work() -> None:
            try:
                path = updater.download(self.info, lambda done, total: signals.progress.emit(done, total), self._cancel)
            except Exception as exc:  # se muestra en la ventana
                signals.failed.emit(str(exc))
            else:
                signals.finished.emit(str(path))

        threading.Thread(target=work, name="descarga-actualizacion", daemon=True).start()

    def _on_progress(self, done: int, total: int) -> None:
        elapsed = max(0.1, time.time() - self._started)
        speed = done / elapsed / 1024 / 1024
        if total:
            self.progress.setRange(0, 1000)
            self.progress.setValue(int(done * 1000 / total))
            self.status.setText(tr("Descargando {0} de {1} · {2} MB/s", _size_text(done), _size_text(total), decimal(speed)))
        else:
            self.status.setText(tr("Descargando {0}…", _size_text(done)))

    def _on_downloaded(self, path: str) -> None:
        self.progress.setRange(0, 1000)
        self.progress.setValue(1000)
        installer = Path(path)
        if not updater.can_install():
            self.status.setText(tr("✓ Descarga verificada: {0}", installer.name))
            self.later_btn.setText(tr("Cerrar"))
            self.update_btn.setText(tr("Abrir carpeta"))
            self.update_btn.setEnabled(True)
            self.update_btn.clicked.disconnect()
            self.update_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(installer.parent))))
            return
        self.status.setText(tr("✓ Descarga verificada. Instalando la nueva versión…"))
        self.later_btn.setEnabled(False)
        self.ctx.store.kv_set("update:installed", self.info.to_json())
        try:
            updater.install_and_restart(installer)
        except Exception as exc:
            self._on_failed(str(exc))
            return
        QApplication.processEvents()
        self.quit_app()

    def _on_failed(self, message: str) -> None:
        self.progress.setVisible(False)
        self.status.setText(f"✗ {message}")
        self.status.setObjectName("Warning")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)
        self.update_btn.setText(tr("Reintentar"))
        self.update_btn.setEnabled(True)
        self.later_btn.setText(tr("Cerrar"))
        self._cancel = threading.Event()

    def reject(self) -> None:
        self._cancel.set()
        super().reject()


class WhatsNewDialog(QDialog):
    def __init__(self, parent: QWidget | None, info: UpdateInfo) -> None:
        super().__init__(parent)
        self.setWindowTitle(tr("{0} actualizado", APP_NAME))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(_Banner(tr("¡Actualización instalada!"), tr("Ahora tienes {0} {1}", APP_NAME, info.version)))
        body = QVBoxLayout()
        body.setContentsMargins(26, 20, 26, 22)
        body.setSpacing(12)
        body.addWidget(label(tr("Novedades de esta versión"), "SectionTitle"))
        body.addWidget(_notes_box(info.localized_notes()), 1)
        row = QHBoxLayout()
        row.addStretch(1)
        ok = button(tr("Entendido"), "check", "primary")
        ok.clicked.connect(self.accept)
        row.addWidget(ok)
        body.addLayout(row)
        layout.addLayout(body)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

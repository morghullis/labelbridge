"""Ajustes → General → «Funcionar siempre» (Windows): que LabelBridge imprima aunque nadie use la PC.

Tres filas con su estado y su botón (ver system/always_on.py): no suspenderse con corriente, despertarse si la
suspenden y trabajar sin sesión iniciada. Cada cambio en Windows se confirma antes, y si algo falla se dice qué.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QGridLayout, QPushButton, QWidget

from ..i18n import tr
from ..system import always_on
from .widgets import Card, Pill, button, confirm, error, info, label, run_async

log = logging.getLogger(__name__)


class AlwaysOnCard(Card):
    def __init__(self) -> None:
        super().__init__(tr("Funcionar siempre"), tr(
            "Para que imprima aunque nadie esté usando la PC. Con la pantalla bloqueada ya funciona; aquí, también "
            "si la suspenden o si nadie inició sesión. Con la PC apagada, nada puede imprimir."))
        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(14)
        grid.setColumnStretch(0, 1)
        self.rows: dict[str, tuple[Pill, QPushButton]] = {}
        for row, (key, title, detail) in enumerate((
            ("sleep", tr("No suspenderse enchufada"),
             tr("Sin suspensión ni hibernación mientras tenga corriente; cerrar la tapa no la apaga.")),
            ("wake", tr("Despertarse si la suspenden"),
             tr("Si alguien la suspende a mano, se despierta sola cada 10 minutos y sigue imprimiendo.")),
            ("background", tr("Imprimir sin sesión iniciada"),
             tr("Al encender la PC, o con la sesión cerrada, LabelBridge trabaja en segundo plano. Windows te pide una "
                "vez la contraseña de tu cuenta, en su propia ventana.")),
        )):
            texts = QWidget()
            column = QGridLayout(texts)
            column.setContentsMargins(0, 0, 0, 0)
            column.setVerticalSpacing(2)
            column.addWidget(label(title, "ItemTitle"), 0, 0)
            column.addWidget(label(detail, "Muted", wrap=True), 1, 0)
            pill = Pill("off", tr("Revisando…"))
            action = button("")
            action.setEnabled(False)
            action.clicked.connect(lambda _=False, k=key: self._act(k))
            grid.addWidget(texts, row, 0)
            grid.addWidget(pill, row, 1, Qt.AlignmentFlag.AlignVCenter)
            grid.addWidget(action, row, 2, Qt.AlignmentFlag.AlignVCenter)
            self.rows[key] = (pill, action)
        self.add(layout=grid)
        self.problem = label("", "Warning", wrap=True)  # la tarea «sin sesión» falló: qué pasó y qué hacer
        self.problem.setVisible(False)
        self.add(self.problem)
        self.status: always_on.Status | None = None
        self.refresh()

    # --- estado ---------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        run_async(always_on.status, self._show, self._unknown, what=tr("Revisar «Funcionar siempre»"))

    def _unknown(self, message: str) -> None:
        for pill, action in self.rows.values():
            pill.set_state("warn", tr("No se pudo revisar"))
            action.setEnabled(False)
        log.warning("Funcionar siempre: no se pudo revisar el estado: %s", message)

    def _show(self, status: always_on.Status) -> None:
        self.status = status
        pill, action = self.rows["sleep"]
        if status.never_sleeps:
            pill.set_state("ok", tr("Activo"))
            action.setText(tr("Aplicado"))
            action.setEnabled(False)
        else:
            minutes = status.sleep_minutes_ac
            pill.set_state("warn", tr("Se suspende a los {0} min", minutes) if minutes else tr("Sin revisar"))
            action.setText(tr("Aplicar"))
            action.setEnabled(True)
        for key, on in (("wake", status.wake), ("background", status.background)):
            pill, action = self.rows[key]
            pill.set_state("ok" if on else "off", tr("Activo") if on else tr("Apagado"))
            action.setText(tr("Quitar") if on else (tr("Activar…") if key == "background" else tr("Activar")))
            action.setEnabled(True)
        if status.background_problem:  # existe pero no está funcionando: nunca un «Activo» engañoso
            self.rows["background"][0].set_state("error", tr("Con problemas"))
        self.problem.setText(status.background_problem)
        self.problem.setVisible(bool(status.background_problem))

    # --- cambios ----------------------------------------------------------------------------------------------------
    def _act(self, key: str) -> None:
        status = self.status
        if status is None:
            return
        if key == "sleep":
            if confirm(self, tr("No suspenderse enchufada"), tr(
                    "Se cambia la energía de Windows: con corriente, la PC no se suspende ni hiberna y cerrar la tapa "
                    "no la apaga (la pantalla sí se apaga). Si Windows pide permiso de administrador, acéptalo.\n\n"
                    "¿Aplicar?")):
                self._run(always_on.prevent_sleep, tr("Listo: con corriente, la PC ya no se suspende."))
        elif key == "wake":
            if status.wake:
                self._run(lambda: always_on.remove_task(always_on.WAKE_TASK), tr("Listo: ya no se despierta sola."))
            elif confirm(self, tr("Despertarse si la suspenden"), tr(
                    "Se crea la tarea de Windows «{0}», que despierta la PC cada 10 minutos si está suspendida, y se "
                    "permiten los despertadores en la energía de Windows. ¿Activar?", always_on.WAKE_TASK)):
                self._run(always_on.enable_wake, tr("Listo: si la suspenden, se despierta sola en 10 minutos."))
        elif key == "background":
            if status.background:
                self._run(lambda: always_on.remove_task(always_on.BACKGROUND_TASK),
                          tr("Listo: sin sesión iniciada, LabelBridge ya no trabaja."))
            else:
                self._enable_background()

    def _enable_background(self) -> None:
        if not confirm(self, tr("Imprimir sin sesión iniciada"), tr(
                "Se crea la tarea de Windows «{0}»: al encender la PC (y cada 5 minutos, si no está abierta) abre "
                "LabelBridge en segundo plano con tu cuenta, aunque nadie haya iniciado sesión. Cuando entres, la "
                "ventana toma el relevo sin imprimir nada dos veces.\n\nWindows te pedirá la contraseña de tu cuenta "
                "(no el PIN) en su ventana de seguridad; la guarda Windows en la tarea, no LabelBridge. ¿Seguir?",
                always_on.BACKGROUND_TASK)):
            return
        try:  # la ventana de Windows es modal: en el hilo de la interfaz
            done = always_on.enable_background(int(self.window().winId()))
        except always_on.AlwaysOnError as exc:
            error(self, tr("Imprimir sin sesión iniciada"), str(exc))
            self.refresh()
            return
        if done:
            info(self, tr("Imprimir sin sesión iniciada"), tr(
                "Listo. Desde ahora, con la PC encendida LabelBridge imprime aunque nadie haya iniciado sesión."))
        self.refresh()

    def _run(self, action, done_text: str) -> None:
        for _pill, button_ in self.rows.values():
            button_.setEnabled(False)

        def finished(_result) -> None:
            info(self, tr("Funcionar siempre"), done_text)
            self.refresh()

        def failed(message: str) -> None:
            error(self, tr("Funcionar siempre"), message)
            self.refresh()

        run_async(action, finished, failed, what=tr("Funcionar siempre"))

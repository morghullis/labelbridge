"""Motor de impresión: impresoras del sistema, red (RAW 9100 / IPP), ZPL y PDF."""

from __future__ import annotations

import threading


class PrintError(Exception):
    """Fallo al imprimir. retryable=False si reintentar no tiene sentido."""

    def __init__(self, message: str, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class PermanentPrintError(PrintError):
    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


# Qt no garantiza que el subsistema de impresión sea reentrante entre hilos:
# toda llamada a QPrinter/QPrinterInfo pasa por este candado.
QT_PRINT_LOCK = threading.RLock()

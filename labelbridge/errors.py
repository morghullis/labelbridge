"""Errores sin silencio: toda falla queda en el registro con su detalle y la persona se entera cuando le afecta.

- `user_message(exc)`: el texto para una persona. Los errores esperados (sin red, impresora, Shopify…) ya traen un
  mensaje pensado para ella; los de programación (KeyError, TypeError…) dicen el tipo y dónde ver el detalle, en vez
  de un «'id'» que no explica nada.
- `report(where, exc)`: para fallas que ninguna ventana atiende (hilos, temporizadores, clics que fallan): las anota con
  su traza y avisa (sin repetir el mismo aviso en ráfaga). La app conecta el aviso con `set_notifier` al arrancar.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from .i18n import tr

log = logging.getLogger(__name__)

# Errores de programación: su texto no le sirve a una persona («'id'», «'NoneType' object…»).
BUGS = (AssertionError, AttributeError, IndexError, KeyError, NameError, RecursionError, TypeError,
        UnboundLocalError, ZeroDivisionError)
REPEAT_SECONDS = 60  # el mismo aviso, como mucho una vez por minuto

Notifier = Callable[[str, str], None]  # (título, mensaje)

_notifier: Notifier | None = None
_last: dict[str, float] = {}
_lock = threading.Lock()


def is_bug(exc: BaseException) -> bool:
    return isinstance(exc, BUGS)


def user_message(exc: BaseException) -> str:
    text = str(exc).strip()
    if is_bug(exc) or not text:
        return tr("Error inesperado ({0}). El detalle quedó en Registro.", type(exc).__name__)
    return text


def set_notifier(notifier: Notifier | None) -> None:
    """Cómo se avisa a la persona (la app: un aviso junto al reloj y en la ventana). Sin notificador, solo se anota."""
    global _notifier
    _notifier = notifier


def report(where: str, exc: BaseException | None = None, message: str = "", title: str = "") -> None:
    """Anota la falla (con su traza si hay excepción) y avisa a la persona.

    `where`: qué se estaba haciendo («Imprimir», «Revisar pedidos»…), ya traducido; va en el registro y es el título del
    aviso si no se da `title`. `message`: el texto para la persona (por defecto, `user_message(exc)`).
    """
    text = message or (user_message(exc) if exc is not None else tr("Algo falló."))
    if exc is not None:
        log.error("%s: %s", where, text, exc_info=(type(exc), exc, exc.__traceback__))
    else:
        log.error("%s: %s", where, text)
    notify(title or where, text)


def notify(title: str, text: str) -> None:
    """El aviso, sin repetir el mismo en ráfaga (un error en un temporizador podría repetirse cada segundo)."""
    key = f"{title}|{text}"
    now = time.monotonic()
    with _lock:
        if now - _last.get(key, -REPEAT_SECONDS) < REPEAT_SECONDS:
            return
        _last[key] = now
    notifier = _notifier
    if notifier is None:
        return
    try:
        notifier(title, text)
    except Exception:  # el aviso nunca puede tumbar a quien avisa
        log.exception("No se pudo mostrar el aviso de error")


def reset() -> None:
    """Para las pruebas: olvida los avisos ya dados."""
    with _lock:
        _last.clear()

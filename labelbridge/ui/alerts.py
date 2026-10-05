"""Qué hace cada aviso según Ajustes → Notificaciones y sonidos: si sale junto al reloj y con qué sonido."""

from __future__ import annotations

from datetime import datetime

from ..config import ConfigManager, NotificationSettings
from ..system import sounds


def toast_enabled(cfg: ConfigManager, kind: str) -> bool:
    """¿Sale el aviso junto al reloj? Los avisos sin tipo (actualizaciones, impresoras nuevas…) salen siempre."""
    g, n = cfg.data.general, cfg.data.notifications
    if kind == "printed":
        return g.notify_printed
    if kind == "print_error":
        return g.notify_errors
    if kind in ("new_order", "action", "delivery"):
        return bool(getattr(n, kind))
    return True


def _minutes(text: str) -> int:
    try:
        hours, minutes = (text or "0:0").split(":")[:2]
        return (int(hours) % 24) * 60 + int(minutes) % 60
    except ValueError:
        return 0


def quiet_now(n: NotificationSettings, now: datetime | None = None) -> bool:
    """¿Estamos en el horario de silencio? (puede pasar la medianoche, p. ej. de 22:00 a 07:00)."""
    if not n.quiet:
        return False
    now = now or datetime.now()
    start, end, current = _minutes(n.quiet_from), _minutes(n.quiet_to), now.hour * 60 + now.minute
    if start == end:
        return False
    return start <= current < end if start < end else (current >= start or current < end)


def play_for(cfg: ConfigManager, kind: str) -> bool:
    """Suena el sonido elegido para ese aviso (nada fuera de horario o si no tiene sonido)."""
    n = cfg.data.notifications
    if not kind or quiet_now(n):
        return False
    return sounds.play(n.sound(kind), n.volume, n.custom_sound)

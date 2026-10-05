"""Sonidos de aviso: tonos propios de LabelBridge (se generan al usarlos), el de Windows o un .wav propio.

Los tonos se sintetizan (campanas con armónicos y un ataque suave para que no hagan clic) y se guardan una vez por
volumen en la carpeta de datos. En Windows suenan con winsound, sin dependencias; en macOS con afplay (desarrollo).
"""

from __future__ import annotations

import array
import hashlib
import logging
import math
import random
import subprocess
import sys
import wave
from pathlib import Path

from ..i18n import N_
from .platform import pick

log = logging.getLogger(__name__)

RATE = 44100

# Sonidos que se pueden elegir. "windows" es el aviso de Windows y "custom" el .wav que elija la persona.
SOUNDS = {
    "": N_("Sin sonido"),
    "register": N_("Caja registradora"),
    "chime": N_("Campanita"),
    "ding": N_("Ding"),
    "bell": N_("Campana"),
    "harp": N_("Arpa"),
    "pop": N_("Burbuja"),
    "alert": N_("Alerta"),
    "windows": pick(N_("Sonido de Windows"), N_("Sonido de la Mac")),  # el sonido del sistema
    "custom": N_("Mi archivo (.wav)"),
}


# --- síntesis --------------------------------------------------------------------------------------

def _bell(freq: float, seconds: float, decay: float = 4.0, partials=((1.0, 1.0), (2.0, 0.45), (3.01, 0.22), (4.2, 0.12))):
    """Una nota de campana: varios armónicos que se apagan cada uno a su ritmo."""
    total = int(RATE * seconds)
    out = [0.0] * total
    for ratio, amplitude in partials:
        f = freq * ratio
        speed = decay * (1 + 0.6 * (ratio - 1))
        step = 2 * math.pi * f / RATE
        for i in range(total):
            out[i] += amplitude * math.exp(-speed * i / RATE) * math.sin(step * i)
    return out


def _mix(length: float, *notes: tuple[float, list[float]]) -> list[float]:
    """Suma notas que empiezan en distintos momentos (segundos)."""
    out = [0.0] * int(RATE * length)
    for start, samples in notes:
        offset = int(RATE * start)
        for i, value in enumerate(samples[: max(0, len(out) - offset)]):
            out[offset + i] += value
    return out


def _beep(freq: float, seconds: float) -> list[float]:
    total = int(RATE * seconds)
    step = 2 * math.pi * freq / RATE
    fade = int(RATE * 0.01)
    out = []
    for i in range(total):
        envelope = min(1.0, i / fade, (total - i) / fade)
        out.append(envelope * (math.sin(step * i) + 0.25 * math.sin(3 * step * i)))
    return out


def _noise(seconds: float, decay: float) -> list[float]:
    rng = random.Random(7)  # siempre el mismo sonido
    last = 0.0
    out = []
    for i in range(int(RATE * seconds)):
        value = rng.uniform(-1, 1)
        last = value - 0.85 * last  # más agudo, como monedas
        out.append(last * math.exp(-decay * i / RATE))
    return out


def _pop() -> list[float]:
    total = int(RATE * 0.12)
    out, phase = [], 0.0
    for i in range(total):
        t = i / total
        phase += 2 * math.pi * (900 - 600 * t) / RATE
        out.append(math.sin(phase) * math.exp(-28 * i / RATE))
    return out


def synth(key: str) -> list[float]:
    """Las muestras (−1…1) de un tono de LabelBridge."""
    if key == "register":  # «cha-ching»: monedas y dos campanas brillantes
        return _mix(1.0, (0.0, [0.35 * v for v in _noise(0.09, 30)]), (0.07, _bell(2637.0, 0.9, 5.0)),
                    (0.11, [0.8 * v for v in _bell(3322.4, 0.85, 5.5)]))
    if key == "chime":
        return _mix(1.2, (0.0, _bell(1318.5, 1.0, 4.5)), (0.13, _bell(1975.5, 1.05, 4.5)))
    if key == "ding":
        return _bell(880.0, 1.0, 4.0)
    if key == "bell":
        return _bell(523.25, 1.6, 2.6, ((1.0, 1.0), (2.0, 0.6), (2.76, 0.35), (5.4, 0.2), (8.93, 0.1)))
    if key == "harp":
        notes = (1046.5, 1318.5, 1568.0, 2093.0)
        return _mix(1.2, *((0.075 * n, _bell(f, 0.9, 5.0)) for n, f in enumerate(notes)))
    if key == "pop":
        return _pop()
    if key == "alert":
        high, low = _beep(880.0, 0.14), _beep(659.25, 0.14)
        return _mix(0.9, (0.0, high), (0.17, low), (0.45, high), (0.62, low))
    raise KeyError(key)


def _write(path: Path, samples: list[float], volume: float) -> None:
    peak = max((abs(v) for v in samples), default=1.0) or 1.0
    gain = 0.9 * max(0.0, min(1.0, volume)) * 32767 / peak
    data = array.array("h", (int(v * gain) for v in samples))
    if sys.byteorder == "big":
        data.byteswap()
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(data.tobytes())


def tone_file(key: str, volume: int, folder: Path | None = None) -> Path:
    """El .wav del tono a ese volumen (0–100), generado la primera vez."""
    from ..paths import data_dir

    volume = max(0, min(100, int(volume)))
    path = (folder or data_dir() / "sonidos") / f"{key}-{volume}.wav"
    if not path.exists():
        _write(path, synth(key), volume / 100)
    return path


def scaled_copy(source: Path, volume: int, folder: Path | None = None) -> Path:
    """Un .wav propio a ese volumen (si es PCM de 16 bits; si no, el archivo tal cual)."""
    from ..paths import data_dir

    volume = max(0, min(100, int(volume)))
    if volume == 100:
        return source
    try:
        with wave.open(str(source), "rb") as wav:
            if wav.getsampwidth() != 2:
                return source
            params = wav.getparams()
            frames = wav.readframes(wav.getnframes())
    except (OSError, wave.Error, EOFError):
        return source
    digest = hashlib.sha1(f"{source.resolve()}|{source.stat().st_mtime}".encode()).hexdigest()[:12]
    target = (folder or data_dir() / "sonidos") / f"propio-{digest}-{volume}.wav"
    if not target.exists():
        data = array.array("h", frames)
        if sys.byteorder == "big":
            data.byteswap()
        data = array.array("h", (int(v * volume / 100) for v in data))
        if sys.byteorder == "big":
            data.byteswap()
        target.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(target), "wb") as out:
            out.setparams(params)
            out.writeframes(data.tobytes())
    return target


# --- reproducción --------------------------------------------------------------------------------

def play(key: str, volume: int = 80, custom: str = "") -> bool:
    """Suena sin bloquear. Devuelve False si no hay nada que sonar o no se pudo."""
    if not key or volume <= 0:
        return False
    try:
        if key == "windows":
            return _play_system()
        if key == "custom":
            source = Path(custom)
            if not custom or not source.is_file():
                return False
            return _play_file(scaled_copy(source, volume))
        return _play_file(tone_file(key, volume))
    except Exception:
        log.warning("No se pudo reproducir el sonido %s", key, exc_info=True)
        return False


def _play_file(path: Path) -> bool:
    if sys.platform == "win32":
        import winsound

        winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return True
    if sys.platform == "darwin":
        subprocess.Popen(["afplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    for player in ("paplay", "aplay"):
        try:
            subprocess.Popen([player, str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            continue
    return False


def _play_system() -> bool:
    if sys.platform == "win32":
        import winsound

        for alias in ("Notification.Default", "SystemAsterisk"):
            try:
                winsound.PlaySound(alias, winsound.SND_ALIAS | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                return True
            except RuntimeError:
                continue
        return False
    if sys.platform == "darwin":  # el sonido «Vidrio» de la Mac
        chime = Path("/System/Library/Sounds/Glass.aiff")
        if chime.exists():
            subprocess.Popen(["afplay", str(chime)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
    from PySide6.QtWidgets import QApplication

    QApplication.beep()
    return True

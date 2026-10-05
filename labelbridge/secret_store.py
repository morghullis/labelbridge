"""Secretos (tokens, claves) en el almacén seguro del sistema.

Windows: Administrador de credenciales. macOS: Llavero. Si no hay almacén disponible se usa un archivo
local de respaldo; en Windows ese archivo va cifrado con DPAPI (solo el mismo usuario en el mismo equipo
puede leerlo).
"""

from __future__ import annotations

import base64
import importlib.util
import json
import logging
import os
import sys
import threading
from pathlib import Path
from .i18n import tr

log = logging.getLogger(__name__)

SERVICE = "LabelBridge"

# Nombres de secretos usados por la app
SHOPIFY_CLIENT_SECRET = "shopify_client_secret"
SHOPIFY_ACCESS_TOKEN = "shopify_access_token"
MQTT_PASSWORD = "mqtt_password"
LOCAL_API_KEY = "local_api_key"
ANTHROPIC_API_KEY = "anthropic_api_key"  # asistente de configuración (Claude)
DATA_KEY = "data_encryption_key"  # cifrado de los datos en disco (ver security/crypto.py)

FILE_FORMAT = 2  # 1 = base64 sin cifrar (versiones antiguas); 2 = protegido con DPAPI en Windows


class SecretStoreError(Exception):
    """El almacén seguro falló (no es lo mismo que «el secreto no existe»)."""


def _dpapi_available() -> bool:
    return sys.platform == "win32" and importlib.util.find_spec("win32crypt") is not None


def _protect(data: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptProtectData(data, "LabelBridge", None, None, None, 0)


def _unprotect(blob: bytes) -> bytes:
    import win32crypt

    return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]


class SecretStore:
    def __init__(self, fallback_path: Path, backend: str | None = None) -> None:
        self._fallback_path = fallback_path
        self._lock = threading.Lock()
        self._cache: dict[str, str] = {}
        self._keyring = None
        self._warned = False
        backend = backend or os.environ.get("LABELBRIDGE_SECRETS", "auto")
        if backend != "file":
            try:
                import keyring
                from keyring.backends import fail

                if not isinstance(keyring.get_keyring(), fail.Keyring):
                    self._keyring = keyring
            except Exception:
                log.warning("No hay almacén seguro de credenciales; se usará un archivo local")

    @property
    def backend_name(self) -> str:
        if self._keyring is None:
            return tr("archivo local cifrado (DPAPI)") if _dpapi_available() else tr("archivo local")
        return type(self._keyring.get_keyring()).__name__

    def get(self, name: str, strict: bool = False) -> str:
        """Valor del secreto ("" si no existe). Con strict=True, un fallo del almacén lanza SecretStoreError."""
        with self._lock:
            if name in self._cache and (self._cache[name] or not strict):
                return self._cache[name]  # un vacío en caché no basta en modo estricto: se vuelve a consultar
            value = ""
            if self._keyring is not None:
                try:
                    value = self._keyring.get_password(SERVICE, name) or ""
                except Exception as exc:
                    if strict:
                        raise SecretStoreError(tr("No se pudo leer {0} del almacén seguro", name)) from exc
                    log.exception("No se pudo leer el secreto %s del almacén seguro", name)
            if not value:
                value = self._read_file().get(name, "")
            self._cache[name] = value
            return value

    def set(self, name: str, value: str) -> None:
        value = (value or "").strip()
        with self._lock:
            self._cache[name] = value
            stored = False
            if self._keyring is not None:
                try:
                    if value:
                        self._keyring.set_password(SERVICE, name, value)
                    else:
                        try:
                            self._keyring.delete_password(SERVICE, name)
                        except Exception:
                            pass
                    stored = True
                except Exception:
                    log.exception("No se pudo guardar %s en el almacén seguro; uso archivo", name)
            data = self._read_file()
            if stored:
                data.pop(name, None)
            elif value:
                data[name] = value
            else:
                data.pop(name, None)
            self._write_file(data)

    # --- archivo de respaldo ---------------------------------------------------------------------
    def _read_file(self) -> dict[str, str]:
        try:
            raw = json.loads(self._fallback_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except Exception:
            log.warning("secrets.json ilegible; se ignora")
            return {}
        try:
            if isinstance(raw, dict) and raw.get("format") == FILE_FORMAT:
                blob = base64.b64decode(raw["data"])
                plain = _unprotect(blob) if raw.get("dpapi") else blob
                return json.loads(plain.decode("utf-8"))
            return {k: base64.b64decode(v).decode("utf-8") for k, v in raw.items()}  # formato antiguo
        except Exception:
            log.warning("secrets.json no se pudo descifrar (¿de otro usuario o equipo?); se ignora")
            return {}

    def _write_file(self, data: dict[str, str]) -> None:
        if not data:
            try:
                self._fallback_path.unlink()
            except FileNotFoundError:
                pass
            return
        plain = json.dumps(data).encode("utf-8")
        dpapi = _dpapi_available()
        if not dpapi and not self._warned:
            self._warned = True
            log.warning("Los secretos se guardan en un archivo local sin cifrar: no hay almacén seguro disponible")
        blob = _protect(plain) if dpapi else plain
        payload = {"format": FILE_FORMAT, "dpapi": dpapi, "data": base64.b64encode(blob).decode("ascii")}
        tmp = self._fallback_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, self._fallback_path)

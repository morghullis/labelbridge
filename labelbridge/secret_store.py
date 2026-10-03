"""Secretos (tokens, claves) en el almacén seguro del sistema.

Windows: Administrador de credenciales. macOS: Llavero. Si no hay almacén disponible
se usa un archivo local como respaldo (y se avisa en el registro).
"""

from __future__ import annotations

import base64
import json
import logging
import os
import threading
from pathlib import Path

log = logging.getLogger(__name__)

SERVICE = "LabelBridge"

# Nombres de secretos usados por la app
SHOPIFY_CLIENT_SECRET = "shopify_client_secret"
SHOPIFY_ACCESS_TOKEN = "shopify_access_token"
MQTT_PASSWORD = "mqtt_password"
LOCAL_API_KEY = "local_api_key"


class SecretStore:
    def __init__(self, fallback_path: Path, backend: str | None = None) -> None:
        self._fallback_path = fallback_path
        self._lock = threading.Lock()
        self._cache: dict[str, str] = {}
        self._keyring = None
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
            return "archivo local"
        return type(self._keyring.get_keyring()).__name__

    def get(self, name: str) -> str:
        with self._lock:
            if name in self._cache:
                return self._cache[name]
            value = ""
            if self._keyring is not None:
                try:
                    value = self._keyring.get_password(SERVICE, name) or ""
                except Exception:
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

    def _read_file(self) -> dict[str, str]:
        try:
            raw = json.loads(self._fallback_path.read_text(encoding="utf-8"))
            return {k: base64.b64decode(v).decode("utf-8") for k, v in raw.items()}
        except FileNotFoundError:
            return {}
        except Exception:
            log.warning("secrets.json ilegible; se ignora")
            return {}

    def _write_file(self, data: dict[str, str]) -> None:
        if not data:
            try:
                self._fallback_path.unlink()
            except FileNotFoundError:
                pass
            return
        encoded = {k: base64.b64encode(v.encode("utf-8")).decode("ascii") for k, v in data.items()}
        self._fallback_path.write_text(json.dumps(encoded), encoding="utf-8")
        try:
            os.chmod(self._fallback_path, 0o600)
        except OSError:
            pass

"""Cifrado de los datos en disco con AES-256-GCM (cifrado autenticado: detecta cualquier alteración).

La clave de datos es aleatoria (256 bits), se crea la primera vez y se guarda en el almacén seguro del
sistema (en Windows, el Administrador de credenciales, protegido por DPAPI con la sesión del usuario).
Sin esa clave los archivos no se pueden leer, ni siquiera copiándolos a otro equipo.

Cada dato cifrado va ligado a su contexto (p. ej. «jobs.url:<id>»): un valor cifrado no se puede
mover a otro campo u otro registro sin que el descifrado falle.
"""

from __future__ import annotations

import base64
import binascii
import logging
import os
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..i18n import tr
from ..secret_store import DATA_KEY, SecretStoreError

log = logging.getLogger(__name__)

MAGIC = b"LBX1"  # binario: MAGIC + nonce (12) + texto cifrado + etiqueta (16)
TEXT_PREFIX = "lbx1:"  # texto: prefijo + base64url(nonce + texto cifrado + etiqueta)
NONCE_BYTES = 12


class CryptoError(Exception):
    """El dato no se puede descifrar (alterado, de otra instalación o sin clave)."""


class DataProtector:
    def __init__(self, key: bytes) -> None:
        if len(key) != 32:
            raise ValueError(tr("La clave de datos debe tener 256 bits"))
        self._aead = AESGCM(key)

    @classmethod
    def from_secrets(cls, secrets, attempts: int = 3) -> "DataProtector":
        """Clave del almacén seguro; la crea si no existe. Nunca la reemplaza si el almacén falla."""
        for attempt in range(attempts):
            try:
                encoded = secrets.get(DATA_KEY, strict=True)
                break
            except SecretStoreError:
                if attempt == attempts - 1:
                    raise CryptoError(tr("No se pudo leer la clave de cifrado del almacén seguro del sistema")) from None
                time.sleep(0.5)
        if encoded:
            try:
                key = base64.urlsafe_b64decode(encoded.encode("ascii"))
            except (binascii.Error, UnicodeEncodeError):
                raise CryptoError(tr("La clave de cifrado guardada no es válida")) from None
            return cls(key)
        key = AESGCM.generate_key(bit_length=256)
        secrets.set(DATA_KEY, base64.urlsafe_b64encode(key).decode("ascii"))
        if secrets.get(DATA_KEY, strict=True) != base64.urlsafe_b64encode(key).decode("ascii"):
            raise CryptoError(tr("No se pudo guardar la clave de cifrado en el almacén seguro"))
        log.info("Clave de cifrado de datos creada en el almacén seguro")
        return cls(key)

    # --- binario ---------------------------------------------------------------------------
    @staticmethod
    def is_encrypted(blob: bytes) -> bool:
        return blob.startswith(MAGIC)

    def encrypt(self, data: bytes, context: str) -> bytes:
        nonce = os.urandom(NONCE_BYTES)
        return MAGIC + nonce + self._aead.encrypt(nonce, data, context.encode("utf-8"))

    def decrypt(self, blob: bytes, context: str) -> bytes:
        """Descifra; un dato antiguo sin cifrar se devuelve tal cual (se cifra al migrar)."""
        if not self.is_encrypted(blob):
            return blob
        body = blob[len(MAGIC):]
        try:
            return self._aead.decrypt(body[:NONCE_BYTES], body[NONCE_BYTES:], context.encode("utf-8"))
        except (InvalidTag, ValueError):
            raise CryptoError(tr("Dato cifrado dañado o de otra instalación")) from None

    # --- texto -----------------------------------------------------------------------------
    def encrypt_text(self, text: str, context: str) -> str:
        if not text:
            return text
        nonce = os.urandom(NONCE_BYTES)
        sealed = nonce + self._aead.encrypt(nonce, text.encode("utf-8"), context.encode("utf-8"))
        return TEXT_PREFIX + base64.urlsafe_b64encode(sealed).decode("ascii")

    def decrypt_text(self, value: str, context: str) -> str:
        if not value or not value.startswith(TEXT_PREFIX):
            return value
        try:
            sealed = base64.urlsafe_b64decode(value[len(TEXT_PREFIX):].encode("ascii"))
            return self._aead.decrypt(sealed[:NONCE_BYTES], sealed[NONCE_BYTES:], context.encode("utf-8")).decode("utf-8")
        except (InvalidTag, ValueError, binascii.Error, UnicodeDecodeError):
            raise CryptoError(tr("Dato cifrado dañado o de otra instalación")) from None

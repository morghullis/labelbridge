"""Lo que el instalador deja para la primera vez que se abre la app: el idioma elegido y, si se quiso, la tienda de
Shopify (dirección, Client ID y Client secret). La app pasa la clave al almacén seguro de Windows, borra lo que dejó el
instalador, prueba la conexión y, si funciona, activa la tienda (ver ShopifyPage).

Valores en HKCU\\Software\\LabelBridge (Language) y HKCU\\Software\\LabelBridge\\Setup (ShopDomain, ClientId,
ClientSecret).
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

KEY = r"Software\LabelBridge"
SETUP_KEY = KEY + r"\Setup"


def read() -> dict[str, str]:
    """{"language", "shop", "client_id", "client_secret"} con lo que haya (vacío fuera de Windows)."""
    if sys.platform != "win32":
        return {}
    import winreg

    hints: dict[str, str] = {}
    for key, names in ((KEY, (("Language", "language"),)), (SETUP_KEY, (("ShopDomain", "shop"), ("ClientId", "client_id"), ("ClientSecret", "client_secret")))):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                for name, field in names:
                    try:
                        value = winreg.QueryValueEx(handle, name)[0]
                    except OSError:
                        continue
                    if isinstance(value, str) and value.strip():
                        hints[field] = value.strip()
        except OSError:
            continue
    return hints


def forget_store() -> None:
    """La tienda del instalador ya se usó: se borra, con la clave (el idioma se queda como pista)."""
    if sys.platform != "win32":
        return
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, SETUP_KEY)
    except FileNotFoundError:
        pass  # ya no estaba
    except OSError as exc:  # quedaría la clave del instalador en el registro de Windows: que se sepa
        log.warning("No se pudo borrar los datos del instalador del registro de Windows: %s", exc)


def apply(cfg, hints: dict[str, str]) -> bool:
    """Pone la tienda del instalador en la configuración si aún no hay ninguna. True si cambió algo."""
    s = cfg.data.shopify
    shop = hints.get("shop", "")
    if not shop or s.shop.strip():
        return False
    from ..connectors.shopify import ShopifyError, normalize_shop

    try:
        s.shop = normalize_shop(shop)
    except ShopifyError:
        log.warning("El instalador dejó una tienda no válida: %s", shop)
        return False
    if hints.get("client_id") and not s.client_id.strip():
        s.client_id = hints["client_id"].strip()
    s.auth_mode = "client_credentials"
    cfg.save("shopify")
    log.info("Tienda del instalador: %s", s.shop)
    return True


def apply_secret(secrets, cfg, hints: dict[str, str]) -> bool:
    """La clave del instalador pasa al almacén seguro si es de la tienda configurada y aún no había una. True si quedó
    todo listo para probar la conexión (tienda, Client ID y clave)."""
    from ..secret_store import SHOPIFY_CLIENT_SECRET

    secret = hints.get("client_secret", "").strip()
    s = cfg.data.shopify
    if not secret or not s.shop.strip() or not s.client_id.strip() or s.auth_mode != "client_credentials":
        return False
    if secrets.get(SHOPIFY_CLIENT_SECRET):
        return False  # ya había una: no se cambia por la del instalador
    secrets.set(SHOPIFY_CLIENT_SECRET, secret)
    log.info("Clave de la tienda del instalador guardada en el almacén seguro")
    return True

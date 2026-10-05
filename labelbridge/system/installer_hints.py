"""Lo que el instalador deja para la primera vez que se abre la app: el idioma elegido y la tienda de Shopify
(dirección y Client ID). La clave secreta nunca pasa por el instalador: la pide la app, que la guarda en el almacén
seguro de Windows y prueba la conexión.

Valores en HKCU\\Software\\LabelBridge (Language) y HKCU\\Software\\LabelBridge\\Setup (ShopDomain, ClientId).
"""

from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)

KEY = r"Software\LabelBridge"
SETUP_KEY = KEY + r"\Setup"


def read() -> dict[str, str]:
    """{"language", "shop", "client_id"} con lo que haya (vacío fuera de Windows)."""
    if sys.platform != "win32":
        return {}
    import winreg

    hints: dict[str, str] = {}
    for key, names in ((KEY, (("Language", "language"),)), (SETUP_KEY, (("ShopDomain", "shop"), ("ClientId", "client_id")))):
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
    """La tienda del instalador ya se usó: se borra (el idioma se queda como pista)."""
    if sys.platform != "win32":
        return
    import winreg

    try:
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, SETUP_KEY)
    except OSError:
        pass


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

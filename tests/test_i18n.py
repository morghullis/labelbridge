"""La app en inglés: todo texto visible pasa por tr() y tiene su traducción, con los mismos huecos y etiquetas."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "devtools"))

from labelbridge import i18n  # noqa: E402


@pytest.fixture
def english():
    i18n.set_language("en")
    try:
        yield
    finally:
        i18n.set_language("es")


def _holes(text: str) -> list[str]:
    return sorted(re.findall(r"\{[^{}]*\}", text.replace("{{", "").replace("}}", "")))


def _tags(text: str) -> list[str]:
    return sorted(re.findall(r"</?(?:b|i|u|br|code|ol|ul|li|p|a|span|small)\b", text))


def test_every_text_has_an_english_translation():
    from i18n_extract import catalog, keys

    english = catalog("en")
    missing = [key for key in keys() if key not in english]
    assert missing == [], f"Sin traducir ({len(missing)}): {missing[:5]}"
    for key, text in english.items():
        assert text.strip(), key
        assert _holes(key) == _holes(text), f"Huecos distintos: {key!r} → {text!r}"
        assert _tags(key) == _tags(text), f"Etiquetas HTML distintas: {key!r}"
        assert key.count("\n") == text.count("\n"), f"Saltos de línea distintos: {key!r}"


def test_translations_have_no_spanish_left():
    english = json.loads((ROOT / "assets" / "locale" / "en.json").read_text(encoding="utf-8"))
    spanish = re.compile(r"[áéíóúñ¿¡]|\b(el|la|los|las|del|para|una|está|pedido|guía|impresora|etiqueta)\b", re.I)
    # Excepciones: nombres propios o archivos que llevan palabras en español.
    leftovers = {k: v for k, v in english.items() if spanish.search(re.sub(r"<[^>]+>|\S+\.(json|log)\b", " ", v))}
    assert leftovers == {}


def test_tr_fills_holes_and_falls_back(english):
    assert i18n.tr("Pedidos") == "Orders"
    assert i18n.tr("Se guardaron {0} pedidos en {1}.", 3, "x.csv") == "3 orders were saved to x.csv."
    assert i18n.tr("Texto que no está en el catálogo {0}", 1) == "Texto que no está en el catálogo 1"
    i18n.set_language("es")
    assert i18n.tr("Pedidos") == "Pedidos"
    assert i18n.set_language("fr") == "es"  # un idioma desconocido queda en español


def test_language_choice_order(monkeypatch):
    monkeypatch.setattr(i18n, "system_language", lambda: "en")
    assert i18n.choose("es", "en") == "es"  # lo de Ajustes manda
    assert i18n.choose("", "es") == "es"  # si no, lo que se eligió en el instalador
    assert i18n.choose("", "") == "en"  # si no, el idioma de Windows


def test_no_visible_text_escapes_translation():
    from i18n_audit import visible_leftovers

    assert visible_leftovers() == []


def _window(core):
    from PySide6.QtWidgets import QApplication

    from labelbridge.services import Services
    from labelbridge.ui.context import AppContext, QtBridge
    from labelbridge.ui.main_window import MainWindow

    core.cfg.data.local_api.enabled = False
    services = Services(core.cfg, core.secrets, core.store, core.engine, core.events)
    window = MainWindow(AppContext(core.cfg, core.secrets, core.store, core.engine, services, core.events,
                                   QtBridge(core.events, core.cfg)))
    QApplication.processEvents()
    return window


def test_the_window_in_english(core, english):
    from labelbridge.connectors.orders_overview import classify
    from test_orders import NOW, SINCE, make_order

    window = _window(core)
    assert window.nav_buttons["orders"].text().strip() == "Orders"
    assert window.nav_buttons["settings"].text().strip() == "Settings"
    row = classify(make_order(subtotal=1500), settings=core.cfg.data.shopify, since=SINCE, now=NOW)
    assert row.status == "Buy the FedEx label in Shopify" and "items over US$1,000.00" in row.reason
    window.quitting = True
    window.close()


def test_installer_store_is_used_once(core):
    from labelbridge.system import installer_hints

    assert installer_hints.apply(core.cfg, {"shop": "Mi-Tienda", "client_id": "abc123"}) is True
    s = core.cfg.data.shopify
    assert (s.shop, s.client_id, s.auth_mode) == ("mi-tienda.myshopify.com", "abc123", "client_credentials")
    assert installer_hints.apply(core.cfg, {"shop": "otra-tienda"}) is False  # ya había tienda: no se toca
    assert installer_hints.apply(core.cfg, {"shop": "www.ejemplo.com"}) is False


def test_changing_the_language_offers_a_restart(core, monkeypatch):
    from labelbridge.ui.pages import settings as settings_page

    window = _window(core)
    asked, restarted = [], []
    monkeypatch.setattr(settings_page, "confirm", lambda parent, title, text: asked.append(text) or True)
    window.restart_app = lambda: restarted.append(True)
    page = window.pages["settings"]
    page.language.setCurrentIndex(page.language.findData("en"))
    page._save()
    assert core.cfg.data.general.language == "en" and len(asked) == 1 and restarted == [True]
    page._save()  # sin cambio de idioma no vuelve a preguntar
    assert len(asked) == 1
    window.quitting = True
    window.close()


def test_the_assistant_speaks_the_app_language(english):
    from labelbridge.assistant.prompt import SYSTEM_PROMPT, system_prompt

    assert system_prompt().startswith(SYSTEM_PROMPT) and "responde en inglés" in system_prompt()
    i18n.set_language("es")
    assert system_prompt() == SYSTEM_PROMPT


def test_existing_installs_stay_in_spanish(tmp_path):
    import json

    from labelbridge.config import ConfigManager

    old = tmp_path / "old.json"
    old.write_text(json.dumps({"general": {"agent_name": "PC-BODEGA"}}), encoding="utf-8")  # guardada por la 1.6
    assert ConfigManager(old).data.general.language == "es"
    new = ConfigManager(tmp_path / "new.json")  # instalación nueva: el idioma del instalador o de Windows
    assert new.data.general.language == ""
    new.save()
    assert ConfigManager(tmp_path / "new.json").data.general.language == ""


def test_dates_and_numbers_follow_the_language(english):
    from datetime import datetime

    when = datetime(2026, 10, 4, 13, 5, 9)
    assert i18n.date_text(when) == "10/04/2026 13:05"
    assert i18n.date_text(when, year=False, clock="%H:%M:%S") == "10/04 13:05:09"
    assert i18n.decimal(35.24) == "35.2"
    i18n.set_language("es")
    assert i18n.date_text(when) == "04/10/2026 13:05" and i18n.date_text(when, clock="") == "04/10/2026"
    assert i18n.decimal(35.24) == "35,2"


def test_settings_of_newer_versions_survive_a_downgrade(tmp_path):
    import json

    from labelbridge.config import ConfigManager

    path = tmp_path / "config.json"
    path.write_text(json.dumps({
        "general": {"language": "en", "ajuste_futuro": 7},
        "seccion_futura": {"a": 1},
        "shopify": {"shop": "demo.myshopify.com", "package": {"length": 9.0, "medida_futura": "x"}},
    }), encoding="utf-8")
    cfg = ConfigManager(path)
    cfg.data.general.agent_name = "PC-2"
    cfg.save()
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["general"]["ajuste_futuro"] == 7 and saved["seccion_futura"] == {"a": 1}
    assert saved["shopify"]["package"]["medida_futura"] == "x" and saved["shopify"]["package"]["length"] == 9.0
    assert saved["general"]["agent_name"] == "PC-2" and saved["general"]["language"] == "en"

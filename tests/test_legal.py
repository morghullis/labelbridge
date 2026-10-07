"""Acerca de y documentos legales: los textos en los dos idiomas, aceptar los términos al abrir (o desde el
instalador), el aviso del asistente de IA antes del primer mensaje y la pestaña «Acerca de»."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QDialog

from labelbridge import __version__, legal


@pytest.mark.parametrize("name", [legal.TERMS, legal.PRIVACY, legal.AI_NOTICE])
@pytest.mark.parametrize("lang", ["es", "en"])
def test_every_document_exists_in_both_languages(name, lang):
    text = legal.document(name, lang)
    assert text.startswith("# ") and legal.document_path(name, lang).name == f"{name}.{lang}.md"
    if name != legal.AI_NOTICE:
        assert legal.CONTACT_EMAIL in text and ("Versión 1" in text or "Version 1" in text)


def test_the_privacy_policy_says_what_goes_to_the_ai_and_what_never_does():
    for lang, never in (("es", "Nunca se envían"), ("en", "Never sent")):
        text = legal.document(legal.PRIVACY, lang)
        assert never in text and "read_reports" not in text  # en palabras de usuario, no de programador
    assert "LGPL-3.0" in legal.document(legal.THIRD_PARTY)


def test_system_info_is_for_support_without_store_data():
    info = legal.system_info()
    assert info.startswith(f"LabelBridge {__version__}") and "Python" in info


def test_terms_are_asked_once_and_the_installer_counts(core, monkeypatch):
    from labelbridge import app
    from labelbridge.ui import legal_dialog

    core.cfg.data.general.terms_accepted = 0
    assert app._terms_pending(core.cfg, {})
    assert not app._terms_pending(core.cfg, {"terms_accepted": str(legal.TERMS_VERSION)})  # aceptados al instalar
    assert core.cfg.data.general.terms_accepted == legal.TERMS_VERSION
    core.cfg.data.general.terms_accepted = 0
    monkeypatch.setattr(legal_dialog.AcceptDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert not app._accept_terms(core.cfg) and core.cfg.data.general.terms_accepted == 0
    monkeypatch.setattr(legal_dialog.AcceptDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    assert app._accept_terms(core.cfg) and not app._terms_pending(core.cfg, {})


def test_an_install_that_was_working_keeps_printing_while_terms_are_asked(core, qapp, monkeypatch):
    """Tras actualizarse sola, la PC sigue imprimiendo: los términos se piden sin frenar nada (y si no se aceptan,
    la app se cierra)."""
    from labelbridge import app
    from labelbridge.ui import legal_dialog

    shown: list = []
    original = legal_dialog.terms_dialog
    monkeypatch.setattr(legal_dialog, "terms_dialog", lambda parent=None: shown.append(original(parent)) or shown[-1])
    core.cfg.data.general.terms_accepted = 0
    quits: list[int] = []
    app._ask_terms_while_working(core.cfg, None, lambda: quits.append(1))
    assert shown and shown[0].isVisible()  # abierta, sin esperar la respuesta
    shown[0].reject()
    assert quits == [1] and core.cfg.data.general.terms_accepted == 0
    app._ask_terms_while_working(core.cfg, None, lambda: quits.append(1))
    shown[1].agree.setChecked(True)
    shown[1].accept()
    assert quits == [1] and core.cfg.data.general.terms_accepted == legal.TERMS_VERSION


def test_accepting_needs_the_checkbox(qapp):
    from labelbridge.ui.legal_dialog import terms_dialog

    dialog = terms_dialog()
    assert not dialog.accept_btn.isEnabled()
    dialog.agree.setChecked(True)
    assert dialog.accept_btn.isEnabled()


def test_the_ai_notice_comes_before_the_first_message(core, monkeypatch):
    from labelbridge.ui import legal_dialog

    core.cfg.data.assistant.ai_notice_accepted = 0
    monkeypatch.setattr(legal_dialog.AcceptDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    assert not legal_dialog.ensure_ai_notice(core.cfg)
    assert core.cfg.data.assistant.ai_notice_accepted == 0
    monkeypatch.setattr(legal_dialog.AcceptDialog, "exec", lambda self: QDialog.DialogCode.Accepted)
    assert legal_dialog.ensure_ai_notice(core.cfg)
    assert core.cfg.data.assistant.ai_notice_accepted == legal.AI_NOTICE_VERSION


def test_about_shows_the_developer_contact_and_copies_system_info(qapp):
    from PySide6.QtWidgets import QApplication, QLabel

    from labelbridge.ui.pages.about import AboutPanel

    checks: list[int] = []
    panel = AboutPanel(lambda: checks.append(1))
    texts = " ".join(widget.text() for widget in panel.findChildren(QLabel))
    assert legal.CONTACT_EMAIL in texts and __version__ in texts and legal.DEVELOPER in texts
    panel.copy_btn.click()
    assert QApplication.clipboard().text() == legal.system_info()


def test_the_installer_shows_the_same_terms_as_rtf(tmp_path, monkeypatch):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    import make_legal

    monkeypatch.setattr(make_legal, "OUT", tmp_path)
    spanish, english = make_legal.build()
    rtf = spanish.read_text(encoding="ascii")
    assert rtf.startswith("{\\rtf1") and rtf.count("{") == rtf.count("}")
    assert "T\\u233?rminos de uso" in rtf and "Pol\\u237?tica de privacidad" in rtf
    assert legal.CONTACT_EMAIL in rtf and "Terms of Use" in english.read_text(encoding="ascii")


def test_the_download_page_publishes_privacy_and_terms(tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))
    import publish_update

    assert publish_update.CONTACT_EMAIL == legal.CONTACT_EMAIL  # el script no importa la app: deben coincidir
    installer = tmp_path / f"LabelBridge-Setup-{__version__}.exe"
    installer.write_bytes(b"MZ actual")
    out = tmp_path / "site"
    publish_update.build_site(out, None, installer)
    privacy = (out / "privacidad.html").read_text(encoding="utf-8")
    assert "<h1>Política de privacidad</h1>" in privacy and "<li>" in privacy and "<b>" in privacy
    assert "Terms of Use" in (out / "terms.html").read_text(encoding="utf-8")
    page = (out / "index.html").read_text(encoding="utf-8")
    assert 'href="privacidad.html"' in page and f"mailto:{legal.CONTACT_EMAIL}" in page
    assert 'href="privacy.html"' in (out / "en.html").read_text(encoding="utf-8")

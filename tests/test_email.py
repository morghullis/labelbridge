"""Avisos por email a los dueños: solo a las direcciones de Ajustes, con tope por hora y sin repetir."""

from __future__ import annotations

import smtplib

import pytest
from conftest import wait_for

from labelbridge.connectors import email as email_mod
from labelbridge.connectors.email import EmailError, EmailSender
from labelbridge.secret_store import EMAIL_PASSWORD


class FakeSMTP:
    """Servidor SMTP falso: guarda cada correo."""

    sent: list = []
    logins: list = []
    fail_login = False

    def __init__(self, host, port, timeout=None, context=None) -> None:
        self.host, self.port = host, port
        self.tls = False

    def starttls(self, context=None) -> None:
        self.tls = True

    def login(self, user, password) -> None:
        if FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        FakeSMTP.logins.append((user, password, self.tls))

    def send_message(self, message, to_addrs=None) -> None:
        FakeSMTP.sent.append((message, list(to_addrs or [])))

    def quit(self) -> None:
        pass


@pytest.fixture()
def sender(core, monkeypatch):
    FakeSMTP.sent, FakeSMTP.logins, FakeSMTP.fail_login = [], [], False
    monkeypatch.setattr(email_mod.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(email_mod.smtplib, "SMTP_SSL", FakeSMTP)
    e = core.cfg.data.email
    e.enabled, e.username, e.recipients = True, "tienda@gmail.com", "dueno@tienda.com; socia@tienda.com"
    core.secrets.set(EMAIL_PASSWORD, "abcd efgh ijkl mnop")
    return EmailSender(core.cfg, core.secrets, core.events)


def test_email_goes_only_to_the_owners(sender, core):
    assert sender.send("Pedido #1023 necesita acción", "Compra la guía de FedEx en Shopify.") == 2
    message, to = FakeSMTP.sent[0]
    assert to == ["dueno@tienda.com", "socia@tienda.com"]
    assert message["Subject"] == "[LabelBridge] Pedido #1023 necesita acción"
    assert "tienda@gmail.com" in message["From"] and FakeSMTP.logins == [("tienda@gmail.com", "abcd efgh ijkl mnop", True)]
    assert core.cfg.data.general.agent_name in message.get_content()  # desde qué equipo


def test_email_explains_what_is_missing(sender, core):
    core.cfg.data.email.recipients = "nadie"
    with pytest.raises(EmailError, match="a quién avisar"):
        sender.send("x", "y")
    core.cfg.data.email.recipients = "dueno@tienda.com"
    core.secrets.set(EMAIL_PASSWORD, "")
    with pytest.raises(EmailError, match="contraseña"):
        sender.send("x", "y")
    core.cfg.data.email.enabled = False
    assert "desactivados" in sender.problem()


def test_wrong_password_mentions_app_passwords(sender):
    FakeSMTP.fail_login = True
    with pytest.raises(EmailError, match="contraseña de aplicación"):
        sender.send("x", "y")


def test_emails_per_hour_are_capped(sender, core):
    core.cfg.data.email.per_hour = 2
    sender.send("1", "a")
    sender.send("2", "b")
    with pytest.raises(EmailError, match="tope"):
        sender.send("3", "c")
    assert len(FakeSMTP.sent) == 2


def test_chosen_alerts_are_emailed_once(sender, core):
    core.cfg.data.email.alerts = {"action": True}
    core.events.notify("Pedido #1023: compra la guía de FedEx", "Cómprala en Shopify (1 clic).", kind="action")
    core.events.notify("Pedido #1023: compra la guía de FedEx", "Cómprala en Shopify (1 clic).", kind="action")
    core.events.notify("Pedido nuevo #1024", "María · US$120", kind="new_order")  # sin marcar «Email»
    core.events.notify("Impresoras encontradas", "Zebra")  # aviso sin tipo: nunca por email
    assert wait_for(lambda: len(FakeSMTP.sent) >= 1, 5)
    assert len(FakeSMTP.sent) == 1 and "FedEx" in FakeSMTP.sent[0][0]["Subject"]

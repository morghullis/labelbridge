"""Empaques: cada guía se compra para el empaque con que sale el paquete (sobre blanco, bolsa de nailon, caja propia o
un empaque oficial de USPS), con el servicio que ese empaque admite, según el envío que pagó el cliente."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from test_connectors import FakeShopify
from test_orders import _service, _ui_context, iso, make_order, row_of

from labelbridge.config import ConfigManager, ShippingPackage, ShippingRule
from labelbridge.connectors import packaging as pk
from labelbridge.connectors.shopify import ShopifyError

BAG = ShippingPackage("bolsa", "Bolsa de nailon", "SOFT_PACK", 10, 13, 0.5, "INCHES", 1, "OUNCES")
PRIORITY_BOX = ShippingPackage("caja-priority", "Caja Priority", "BOX", 11, 8.5, 5.5, "INCHES", 4, "OUNCES", True)


def _rules(s) -> None:
    """Como la tienda: Express sale en el sobre Flat Rate de USPS; lo demás, en la bolsa de nailon con Ground Advantage."""
    s.packages = [BAG, PRIORITY_BOX]
    s.shipping_rules = [ShippingRule("", "FedEx", False, 1000.0),
                        ShippingRule("Express", "USPS", True, 0.0, "usps:flat_rate_envelope", "")]
    s.default_package, s.default_service = "bolsa", "usps:GroundAdvantage"


# --- reglas ---------------------------------------------------------------------------------------

def test_each_order_gets_the_package_and_service_of_the_shipping_it_paid_for(core):
    s = core.cfg.data.shopify
    _rules(s)
    express = pk.for_order(s, "Express", 210)
    assert (express.package, express.service) == ("usps:flat_rate_envelope", "usps:Priority")
    assert "Priority Mail" in express.note  # «Automático» con empaque oficial: Priority, y se dice por qué
    economy = pk.for_order(s, "Economy", 100)
    assert (economy.package, economy.service, economy.note) == ("bolsa", "usps:GroundAdvantage", "")
    assert pk.custom_for(s, "bolsa") == {"type": "SOFT_PACK", "weight": {"value": 1.0, "unit": "OUNCES"},
                                         "dimensions": {"length": 10.0, "width": 13.0, "height": 0.5, "unit": "INCHES"}}
    assert pk.for_order(s, "Economy", 1500).package == ""  # la regla de FedEx (piezas caras): el de Shopify


def test_official_usps_packaging_only_goes_with_priority_mail(core):
    s = core.cfg.data.shopify
    _rules(s)
    with pytest.raises(pk.PackagingError, match="Ground Advantage"):
        pk.fit(s, "usps:flat_rate_envelope", "usps:GroundAdvantage")
    with pytest.raises(pk.PackagingError, match="Priority Mail"):
        pk.fit(s, "usps:flat_rate_envelope", "ups_shipping:03")
    with pytest.raises(pk.PackagingError):  # el sobre Flat Rate es de Priority Mail, no de Express
        pk.fit(s, "usps:flat_rate_envelope", "usps:PriorityExpress")
    # Una caja oficial de Priority sin tarifa plana: Priority o Express, por peso (empaque propio).
    assert pk.fit(s, "caja-priority", "usps:PriorityExpress").service == "usps:PriorityExpress"
    assert pk.fit(s, "caja-priority").service == "usps:Priority"
    assert pk.carrier_package("caja-priority") is None and pk.custom_for(s, "caja-priority")["type"] == "BOX"
    # Los propios van con cualquier servicio; un empaque borrado no compra nada.
    assert pk.fit(s, "bolsa", "ups_shipping:03").service == "ups_shipping:03"
    with pytest.raises(pk.PackagingError, match="ya no existe"):
        pk.fit(s, "empaque-borrado")


def test_the_old_saved_box_becomes_the_first_package(tmp_path):
    def load(shopify: dict) -> ConfigManager:
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"shopify": shopify}), encoding="utf-8")
        return ConfigManager(path)

    box = {"saved": True, "use_custom": True, "auto": True, "type": "BOX", "length": 6, "width": 4, "height": 2,
           "weight": 3}
    s = load({"package": box}).data.shopify
    assert [(p.id, p.type, p.length, p.weight) for p in s.packages] == [("mi-caja", "BOX", 6.0, 3.0)]
    assert s.default_package == "mi-caja" and pk.name(s, "mi-caja") == "Mi caja"  # también en las automáticas
    s = load({"package": {**box, "auto": False}}).data.shopify
    assert len(s.packages) == 1 and s.default_package == ""  # solo a mano: las automáticas siguen igual
    assert load({}).data.shopify.packages == []
    s = load({"package": box, "packages": []}).data.shopify
    assert s.packages == [] and s.default_package == ""  # ya migrada (y quizá borrada): no vuelve


# --- compra ---------------------------------------------------------------------------------------

class StoreWithFlatRate(FakeShopify):
    """Pedidos Express. Shopify acepta solo `accepted` como código del sobre Flat Rate: con otro, RATES_NOT_FOUND
    (no compra ni cobra nada)."""

    def __init__(self, now_iso: str, accepted: str = "flat_rate_envelope") -> None:
        super().__init__(now_iso)
        self.accepted = accepted
        self.attempts: list[dict] = []
        self.reject_all_with = ""

    def order(self, number: int, created: str, **extra) -> dict:
        extra.setdefault("shippingLine", {"title": "Express"})
        extra.setdefault("currentSubtotalPriceSet", {"shopMoney": {"amount": "210.00"}})
        extra.setdefault("shippingAddress", {"name": "Ana López", "address1": "123 Main St", "city": "Miami",
                                             "provinceCode": "FL", "zip": "33101", "countryCodeV2": "US"})
        return super().order(number, created, **extra)

    def __call__(self, method, url, **kwargs):
        body = kwargs.get("json_body") or {}
        if "LabelBridgePurchaseLabel" in body.get("query", ""):
            purchase = body["variables"]["input"]
            self.attempts.append(purchase)
            carrier = (purchase.get("packageInfo") or {}).get("carrierPackage") or {}
            code = self.reject_all_with or ("" if carrier.get("carrierPackageCode") == self.accepted
                                            else "RATES_NOT_FOUND")
            if carrier and code:
                errors = [{"field": ["input"], "code": code, "message": "Rates not found."}]
                data = {"shippingLabelPurchase": {"shippingLabelPurchaseResult": None, "userErrors": errors}}
                from labelbridge import net

                return net.Response(200, json.dumps({"data": data}).encode(), url)
        return super().__call__(method, url, **kwargs)


def test_flat_rate_codes_are_tried_without_buying_twice_and_remembered(core, monkeypatch):
    fake = StoreWithFlatRate(iso(datetime.now(timezone.utc).timestamp()), accepted="usps_flat_rate_envelope")
    service, s = _service(core, monkeypatch, fake)
    _rules(s)
    service.cycle(service.client(), s)
    tried = [a["packageInfo"]["carrierPackage"]["carrierPackageCode"] for a in fake.attempts]
    assert tried == ["flat_rate_envelope", "usps_flat_rate_envelope"]  # el primero lo rechazó Shopify sin cobrar
    assert len(fake.purchases) == 1  # una sola compra de verdad
    bought = fake.purchases[0]
    assert bought["packageInfo"]["carrierPackage"] == {"carrierCode": "usps",
                                                       "carrierPackageCode": "usps_flat_rate_envelope",
                                                       "name": "Priority Mail Flat Rate Envelope"}
    assert bought["preferredRateSelection"] == {"carrierCode": "usps", "serviceCode": "Priority"}
    assert service.ledger("3001")["state"] == "purchased"
    # La próxima compra va directo con el código que funcionó.
    fake.attempts.clear()
    service._purchase(service.client(), "gid://shopify/FulfillmentOrder/88", False, custom=None,
                      official=pk.carrier_package("usps:flat_rate_envelope"), total_weight=None,
                      preferred={"carrierCode": "usps", "serviceCode": "Priority"})
    assert [a["packageInfo"]["carrierPackage"]["carrierPackageCode"] for a in fake.attempts] == [
        "usps_flat_rate_envelope"]


def test_a_rejection_for_another_reason_is_not_retried_with_other_codes(core, monkeypatch):
    fake = StoreWithFlatRate(iso(datetime.now(timezone.utc).timestamp()))
    fake.reject_all_with = "TOTAL_WEIGHT_ZERO"
    service, s = _service(core, monkeypatch, fake)
    _rules(s)
    service.cycle(service.client(), s)
    assert len(fake.attempts) == 1 and fake.purchases == []
    entry = service.ledger("3001")
    assert entry["state"] == "failed" and "TOTAL_WEIGHT_ZERO" in entry["reason"]


def test_when_no_code_works_nothing_is_bought_and_it_says_why(core, monkeypatch):
    fake = StoreWithFlatRate(iso(datetime.now(timezone.utc).timestamp()), accepted="otro_codigo")
    service, s = _service(core, monkeypatch, fake)
    _rules(s)
    result = service.buy_label_now("3001")  # «Según tus reglas»: Express → sobre Flat Rate con Priority Mail
    assert len(fake.attempts) == len(pk.carrier_package("usps:flat_rate_envelope").codes) and fake.purchases == []
    assert result["state"] == "failed" and "Sobre Flat Rate" in result["reason"]
    assert "No se compró nada" in result["reason"]


def test_own_packages_go_as_custom_packages_and_bad_rules_buy_nothing(core, monkeypatch):
    fake = StoreWithFlatRate(iso(datetime.now(timezone.utc).timestamp()))
    service, s = _service(core, monkeypatch, fake)
    _rules(s)
    # Una regla con un empaque borrado: la compra automática no compra nada y avisa una sola vez.
    s.shipping_rules[1].package = "empaque-borrado"
    service.cycle(service.client(), s)
    service.cycle(service.client(), s)
    assert fake.purchases == [] and service.ledger("3001")["state"] == "failed"
    notices = [d for kind, d in core.received if kind == "notify" and "empaque" in d.get("message", "")]
    assert len(notices) == 1
    # Desde la app: un empaque oficial con Ground Advantage no se compra; uno propio va como customPackage.
    with pytest.raises(ShopifyError, match="Ground Advantage"):
        service.buy_label_now("3001", package="usps:flat_rate_envelope", service="usps:GroundAdvantage")
    assert fake.attempts == []
    result = service.buy_label_now("3001", package="bolsa", service="usps:GroundAdvantage")
    sent = fake.purchases[-1]
    assert result["state"] == "purchased" and sent["packageInfo"] == {"customPackage": pk.custom_for(s, "bolsa")}
    assert sent["preferredRateSelection"] == {"carrierCode": "usps", "serviceCode": "GroundAdvantage"}
    assert (result["package"], result["service"]) == ("bolsa", "usps:GroundAdvantage")


# --- pantallas ------------------------------------------------------------------------------------

def test_print_label_window_starts_with_the_rule_and_limits_services(core):
    from labelbridge.ui.purchase_dialog import CUSTOM, PurchaseDialog

    ctx = _ui_context(core)
    s = core.cfg.data.shopify
    _rules(s)
    s.shop = ""  # sin preguntar a Shopify qué ofrece
    dialog = PurchaseDialog(None, ctx, row_of(make_order(90, method="Express", subtotal=210)))
    assert dialog.package.key() == "usps:flat_rate_envelope"
    services = [dialog.services.combo.itemData(i) for i in range(dialog.services.combo.count())]
    assert dialog.services.key() == "usps:Priority" and "usps:GroundAdvantage" not in services
    assert "fedex" not in services and "auto" not in services
    dialog.package.choice.setCurrentIndex(dialog.package.choice.findData("bolsa"))
    services = [dialog.services.combo.itemData(i) for i in range(dialog.services.combo.count())]
    assert "auto" in services and "usps:GroundAdvantage" in services  # un empaque propio: todos
    assert dialog.options()["package"] == "bolsa"
    # «Otro empaque solo para esta guía…», guardado en Empaques con su nombre.
    dialog.package.choice.setCurrentIndex(dialog.package.choice.findData(CUSTOM))
    dialog.package.remember.setChecked(True)
    assert "nombre" in dialog.package.problem()
    dialog.package.remember_name.setText("Sobre blanco")
    assert dialog.package.problem() == "" and dialog.options()["package"]["type"]
    dialog.package.remember_box()
    assert [p.name for p in s.packages][-1] == "Sobre blanco"
    economy = PurchaseDialog(None, ctx, row_of(make_order(91, method="Economy", subtotal=100)))
    assert (economy.package.key(), economy.services.key()) == ("bolsa", "usps:GroundAdvantage")


def test_packages_window_adds_and_keeps_packages_in_use(core, monkeypatch):
    from labelbridge.ui import packages_dialog as pd

    ctx = _ui_context(core)
    s = core.cfg.data.shopify
    _rules(s)
    errors: list[str] = []
    monkeypatch.setattr(pd, "error", lambda _parent, _title, text: errors.append(text))
    dialog = pd.PackagesDialog(None, ctx)
    assert [row.name.text() for row in dialog.rows] == ["Bolsa de nailon", "Caja Priority"]
    dialog._remove_row(dialog.rows[0])  # la usa «Todo lo demás»: no se quita
    assert len(dialog.rows) == 2 and "reglas" in errors[-1]
    dialog._remove_row(dialog.rows[1])
    dialog._add_row(ShippingPackage(pk.new_id(), ""))
    assert dialog.problem() == "Ponle nombre a cada empaque."
    new = dialog.rows[-1]
    new.name.setText("Sobre blanco")
    new.type.setCurrentIndex(new.type.findData("ENVELOPE"))
    assert dialog.problem() == ""
    assert [(p.name, p.type) for p in dialog.packages()] == [("Bolsa de nailon", "SOFT_PACK"), ("Sobre blanco", "ENVELOPE")]


# --- Priority Mail por defecto y casos de la revisión -------------------------------------------------

def test_out_of_the_box_every_usps_label_is_priority_mail_so_it_fits_any_packaging(core, monkeypatch):
    fake = StoreWithFlatRate(iso(datetime.now(timezone.utc).timestamp()))
    fake.order = lambda number, created, **extra: FakeShopify.order(  # Economy, dentro de EE. UU.
        fake, number, created, **{"shippingLine": {"title": "Economy"},
                                  "shippingAddress": {"name": "Ana", "address1": "1 Main", "city": "Miami",
                                                      "provinceCode": "FL", "zip": "33101", "countryCodeV2": "US"},
                                  **extra})
    service, s = _service(core, monkeypatch, fake)
    assert (s.default_package, s.default_service) == ("", "usps:Priority")  # sin configurar nada
    service.cycle(service.client(), s)
    bought = fake.purchases[0]
    assert "packageInfo" not in bought  # el paquete de Shopify (el peso lo calcula Shopify)
    assert bought["preferredRateSelection"] == {"carrierCode": "usps", "serviceCode": "Priority"}


def test_abroad_a_domestic_service_from_the_rules_is_left_to_shopify(core):
    s = core.cfg.data.shopify
    _rules(s)
    s.default_service = "usps:Priority"
    abroad = pk.for_order(s, "Economy", 100, domestic=False)
    assert (abroad.package, abroad.service) == ("bolsa", "auto") and "otro país" in abroad.note
    with pytest.raises(pk.PackagingError, match="otro país"):  # el sobre Flat Rate es solo para EE. UU.
        pk.for_order(s, "Express", 210, domestic=False)


def test_upgrading_switches_usps_to_priority_and_keeps_the_old_box_everywhere(tmp_path):
    def load(shopify: dict):
        path = tmp_path / "config.json"
        path.write_text(json.dumps({"shopify": shopify}), encoding="utf-8")
        return ConfigManager(path).data.shopify

    rules = [{"method": "", "carrier": "FedEx", "auto": False, "over": 1000.0},
             {"method": "Express", "carrier": "USPS", "auto": True, "over": 0.0},
             {"method": "Overnight", "carrier": "UPS", "auto": True, "over": 0.0}]
    box = {"saved": True, "use_custom": True, "auto": True, "type": "BOX", "length": 6, "width": 4, "height": 2}
    s = load({"shipping_rules": rules, "package": box})
    assert s.default_service == "usps:Priority"
    assert [(r.carrier, r.package, r.service) for r in s.shipping_rules] == [
        ("FedEx", "mi-caja", ""), ("USPS", "mi-caja", "usps:Priority"), ("UPS", "mi-caja", "")]
    s = load({"shipping_rules": rules, "package": {**box, "auto": False}})  # Mi caja solo a mano
    assert [r.package for r in s.shipping_rules] == ["mi-caja", "", ""] and s.default_package == ""
    s = load({"shipping_rules": rules, "default_carrier": "UPS"})  # otra paquetería por defecto: no se toca
    assert s.default_service == "" and s.shipping_rules[1].service == "usps:Priority"


def test_a_rejection_that_also_started_a_purchase_is_never_retried(core, monkeypatch):
    from labelbridge import net

    fake = StoreWithFlatRate(iso(datetime.now(timezone.utc).timestamp()))
    service, s = _service(core, monkeypatch, fake)
    _rules(s)
    calls: list[str] = []

    def strange(method, url, **kwargs):
        body = kwargs.get("json_body") or {}
        if "LabelBridgePurchaseLabel" in body.get("query", ""):
            calls.append(body["variables"]["input"]["packageInfo"]["carrierPackage"]["carrierPackageCode"])
            data = {"shippingLabelPurchase": {
                "shippingLabelPurchaseResult": {"id": "gid://shopify/ShippingLabelPurchaseResult/9"},
                "userErrors": [{"field": ["input"], "code": "RATES_NOT_FOUND", "message": "?"}]}}
            return net.Response(200, json.dumps({"data": data}).encode(), url)
        return fake(method, url, **kwargs)

    monkeypatch.setattr("labelbridge.connectors.shopify.net.request", strange)
    service.cycle(service.client(), s)
    assert calls == ["flat_rate_envelope"] and service.ledger("3001")["state"] == "uncertain"
    service.cycle(service.client(), s)
    assert calls == ["flat_rate_envelope"]  # nunca se reintenta sola


def test_a_network_failure_mid_codes_stops_and_marks_it_uncertain(core, monkeypatch):
    from labelbridge import net

    fake = StoreWithFlatRate(iso(datetime.now(timezone.utc).timestamp()), accepted="otro")
    service, s = _service(core, monkeypatch, fake)
    _rules(s)

    def flaky(method, url, **kwargs):
        body = kwargs.get("json_body") or {}
        if "LabelBridgePurchaseLabel" in body.get("query", "") and len(fake.attempts) == 1:
            fake.attempts.append(body["variables"]["input"])
            return net.Response(502, b"Bad gateway", url)
        return fake(method, url, **kwargs)

    monkeypatch.setattr("labelbridge.connectors.shopify.net.request", flaky)
    service.cycle(service.client(), s)
    assert len(fake.attempts) == 2 and service.ledger("3001")["state"] == "uncertain"
    service.cycle(service.client(), s)
    assert len(fake.attempts) == 2


def test_by_rules_never_buys_an_order_your_rules_send_to_shopify(core, monkeypatch):
    fake = StoreWithFlatRate(iso(datetime.now(timezone.utc).timestamp()))
    fake.order = lambda number, created, **extra: FakeShopify.order(
        fake, number, created, **{"currentSubtotalPriceSet": {"shopMoney": {"amount": "1500.00"}}, **extra})
    service, s = _service(core, monkeypatch, fake)
    _rules(s)
    with pytest.raises(ShopifyError, match="FedEx"):
        service.buy_label_now("3001")
    assert fake.attempts == [] and fake.purchases == []


def test_print_label_window_keeps_the_limits_after_leaving_fedex(core):
    from labelbridge.connectors import shipping_services as ss
    from labelbridge.ui.purchase_dialog import CUSTOM, PurchaseDialog

    ctx = _ui_context(core)
    s = core.cfg.data.shopify
    _rules(s)
    s.shop = ""
    dialog = PurchaseDialog(None, ctx, row_of(make_order(92, method="Economy", subtotal=1500)), prefer=ss.FEDEX)
    assert dialog.services.key() == ss.FEDEX
    dialog.package.choice.setCurrentIndex(dialog.package.choice.findData("usps:flat_rate_envelope"))
    dialog.services.combo.setCurrentIndex(dialog.services.combo.findData("usps:GroundAdvantage"))
    assert dialog.services.key() == "usps:Priority"  # al dejar FedEx, el empaque oficial vuelve a mandar
    dialog.package.choice.setCurrentIndex(dialog.package.choice.findData("usps:flat_rate_small_box"))
    assert "Caja chica Flat Rate" in dialog.services.hint.text()
    # «Guardarlo en Empaques»: una vez, y queda elegido (reintentar no lo duplica).
    dialog.package.choice.setCurrentIndex(dialog.package.choice.findData(CUSTOM))
    dialog.package.remember.setChecked(True)
    dialog.package.remember_name.setText("Sobre blanco")
    dialog.package.remember_box()
    dialog.package.remember_box()
    assert [p.name for p in s.packages].count("Sobre blanco") == 1
    assert dialog.package.key() == s.packages[-1].id


def test_packages_window_checks_the_rules_being_edited(core, monkeypatch):
    from labelbridge.ui import packages_dialog as pd

    ctx = _ui_context(core)
    s = core.cfg.data.shopify
    _rules(s)
    errors: list[str] = []
    monkeypatch.setattr(pd, "error", lambda _parent, _title, text: errors.append(text))
    # Abierta desde las reglas: la bolsa ya no se usa en pantalla (aunque sí en lo guardado), la caja sí.
    uses = [("FedEx", "", ""), ("USPS", "caja-priority", "usps:GroundAdvantage"), ("USPS", "", "usps:Priority")]
    dialog = pd.PackagesDialog(None, ctx, uses)
    assert "Advantage" in dialog.problem()  # la caja es oficial de Priority y una regla la usa con Ground Advantage
    dialog.rows[1].priority.setChecked(False)
    assert dialog.problem() == ""
    dialog._remove_row(dialog.rows[1])
    assert len(dialog.rows) == 2 and errors
    dialog._remove_row(dialog.rows[0])
    assert [row.name.text() for row in dialog.rows] == ["Caja Priority"]


def test_new_usps_rules_start_with_priority_mail(core):
    from labelbridge.ui.pages import orders as orders_page

    ctx = _ui_context(core)
    s = core.cfg.data.shopify
    dialog = orders_page.ShippingRulesDialog(None, s.shipping_rules, "USPS", ctx=ctx)
    dialog._add_row(ShippingRule("", "USPS", True, 0.0, "", pk.PRIORITY))
    _o, _m, carrier, _p, service, _mo, _r = dialog.rows[-1]
    assert service.currentData() == pk.PRIORITY
    carrier.setCurrentIndex(carrier.findData("UPS"))
    assert service.currentData() == ""  # UPS: «Automático»
    carrier.setCurrentIndex(carrier.findData("USPS"))
    assert service.currentData() == pk.PRIORITY  # de vuelta a USPS: Priority Mail
    assert dialog.default_service.currentData() == pk.PRIORITY  # «Todo lo demás», de fábrica

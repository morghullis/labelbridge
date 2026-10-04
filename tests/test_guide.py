"""La guía paso a paso solo marca un paso cuando de verdad está hecho."""

from __future__ import annotations

from labelbridge.models import parse_job
from labelbridge.secret_store import SHOPIFY_CLIENT_SECRET


def _context(core):
    from labelbridge.services import Services
    from labelbridge.ui.context import AppContext, QtBridge

    core.cfg.data.local_api.enabled = False
    services = Services(core.cfg, core.secrets, core.store, core.engine, core.events)
    return AppContext(core.cfg, core.secrets, core.store, core.engine, services, core.events, QtBridge(core.events, core.cfg))


def test_guide_progress_follows_real_state(core):
    from labelbridge.ui.pages.guide import GuidePage, admin_draft_order_url, is_complete, progress

    ctx = _context(core)
    page = GuidePage(ctx, lambda _key: None)
    assert progress(ctx) == (1, 8)  # solo la impresora (el fixture trae una de archivo)

    core.store.kv_set("guide:test_printed", "1")
    core.cfg.data.labels.store_name = "Pedro Yoruba Jewelry"
    assert progress(ctx)[0] == 3

    s = core.cfg.data.shopify
    s.shop, s.client_id = "pedroyorubajewelry", "client-id"
    core.secrets.set(SHOPIFY_CLIENT_SECRET, "secreto")
    assert progress(ctx)[0] == 4

    s.enabled = True
    core.store.kv_set(ctx.services.shopify._key("cursor"), "2026-10-04T12:00:00Z")  # ya revisó pedidos
    assert progress(ctx)[0] == 5

    page._run_action("ack:what", page.cards[0].toggle)
    job, _ = core.store.add(parse_job({"id": "pedido-1", "content": "^XA^XZ"}), "shopify")
    core.store.set_done(job["id"], "archivo")
    assert progress(ctx)[0] == 6  # «Entendido» no basta: no hay nada elegido para imprimir
    s.buy_shipping_label = True  # la guía de Shopify Shipping
    assert progress(ctx)[0] == 7

    page._run_action("ack:daily", page.cards[0].toggle)
    assert is_complete(ctx)
    page.refresh()
    assert page.summary_title.text().startswith("✓")
    assert admin_draft_order_url(ctx) == "https://admin.shopify.com/store/pedroyorubajewelry/draft_orders/new"

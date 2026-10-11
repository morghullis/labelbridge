"""Pedidos en detalle, pedidos manuales e instrucciones de cada pedido (Pedidos → «Ver pedido», «Nueva guía manual» y
el asistente).

- El detalle se pide a Shopify al abrir un pedido (productos, cliente, dirección, totales, notas y envíos). Con
  read_customers dice además si el cliente ya compró antes, y su email si Shopify aprobó ese dato para la app: lo
  que la app no tiene llega vacío (o se pide sin esa parte) y el resto se muestra igual.
- Una guía manual necesita un pedido: Shopify Shipping solo vende guías de pedidos. Se crea uno marcado MANUAL_TAG,
  pagado y sin cobrar al cliente (precios en 0), con productos de la tienda o un artículo libre; la compra automática
  lo deja pasar (la guía la compra quien la pidió, con confirmación).
- Las instrucciones (empaque, regalo, cuidado…) van al final de la nota del pedido, después de NOTE_MARK: se ven en
  Shopify y en la hoja de empaque, y la nota que dejó el cliente no se toca.
Las consultas y lo que no depende de la pantalla viven aquí para poder probarlo.
"""

from __future__ import annotations

import base64
import binascii
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ..i18n import N_, tr

log = logging.getLogger(__name__)

MANUAL_TAG = "LabelBridge-manual"
NOTE_MARK = "— Instrucciones (LabelBridge) —"
MAX_INSTRUCTIONS = 2000  # caracteres: es una nota, no un documento
MAX_ITEMS = 50

ORDER_DETAIL_QUERY = """
query LabelBridgeOrderDetail($id: ID!) {
  order(id: $id) {
    id legacyResourceId name createdAt cancelledAt
    displayFinancialStatus displayFulfillmentStatus
    note tags
    shippingAddress { name company address1 address2 city province provinceCode zip country countryCodeV2 phone }
    shippingLine { title }
    customAttributes { key value }
    subtotalPriceSet { shopMoney { amount currencyCode } }
    totalShippingPriceSet { shopMoney { amount } }
    totalTaxSet { shopMoney { amount } }
    totalDiscountsSet { shopMoney { amount } }
    totalPriceSet { shopMoney { amount currencyCode } }
    lineItems(first: 100) {
      nodes {
        title variantTitle sku quantity currentQuantity requiresShipping
        originalUnitPriceSet { shopMoney { amount } }
        image { url(transform: {maxWidth: 160, maxHeight: 160}) }
        customAttributes { key value }
      }
    }
    fulfillments(first: 10) { status createdAt displayStatus trackingInfo { company number url } }
  }
}
"""

# Sin permiso de órdenes de preparación (apps solo con read_orders): todo menos el seguimiento.
ORDER_DETAIL_QUERY_BASIC = ORDER_DETAIL_QUERY.replace(
    "    fulfillments(first: 10) { status createdAt displayStatus trackingInfo { company number url } }\n", ""
)
# Con read_customers: si ya compró antes y desde cuándo es cliente; el email, si Shopify lo aprobó para la app.
CUSTOMER_FIELDS = "    email\n    customer { numberOfOrders amountSpent { amount currencyCode } createdAt }\n"
ORDER_DETAIL_QUERY_CUSTOMER = ORDER_DETAIL_QUERY.replace("    note tags\n", "    note tags\n" + CUSTOMER_FIELDS)
DETAIL_QUERIES = (ORDER_DETAIL_QUERY_CUSTOMER, ORDER_DETAIL_QUERY, ORDER_DETAIL_QUERY_BASIC)
PROTECTED_FIELDS = ("email", "customer")  # datos protegidos: si Shopify no los aprobó, llegan vacíos

# «Página del pedido» (la que se imprime con cada pedido, como «Imprimir página de pedido» de Shopify): lo de «Ver
# pedido» más a nombre de quién (la dirección de facturación, para los retiros en tienda), lo pagado y lo pendiente, y
# si se retira en la tienda (y en cuál).
PAGE_FIELDS = ("    billingAddress { name phone }\n"
               "    totalReceivedSet { shopMoney { amount } }\n"
               "    totalOutstandingSet { shopMoney { amount } }\n")
PAGE_DELIVERY = "    fulfillmentOrders(first: 5) { nodes { deliveryMethod { methodType } assignedLocation { name } } }\n"


def _page_query(detail: str, extra: str) -> str:
    """La consulta de «Ver pedido» con lo de la página (las fotos de los productos, chicas, como en la de Shopify)."""
    # En JPG: las fotos subidas desde un iPhone son HEIC, que la impresión no sabe leer.
    return (detail.replace("query LabelBridgeOrderDetail", "query LabelBridgeOrderPage")
            .replace("    note tags\n", "    note tags\n" + extra)
            .replace("image { url(transform: {maxWidth: 160, maxHeight: 160}) }",
                     "image { url(transform: {maxWidth: 300, maxHeight: 300, preferredContentType: JPG}) }"))


PHOTO_HOSTS = ("cdn.shopify.com",)  # las fotos de los productos solo se descargan de la CDN de Shopify
MAX_PHOTOS = 8  # las que caben en la hoja
MAX_PHOTO_BYTES = 300_000
PHOTO_SECONDS = 8.0  # entre todas: la página nunca hace esperar al ciclo


def photo_guard(url: str) -> None:
    """Cada salto (también las redirecciones) solo a la CDN de Shopify, por https."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "").lower() not in PHOTO_HOSTS:
        raise ValueError(f"foto fuera de la CDN de Shopify: {url[:80]}")


def attach_photos(order: dict, fetch=None, *, seconds: float = PHOTO_SECONDS) -> int:
    """Descarga la foto chica de cada producto y la deja en el pedido («photo», en base64): la página se imprime sin
    salir a internet y la foto queda con el trabajo (cifrada, como todo lo de la cola). Solo las que caben, una vez cada
    foto, en `seconds` como mucho y sin insistir si la CDN no contesta: sin foto, el producto sale igual. Devuelve
    cuántas se agregaron."""
    from .. import net

    fetch = fetch or (lambda url: net.request("GET", url, timeout=min(5.0, seconds), max_bytes=MAX_PHOTO_BYTES,
                                              url_guard=photo_guard).body)
    deadline = time.monotonic() + seconds
    cache: dict[str, str] = {}
    added = 0
    for node in ((order.get("lineItems") or {}).get("nodes") or [])[:MAX_PHOTOS]:
        url = str((node.get("image") or {}).get("url") or "")
        try:
            photo_guard(url)
        except ValueError:
            continue
        if url not in cache:
            if time.monotonic() > deadline:
                break
            try:
                cache[url] = base64.b64encode(fetch(url)).decode("ascii")
            except Exception as exc:  # noqa: BLE001  sin esa foto (ni las demás: la CDN no contesta) sale igual
                log.info("Página del pedido sin fotos (%s): %s", url[:120], exc)
                break
        node["photo"] = cache[url]
        added += 1
    return added


ORDER_PAGE_QUERY = _page_query(ORDER_DETAIL_QUERY, PAGE_FIELDS + PAGE_DELIVERY)
ORDER_PAGE_QUERY_BASIC = _page_query(ORDER_DETAIL_QUERY_BASIC, PAGE_FIELDS)
PAGE_QUERIES = (ORDER_PAGE_QUERY, ORDER_PAGE_QUERY_BASIC)
PAGE_PROTECTED = ("billingAddress",) + PROTECTED_FIELDS

ORDER_LOOKUP_QUERY = """
query LabelBridgeOrderLookup($query: String!) {
  orders(first: 1, query: $query) { nodes { id legacyResourceId name } }
}
"""

ORDER_NOTE_QUERY = """
query LabelBridgeOrderNote($id: ID!) { order(id: $id) { id name note } }
"""

ORDER_NOTE_MUTATION = """
mutation LabelBridgeOrderNote($input: OrderInput!) {
  orderUpdate(input: $input) { order { id note } userErrors { field message } }
}
"""

PRODUCTS_QUERY = """
query LabelBridgeProducts($query: String!) {
  products(first: 10, query: $query, sortKey: RELEVANCE) {
    nodes {
      id title status
      featuredMedia { preview { image { url(transform: {maxWidth: 120, maxHeight: 120}) } } }
      variants(first: 30) { nodes { id title sku price inventoryQuantity inventoryPolicy inventoryItem { tracked } } }
    }
  }
}
"""
# Sin read_inventory: los productos sin sus existencias.
PRODUCTS_QUERY_BASIC = PRODUCTS_QUERY.replace(" inventoryQuantity inventoryPolicy inventoryItem { tracked }", "")

ORDER_CREATE_MUTATION = """
mutation LabelBridgeManualOrder($order: OrderCreateOrderInput!, $options: OrderCreateOptionsInput) {
  orderCreate(order: $order, options: $options) {
    order { id legacyResourceId name }
    userErrors { field message }
  }
}
"""


# Estados del pedido como los llama Shopify en su admin.
FINANCIAL_TEXT = {
    "PAID": N_("Pagado"), "PENDING": N_("Pago pendiente"), "AUTHORIZED": N_("Pago autorizado"),
    "PARTIALLY_PAID": N_("Pagado en parte"), "PARTIALLY_REFUNDED": N_("Reembolsado en parte"),
    "REFUNDED": N_("Reembolsado"), "VOIDED": N_("Pago anulado"), "EXPIRED": N_("Pago vencido"),
}
FULFILLMENT_TEXT = {
    "UNFULFILLED": N_("No preparado"), "OPEN": N_("No preparado"), "PARTIALLY_FULFILLED": N_("Preparado en parte"),
    "FULFILLED": N_("Preparado"), "IN_PROGRESS": N_("En preparación"), "PENDING_FULFILLMENT": N_("En preparación"),
    "ON_HOLD": N_("En espera"), "SCHEDULED": N_("Programado"), "RESTOCKED": N_("Devuelto al inventario"),
    "REQUEST_DECLINED": N_("Preparación rechazada"),
}


def status_text(table: dict[str, str], code: str) -> str:
    return tr(table[code]) if code in table else code.replace("_", " ").capitalize()


# --- instrucciones en la nota del pedido -------------------------------------------------------

def split_note(note: str | None) -> tuple[str, str]:
    """(nota del cliente, instrucciones de LabelBridge)."""
    text = (note or "").replace("\r\n", "\n")
    if NOTE_MARK not in text:
        return text.strip(), ""
    customer, _mark, ours = text.partition(NOTE_MARK)
    return customer.strip(), ours.strip()


def join_note(customer: str, instructions: str) -> str:
    customer, instructions = customer.strip(), instructions.strip()
    if not instructions:
        return customer
    return f"{customer}\n\n{NOTE_MARK}\n{instructions}" if customer else f"{NOTE_MARK}\n{instructions}"


def with_instructions(note: str | None, text: str, append: bool = False) -> str:
    """La nota con las instrucciones nuevas (o agregadas a las que había); la del cliente queda igual."""
    customer, current = split_note(note)
    text = (text or "").strip()
    if append and current and text:
        text = f"{current}\n{text}"
    if len(text) > MAX_INSTRUCTIONS:
        raise ValueError(tr("Las instrucciones son demasiado largas (máximo {0} caracteres).", MAX_INSTRUCTIONS))
    return join_note(customer, text)


# --- detalle -----------------------------------------------------------------------------------

def _amount(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _money(value: Any) -> float:
    return _amount(((value or {}).get("shopMoney") or {}).get("amount"))


@dataclass
class OrderItem:
    title: str
    variant: str = ""
    sku: str = ""
    quantity: int = 0
    price: float = 0.0
    image: str = ""
    properties: list[tuple[str, str]] = field(default_factory=list)  # personalización (grabado, talla…)
    shipping: bool = True
    photo: bytes = b""  # la foto ya descargada (página del pedido impresa)


@dataclass
class OrderDetail:
    gid: str
    legacy: str
    name: str
    created: str = ""
    cancelled: bool = False
    financial: str = ""
    fulfillment: str = ""
    customer: str = ""
    email: str = ""
    phone: str = ""
    address: list[str] = field(default_factory=list)  # líneas de la dirección de envío
    method: str = ""
    customer_note: str = ""
    instructions: str = ""
    attributes: list[tuple[str, str]] = field(default_factory=list)  # mensajes de regalo y datos del carrito
    tags: list[str] = field(default_factory=list)
    items: list[OrderItem] = field(default_factory=list)
    subtotal: float = 0.0
    shipping: float = 0.0
    taxes: float = 0.0
    discounts: float = 0.0
    total: float = 0.0
    currency: str = "USD"
    tracking: list[tuple[str, str, str]] = field(default_factory=list)  # (paquetería, número, enlace)
    orders_count: int = 0  # pedidos del cliente en la tienda (0: no se sabe)
    customer_since: str = ""  # fecha ISO
    spent: float = 0.0  # lo que lleva gastado en la tienda
    buyer: str = ""  # a nombre de quién (facturación): los retiros en tienda no tienen dirección de envío
    pickup: str = ""  # se retira en la tienda: en cuál («» si se envía)
    paid: float = 0.0  # lo que pagó el cliente
    due: float = 0.0  # lo que falta pagar

    @property
    def manual(self) -> bool:
        return MANUAL_TAG.lower() in (tag.lower() for tag in self.tags)

    @property
    def units(self) -> int:
        return sum(item.quantity for item in self.items)

    def as_text(self) -> str:
        """Resumen para el asistente (sin datos que no hagan falta para atender el pedido)."""
        lines = [f"{self.name} · {self.created[:10]} · {self.financial} / {self.fulfillment}"
                 + (" · cancelado" if self.cancelled else "") + (" · manual" if self.manual else "")]
        if self.customer or self.address:
            lines.append("Envío a: " + " · ".join([self.customer] + self.address[1:] if self.customer else self.address))
        if self.method:
            lines.append(f"Método: {self.method}")
        for item in self.items:
            extra = " · ".join(f"{k}: {v}" for k, v in item.properties)
            lines.append(f"- {item.quantity} × {item.title}" + (f" ({item.variant})" if item.variant else "")
                         + (f" · SKU {item.sku}" if item.sku else "") + (f" · {extra}" if extra else ""))
        lines.append(f"Total: {self.total:.2f} {self.currency}")
        if self.customer_note:
            lines.append(f"Nota del cliente: {self.customer_note}")
        for key, value in self.attributes:
            lines.append(f"{key}: {value}")
        if self.instructions:
            lines.append(f"Instrucciones: {self.instructions}")
        for company, number, _url in self.tracking:
            lines.append(f"Seguimiento: {company} {number}".strip())
        return "\n".join(lines)


def _photo(value: Any) -> bytes:
    try:
        return base64.b64decode(str(value), validate=True) if value else b""
    except (binascii.Error, ValueError):
        return b""


def parse_detail(order: dict) -> OrderDetail:
    address = order.get("shippingAddress") or {}
    place = " ".join(part for part in (address.get("city"), address.get("provinceCode") or address.get("province"),
                                       address.get("zip")) if part)
    lines = [part for part in (address.get("name"), address.get("company"), address.get("address1"),
                               address.get("address2"), place,
                               address.get("country") or address.get("countryCodeV2")) if part]
    customer_note, instructions = split_note(order.get("note"))
    items = []
    for node in ((order.get("lineItems") or {}).get("nodes") or []):
        quantity = node.get("currentQuantity")
        quantity = node.get("quantity") if quantity is None else quantity
        items.append(OrderItem(
            title=str(node.get("title") or node.get("name") or ""),
            variant=str(node.get("variantTitle") or ""),
            sku=str(node.get("sku") or ""),
            quantity=int(quantity or 0),
            price=_money(node.get("originalUnitPriceSet")),
            image=str((node.get("image") or {}).get("url") or ""),
            properties=[(str(a.get("key")), str(a.get("value"))) for a in node.get("customAttributes") or []
                        if a.get("key") and a.get("value") is not None and not str(a.get("key")).startswith("_")],
            shipping=bool(node.get("requiresShipping", True)),
            photo=_photo(node.get("photo")),
        ))
    customer = order.get("customer") or {}
    try:
        orders_count = int(customer.get("numberOfOrders") or 0)
    except (TypeError, ValueError):
        orders_count = 0
    tracking = []
    for fulfillment in order.get("fulfillments") or []:
        for info in fulfillment.get("trackingInfo") or []:
            if info.get("number"):
                tracking.append((str(info.get("company") or ""), str(info["number"]), str(info.get("url") or "")))
    total = order.get("totalPriceSet") or {}
    pickup = ""
    for node in ((order.get("fulfillmentOrders") or {}).get("nodes") or []):
        if (node.get("deliveryMethod") or {}).get("methodType") == "PICK_UP":
            pickup = str((node.get("assignedLocation") or {}).get("name") or "") or tr("la tienda")
            break
    billing = order.get("billingAddress") or {}
    return OrderDetail(
        gid=str(order.get("id") or ""),
        legacy=str(order.get("legacyResourceId") or ""),
        name=str(order.get("name") or ""),
        created=str(order.get("createdAt") or ""),
        cancelled=bool(order.get("cancelledAt")),
        financial=str(order.get("displayFinancialStatus") or ""),
        fulfillment=str(order.get("displayFulfillmentStatus") or ""),
        customer=str(address.get("name") or ""),
        email=str(order.get("email") or ""),
        phone=str(address.get("phone") or (order.get("billingAddress") or {}).get("phone") or ""),
        address=lines,
        method=str((order.get("shippingLine") or {}).get("title") or ""),
        customer_note=customer_note,
        instructions=instructions,
        attributes=[(str(a.get("key")), str(a.get("value"))) for a in order.get("customAttributes") or []
                    if a.get("key") and not str(a.get("key")).startswith("_")],
        tags=[str(tag) for tag in order.get("tags") or []],
        items=items,
        subtotal=_money(order.get("subtotalPriceSet")),
        shipping=_money(order.get("totalShippingPriceSet")),
        taxes=_money(order.get("totalTaxSet")),
        discounts=_money(order.get("totalDiscountsSet")),
        total=_money(total),
        currency=str((total.get("shopMoney") or {}).get("currencyCode") or "USD"),
        tracking=tracking,
        orders_count=orders_count,
        customer_since=str(customer.get("createdAt") or ""),
        spent=_amount((customer.get("amountSpent") or {}).get("amount")),
        buyer=str(address.get("name") or billing.get("name") or ""),
        pickup=pickup,
        paid=_money(order.get("totalReceivedSet")),
        due=_money(order.get("totalOutstandingSet")),
    )


# --- productos (para el pedido manual) ---------------------------------------------------------

@dataclass
class ProductChoice:
    variant_id: str
    title: str  # producto · variante
    sku: str = ""
    price: float = 0.0
    image: str = ""
    stock: int | None = None  # existencias (None: sin read_inventory o sin seguimiento de inventario)
    oversell: bool = False  # se puede vender sin existencias

    @property
    def stock_text(self) -> str:
        if self.stock is None:
            return ""
        if self.stock <= 0:
            return tr("sin existencias") + (tr(" (se puede vender igual)") if self.oversell else "")
        return tr("{0} en existencia", self.stock)


def parse_products(data: dict) -> list[ProductChoice]:
    choices = []
    for product in ((data.get("products") or {}).get("nodes") or []):
        if str(product.get("status") or "ACTIVE") == "ARCHIVED":
            continue
        image = (((product.get("featuredMedia") or {}).get("preview") or {}).get("image") or {}).get("url") or ""
        for variant in ((product.get("variants") or {}).get("nodes") or []):
            name = str(product.get("title") or "")
            if variant.get("title") and variant["title"] != "Default Title":
                name += f" · {variant['title']}"
            try:
                price = float(variant.get("price") or 0)
            except (TypeError, ValueError):
                price = 0.0
            tracked = (variant.get("inventoryItem") or {}).get("tracked")
            quantity = variant.get("inventoryQuantity")
            stock = int(quantity) if tracked and isinstance(quantity, (int, float)) else None
            choices.append(ProductChoice(str(variant.get("id") or ""), name, str(variant.get("sku") or ""), price,
                                         str(image), stock, variant.get("inventoryPolicy") == "CONTINUE"))
    return choices


def search_terms(text: str) -> str:
    """Lo que escribe la persona → búsqueda de Shopify (título o SKU, sin operadores raros)."""
    words = re.findall(r"[\w\-.#]+", text or "", flags=re.UNICODE)[:6]
    return " ".join(f"{word}*" for word in words)


# --- pedido manual -----------------------------------------------------------------------------

@dataclass
class Recipient:
    name: str
    address1: str
    city: str
    zip: str
    country: str = "US"  # código ISO de dos letras
    province: str = ""  # código del estado (CA, NY…), si el país los usa
    address2: str = ""
    company: str = ""
    phone: str = ""
    email: str = ""


@dataclass
class ManualItem:
    quantity: int
    title: str = ""  # artículo libre
    variant_id: str = ""  # producto de la tienda
    weight: float = 0.0  # por unidad (artículo libre)
    weight_unit: str = "OUNCES"


def manual_problem(recipient: Recipient, items: list[ManualItem]) -> str:
    """Qué falta para crear el pedido manual («» si está todo)."""
    if not recipient.name.strip():
        return tr("Escribe el nombre del destinatario.")
    if not recipient.address1.strip() or not recipient.city.strip():
        return tr("Escribe la dirección y la ciudad del destinatario.")
    if not recipient.zip.strip():
        return tr("Escribe el código postal del destinatario.")
    if not re.fullmatch(r"[A-Za-z]{2}", recipient.country.strip()):
        return tr("El país va con su código de dos letras (US, MX, CA…).")
    if recipient.country.strip().upper() in ("US", "CA", "MX") and not recipient.province.strip():
        return tr("Escribe el estado o la provincia del destinatario (por ejemplo CA o NY).")
    if recipient.email.strip() and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", recipient.email.strip()):
        return tr("El email del destinatario no es válido.")
    if not items:
        return tr("Agrega al menos un producto o un artículo.")
    if len(items) > MAX_ITEMS:
        return tr("Demasiados artículos para un pedido manual (máximo {0}).", MAX_ITEMS)
    for item in items:
        if item.quantity < 1 or item.quantity > 999:
            return tr("Revisa las cantidades (de 1 a 999).")
        if not item.variant_id and not item.title.strip():
            return tr("Escribe qué contiene el envío.")
    return ""


def order_input(recipient: Recipient, items: list[ManualItem], note: str, currency: str = "USD") -> dict[str, Any]:
    """Lo que pide orderCreate: pagado, sin cobrar al cliente (todo en 0) y marcado como manual."""
    zero = {"shopMoney": {"amount": "0.00", "currencyCode": currency}}
    first, _space, last = recipient.name.strip().partition(" ")
    if not last:  # un solo nombre: va completo (en la guía sale «nombre apellido»)
        first, last = "", first
    address = {
        "firstName": first or None, "lastName": last, "address1": recipient.address1.strip(),
        "address2": recipient.address2.strip() or None, "city": recipient.city.strip(), "zip": recipient.zip.strip(),
        "countryCode": recipient.country.strip().upper(), "provinceCode": recipient.province.strip().upper() or None,
        "company": recipient.company.strip() or None, "phone": recipient.phone.strip() or None,
    }
    lines = []
    for item in items:
        line: dict[str, Any] = {"quantity": int(item.quantity), "requiresShipping": True, "priceSet": zero,
                                "taxable": False}
        if item.variant_id:
            line["variantId"] = item.variant_id
        else:
            line["title"] = item.title.strip()
            if item.weight > 0:
                line["weight"] = {"value": float(item.weight), "unit": item.weight_unit}
        lines.append(line)
    order: dict[str, Any] = {
        "lineItems": lines,
        "shippingAddress": {key: value for key, value in address.items() if value is not None},
        "shippingLines": [{"title": tr("Envío manual (LabelBridge)"), "priceSet": zero}],
        "financialStatus": "PAID",
        "currency": currency,
        "tags": [MANUAL_TAG],
    }
    if note.strip():
        order["note"] = join_note("", note)
    if recipient.email.strip():
        order["email"] = recipient.email.strip()
    if recipient.phone.strip():
        order["phone"] = recipient.phone.strip()
    return order


class ManualOrders:
    """Lo que hace falta de Shopify para el detalle, los pedidos manuales y las instrucciones (con un ShopifyClient)."""

    def __init__(self, client) -> None:
        self.client = client

    def resolve(self, text: str) -> str:
        """«#1023», «1023» o el número interno → gid del pedido."""
        from .shopify import ShopifyError

        text = str(text or "").strip()
        if text.startswith("gid://shopify/Order/"):
            return text
        if text.isdigit() and len(text) >= 9:  # número interno (los nombres de pedido son cortos: #1001)
            return f"gid://shopify/Order/{text}"
        name = text if text.startswith("#") else f"#{text}"
        if not re.fullmatch(r"#[\w\-]{1,30}", name):
            raise ShopifyError(tr("Pedido no válido: {0}", text))
        nodes = ((self.client.graphql(ORDER_LOOKUP_QUERY, {"query": f"name:{name}"}) or {}).get("orders") or {}).get("nodes") or []
        if not nodes:
            raise ShopifyError(tr("Shopify no encontró el pedido {0}.", name))
        return str(nodes[0]["id"])

    def detail(self, order_gid: str) -> OrderDetail:
        from .shopify import ShopifyError

        data = self.client.graphql_first(DETAIL_QUERIES, {"id": order_gid}, optional=PROTECTED_FIELDS)
        order = (data or {}).get("order")
        if not order:
            raise ShopifyError(tr("Shopify no encontró ese pedido."))
        return parse_detail(order)

    def page_order(self, order_gid: str) -> dict:
        """El pedido con todo lo que lleva su página impresa (ver PAGE_QUERIES), tal como lo da Shopify."""
        from .shopify import ShopifyError

        data = self.client.graphql_first(PAGE_QUERIES, {"id": order_gid}, optional=PAGE_PROTECTED)
        order = (data or {}).get("order")
        if not order:
            raise ShopifyError(tr("Shopify no encontró ese pedido."))
        return order

    def products(self, text: str) -> list[ProductChoice]:
        terms = search_terms(text)
        if not terms:
            return []
        return parse_products(self.client.graphql_first((PRODUCTS_QUERY, PRODUCTS_QUERY_BASIC), {"query": terms}))

    def set_instructions(self, order_gid: str, text: str, append: bool = False) -> str:
        """Escribe las instrucciones en la nota del pedido (lee la nota justo antes: lo demás queda igual)."""
        from .shopify import ShopifyError

        current = (self.client.graphql(ORDER_NOTE_QUERY, {"id": order_gid}) or {}).get("order")
        if not current:
            raise ShopifyError(tr("Shopify no encontró ese pedido."))
        try:
            note = with_instructions(current.get("note"), text, append=append)
        except ValueError as exc:
            raise ShopifyError(str(exc)) from exc
        data = self.client.graphql(ORDER_NOTE_MUTATION, {"input": {"id": order_gid, "note": note}}, mutation=True)
        errors = (data.get("orderUpdate") or {}).get("userErrors") or []
        if errors:
            raise ShopifyError("; ".join(str(e.get("message")) for e in errors))
        return split_note(note)[1]

    def create(self, recipient: Recipient, items: list[ManualItem], note: str = "", *, decrement: bool = True,
               currency: str = "USD") -> dict[str, str]:
        """Crea el pedido manual (sin avisar al cliente) y espera a que Shopify prepare su envío."""
        from .shopify import ShopifyError

        problem = manual_problem(recipient, items)
        if problem:
            raise ShopifyError(problem)
        behaviour = "DECREMENT_IGNORING_POLICY" if decrement and any(i.variant_id for i in items) else "BYPASS"
        data = self.client.graphql(
            ORDER_CREATE_MUTATION,
            {"order": order_input(recipient, items, note, currency),
             "options": {"inventoryBehaviour": behaviour, "sendReceipt": False, "sendFulfillmentReceipt": False}},
            mutation=True,
        )
        result = data.get("orderCreate") or {}
        errors = result.get("userErrors") or []
        if errors:
            raise ShopifyError(tr("Shopify no creó el pedido: {0}", "; ".join(str(e.get("message")) for e in errors)))
        order = result.get("order") or {}
        if not order.get("id"):
            raise ShopifyError(tr("Shopify no devolvió el pedido creado: revisa en Shopify antes de crear otro."),
                               uncertain=True)
        self._wait_for_fulfillment(order["id"])
        return {"gid": str(order["id"]), "legacy": str(order.get("legacyResourceId") or ""),
                "name": str(order.get("name") or "")}

    def _wait_for_fulfillment(self, order_gid: str, timeout: float = 20.0) -> None:
        """Shopify prepara la orden de envío unos segundos después de crear el pedido."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if any(fo.get("status") in ("OPEN", "IN_PROGRESS") for fo in self.client.fulfillment_orders(order_gid)):
                return
            time.sleep(1.5)

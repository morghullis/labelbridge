# Conectar LabelBridge con tu tienda Shopify

LabelBridge habla directo con la **GraphQL Admin API** de Shopify (versión `2026-10`). No necesitas n8n para el caso normal.

La etiqueta que se imprime es la **guía de envío de Shopify Shipping**: LabelBridge la compra por la API para cada pedido pagado y la imprime en tu impresora de etiquetas. Las guías compradas a mano en el admin de Shopify no se pueden descargar por la API, por eso la compra la hace LabelBridge.

> ¿Prefieres no hacerlo a mano? Abre **Asistente** en la app y escríbele tus datos: hace estos pasos por ti, pide los secretos en una ventana segura y te pide confirmación antes de activar la compra de guías.

## 1. Crear la app en el Dev Dashboard

Desde 2026 Shopify **ya no permite crear apps personalizadas desde el admin** (las que ya existen siguen funcionando). Las apps nuevas se crean en el **Dev Dashboard**:

1. Entra a <https://dev.shopify.com/dashboard> con la cuenta dueña de la tienda.
2. **Apps → Create app → Start from Dev Dashboard**. Nombre: `LabelBridge`.
3. En la versión de la app, en **Access scopes**, marca:

   | Para… | Scopes |
   |---|---|
   | Comprar e imprimir las guías de Shopify Shipping (recomendado) | `read_orders`, `write_orders`, `read_merchant_managed_fulfillment_orders`, `write_merchant_managed_fulfillment_orders` |
   | Opcionales (sin ellos lo demás funciona igual) | `read_products` (elegir productos en la guía manual), `read_inventory` (ver sus existencias), `read_shipping` (ver solo los servicios que Shopify ofrece en cada envío), `read_customers` (saber en «Ver pedido» si el cliente ya compró antes) |
   | Solo leer pedidos (etiqueta interna de LabelBridge) | `read_orders` |
   | Agregar un tag al pedido al imprimir | `write_orders` |

   El botón **Copiar permisos** de la guía (y de la pantalla Shopify, cuando falta alguno) copia las dos primeras filas completas.

4. **Datos protegidos del cliente**: la etiqueta necesita nombre, dirección y teléfono. En la configuración de la app, en *Protected customer data*, selecciona los datos de clientes y los campos **Name, Address y Phone** (y **Email** si quieres ver el email del cliente en «Ver pedido»). Para apps personalizadas (no publicadas en la App Store) no hace falta revisión de Shopify; en las apps antiguas creadas desde el admin, el acceso a estos campos depende del plan de la tienda. Sin esto, Shopify devuelve la dirección vacía o un error de acceso.
5. **Release** la versión e **instálala** en tu tienda.
6. En **App settings** copia el **Client ID** y el **Client secret**.

> La app y la tienda deben estar en la **misma organización** del Dev Dashboard. Si al probar aparece `shop_not_permitted`, revisa en *Dev Dashboard → Stores* que tu tienda aparezca en esa organización.

## 2. Configurar LabelBridge

En **Shopify** dentro de la app:

- **Tienda**: `mi-tienda.myshopify.com` (el dominio interno, no tu dominio público).
- **Autenticación**: *App del Dev Dashboard* → pega Client ID y Client secret.
  LabelBridge pide un token de acceso que dura 24 h y lo renueva solo.
- ¿Tienes una app antigua creada desde el admin con token `shpat_…`? Elige *Token de acceso fijo* y pégalo.
- Pulsa **Guardar y probar conexión**: verás el nombre de la tienda, los permisos concedidos y si falta alguno.

El secreto y los tokens se guardan en el **Administrador de credenciales de Windows**, no en archivos de texto.

## 3. Qué imprime

| Opción | Qué hace | Cuándo usarla |
|---|---|---|
| **Guía de envío de Shopify Shipping** (recomendado) | Por cada pedido nuevo, pagado y pendiente de envío llama a `shippingLabelPurchase`, espera el resultado y descarga la guía (PDF o ZPL) y, si existe, el formulario de aduana. | La etiqueta con franqueo que va en el paquete (UPS, USPS, DHL, Canada Post…). **Shopify cobra cada guía.** Se activa con confirmación. |
| **Etiqueta de pedido de LabelBridge** (opcional) | Genera una etiqueta 4×6 con remitente, destinatario, productos, código de barras y QR del pedido. | Picking/empaque, o si la paquetería pone su propia guía. **No lleva franqueo.** Desactivada por defecto desde la versión 1.2.0. |
| **Etiqueta desde metafield** | Imprime la URL que otro sistema guardó en el metafield del pedido (por defecto `custom.label_url`). Acepta una URL, una lista JSON de URLs o `{"url": "…"}`. | Etiquetas de otra paquetería creadas por n8n (plantilla 03), tu web o un ERP. |

Detalles de la compra automática:

- Usa el **paquete y la tarifa predeterminados** de *Configuración → Envío y entrega* en Shopify. Configúralos antes de activarla, junto con la dirección completa de la ubicación de envío y un método de pago para las guías.
- Si haces un pedido de prueba con la compra activa, se compra una guía real: si no la usas, anúlala desde el pedido en Shopify.
- Solo compra para *fulfillment orders* en estado `OPEN`/`IN_PROGRESS` con método de envío. **Nunca compra dos veces** para el mismo fulfillment order, aunque reinicies el PC.
- Tiene un **límite diario** configurable y te avisa si se alcanza.
- Shopify no permite comprar etiquetas de **FedEx** por API (ni pasarle firma o seguro). En *Reglas de paquetería* eliges quién la compra:
  - **Shopify Flow, automáticamente**: un flujo de tu tienda la compra en cuanto entra el pedido (Flow no ofrece firma ni seguro adicional). LabelBridge no te pide comprarla: la imprime al aparecer o, si Shopify no le entrega el PDF (Shopify solo se lo da a quien compró la guía), abre el pedido en Shopify para pulsar «Imprimir etiqueta». Si en 15 minutos no aparece, avisa y el pedido pasa a «Necesitan acción». El flujo, paso a paso (también en LabelBridge → **Cómo armar el flujo…**):
    1. Shopify → **Flow** → **Crear flujo de trabajo**.
    2. Disparador: **Fulfillment order ready to fulfill**.
    3. Condición, todas con «Y»: `fulfillmentOrder / deliveryMethod / methodType` es igual a `SHIPPING`; `fulfillmentOrder / order / displayFinancialStatus` es igual a `PAID`; `fulfillmentOrder / order / currentSubtotalPriceSet / shopMoney / amount` es mayor que el valor de tu regla de FedEx (por defecto `1000`); `fulfillmentOrder / order / tags` no incluye `LabelBridge-manual`.
    4. Acción: **Purchase shipping label**, servicio FedEx (por defecto **FedEx 2Day®**), tu paquete y el peso de los artículos, confirmación de envío el mismo día.
    5. **Activar flujo**. Si tenías otro flujo que compraba etiquetas (por ejemplo, uno que llamaba a tu web), desactívalo para no comprar dos.
  - **Tú, con la ventana «Guía FedEx»**: **Comprar guía FedEx** abre el pedido en Shopify y una ventana de LabelBridge dice qué elegir (servicio, firma, seguro por el valor de las piezas y la caja); en cuanto la compras, LabelBridge la imprime sola.
- Desde **Pedidos → Imprimir guía** eliges el servicio (USPS Ground Advantage, Priority Mail, UPS Ground…) o dejas **Automático** (el que pagó el cliente o tu paquetería preferida en Shopify). Si Shopify no ofrece ese servicio para el envío, contesta `RATES_NOT_FOUND` y **no cobra nada**: elige otro. Con `read_shipping` la lista muestra solo lo que Shopify ofrece para ese envío.
- LabelBridge solo trata pedidos que salen por paquetería con a quién y adónde mandarlos (nombre, dirección, ciudad, país, código postal y estado cuando corresponde): los de retiro en tienda, las ventas en el local y los pedidos de otros canales sin dirección (p. ej. de Etsy) no aparecen en Pedidos y nunca se les compra guía.
- **Rastrear** muestra el seguimiento en una ventana de LabelBridge con los movimientos que Shopify recibe de la paquetería.
- Con varios pedidos elegidos, **Imprimir N guías** reimprime los que ya tienen guía y compra los demás con una sola confirmación (hasta 25 por tanda).
- Actívala **en un solo equipo**, y no la combines con la plantilla `02` de n8n: cada pedido se compraría dos veces.

## 4. Filtros y funcionamiento

- Revisa los pedidos actualizados cada 30 s (configurable, mínimo 15 s).
- Al activarlo procesa solo los pedidos **creados desde ese momento**. Para reimprimir pedidos anteriores usa *Reprocesar las últimas X h*.
- *Solo pedidos pagados* incluye `PAID`, `PARTIALLY_REFUNDED` y `AUTHORIZED` (pago autorizado, captura al enviar).
- *Solo pendientes de envío*: `UNFULFILLED`, `PARTIALLY_FULFILLED`, `OPEN`, `IN_PROGRESS`, `PENDING_FULFILLMENT`.
- *Solo pedidos con el tag*: útil para imprimir solo lo que tu web o un flujo marque.
- Los pedidos cancelados nunca se imprimen.

## 5. Problemas comunes

| Mensaje | Solución |
|---|---|
| `shop_not_permitted` | La app y la tienda no están en la misma organización del Dev Dashboard. |
| `Access denied` / faltan permisos | Agrega los scopes, publica una nueva versión de la app y acepta el cambio en la tienda. |
| La etiqueta sale sin dirección | Falta el acceso a *Protected customer data* (Name, Address, Phone). |
| `RATES_NOT_FOUND`, `FULFILLMENT_ORDER_INVALID` | Revisa el paquete predeterminado, el peso de los productos y la dirección del pedido en Shopify. |
| HTTP 402 | La tienda está pausada o sin plan. |

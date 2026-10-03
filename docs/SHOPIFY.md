# Conectar LabelBridge con tu tienda Shopify

LabelBridge habla directo con la **GraphQL Admin API** de Shopify (versión `2026-10`). No necesitas n8n para el caso normal.

## 1. Crear la app en el Dev Dashboard

Desde 2026 Shopify **ya no permite crear apps personalizadas desde el admin** (las que ya existen siguen funcionando). Las apps nuevas se crean en el **Dev Dashboard**:

1. Entra a <https://dev.shopify.com/dashboard> con la cuenta dueña de la tienda.
2. **Apps → Create app → Start from Dev Dashboard**. Nombre: `LabelBridge`.
3. En la versión de la app, en **Access scopes**, marca:

   | Para… | Scopes |
   |---|---|
   | Leer pedidos (siempre) | `read_orders` |
   | Comprar etiquetas de Shopify Shipping | `write_orders`, `read_merchant_managed_fulfillment_orders`, `write_merchant_managed_fulfillment_orders` |
   | Agregar un tag al pedido al imprimir | `write_orders` |

4. **Datos protegidos del cliente**: la etiqueta necesita nombre, dirección y teléfono. En la configuración de la app, en *Protected customer data*, selecciona los datos de clientes y los campos **Name, Address y Phone**. Para apps personalizadas (no publicadas en la App Store) no hace falta revisión de Shopify; en las apps antiguas creadas desde el admin, el acceso a estos campos depende del plan de la tienda. Sin esto, Shopify devuelve la dirección vacía o un error de acceso.
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
| **Etiqueta de pedido de LabelBridge** | Genera una etiqueta 4×6 con remitente, destinatario, productos, código de barras y QR del pedido. | Picking/empaque, o si la paquetería pone su propia guía. **No lleva franqueo.** |
| **Comprar etiqueta con Shopify Shipping** | Por cada pedido nuevo, pagado y pendiente de envío llama a `shippingLabelPurchase`, espera el resultado y descarga la etiqueta (PDF o ZPL) y, si existe, el formulario de aduana. | Si envías con Shopify Shipping (UPS, USPS, DHL, Canada Post…). **Genera cargos reales.** |
| **Etiqueta desde metafield** | Imprime la URL que otro sistema guardó en el metafield del pedido (por defecto `custom.label_url`). Acepta una URL, una lista JSON de URLs o `{"url": "…"}`. | Etiquetas de otra paquetería creadas por n8n (plantilla 03), tu web o un ERP. |

Detalles de la compra automática:

- Usa el **paquete y la tarifa predeterminados** de *Configuración → Envío y entrega* en Shopify. Configúralos antes de activarla.
- Solo compra para *fulfillment orders* en estado `OPEN`/`IN_PROGRESS` con método de envío. **Nunca compra dos veces** para el mismo fulfillment order, aunque reinicies el PC.
- Tiene un **límite diario** configurable y te avisa si se alcanza.
- Shopify no permite comprar etiquetas de **FedEx** por API.
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

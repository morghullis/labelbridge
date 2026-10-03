# Protocolo de trabajos de impresión

El mismo formato sirve para la **API local**, la **cola de n8n** y los trabajos internos de Shopify.

## Trabajo

```json
{
  "id": "pedido-1001-envio",
  "title": "Pedido #1001",
  "printer": "etiquetas",
  "copies": 1,
  "format": "auto",
  "url": "https://.../etiqueta.pdf"
}
```

| Campo | Obligatorio | Descripción |
|---|---|---|
| `id` | Sí (en n8n). En la API local se genera si falta. | Identificador único. Un trabajo con un `id` repetido **no se imprime dos veces**. |
| `title` | No | Texto para el historial. |
| `printer` | No | Alias configurado en LabelBridge (`etiquetas`), nombre exacto de la impresora de Windows, `tcp://IP:9100` o `ipp://IP/ipp/print`. Vacío = impresora predeterminada. |
| `copies` | No | 1 a 50. |
| `format` | No | `auto` (detecta por contenido), `pdf`, `image` (PNG/JPG/GIF/BMP), `zpl`, `raw` (EPL, TSPL, ESC/POS…), `order`. |
| `meta` | No | Objeto libre (número de pedido, guía…). Se guarda en el historial. |

Y **exactamente una** fuente de contenido:

| Campo | Contenido |
|---|---|
| `url` | La app descarga el archivo apenas recibe el trabajo (las URLs firmadas caducan). Opcional: `headers` con cabeceras para la descarga. |
| `content_base64` | Archivo en base64 (se acepta `data:...;base64,`). Máximo 25 MB. |
| `content` | Texto plano, por ejemplo ZPL `^XA ... ^XZ`. |
| `order` | El JSON del pedido de Shopify (webhook REST o GraphQL). La app dibuja la etiqueta de pedido. |

## Cómo se imprime cada formato

| Impresora | PDF / imagen | ZPL / RAW | `order` |
|---|---|---|---|
| Windows, lenguaje *driver* | Driver de Windows (escala y rota a la etiqueta) | Spooler en modo RAW | Dibujo vectorial vía driver |
| Windows, lenguaje *ZPL* | Se convierte a ZPL (`^GFA`) y va en RAW | RAW | ZPL nativo |
| Red RAW 9100, *ZPL* | Se convierte a ZPL | Tal cual | ZPL nativo |
| Red RAW 9100, *PDF* | Tal cual | Tal cual | Se genera PDF |
| Red IPP / IPPS | PDF por IPP | No soportado | Se genera PDF |
| Archivo (pruebas) | Guarda PDF | Guarda `.zpl` | Guarda PDF |

Las páginas apaisadas se rotan solas en etiquetas verticales, y una etiqueta 4×6 dentro de una hoja carta/A4 se **recorta** automáticamente.

## API local

Base: `http://127.0.0.1:7300` (o la IP del PC si activas *Permitir conexiones desde la red*).
Autenticación: `Authorization: Bearer <clave>` o `X-Api-Key: <clave>`.

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/v1/health` | Estado (sin clave). |
| `POST` | `/api/v1/jobs` | Un trabajo, una lista o `{"jobs": [...]}`. Responde `202 {"accepted": [{"id", "external_id", "status", "duplicate"}]}`. |
| `GET` | `/api/v1/jobs?limit=50&status=failed` | Trabajos recientes. |
| `GET` | `/api/v1/jobs/{id}` | Un trabajo (por id interno o el tuyo). |
| `DELETE` | `/api/v1/jobs/{id}` | Cancela un trabajo en cola. |
| `GET` | `/api/v1/printers` | Impresoras configuradas y la predeterminada. |
| `POST` | `/api/v1/print/test?printer=etiquetas` | Etiqueta de prueba. |
| `POST` | `/api/v1/pause` · `/api/v1/resume` | Pausa o reanuda la impresión automática. |

Errores: `400` (trabajo inválido, con `{"error": "..."}`), `401` (clave), `413` (demasiado grande).

Para llamarla desde el navegador (tu web abierta en el mismo PC) agrega el origen en *Orígenes web (CORS)*.

## Canal en tiempo real (MQTT) para n8n

n8n se ejecuta solo cuando ocurre un evento y **publica** el trabajo; el PC mantiene una conexión saliente cifrada con el broker (por ejemplo EMQX Serverless) y lo recibe al instante. `canal` es el identificador del PC que configuras en la app (p. ej. `pc-bodega`).

| Tema | Quién publica | Contenido |
|---|---|---|
| `labelbridge/<canal>/jobs/<id>` | n8n, **QoS 1 + retain** | Un trabajo. El retain hace que espere si el PC está apagado; la app lo borra en cuanto lo guarda en su cola. |
| `labelbridge/<canal>/jobs` | n8n o tu sistema (QoS 1) | Un trabajo o `{"jobs": [...]}`, sin retain. |
| `labelbridge/<canal>/acks/<id>` | la app (QoS 1) | `{"id", "status": "printed" \| "failed", "error", "agent", "printer", "at"}` |
| `labelbridge/<canal>/errors` | la app (QoS 1) | Lo mismo, solo cuando falla (útil para avisarte desde n8n). |
| `labelbridge/<canal>/status` | la app (retain + last will) | `{"online": true/false, "agent", "version", "at"}` |

- `<id>` en el tema debe ser seguro para MQTT: letras, números, `-` y `_` (las plantillas lo limpian solas).
- La app se conecta con sesión persistente y QoS 1; un trabajo repetido (mismo `id`) no se imprime dos veces.
- Prueba rápida con `mosquitto_pub`:

```bash
mosquitto_pub -h TU-BROKER.emqxsl.com -p 8883 -u n8n -P 'CLAVE' -q 1 -r -t labelbridge/pc-bodega/jobs/prueba-1 -m '{"id":"prueba-1","format":"zpl","content":"^XA^FO50,50^A0N,50,50^FDHola^FS^XZ"}'
```

Las plantillas listas para importar y la guía del broker están en [`n8n/`](../n8n/README.md).

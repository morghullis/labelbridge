# Política de privacidad

**Versión 1 · vigente desde el 7 de octubre de 2026**

LabelBridge es una aplicación para Windows y Mac que imprime las guías de envío y las etiquetas de los pedidos de tu tienda. Esta política explica qué datos usa, dónde quedan y con quién se comparten.

**En resumen:** tus datos se quedan en tu equipo. Solo salen hacia los servicios que tú conectas (Shopify, tu proveedor de inteligencia artificial, tu correo, n8n) y nunca hacia el desarrollador.

## 1. Responsable

LabelBridge (el desarrollador). Contacto: tonymonzon94@gmail.com.

Como dueño de la tienda, tú eres responsable de los datos de tus clientes que tratas con LabelBridge, igual que en Shopify.

## 2. Qué datos usa la app

- **De tu tienda de Shopify:** pedidos (número, productos, totales, estado y notas), nombre y dirección de envío del destinatario, guías y seguimiento, y estadísticas de la tienda (ventas y visitas) para el reporte semanal. Solo lo que permiten los permisos que tú das a la app en Shopify.
- **De este equipo:** impresoras, configuración, historial de impresiones y registros técnicos para resolver problemas.
- **Claves de acceso:** la de Shopify, la de tu proveedor de IA y la contraseña de correo se guardan en el almacén seguro del sistema (Administrador de credenciales de Windows o Llavero de la Mac).

LabelBridge no usa cookies, no tiene analítica ni telemetría y no te pide crear una cuenta.

## 3. Dónde se guardan

En tu equipo, en la carpeta de datos de LabelBridge de tu usuario (en Windows, `%LOCALAPPDATA%\LabelBridge`; en la Mac, `~/Library/Application Support/LabelBridge`). La configuración, la cola de impresión y los archivos de etiquetas se guardan cifrados (AES-256-GCM), con la llave en el almacén seguro del sistema. Los reportes semanales se conservan 26 semanas y los registros técnicos se renuevan solos.

## 4. Con quién se comparten (solo si tú lo activas)

- **Shopify:** la app lee y actualiza tus pedidos y compra guías con los permisos que tú das. Rige la política de privacidad de Shopify.
- **Tu proveedor de inteligencia artificial** (por ejemplo Anthropic, OpenAI o Google), si activas el asistente con tu propia clave. Ver la sección 5.
- **Tu servidor de correo**, si activas los avisos por email. Los avisos se envían solo a las direcciones que tú escribes en Ajustes.
- **n8n, MQTT o la API local**, si los conectas. Reciben o envían los trabajos de impresión que tú configures.
- **La página de actualizaciones (GitHub):** cada pocas horas la app consulta si hay una versión nueva y descarga solo archivos públicos. Como cualquier sitio web, GitHub ve la dirección IP.

No vendemos datos ni los compartimos con nadie más. El desarrollador no recibe tus datos.

## 5. El asistente de inteligencia artificial

El asistente es opcional y está apagado hasta que conectas un proveedor con tu propia clave. Cuando lo usas, se envía al proveedor que elegiste:

- lo que escribes en el chat;
- los datos que el asistente consulta para responderte: pedidos (productos, totales, estado, notas, nombre y dirección de envío del destinatario), productos de la tienda, el estado de las impresoras y de la app, y los números del reporte semanal;
- si activas el control de la pantalla (solo Windows), capturas de la pantalla durante cada tarea que apruebas.

**Nunca se envían** el email ni el teléfono de tus clientes (salvo que tú los escribas en el chat), ni tus claves: la app las aparta antes de enviar cualquier mensaje.

El proveedor procesa estos datos según sus propias condiciones y su política de privacidad (por ejemplo, cuánto tiempo los guarda y si los usa para mejorar sus modelos). Revísalas antes de activarlo. Puedes apagar el asistente o borrar tu clave en cualquier momento en Ajustes → Asistente.

La inteligencia artificial puede equivocarse: revisa lo importante. Comprar guías, crear pedidos y hacer cambios siempre pide tu confirmación.

## 6. Tus derechos y los de tus clientes

Tus datos están en tu equipo: puedes revisarlos o borrarlos en cualquier momento. Para borrarlos por completo, desinstala LabelBridge y borra su carpeta de datos (sección 3). Si un cliente pide ver o borrar sus datos, atiéndelo desde Shopify; LabelBridge solo guarda copias de trabajo (pedidos impresos y guías) en tu equipo.

## 7. Menores

LabelBridge es una herramienta para negocios y no está dirigida a menores de edad.

## 8. Cambios y contacto

Si esta política cambia, la app te mostrará la versión nueva para que la aceptes antes de seguir. Para cualquier duda sobre tus datos: tonymonzon94@gmail.com.

# Novedades de LabelBridge

Cada versión publicada muestra estas notas en la ventana de actualización de la app y en la página de descargas.

## 1.4.0

- El asistente ya no depende de Claude: elige la IA que prefieras en **Asistente → Conectar IA**. Funciona con Claude (Anthropic), ChatGPT (OpenAI), Gemini (Google), OpenRouter, DeepSeek, Groq, xAI, Mistral, Together, modelos en tu propio equipo con Ollama o LM Studio, o cualquier API compatible con OpenAI.
- Con cualquier modelo se aplican los mismos permisos, el mismo filtro de secretos y los mismos límites de gasto diario y de mensajes por hora.
- La app prueba la clave antes de guardarla y elige un modelo disponible en tu cuenta. En **Ajustes → Asistente** puedes cambiar el proveedor, el modelo, la dirección del servidor, el nivel de razonamiento y el precio por millón de tokens.
- Uso eficiente con cada proveedor: aprovecha la caché del prompt y reutiliza el razonamiento del modelo entre pasos. Si un modelo no admite una opción, se adapta solo.
- Más seguridad: cada clave queda ligada a su servidor, las direcciones sin https solo se admiten en tu red local, nunca se siguen redirecciones, los enlaces del chat solo abren páginas web y las claves de IA que pegues en el chat se quitan antes de enviarlo.
- Tu configuración actual con Claude sigue funcionando igual, sin hacer nada.

## 1.3.1

- Importante: si tienes la 1.3.0 o una anterior, instala esta versión desde la página de descargas (botón «Página de descargas»). En esas versiones la actualización automática no llegaba a ejecutar el instalador; desde la 1.3.1 ya funciona sola.
- Actualizaciones arregladas: la app se cierra, se instala la versión nueva y se vuelve a abrir sola. Si la app tarda en cerrarse, el instalador espera en lugar de cancelarse.
- Si una actualización no se completa, la app te avisa al abrirse y te ofrece reintentar o descargar el instalador.
- Cada actualización deja registro (actualizacion.log e instalador.log) para revisar cualquier problema.

## 1.3.0

- Datos cifrados en el PC: la configuración, los pedidos y las etiquetas se guardan con AES-256 y la clave vive en el Administrador de credenciales de Windows (lo anterior se cifra solo al actualizar).
- Nueva capa de seguridad: toda petición de la API local, n8n o Shopify pasa por una política que limita, bloquea y registra; las descargas están protegidas contra accesos a tu red interna.
- API local blindada: límite de peticiones, bloqueo tras claves incorrectas con aviso, protección contra DNS rebinding y registro de seguridad.
- Asistente más seguro y económico: pide permiso antes de cada cambio, solo atiende temas de LabelBridge, tiene límite de gasto diario y de mensajes, y gasta menos tokens.
- El asistente ahora revisa todo con un diagnóstico, instala impresoras de red en Windows y te dice exactamente qué falta.
- Nuevas secciones Asistente y Seguridad en Ajustes para ajustar los límites sin tocar código.

## 1.2.0

- Nuevo Asistente: escríbele tus datos con tus palabras y configura LabelBridge por ti (impresora, tienda de Shopify y guías de envío), con las mismas acciones de las pantallas.
- La guía de envío de Shopify Shipping es ahora la etiqueta principal: LabelBridge la compra e imprime sola en cada pedido pagado, siempre con tu confirmación al activarla.
- Las claves y contraseñas se piden en una ventana segura y nunca se envían al asistente; lo que pegues en el chat se guarda solo en este PC.
- Pantalla Shopify reordenada y guía actualizada con capturas del asistente y de Shopify Shipping.

## 1.1.0

- Actualizaciones automáticas: la app avisa cuando hay una versión nueva, la descarga verificada y se actualiza sola con un clic.
- Página de descargas pública con la última versión para instalar en cualquier PC.
- Guía paso a paso con capturas que señalan exactamente dónde hacer clic.
- Sección «Actualizaciones» en Ajustes y opción «Buscar actualizaciones» en la bandeja.

## 1.0.2

- Guía paso a paso integrada: lleva de la mano por toda la configuración y marca cada paso cuando queda listo.

## 1.0.1

- Icono de Pedro Yoruba Jewelry en la app, el instalador y la bandeja.
- Diálogos en español e instalador más liviano.

## 1.0.0

- Primera versión: impresoras por driver, IP o Wi‑Fi, Shopify directo, n8n en tiempo real y cola con reintentos.

# Novedades de LabelBridge

Cada versión publicada muestra estas notas en la ventana de actualización de la app y en la página de descargas.

## 1.9.1

- **Windows: el instalador ya no pide permiso de administrador.** Siempre instala LabelBridge solo para tu usuario, sin la pregunta «para mí / para todos».
- Si en algún PC la instalaste «para todos los usuarios», se sigue actualizando esa misma copia (Windows pide permiso de administrador en cada actualización). Para que no lo pida más, desinstálala una vez y vuelve a instalarla: conserva tu configuración.

## 1.9.0

- **LabelBridge para Mac.** Nueva versión para Mac con Apple Silicon (M1 o más nueva) y macOS 15 o más nueva: en la página de descargas pulsa **Descargar para Mac**, abre el .dmg y arrastra LabelBridge a Aplicaciones. Hace lo mismo que en Windows: imprime los pedidos de Shopify, compra guías, avisa con sonidos y trae el asistente y la guía paso a paso.
- En la Mac vive en la **barra de menús** (arriba a la derecha): al cerrar la ventana sigue imprimiendo, se abre sola al iniciar sesión y la ventana vuelve desde su icono o desde el Dock.
- Imprime en las impresoras de la Mac (USB o instaladas en Ajustes del Sistema) y en las de red o Wi‑Fi sin driver; las claves se guardan en el **Llavero** de la Mac.
- Las actualizaciones también llegan solas a la Mac: la app avisa y se actualiza con un clic, conservando tu configuración y los permisos que ya diste.
- La primera vez, como la app todavía no está firmada por Apple, la Mac pide confirmarla: **Ajustes del Sistema → Privacidad y seguridad → Abrir igualmente** (solo una vez).
- La página de descargas ofrece Windows y Mac (primero el de tu equipo) y cada versión con sus dos descargas.
- Si «Salir» se cancela (por ejemplo, con una compra de guía en curso), la app ya no se cierra sola a los 20 segundos.

## 1.8.1

- **Revisión completa del código** (arquitectura, errores, seguridad, pantallas y pruebas) y corrección de todo lo importante que encontró:
- **Compras de guías más seguras.** «Comprar guía…» ya no puede comprar a la vez que la compra automática (en un caso raro se podían pagar dos guías del mismo pedido). Una compra sin confirmar nunca se repite, una que Shopify rechazó sí se puede volver a comprar, y una compra pendiente que da error ya no frena la impresión de las demás.
- Si Windows está en **modo oscuro**, la app sigue en claro: antes tablas, menús y listas podían quedar con texto blanco sobre blanco.
- Las confirmaciones importantes tienen **«No»** como respuesta predeterminada (Enter no compra ni borra nada), «Reprocesar» con la compra activa pide confirmación y la ventana del asistente tiene «No permitir» como predeterminado.
- **Seguridad:** el acceso a Shopify solo se usa en los trabajos de Shopify; un trabajo de la API local o de n8n solo puede imprimir en impresoras de la red local; una página web ya no puede bloquear ni usar la API local; las actualizaciones sin su huella SHA-256 no se instalan; el asistente pide confirmación (con todos los valores) para cambiar el máximo de guías por día, comprar pedidos sin pagar, avisar a clientes o mover n8n a otro servidor, y solo agrega impresoras de la red local.
- Pedidos carga hasta **1000 pedidos** del período (antes 200) y avisa si quedan más.
- Pedidos funciona también en inglés con apps de Shopify con pocos permisos, un trabajo cancelado ya no queda «Imprimiendo…», las fechas de entrega estimadas ya no avisan de retraso un día antes y «Reprocesar» durante una revisión ya no se pierde.
- Los ajustes que agrega una versión nueva se conservan aunque vuelvas a una anterior y actualices otra vez.
- Más textos traducidos al inglés, ventanas que liberan memoria al cerrarse, la guía carga sus capturas al abrir cada paso y los sonidos ya no traban la ventana.
- Compilación más segura: se detiene ante cualquier error, comprueba la versión antes de publicar, no publica dos versiones a la vez y vuelve a correr la prueba de actualización en Windows.

## 1.8.0

- **Comprar guía desde LabelBridge.** Cualquier pedido que necesite a una persona (Shopify rechazó la compra, se llegó al límite diario, es de antes de conectar la app…) tiene el botón **Comprar guía…** en **Pedidos**: eliges el paquete (el de Shopify o tu caja con sus medidas, que se puede recordar), el peso si hace falta y si avisar al cliente, pulsas **Confirmar y comprar** y LabelBridge la compra con Shopify Shipping, la imprime y Shopify deja el pedido preparado con su número de seguimiento.
- Los motivos de Shopify se explican en palabras claras («Falta el peso del paquete», «Falta el teléfono completo del cliente»…).
- FedEx: Shopify no deja que ninguna app compre sus guías, así que sigue siendo un clic en Shopify (y se imprime sola) o, si prefieres, se compra desde aquí con la paquetería que elija Shopify.
- **Notificaciones y sonidos** (Ajustes): para cada cosa (pedido nuevo, pedido que necesita acción, retraso de entrega, guía impresa, error de impresión) eliges si sale el aviso y con qué sonido: caja registradora, campanita, ding, campana, arpa, burbuja, alerta, el sonido de Windows o tu propio archivo .wav. Con volumen, botón para probar cada sonido, horario de silencio y recordatorio de los pedidos que siguen esperando.
- Al entrar un pedido pagado, LabelBridge avisa con su sonido: quién, cuánto y por qué paquetería sale.
- **Alertas de entrega.** Nueva sección **Retrasos y entregas** en Pedidos (y su contador arriba): entregas atrasadas, intentos de entrega fallidos, envíos con problemas, paquetes que la paquetería todavía no recibió días después de comprar la guía y envíos que llevan demasiado tiempo en camino, con botones para rastrear, copiar el seguimiento o abrir el pedido. Se revisan solas cada media hora y cada una avisa una vez.
- La tabla de Pedidos muestra cómo va cada envío («En camino · llega el…», «Entregado el…») y los pedidos sin enviar marcan cuántos días llevan esperando.
- La página de descargas ofrece **todas las versiones desde la 1.5**, cada una con sus novedades, por si necesitas volver a una anterior.

## 1.7.1

- **Guías de FedEx: un clic en Shopify y LabelBridge la imprime sola.** Shopify no deja que ninguna app compre guías de FedEx automáticamente, así que los pedidos que salen por FedEx (piezas de más de US$1000) aparecen en **Pedidos → Necesitan acción** con el botón **Comprar en Shopify**. En cuanto compras la guía en el pedido, LabelBridge la detecta, la trae de Shopify y la imprime a 4 × 6 in en tu impresora de guías: sin descargarla ni imprimirla a mano.
- Lo mismo con cualquier pedido que esperaba a una persona (compra rechazada, límite diario…): si su guía se compra en Shopify, se imprime sola.
- **Reimprimir guía** (Pedidos) también funciona con las guías compradas en Shopify: LabelBridge trae el PDF de ahí.

## 1.7.0

- **LabelBridge en español o en inglés.** Toda la app está en los dos idiomas: pestañas, ventanas, avisos, la guía paso a paso con sus capturas, la etiqueta de prueba y las notas de cada actualización. El asistente responde en el idioma de la app.
- **Instalador nuevo, con la marca**: el logo dorado en cada paso (nítido en pantallas de alta resolución) y, al empezar, **English / Español**. La app queda en el idioma que elijas.
- **Tu tienda desde el instalador**: la primera vez, el instalador pregunta la dirección de tu tienda de Shopify y el Client ID para adelantar. Al terminar, LabelBridge se abre directo en «Conecta tu tienda» para pegar el Client secret en su ventana segura (la clave secreta nunca pasa por el instalador). Si ya tenías una tienda configurada, no se toca.
- Nuevo ajuste **Ajustes → General → Idioma / Language**: Automático (el del instalador o el de Windows), Español o English. Al cambiarlo, LabelBridge ofrece reiniciarse para aplicarlo.
- Si ya usabas LabelBridge, sigue en español después de actualizar, aunque Windows esté en inglés.
- Las fechas y los números siguen el formato de cada idioma (día/mes en español, mes/día en inglés).
- La página de descargas también está en inglés.

## 1.6.0

- Nueva pestaña **Pedidos**: todos tus pedidos de Shopify, ordenados.
  - Arriba, el resumen del período: pedidos, gasto en guías, promedio por guía y gasto por paquetería.
  - Primero, **Necesitan acción**, cada uno con el porqué y el botón para resolverlo: comprar a mano la guía de FedEx, reimprimir una guía que no salió, reintentar una compra que Shopify rechazó (con su motivo), anular la guía de un pedido cancelado o revisar pedidos que Shopify marcó como preparados sin guía.
  - Después, la tabla por paquetería (USPS, FedEx, otras y retiro en tienda): cliente, destino, método de envío, valor de las piezas, número de seguimiento, costo de cada guía (también las compradas a mano en Shopify y las anuladas) y si ya se imprimió. Con búsqueda, rastreo con un clic y exportación a Excel (CSV).
  - En la barra lateral, un aviso con el número de pedidos que necesitan acción.
- **Reglas de paquetería** (Pedidos → Reglas de paquetería). Por defecto, los pedidos con piezas de más de US$1000 salen por FedEx y su guía se compra a mano; todo lo demás sale por USPS y LabelBridge compra la guía sola. Ya no se compra una guía USPS por error para un pedido de FedEx.
- Si Shopify marca los pedidos como preparados apenas se pagan (así LabelBridge no puede comprar la guía), la pestaña Pedidos lo detecta y explica cómo cambiarlo.

## 1.5.3

- La guía de envío de Shopify Shipping sale siempre a 4 × 6 in reales, en cualquier papel: en un rollo de etiquetas 4×6 lo llena y en una hoja carta o A4 va arriba y centrada para recortarla, sin agrandarse. Si Shopify la entrega en formato carta, se toma solo la guía, a tamaño real.
- Nuevo ajuste en **Shopify → Qué imprimir → Tamaño**: 4 × 6 in (predeterminado), 100 × 150 mm o «Ajustar a la hoja». El asistente también puede cambiarlo.
- En impresoras de red por IPP la guía se envía pidiendo tamaño real (sin «ajustar a la página»).

## 1.5.2

- La máscara del asistente es el logo original tal cual (oro sobre negro, con su corona de seis gemas) y cobra vida encima: parpadea, mueve las pupilas hacia donde está el cursor, entrecierra o sonríe con los párpados, abre la boca al responder y enciende las gemas de la corona.
- El asistente ahora es un bubble flotante: un botón dorado con la máscara de la marca, abajo a la derecha de todas las pantallas. Ábrelo cuando quieras sin salir de lo que estás haciendo (también desde la pestaña «Asistente» o con F1) y minimízalo con Esc. Tus pestañas siguen igual: «Asistente» abre el mismo chat encima de la pantalla en la que estás y se marca con un borde dorado mientras está abierto.
- La máscara está viva: parpadea, te sigue con la mirada y cambia de expresión según la conversación. Escucha mientras escribes, piensa (la corona se ilumina como un indicador de carga), trabaja mientras usa las herramientas, habla al responder, se alegra cuando algo queda listo, inclina la cabeza cuando te pregunta algo o pide permiso, se preocupa si hay un error y se duerme si no la usas.
- Si cerraste el chat y llega una respuesta, el bubble salta y muestra un aviso con el número de mensajes nuevos.
- Chat rediseñado para el panel: cabecera oscura con la máscara y el estado («Pensando…», «Agregando la impresora…»), avatares dorados y botones más claros.
- Accesible: se llega con Tab, se abre con Enter o F1, y si en Windows activaste «menos animaciones», la máscara cambia de cara sin saltos ni balanceos. Con la ventana oculta o minimizada no gasta nada.
- Guía actualizada con las capturas del nuevo asistente.

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

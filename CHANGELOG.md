# Novedades de LabelBridge

Cada versión publicada muestra estas notas en la ventana de actualización de la app y en la página de descargas.

## 1.17.0

- **Reporte semanal de la tienda** (pestaña nueva **Reporte**, en Operación). Cada lunes a las 8 (hora de la tienda) LabelBridge prepara el reporte de la semana que terminó y te avisa:
  - Los números de la semana contra la anterior y el promedio de 4 semanas: ventas, pedidos, ticket promedio, clientes que vuelven, preparados en 2 días, reembolsos, descuentos, lo cobrado de envío contra lo que costaron las guías y entregas atrasadas, más las ventas por día.
  - **Lo que hay que arreglar**, con pasos concretos, **lo que va bien** y el **plan para esta semana**.
  - **Comparación con el sector**: tu tienda contra promedios públicos de comercio electrónico y joyería (Dynamic Yield, NRF, Baymard, Little Stream), cada uno con su fuente y marcado como estimación.
  - Con pocos pedidos no saca conclusiones de los porcentajes (y lo dice), y una semana en curso se compara con los mismos días de las anteriores.
- **El asistente lee el reporte** y te lo explica: pregúntale «¿cómo va la tienda?» o «¿qué mejoro esta semana?».
- Puedes ver semanas anteriores (quedan guardadas en el equipo) o recalcularlo con **Actualizar**. Todo queda en la app: no se envía por email.

## 1.16.0

- **La etiqueta del pedido sale por otra impresora.** En Shopify → **Por dónde sale cada cosa** eliges la impresora de la guía, la de la etiqueta del pedido (la que sale primero, con el número de pedido, los productos y la dirección) y la de las guías de un metafield, cada una con **Imprimir prueba**. Si la etiqueta del pedido y la guía salen por la misma impresora te avisa (aunque tengan nombres distintos) y, si no tienes otra, te lleva a agregarla. En una impresora de oficina con hoja carta, la etiqueta del pedido sale como hoja de empaque. El asistente también lo ve y lo cambia.
- **Diseño nuevo, sobrio y limpio:** superficies blancas sin cajas con borde, avisos con una línea de color, la pestaña activa marcada con una línea dorada y el estado de la impresión (abajo a la izquierda) más claro: un punto de color que late suave solo mientras imprime. Las casillas marcadas llevan su palomita y las animaciones son cortas y respetan «menos animaciones» de Windows.
- **Ventana chica:** el menú pasa a una barra abajo (Inicio, Pedidos, Impresoras, Historial y **Más**) y ninguna pantalla se sale por los lados: filtros, botones, formularios y capturas de la guía se acomodan al ancho.
- **Ningún error en silencio:** si algo falla, queda en el Registro con su detalle y te enteras, con un aviso junto al reloj y otro arriba de la ventana con **Ver registro** (también si no hay bandeja del sistema).
  - La impresión y las conexiones con Shopify y n8n se reponen solas de un error inesperado; mientras tanto el estado dice **Problema al imprimir**.
  - Una etiqueta que Windows rechazó ya no se marca como impresa.
  - Si la app no puede abrir o su configuración se dañó, lo dice (y guarda una copia).
  - También avisa cuando n8n manda un trabajo inválido, cuando un metafield no trae etiqueta, cuando no se pudo poner el tag del pedido o cuando los avisos por email no pueden salir.

## 1.15.0

- **Reimprimir nunca cobra otra guía.** Si un pedido ya tiene guía, el botón dice **Reimprimir guía**: imprime la misma (la copia guardada en el equipo o, si ya no está, la que tiene Shopify) y no compra ni paga otra.
- **Clic derecho en toda la app:** en los pedidos (la tabla y las tarjetas de «Necesitan acción» y «Retrasos»), en Historial (vista previa, reimprimir, reintentar, cancelar, copiar el seguimiento, abrir el pedido en Shopify), en Impresoras (editar, predeterminada, prueba, eliminar; agregar las detectadas o instalarlas en Windows) y en «Lo último» de Inicio.
- **El asistente puede configurar la PC** (Ajustes → Asistente → **Acceso a la PC**, solo Windows y apagado de fábrica):
  - **Herramientas de Windows:** revisa cómo está la PC, la cola de impresión de Windows y si la impresora responde en la red; vacía colas atascadas, reinicia el servicio de impresión, cambia la impresora predeterminada de Windows o la suspensión, abre la Configuración de Windows e imprime la página de prueba de Windows.
  - **Control de la pantalla (con Claude):** para lo que no tiene herramienta (el instalador de un driver, la página de la impresora), ve la pantalla y usa el mouse y el teclado. Primero pide permiso para la tarea; después, **cada acción** te muestra una ventana con el punto marcado o el texto exacto, y la puedes aprobar, rechazar o **detener todo**. LabelBridge se minimiza mientras tanto y arriba queda una barra con **Detener**.
  - Siempre con tu permiso: lo de administrador abre además el permiso de Windows, que solo tú puedes aceptar. El asistente nunca usa las ventanas de LabelBridge con el mouse ni el teclado, no escribe contraseñas y no puede cambiar estos ajustes.

## 1.14.0

- **La FedEx la compra Shopify Flow, sola.** En Pedidos → **Reglas de paquetería** eliges quién compra la FedEx: **Shopify Flow, automáticamente** o tú, con la ventana «Guía FedEx» (como hasta ahora). Con Flow, un flujo de tu tienda compra la guía FedEx en cuanto entra el pedido y LabelBridge hace el resto: no te pide que la compres, la imprime en cuanto aparece y, si Shopify no le da el PDF, abre el pedido en Shopify para que pulses «Imprimir etiqueta» (y deja de pedirlo cuando Shopify la marca impresa). Si en 15 minutos Flow no la compró, te avisa y el pedido pasa a «Necesitan acción» con **Comprar guía FedEx**.
- **Cómo armar el flujo, paso a paso:** al elegir Shopify Flow, LabelBridge te muestra el disparador, las condiciones (con el valor de tu regla de FedEx y botones para copiar) y la acción, con un botón para abrir Flow. Flow no ofrece firma ni seguro adicional: si los necesitas, sigue con la ventana «Guía FedEx».
- Mientras Flow compra una guía, ese pedido no ofrece botones para comprar otra (ni en **Imprimir N guías**, ni con el asistente), así nunca se pagan dos. Con Flow, la regla de FedEx va antes que las demás (como en el flujo), solo cuenta en pedidos pagados del todo y, si el pedido está retenido en Shopify, Flow la compra al liberarlo.

## 1.13.0

- **Funciona siempre.** Por defecto, el equipo no se duerme solo mientras LabelBridge está abierto (la pantalla sí se apaga), así que sigue imprimiendo con la pantalla apagada. Si alguien lo suspende a mano o cierra la tapa, al despertar se pone al día enseguida. Se abre sola al encender el equipo y, en la Mac, si se cierra por un error se vuelve a abrir.
- **Se actualiza sola** (Ajustes → General → Actualizaciones, encendido por defecto): descarga y verifica la versión nueva y la instala cuando no está imprimiendo ni comprando guías; al abrirse muestra las novedades. Si una versión no se instala, avisa como antes y decides tú.
- **Pedidos procesados**: un filtro **Todos / Pendientes / Procesados / Retrasos** (procesado = «Preparado» en Shopify; por defecto, todos, como siempre). En **Más** o con clic derecho: **Marcar preparado en Shopify** (sin guía, para lo que enviaste por otra vía; también varios a la vez) o **Cancelar preparación** (vuelve a Pendientes; si tiene guía activa, te explica que se anula en Shopify y Shopify devuelve el dinero).
- **Diseño más organizado:**
  - Menú lateral por secciones (Operación, Tienda y conexiones, Ayuda), con las mismas pestañas.
  - **Inicio** como resumen del día: pedidos de hoy, guías impresas, lo que espera y la impresora, más las conexiones y lo último que pasó.
  - **Pedidos** más limpio: una franja con los números, tarjetas de «Necesitan acción» en dos líneas y la paquetería en una lista.
  - **Ajustes** por pestañas (General, Avisos, Email, Etiqueta, Asistente, Seguridad), con **Guardar ajustes** siempre arriba.

## 1.12.0

- **Guía FedEx, guiada y con pocos clics.** Shopify no deja que ninguna app compre FedEx ni le pase la firma o el seguro, así que la guía se compra en Shopify, pero LabelBridge hace todo lo demás: con un clic abre el pedido en Shopify y muestra una tarjeta encima del navegador con lo que hay que elegir, en el orden de la pantalla de Shopify (FedEx 2Day, firma requerida, el seguro por el valor de las piezas con botón para copiarlo y tu caja). En cuanto la compras, LabelBridge la detecta, la imprime sola y lo confirma con el número de FedEx; el pedido queda preparado en Shopify.
- En Pedidos, los pedidos de FedEx tienen **Comprar guía FedEx** (y **Imprimir guía** abre lo mismo); **Otra paquetería…** sigue disponible.
- En **Reglas de paquetería** eliges cómo se compra FedEx: el servicio, la firma (requerida, de adulto o sin firma) y si se asegura por el valor de las piezas.

## 1.11.0

- **Imprimir guía, con un botón.** En Pedidos eliges un pedido y pulsas **Imprimir guía**: si ya tiene guía la reimprime; si no, abre una ventana corta para comprarla e imprimirla con un clic. Los pedidos sin guía (retiro en tienda, cancelados, ya enviados) dicen por qué.
- **Elige la paquetería y el servicio:** USPS Ground Advantage, Priority Mail o Priority Mail Express; UPS Ground, 3 Day Select, 2nd Day Air o Next Day Air; y DHL Express para el extranjero. O **Automático**, que usa el servicio que pagó el cliente o tu paquetería preferida en Shopify. Si Shopify no ofrece ese servicio para el envío, avisa y no cobra nada.
- **FedEx desde la misma lista:** como Shopify no deja que las apps la compren, al elegirla se abre el pedido en Shopify (1 clic para comprarla) y LabelBridge la imprime sola en cuanto la ve.
- **Varios pedidos a la vez:** eliges varios en la tabla (Ctrl o Mayús + clic) y pulsas **Imprimir N guías**. Una sola confirmación: las que ya tienen guía se reimprimen, las demás se compran con el servicio y la caja que elijas, y ves el resultado de cada una y el total gastado (hasta 25 compras por tanda; se puede detener).
- **Solo pedidos que se envían.** En LabelBridge están solo los pedidos que salen por paquetería con a quién y adónde mandarlos (nombre, dirección, ciudad, país, código postal y estado cuando corresponde). Los de retiro en tienda, las ventas en el local, los cancelados y los de otros canales sin dirección no aparecen, y nunca se compra una guía sin dirección: ni sola, ni con **Imprimir guía**, ni el asistente.
- **Rastrear en una ventana de LabelBridge**, sin ir a la web: el estado del envío, cuándo llega, los pasos (guía creada → recogido → en camino → en reparto → entregado) y cada movimiento que informó la paquetería, con fecha y lugar.
- Botones más compactos en Pedidos: **Imprimir guía**, **Ver pedido** y **Más** (abrir en Shopify, rastrear, copiar el seguimiento). La caja se elige en una lista: el paquete de Shopify, **Mi caja** (la que guardaste) u otra.
- **Permisos nuevos opcionales** (agrégalos con **Copiar permisos** en el Dev Dashboard): `read_shipping` muestra solo los servicios que Shopify ofrece para cada envío, `read_inventory` muestra las existencias en la guía manual y `read_customers` dice en **Ver pedido** si el cliente ya compró antes (y su email, si marcas **Email** en *Protected customer data*). Sin ellos todo lo demás funciona igual, y la pantalla Shopify dice cuáles faltan.
- El asistente también puede elegir el servicio al comprar una guía (siempre con tu confirmación) y consultar qué servicios ofrece Shopify para un pedido.

## 1.10.0

- **Ver pedido.** En Pedidos, doble clic (o **Ver pedido**) muestra qué tiene cada pedido sin salir de la app: productos con su foto, variante, SKU y personalización (grabados, tallas), dirección y método de envío, totales, la nota del cliente, el mensaje de regalo y el seguimiento.
- **Instrucciones en cada pedido** (empaque, regalo, firma…): se escriben en **Ver pedido** o las deja el asistente, y van a la nota del pedido en Shopify (se ven allí y en la hoja de empaque) sin tocar la nota del cliente.
- **Nueva guía manual.** Para un envío que no vino de la tienda en línea (un reemplazo, una venta por mensaje): escribes a quién va y qué lleva (productos de la tienda o un artículo libre), LabelBridge crea en Shopify un pedido marcado «Manual · LabelBridge», pagado y sin cobrar al cliente, y después eliges la caja y se compra e imprime su guía. La compra automática no toca estos pedidos.
- **Avisos por email** a los dueños desde tu correo (Gmail, iCloud u otro): eliges qué avisos llegan también por email (columna **Email** en Notificaciones), con botón para enviar una prueba y tope por hora. Solo se escribe a las direcciones de Ajustes.
- **El asistente trabaja con los pedidos:** te dice qué tiene cada uno, escribe sus instrucciones y avisa a los dueños por email sin pedir permiso (con tope), y crea pedidos manuales y compra guías siempre con tu confirmación.
- **Reglas del dueño** (Ajustes → Asistente): instrucciones permanentes que el asistente sigue siempre, por ejemplo «los pedidos de más de $500 van con firma». No pueden saltarse confirmaciones ni límites de seguridad.
- **Instalador:** también pide, si quieres, el Client secret de tu app de Shopify; al abrirse, la app lo guarda en el almacén seguro, prueba la conexión y deja la tienda activa. La primera conexión que funciona activa la tienda también desde la app (y en la Mac).
- **Copiar permisos** incluye read_products, para elegir productos de la tienda en la guía manual.

## 1.9.1

- **Windows: el instalador ya no pide permiso de administrador.** Siempre instala LabelBridge solo para tu usuario, sin la pregunta «para mí / para todos».
- Si en algún PC la instalaste «para todos los usuarios» (que pedía permiso de administrador en cada actualización), al actualizar se pasa sola a «solo para ti»: Windows pide permiso una última vez y después ya no. Tu configuración, tus pedidos y tus impresoras se conservan.

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

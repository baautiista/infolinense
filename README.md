# InfoLinense Desk 2.4 — despliegue

El backend FastAPI corre en Railway y el panel editorial de Lovable consume la misma API. El flujo gráfico usa exclusivamente la plantilla de marca de Canva `EAHWTjWEEnA`; la imagen final procede de su exportación PNG.

## Antes de desplegar

- Railway: cuenta y proyecto con un servicio Docker para este directorio.
- Una contraseña nueva y larga para `ADMIN_PASSWORD` y una cadena aleatoria distinta para `JWT_SECRET` (por ejemplo, `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`). No uses los valores de ejemplo.
- Sin claves de IA, la aplicación prepara un borrador breve desde la fuente original, sin contraste independiente. Con `ANTHROPIC_API_KEY`, usa Claude Sonnet 5.5 para redactar al pulsar «Redactar»; si también existe una clave OpenAI, Claude tiene prioridad con `AI_PROVIDER=auto`. La clave se guarda en Railway y el uso de la API se factura aparte de Claude Pro. Claude no realiza búsquedas web por defecto; estas solo se activan poniendo `ANTHROPIC_WEB_SEARCH=true` y pueden sumar coste. Revisa fechas, cifras y fuentes antes de publicar.
- El proyecto de Lovable se crea **desde el prompt**, porque Lovable no importa repositorios existentes. `lovable/PROMPT_LOVABLE.md` describe el frontend y `lovable/API_INTEGRATION.md` los contratos de API.

## Despliegue Railway

- Añade en Variables de Railway `ANTHROPIC_API_KEY` con la clave creada en Claude Console. Al reiniciar, `/api/health` mostrará `ai_provider=anthropic` y `draft_mode=ai`. No guardes la clave en GitHub ni en el código.

1. Crea un proyecto y un servicio vacío desde la interfaz de Railway o enlázalo con la CLI (`railway init` y `railway add --service infolinense-desk`). Despliega la carpeta raíz con `railway up`. `railway.toml` usa el Dockerfile y comprueba `/api/health`.
2. Añade un volumen persistente montado en `/data`. Configura las variables del servicio siguiendo `.env.example`: `DATA_DIR=/data`, `DB_PATH=/data/infolinense.db`, `UPLOAD_DIR=/data/uploads`, `RENDER_DIR=/data/renders`, `ADMIN_PASSWORD`, `JWT_SECRET`, y opcionalmente `OPENAI_API_KEY` o `ANTHROPIC_API_KEY`. Configura también `CANVA_CLIENT_ID`, `CANVA_CLIENT_SECRET`, `CANVA_BRAND_TEMPLATE_ID=EAHWTjWEEnA` y `PUBLIC_BASE_URL`. Mantén `PUBLISH_MODE=none` hasta conectar el destino real de publicación.
3. Genera un dominio público HTTPS para el servicio. Comprueba `https://<dominio>/api/health`: `ok` y `auth_configured` deben ser `true`; `draft_mode=source_draft` indica borrador gratuito a partir de la fuente y `draft_mode=ai` indica redacción con API de pago. La raíz `/` abre el panel privado de reserva.
4. Mantén una sola réplica si usas SQLite y el programador integrado. No adjuntes un segundo servicio con el mismo escáner a la misma base de datos.

## Panel Lovable

1. Crea un proyecto nuevo en Lovable con el contenido de `lovable/PROMPT_LOVABLE.md` y sustituye `TU_BACKEND` por el dominio real de Railway; configura `VITE_INFOLINENSE_API_URL=https://<dominio>`.
2. Publica el panel como aplicación privada con pantalla de contraseña. Añade **solo su origen exacto** (`https://<tu-proyecto>.lovable.app`) a `CORS_ORIGINS` en Railway. Si usas dominio propio, añádelo separado por coma. Redepliega si Railway no aplica las variables automáticamente.
3. Accede al panel, prueba Dashboard y Revisión y comprueba **Plantillas → Canva**. Los PNG privados solo existen tras exportar desde Canva y se descargan mediante `fetch` con token; un enlace `<img src="https://backend/media/render/...">` directo devolverá 401.

## Novedades 4.3

- **Gemini gratis sin atascos**: modelo Flash-Lite por defecto (más cupo gratuito), peticiones espaciadas (`GEMINI_MIN_INTERVAL`, 13 s), cambio automático de modelo si uno no tiene cupo, espera y reintento ante el límite por minuto y pausa de una hora si se agota el cupo diario. Una sola petición por noticia.

## Novedades 4.2

- **Empezar de cero**: al desplegar se borran una vez las noticias encontradas (no las publicadas) y se releen todas las fuentes.
- **Fechas reales**: solo entra lo que tiene fecha de publicación comprobable (RSS, metadatos de la página, fecha del edicto, de la licitación o del boletín). Nunca se muestra la hora en que la encontró el sistema como si fuera la de publicación. Las fechas de solo día se muestran como día.
- **Licitaciones**: lector de Gobierto basado en los enlaces /licitaciones/N con fecha, importe y plazo; se mantienen las que siguen abiertas.
- **Edictos**: lector del tablón con la primera fecha de cada fila; BOP con la fecha del boletín.

## Novedades 4.1 — sin coste

- **Modo sin coste por defecto** (`AI_ALLOW_PAID=false`): no se llama nunca a ChatGPT ni a Claude, aunque sus claves sigan en Railway.
- **IA gratuita: Google Gemini** con `GEMINI_API_KEY` (nivel gratuito de Google AI Studio). Una sola petición por noticia (sin investigación previa ni búsqueda web con IA). Si se alcanza el límite gratuito, la noticia queda con «Reintentar» y se vuelve a intentar sola en la siguiente búsqueda.
- Sin ninguna clave, los borradores se preparan a partir del texto de la fuente.
- Para volver a usar IA de pago: `AI_ALLOW_PAID=true`.

## Novedades 4.0

- **Trabajo por fases**: 1 Ordenar (noticia a noticia por bloques: Urgente, Hoy, Esta semana, Más adelante o No interesa), 2 Redacción (se redactan solas; progreso y reintentos), 3 Revisar (ficha con «Revisada» que pasa a la siguiente) y 4 Publicar (agenda de hoy y próximos días con «Publicar»). Al abrir, el panel va a la primera fase con trabajo. Ajustes en el engranaje.
- **Empezar de cero**: al desplegar la 4.0 se archiva una sola vez todo lo pendiente (lo publicado se conserva) y el radar arranca limpio.

## Novedades 3.4

- **Cuatro bloques** en Noticias: Ayuntamiento, Licitaciones y edictos, Otros medios y Nacionales adaptables. Las redes van aparte, en Redes.
- **Licitaciones y edictos**: lector propio del tablón de edictos de la sede electrónica (con lectura de PDF), Gobierto, BOP y plataforma estatal; más puntuación y 20 días de vigencia.
- **Solo la ciudad**: «La Línea» debe ser La Línea de la Concepción (se descartan líneas de metro, tren, alta velocidad, «línea roja»…).
- **Nada antiguo ni repetido**: el radar oculta lo que ha pasado su plazo y lo parecido a noticias ya aprobadas o publicadas.
- **Medios nacionales** y noticias nacionales adaptables (Gibraltar y frontera, Campo de Gibraltar, y búsqueda con ChatGPT).
- **Redes**: solo quejas de calle, propuestas y asociaciones/colectivos; se descartan ventas, publicidad y noticias de medios compartidas.

## Novedades 3.1

- **Estilo de redacción**: la IA redacta siguiendo `app/estilo_redaccion.md` (guía de InfoLinense). Se puede editar ese archivo para afinar el estilo. Cada borrador guarda su «enfoque principal».
- **Fotos de Google**: la foto de la propia noticia y después Google Imágenes (sin Wikimedia). Para más fiabilidad, añade `GOOGLE_SEARCH_API_KEY` y `GOOGLE_SEARCH_CX` (Programmable Search Engine con búsqueda de imágenes); sin ellas se lee la página de resultados de Google y, si falla, Bing.
- **Panel Redes**: quejas vecinales y noticias públicas de páginas y grupos de Facebook de La Línea (indexadas por Google en los últimos 3 días y, si ChatGPT tiene búsqueda web, un barrido extra). Se clasifican en Quejas y Noticias y se pueden añadir páginas y grupos a vigilar. Los grupos cerrados no son accesibles.

## Novedades 3.0

- **Redacta ChatGPT**: con `AI_PROVIDER=auto` el principal es OpenAI; Claude solo entra si ChatGPT falla.
- **Noticias actuales y por fuentes**: solo entran noticias con fecha comprobable de los últimos `MAX_CANDIDATE_AGE_DAYS` (2 por defecto); si la lista no trae fecha se lee de la propia página. Lo antiguo sin prioridad se retira solo. El radar se divide en Ayuntamiento, Boletines y edictos, Licitaciones, Medios, Redes sociales, Gibraltar y región, y Nacional e internacional, y muestra el medio real (también en Google News).
- **Panel nuevo para móvil** (raíz del servidor): Noticias, Agenda, Redacción y Ajustes. Se eliminan Inicio con métricas, Plantillas, Revisión y Kit por separado: todo se hace en la ficha de la noticia.
- **Publicar en infolinense.com**: el botón «Publicar» aprueba y envía la noticia a la web de Lovable (`PUBLISH_MODE=lovable`, `LOVABLE_WEBHOOK_URL`, `PUBLISH_WEBHOOK_SECRET`). Instrucciones para la web en `lovable/PROMPT_WEB_PUBLICAR.md`.

## Novedades 2.5

- **Redacción automática**: al marcar una noticia como Urgente, Hoy, Esta semana o Futura se investiga y redacta sola, una a una (urgentes primero). También se retoman al arrancar y tras cada búsqueda. Se desactiva con `AUTO_DRAFT_USEFUL=false`.
- **Fotos**: primero la foto de la propia noticia (también desde enlaces de Google News), después fotos de otros medios con la misma historia, búsqueda de imágenes en internet y Wikimedia Commons. Se descartan logos, iconos e imágenes pequeñas. Sin comprobaciones de licencia: el editor decide.
- **Plantilla Canva**: titular a 74 px en las 12 páginas (caben 4 líneas sin pisar el resumen). Cada página es una familia de sección con su color; se exporta la página de la familia (`app/layout.py`). El titular de la imagen (`image_headline`) se ajusta a 4 líneas y el resumen a 150 caracteres.

## Novedades 2.4

- **IA unificada**: investigar, redactar, titulares, carrusel y descubrimiento usan el proveedor activo (Claude si existe `ANTHROPIC_API_KEY`). Si falla por saldo, límite o caída y hay otra clave, se prueba el otro (`AI_FALLBACK=true`). Antes todo llamaba a OpenAI aunque Claude estuviera configurado.
- **Errores reales**: los fallos de IA devuelven `detail` con el proveedor, el código HTTP y lo que respondió (`error` trae los datos estructurados). `GET /api/ai/check` hace una petición mínima real a cada proveedor.
- **Botón Redactar**: `POST /api/candidates/{id}/write` abre el borrador si existe, o investiga (si hace falta) y redacta. Nunca duplica. Si tarda, responde 202 y se consulta `GET /api/candidates/{id}/write`.
- **Redacción**: titular, entradilla, cuerpo de unos 2.200 caracteres, 7 titulares alternativos (`headline_options_json`) y datos que faltan (`missing_data_json`).
- **Fotos**: fotos de la fuente con permiso confirmado por el editor, búsqueda libre en Wikimedia Commons (`?q=`), subida propia y descarga de la foto original (`/photo/file`). Procedencia siempre visible.
- **Agenda**: lo fijado a mano y lo ya publicado no se mueve; si el día se llena se usan huecos cada media hora.
- **Panel final** («Listo para publicar») en el panel del servidor; `kit` incluye `copy_text`, crédito de imagen y descargas. No hay publicación automática.
- `lovable/PROMPT_ACTUALIZACION_2_4.md` contiene el texto para actualizar el frontend de Lovable.

## Flujo editorial

- `POST /api/scan` examina fuentes gratuitas sin clave de IA: Ayuntamiento, Europa Sur, prensa comarcal indexada, BOE, BOP Cádiz, edictos, licitaciones indexadas y un listado municipal de Gobierto, además de publicaciones públicas indexadas en Facebook e Instagram. También prueba el tablón municipal directo. El estado y último error de cada fuente aparecen en **Fuentes**; `GET /api/radar/stats` muestra publicaciones fechadas hoy (hora de Madrid), enlaces detectados y entradas sin fecha por separado, junto al objetivo editorial de 50. El volumen de noticias reales varía y no se inventan candidatas para alcanzar una cifra.
- El listado admite nuevas webs RSS/Atom, portadas HTML y páginas concretas de Facebook o Instagram, así como editar, pausar y comprobar cada fuente. Las redes sociales se descubren por indexación pública, con cobertura parcial; una integración completa necesita permisos de Meta. El tablón de edictos municipal puede responder 502: en tal caso conserva el error y las fuentes complementarias de anuncios siguen activas.
- Solo se importan elementos con relación comprobable con La Línea o provenientes de una sección dedicada exclusivamente a la ciudad; se descartan duplicados y entradas RSS anteriores a `MAX_CANDIDATE_AGE_DAYS` (3 por defecto). En Inicio y Radar cada noticia muestra el siguiente paso: **Investigar → Redactar → Enviar a Revisión**. El borrador puede corregirse antes de pasar a Revisión. `AUTO_PIPELINE=false` por defecto deja la decisión en manos del editor. Sin clave las piezas indican que se basan únicamente en la fuente original.
- En la ficha de cada noticia se elige la foto (la de la propia noticia, una de internet o una subida) y se crea la imagen con la plantilla de Canva.
- Publicar en infolinense.com **no está conectado** de forma predeterminada. Para activarlo en Railway configura `PUBLISH_MODE=wordpress`, `WORDPRESS_URL=https://infolinense.com`, `WORDPRESS_USERNAME` y `WORDPRESS_APP_PASSWORD` (contraseña de aplicación creada en el perfil de WordPress). Nunca guardes esta contraseña en GitHub. Solo se publica mediante el botón de una noticia aprobada; el backend adjunta la imagen final exportada de Canva como imagen destacada. También se conservan los modos webhook y Supabase.

### Plantilla principal de Canva

La plantilla de marca `EAHWTjWEEnA` usa `HEADLINE`, `SUMMARY`, `SECTION` y `PHOTO`. La API comprueba los campos mediante `GET /api/canva/template`. El diseño se crea después de seleccionar una foto propia o con licencia reutilizable, desde Revisión → Crear en Canva. El PNG exportado de la primera página es la única imagen disponible en Revisión y Kit. Si editas el diseño directamente en Canva, pulsa **Actualizar PNG desde Canva** (`POST /api/articles/{id}/canva/export`). Si cambió el titular, resumen, sección o foto dentro de Desk, vuelve a crear el diseño desde la plantilla.

La app de Canva necesita los permisos `asset:read`, `asset:write`, `brandtemplate:content:read`, `design:content:read`, `design:content:write` y `design:meta:read`. Tras activar `design:content:read` en Canva Developers, pulsa **Reconectar Canva** en Ajustes. El permiso de exportación se consulta sin mostrar tokens en `GET /api/canva/permissions`. La URL de retorno debe ser `https://infolinense-api-production.up.railway.app/api/canva/callback`.

## Desarrollo local

Copia `.env.example` a `.env` y asigna las credenciales antes de arrancar. Usa rutas de datos locales o `docker compose up --build` con `DATA_DIR=/data` y el volumen `./data:/data`. El servidor arranca con `./start.sh` y escucha en el `PORT` asignado por Railway.

Nunca pongas `OPENAI_API_KEY`, `JWT_SECRET`, `ADMIN_PASSWORD` o una clave Supabase de servicio en Lovable o en el navegador.


## Publicaciones oficiales de hermandades (Facebook)

En **El Cofrade Linense → Ordenar**, el panel existente de hermandades muestra las
publicaciones de los últimos siete días, en orden descendente, con fecha en Madrid,
texto completo, imagen disponible y enlace original. No usa resultados de buscadores
ni noticias antiguas del radar como publicaciones verificadas.

Las 13 direcciones iniciales están en `app/db.py`; son fuentes propuestas, no una
certificación de oficialidad. En **Gestionar → Editar**, comprueba que la dirección
pertenece a la hermandad de La Línea y marca «He comprobado que es la página oficial».
No marques perfiles personales ni páginas de otros municipios. Añadir un resultado de
búsqueda no lo aprueba. Las fuentes pausadas o sin confirmar no aportan publicaciones.
Meta debe identificar el recurso como Page y su ID/username/enlace debe coincidir con
la dirección configurada. Cada publicación debe tener `from.id` y prefijo de ID de
esa misma página, además de una fecha comprobable dentro del intervalo. Nunca se
empareja por nombre, palabras del texto o menciones. No se incluyen posts de visitantes.

El lector reutiliza las páginas y tokens de las conexiones Meta ya guardadas por Desk.
También acepta variables de entorno de servidor, nunca del frontend:

- `META_PAGE_ID` y `META_PAGE_TOKEN`: ID y token de acceso de una página concreta.
- `META_READER_PAGE_TOKENS`: objeto JSON de IDs de páginas a sus tokens de acceso,
  para varias hermandades. Introducirlo mediante la configuración segura del servidor;
  no añadir sus valores a Git, capturas, logs ni archivos de ejemplo.
- OAuth existente: `META_APP_ID`, `META_APP_SECRET`, `PUBLIC_BASE_URL` HTTPS y retorno
  `/api/networks/meta/callback`. Autorizar **cada página de hermandad** con
  `pages_show_list` para enumerarlas y `pages_read_engagement` para leer sus posts.
  La cuenta debe tener acceso asignado a esas páginas. El flujo de publicación de
  Desk solicita también `pages_manage_posts`, pero el lector no lo necesita.

Un token de la página de El Cofrade Linense no permite leer automáticamente las
hermandades. Para páginas no administradas se necesita la aprobación de Meta de
**Page Public Content Access** y un token autorizado para ese acceso; el lector exige
igualmente un ID de página explícito y la validación de su identidad. Si Meta rechaza
la consulta o falta acceso, el panel lo indica y no sustituye el contenido por búsquedas.
En entornos de red restringida, permitir HTTPS a `graph.facebook.com` y, para mostrar
las imágenes de Meta, sus destinos CDN de Facebook. OAuth necesita además acceso a
`www.facebook.com`. No se requieren `pages_read_user_content` ni permisos de escritura
para leer exclusivamente posts publicados por las propias páginas.

Endpoint autenticado: `GET /api/social/facebook/brotherhoods`. `items` contiene solo
publicaciones verificadas; `pages` distingue páginas sin confirmar, sin acceso y
lecturas correctas sin publicaciones. Un fallo de acceso no equivale a cero posts.
No se ha validado acceso real en el entorno inicial: no tiene credenciales Meta.
Pruebas aisladas: `PYTHONPATH=. .venv/bin/python -m unittest discover -s tests -p test_facebook_reader.py`.

### Alternativa sin permisos de las hermandades: Apify

Desk permite seleccionar `FACEBOOK_READER_PROVIDER=apify`. Importa el dataset de la
última ejecución **SUCCEEDED** de una tarea de Apify. No inicia actores, no compra
servicios, no envía cookies de Facebook y no necesita acceso de administración a las
hermandades. La extracción externa puede tener coste y cobertura parcial. No se ha
comprobado todavía que un actor concreto cubra estas 13 páginas: no hay cuenta/token
y la política de red del entorno bloquea la web de Apify. No contratar un plan sin
comprobar antes una extracción real de una de las páginas.

Preparación en Apify y en el servidor:

1. Crear una cuenta en [Apify](https://apify.com/) y evaluar un extractor de posts
   públicos, por ejemplo [Facebook Posts Scraper](https://apify.com/apify/facebook-posts-scraper).
   Este enlace es un candidato para evaluar, no una garantía de cobertura o de formato.
2. Crear una tarea guardada con **solo** las URL oficiales confirmadas en Desk.
   Probar primero una página y revisar en el dataset su autor, fecha y enlace.
   Limitar el periodo a siete días y comprobar que el límite de resultados no recorta
   publicaciones. No usar búsquedas por nombre ni feeds de perfiles personales.
3. Comprobar el contrato de salida de abajo. Si el actor omite la identidad/tipo de
   página o del autor, no es suficiente para este lector: elegir otro actor o adaptar
   la salida con datos de identidad realmente extraídos. Nunca rellenar esa evidencia
   con valores supuestos para hacer pasar la validación.
4. Configurar `FACEBOOK_READER_PROVIDER=apify`, `APIFY_FACEBOOK_TASK_ID` (ID de la tarea,
   no del actor) y `APIFY_TOKEN` mediante las variables seguras del servidor. El token
   debe poder leer la tarea, sus ejecuciones y datasets. No compartirlo en chat.
5. Ejecutar la tarea desde Apify y programar allí su actualización si la prueba es
   satisfactoria, revisando antes sus límites de gasto. Desk rechaza extracciones con
   más de 24 horas y filtra de nuevo las fechas a los últimos siete días.
6. Permitir HTTPS a `api.apify.com` en el entorno de Desk. El navegador también necesita
   acceso a los hosts de las imágenes devueltas. Para probar o configurar el actor,
   acceder a `apify.com` y su consola desde el navegador.

Contrato del dataset (son nombres de campos; no hay publicaciones de demostración
cargadas en Desk):

| Campo | Uso y requisito |
| --- | --- |
| `page.id` o `pageId` | ID numérico de la página, obligatorio |
| `page.url` o `facebookUrl` | URL canónica que coincide exactamente con la fuente configurada, o su ID numérico |
| `page.isPage=true`, `page.type=Page`, `page.category` o `pageCategory` | Evidencia de que el recurso extraído es una página, no un perfil |
| `user.id` | Autor de la publicación; debe coincidir con el ID de página |
| `user.profileUrl` o `user.url` | Si existe, debe identificar la misma página |
| `postId` o `id` | Identificador de publicación, obligatorio |
| `time` o `publishedAt` | Fecha ISO 8601 con zona horaria, obligatoria |
| `url` | Enlace Facebook de posts/fotos/vídeos de esa misma página o permalink con su ID |
| `text` | Texto; puede faltar si solo hay imagen |
| `full_picture` o `media[].thumbnail` | Imagen opcional HTTPS |
| `page.name` o `pageName` | Nombre público de la página |

Los formatos sin identidad verificable, enlaces ambiguos (por ejemplo reels sin
propietario en la URL), posts de visitantes y fechas desconocidas se descartan.
Una página ausente del dataset aparece como **sin lectura verificable**, no como
«cero publicaciones». La interfaz mantiene el diseño y muestra la fecha de la
extracción. Meta sigue disponible seleccionando `FACEBOOK_READER_PROVIDER=meta`.

La integración usa únicamente GET y autenticación Bearer en cabecera. Resuelve un
dataset fijo antes de paginar para no mezclar ejecuciones. API contrastada con la
[documentación oficial de Apify](https://docs.apify.com/api/v2/actor-task-runs-last-get)
y [lectura de datasets](https://docs.apify.com/api/v2/dataset-items-get), cuya
[especificación pública](https://github.com/apify/apify-docs/tree/master/apify-api/openapi)
se consultó durante la implementación. Las pruebas con respuestas simuladas verifican
el adaptador, pero no sustituyen la prueba pendiente con datos reales de Facebook.


Formato comprobado con el JSON real aportado el 5 de noviembre de 2026:
Facebook Posts Scraper entrega `facebookId` para la identidad y
`pageAdLibrary.id` como evidencia de página. El lector admite esos campos y exige
que `user.id` coincida y que `topLevelUrl` identifique exactamente esa página y
`postId`. El ID de la biblioteca de anuncios no se usa como ID de página.
Se excluyen los registros con `sharedPost`. Para reels se utiliza el permalink
numérico de `topLevelUrl`, previamente verificado. Los errores `not_available` y
`no_items` se muestran como lectura no verificada. Esta comprobación valida el
archivo aportado; no confirma que la API esté conectada o programada.

Diagnóstico del servidor: el panel de hermandades muestra el lector activo y si
el proceso recibió `APIFY_TOKEN` y `APIFY_FACEBOOK_TASK_ID`, sin devolver sus valores.
La revisión `2026-11-06.1` identifica esta comprobación. Un error de la API de Apify
no provoca un cambio a Meta. Si `FACEBOOK_READER_PROVIDER` no existe o está vacío,
se elige Apify cuando están presentes token y tarea; un `meta` explícito se respeta.
Las comillas exteriores en el selector se toleran. Si Railway muestra variables
pero el panel indica que faltan, comprobar el servicio, entorno y despliegue que
atienden al dominio abierto; la configuración guardada puede no ser la del proceso.

Diagnóstico de HTTP 404 (revisión `2026-11-06.2`): se consulta la lista de
 ejecuciones correctas de la tarea, ordenada de más reciente a más antigua.
Una tarea accesible sin ejecuciones devuelve una lista vacía y un mensaje específico;
una tarea inaccesible devuelve el error de tarea, y un dataset eliminado o inaccesible
el error de dataset. Guardar una tarea desde un actor no copia las ejecuciones previas
hechas directamente con ese actor. No se inician extracciones automáticamente.

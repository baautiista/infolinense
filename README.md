# InfoLinense Desk 2.3 — despliegue

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

## Flujo editorial

- `POST /api/scan` examina fuentes gratuitas sin clave de IA: Ayuntamiento, Europa Sur, prensa comarcal indexada, BOE, BOP Cádiz, edictos, licitaciones indexadas y un listado municipal de Gobierto, además de publicaciones públicas indexadas en Facebook e Instagram. También prueba el tablón municipal directo. El estado y último error de cada fuente aparecen en **Fuentes**; `GET /api/radar/stats` muestra publicaciones fechadas hoy (hora de Madrid), enlaces detectados y entradas sin fecha por separado, junto al objetivo editorial de 50. El volumen de noticias reales varía y no se inventan candidatas para alcanzar una cifra.
- El listado admite nuevas webs RSS/Atom, portadas HTML y páginas concretas de Facebook o Instagram, así como editar, pausar y comprobar cada fuente. Las redes sociales se descubren por indexación pública, con cobertura parcial; una integración completa necesita permisos de Meta. El tablón de edictos municipal puede responder 502: en tal caso conserva el error y las fuentes complementarias de anuncios siguen activas.
- Solo se importan elementos con relación comprobable con La Línea o provenientes de una sección dedicada exclusivamente a la ciudad; se descartan duplicados y entradas RSS anteriores a `MAX_CANDIDATE_AGE_DAYS` (3 por defecto). En Inicio y Radar cada noticia muestra el siguiente paso: **Investigar → Redactar → Enviar a Revisión**. El borrador puede corregirse antes de pasar a Revisión. `AUTO_PIPELINE=false` por defecto deja la decisión en manos del editor. Sin clave las piezas indican que se basan únicamente en la fuente original.
- En Revisión el editor elige una fotografía real reutilizable, sube una propia o acredita el permiso. Solo después puede crear el diseño en la plantilla Canva y obtener el PNG. Para aprobar, el servidor exige licencia verificable, `publish_safe=true` y una exportación Canva vigente. Cambiar texto o foto invalida la exportación; nunca se ofrece una imagen PPTX como sustituta.
- Publicar en infolinense.com **no está conectado** de forma predeterminada. Para activarlo en Railway configura `PUBLISH_MODE=wordpress`, `WORDPRESS_URL=https://infolinense.com`, `WORDPRESS_USERNAME` y `WORDPRESS_APP_PASSWORD` (contraseña de aplicación creada en el perfil de WordPress). Nunca guardes esta contraseña en GitHub. Solo se publica mediante el botón de una noticia aprobada; el backend adjunta la imagen final exportada de Canva como imagen destacada. También se conservan los modos webhook y Supabase.

### Plantilla principal de Canva

La plantilla de marca `EAHWTjWEEnA` usa `HEADLINE`, `SUMMARY`, `SECTION` y `PHOTO`. La API comprueba los campos mediante `GET /api/canva/template`. El diseño se crea después de seleccionar una foto propia o con licencia reutilizable, desde Revisión → Crear en Canva. El PNG exportado de la primera página es la única imagen disponible en Revisión y Kit. Si editas el diseño directamente en Canva, pulsa **Actualizar PNG desde Canva** (`POST /api/articles/{id}/canva/export`). Si cambió el titular, resumen, sección o foto dentro de Desk, vuelve a crear el diseño desde la plantilla.

La app de Canva necesita los permisos `asset:read`, `asset:write`, `brandtemplate:content:read`, `design:content:read`, `design:content:write` y `design:meta:read`. Tras activar `design:content:read` en Canva Developers, pulsa **Reconectar Canva** en Ajustes. El permiso de exportación se consulta sin mostrar tokens en `GET /api/canva/permissions`. La URL de retorno debe ser `https://infolinense-api-production.up.railway.app/api/canva/callback`.

## Desarrollo local

Copia `.env.example` a `.env` y asigna las credenciales antes de arrancar. Usa rutas de datos locales o `docker compose up --build` con `DATA_DIR=/data` y el volumen `./data:/data`. El servidor arranca con `./start.sh` y escucha en el `PORT` asignado por Railway.

Nunca pongas `OPENAI_API_KEY`, `JWT_SECRET`, `ADMIN_PASSWORD` o una clave Supabase de servicio en Lovable o en el navegador.

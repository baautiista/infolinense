# InfoLinense Desk 2.1 — despliegue

El ZIP contiene un backend FastAPI con panel privado de reserva, radar editorial, redacción, plantillas PPTX y render de imágenes. El backend se despliega en Railway; el panel de Lovable consume la misma API desde otro dominio. La plantilla original de InfoLinense está incluida.

## Antes de desplegar

- Railway: cuenta y proyecto con un servicio Docker para este directorio.
- Una contraseña nueva y larga para `ADMIN_PASSWORD` y una cadena aleatoria distinta para `JWT_SECRET` (por ejemplo, `python3 -c 'import secrets; print(secrets.token_urlsafe(48))'`). No uses los valores de ejemplo.
- Sin `OPENAI_API_KEY` la aplicación prepara fichas editoriales gratuitas basadas en la fuente original. Revisa y reescribe el texto antes de publicarlo: no hay contraste automático ni generación de IA. La clave es opcional para investigación y redacción con API de pago.
- El proyecto de Lovable se crea **desde el prompt**, porque Lovable no importa repositorios existentes. `lovable/PROMPT_LOVABLE.md` describe el frontend y `lovable/API_INTEGRATION.md` los contratos de API.

## Despliegue Railway

1. Crea un proyecto y un servicio vacío desde la interfaz de Railway o enlázalo con la CLI (`railway init` y `railway add --service infolinense-desk`). Despliega la carpeta raíz con `railway up`. `railway.toml` usa el Dockerfile y comprueba `/api/health`.
2. Añade un volumen persistente montado en `/data`. Configura las variables del servicio siguiendo `.env.example`: `DATA_DIR=/data`, `DB_PATH=/data/infolinense.db`, `UPLOAD_DIR=/data/uploads`, `RENDER_DIR=/data/renders`, `TEMPLATE_DIR=/data/templates`, `ADMIN_PASSWORD`, `JWT_SECRET`, y opcionalmente `OPENAI_API_KEY`. Mantén `PUBLISH_MODE=none` hasta que conectes el destino real de publicación.
3. Genera un dominio público HTTPS para el servicio. Comprueba `https://<dominio>/api/health`: `ok` y `auth_configured` deben ser `true`; `draft_mode=source_brief` indica fichas gratuitas y `draft_mode=ai` indica redacción con API de pago. La raíz `/` abre el panel privado de reserva.
4. Mantén una sola réplica si usas SQLite y el programador integrado. No adjuntes un segundo servicio con el mismo escáner a la misma base de datos.

## Panel Lovable

1. Crea un proyecto nuevo en Lovable con el contenido de `lovable/PROMPT_LOVABLE.md` y sustituye `TU_BACKEND` por el dominio real de Railway; configura `VITE_INFOLINENSE_API_URL=https://<dominio>`.
2. Publica el panel como aplicación privada con pantalla de contraseña. Añade **solo su origen exacto** (`https://<tu-proyecto>.lovable.app`) a `CORS_ORIGINS` en Railway. Si usas dominio propio, añádelo separado por coma. Redepliega si Railway no aplica las variables automáticamente.
3. Accede al panel, prueba Dashboard y Revisión y sube una plantilla de prueba. Los PNG privados se descargan mediante `fetch` con token; un enlace `<img src="https://backend/media/render/...">` directo devolverá 401.

## Flujo editorial

- `POST /api/scan` examina fuentes gratuitas sin clave de IA: Ayuntamiento, Europa Sur, prensa comarcal indexada, BOE, BOP Cádiz, edictos, licitaciones indexadas y un listado municipal de Gobierto, además de publicaciones públicas indexadas en Facebook e Instagram. También prueba el tablón municipal directo. El estado y último error de cada fuente aparecen en **Fuentes**; `GET /api/radar/stats` muestra publicaciones fechadas hoy (hora de Madrid), enlaces detectados y entradas sin fecha por separado, junto al objetivo editorial de 50. El volumen de noticias reales varía y no se inventan candidatas para alcanzar una cifra.
- El listado admite nuevas webs RSS/Atom, portadas HTML y páginas concretas de Facebook o Instagram, así como editar, pausar y comprobar cada fuente. Las redes sociales se descubren por indexación pública, con cobertura parcial; una integración completa necesita permisos de Meta. El tablón de edictos municipal puede responder 502: en tal caso conserva el error y las fuentes complementarias de anuncios siguen activas.
- Solo se importan elementos con relación comprobable con La Línea o provenientes de una sección dedicada exclusivamente a la ciudad; se descartan duplicados y entradas RSS anteriores a `MAX_CANDIDATE_AGE_DAYS` (3 por defecto). En Inicio y Radar cada noticia muestra el siguiente paso: **Investigar → Redactar → Enviar a Revisión**. El borrador puede corregirse antes de pasar a Revisión. `AUTO_PIPELINE=false` por defecto deja la decisión en manos del editor. Sin clave todas las piezas se marcan como ficha de fuente, sin afirmar que exista contraste independiente.
- La plantilla PPTX original tiene 12 variantes por sección. Los nuevos PPTX admitidos tienen una o 12 diapositivas y deben incluir `{{HEADLINE}}` en cada una; `{{SUMMARY}}` y `{{SECTION}}` son opcionales. Nombra `PHOTO` al elemento principal de foto o deja que se seleccione la imagen mayor. Subir una plantilla la activa para noticias **nuevas**. Para aprobar una pieza, el servidor exige una imagen real descargada con licencia identificada y `publish_safe=true`; una foto de una fuente externa u oficial sin derechos verificados requiere autorización y no se puede aprobar desde el flujo actual.
- Publicar en infolinense.com **no está conectado** de forma predeterminada. Para activarlo en Railway configura `PUBLISH_MODE=wordpress`, `WORDPRESS_URL=https://infolinense.com`, `WORDPRESS_USERNAME` y `WORDPRESS_APP_PASSWORD` (contraseña de aplicación creada en el perfil de WordPress). Nunca guardes esta contraseña en GitHub. Solo se publica mediante el botón de una noticia aprobada; el backend adjunta la imagen final exportada de Canva como imagen destacada. También se conservan los modos webhook y Supabase.

### Plantilla principal de Canva

La plantilla de marca `EAHWTjWEEnA` usa `HEADLINE`, `SUMMARY`, `SECTION` y `PHOTO`. La API comprueba los campos mediante `GET /api/canva/template`. El diseño se crea después de seleccionar una foto propia o con licencia reutilizable, desde Revisión → Crear en Canva. La exportación PNG de la primera página reemplaza la imagen provisional del kit de redes. Guardar cambios de texto o foto invalida la exportación anterior; hay que regenerarla antes de aprobar.

La app de Canva necesita los permisos `asset:read`, `asset:write`, `brandtemplate:content:read`, `design:content:read`, `design:content:write` y `design:meta:read`. Tras activar `design:content:read` en Canva Developers, pulsa **Reconectar Canva** en Ajustes. El permiso de exportación se consulta sin mostrar tokens en `GET /api/canva/permissions`. La URL de retorno debe ser `https://infolinense-api-production.up.railway.app/api/canva/callback`.

## Desarrollo local

Copia `.env.example` a `.env` y asigna las credenciales antes de arrancar. Usa rutas de datos locales o `docker compose up --build` con `DATA_DIR=/data` y el volumen `./data:/data`. El servidor arranca con `./start.sh` y escucha en el `PORT` asignado por Railway.

Nunca pongas `OPENAI_API_KEY`, `JWT_SECRET`, `ADMIN_PASSWORD` o una clave Supabase de servicio en Lovable o en el navegador.

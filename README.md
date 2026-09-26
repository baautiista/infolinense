# InfoLinense Desk 2.0.1 — despliegue

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

- `POST /api/scan` examina las fuentes configuradas. Sin clave crea fichas atribuidas a la fuente y pendientes de verificación; la búsqueda web y la investigación con IA requieren `OPENAI_API_KEY`.
- Con `AUTO_PIPELINE=true`, puntuaciones 42–69 pasan a Revisión como pieza rápida y 70+ se investigan si hay clave. Sin clave todas las piezas se marcan como ficha de fuente, sin afirmar que exista contraste independiente.
- La plantilla PPTX original tiene 12 variantes por sección. Los nuevos PPTX admitidos tienen una o 12 diapositivas y deben incluir `{{HEADLINE}}` en cada una; `{{SUMMARY}}` y `{{SECTION}}` son opcionales. Nombra `PHOTO` al elemento principal de foto o deja que se seleccione la imagen mayor. Subir una plantilla la activa para noticias **nuevas**. Para aprobar una pieza, el servidor exige una imagen real descargada con licencia identificada y `publish_safe=true`; una foto de una fuente externa u oficial sin derechos verificados requiere autorización y no se puede aprobar desde el flujo actual.
- Publicar en infolinense.com **no está conectado** de forma predeterminada. `/api/articles/{id}/publish` exige aprobación y un `PUBLISH_MODE` configurado con un webhook o un esquema Supabase validado. La API de WordPress de InfoLinense aún no está mapeada.

## Desarrollo local

Copia `.env.example` a `.env` y asigna las credenciales antes de arrancar. Usa rutas de datos locales o `docker compose up --build` con `DATA_DIR=/data` y el volumen `./data:/data`. El servidor arranca con `./start.sh` y escucha en el `PORT` asignado por Railway.

Nunca pongas `OPENAI_API_KEY`, `JWT_SECRET`, `ADMIN_PASSWORD` o una clave Supabase de servicio en Lovable o en el navegador.

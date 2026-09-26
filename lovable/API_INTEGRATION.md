# Contrato de integración Lovable ↔ Railway

API pública: `VITE_INFOLINENSE_API_URL=https://TU_BACKEND` (sin barra final). Sustituye el valor por el dominio real de Railway antes de publicar. Este valor es público; las claves privadas residen solo en Railway.

`POST /api/auth/login` con `{ "password": "..." }` devuelve `{ "token": "..." }`. Guarda el token de sesión y envía `Authorization: Bearer <token>` en cada petición privada. Si llega 401, elimina el token y vuelve al login.

`GET /api/dashboard`, `POST /api/scan`, `GET /api/candidates?status=new` muestran el radar. La redacción automática requiere `OPENAI_API_KEY` en Railway. `GET /api/review` devuelve las noticias preparadas. Edita `headline`, `graphic_summary` y **`body`** (máximo 2.200 caracteres) con `PUT /api/articles/{id}`; el servidor mantiene `social_text` sincronizado con `body`.

`GET /api/articles/{id}/photos`, `POST /api/articles/{id}/photo`, `POST /api/articles/{id}/research-more`, `POST /api/articles/{id}/approve`, `POST /api/articles/{id}/reject` gobiernan la revisión. Comprueba `publish_safe` y la licencia de cada foto antes de aprobarla.

`GET /api/articles/{id}/kit` devuelve texto y ruta del PNG. El PNG `/media/render/{id}.png` **requiere token**: solicítalo con `fetch` y cabecera Bearer, convierte el resultado en `Blob` y usa `URL.createObjectURL(blob)` para `<img>` o descarga. Libera esa URL al desmontar la vista. No incluyas el token en la URL de imagen.

`GET /api/templates`, `POST /api/templates/upload` (multipart `file` PPTX + `name`), `POST /api/templates/{id}/activate`; `GET /api/sources`, `POST /api/sources`, `POST /api/sources/{id}/toggle` son privados. La plantilla personalizada necesita `{{HEADLINE}}` en cada diapositiva.

`GET /api/health` es público y muestra si hay autenticación e IA configuradas. `POST /api/articles/{id}/publish` devuelve error mientras `PUBLISH_MODE=none`; oculta el botón de publicación si no está configurado. Esta publicación apunta a la web editorial, no a publicar el panel de Lovable.

CORS: en Railway configura `CORS_ORIGINS` con el origen exacto del panel de Lovable, por ejemplo `https://mi-proyecto.lovable.app`. No uses `*` para la API privada.

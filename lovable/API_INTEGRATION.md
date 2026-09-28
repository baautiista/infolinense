# Contrato de integración Lovable ↔ Railway

API pública: `VITE_INFOLINENSE_API_URL=https://TU_BACKEND` (sin barra final). Sustituye el valor por el dominio real de Railway antes de publicar. Este valor es público; las claves privadas residen solo en Railway.

`POST /api/auth/login` con `{ "password": "..." }` devuelve `{ "token": "..." }`. Guarda el token de sesión y envía `Authorization: Bearer <token>` en cada petición privada. Si llega 401, elimina el token y vuelve al login.

`GET /api/dashboard`, `POST /api/scan`, `GET /api/candidates?status=pending` muestran el radar. `POST /api/candidates/{id}/investigate`, `/draft` y `/submit` avanzan cada noticia a mano. Sin `OPENAI_API_KEY`, el servidor prepara un borrador basado en la fuente, pendiente de contraste y revisión editorial. `GET /api/review` devuelve las noticias preparadas. Edita `headline`, `graphic_summary` y **`body`** (máximo 2.200 caracteres) con `PUT /api/articles/{id}`; el servidor mantiene `social_text` sincronizado con `body`.

`GET /api/articles/{id}/photos`, `POST /api/articles/{id}/photo`, `POST /api/articles/{id}/research-more`, `POST /api/articles/{id}/approve`, `POST /api/articles/{id}/reject` gobiernan la revisión. Comprueba `publish_safe` y la licencia de cada foto antes de aprobarla.

`GET /api/articles/{id}/kit` devuelve texto y ruta del PNG únicamente después de una exportación Canva válida. `POST /api/articles/{id}/canva` crea el diseño desde la plantilla y lo exporta; `POST /api/articles/{id}/canva/export` vuelve a exportar el mismo diseño después de editarlo en Canva. El PNG `/media/render/{id}.png` **requiere token** y no existe antes de exportar: solicítalo con `fetch` y cabecera Bearer, conviértelo en `Blob` y usa `URL.createObjectURL(blob)` para `<img>` o descarga. Libera esa URL al desmontar la vista. No incluyas el token en la URL de imagen.

`GET /api/capabilities` y `GET /api/canva/template` muestran la única plantilla gráfica, `EAHWTjWEEnA`, y verifican `HEADLINE`, `SUMMARY`, `SECTION` y `PHOTO`. Se edita en Canva. `GET /api/sources`, `POST /api/sources`, `POST /api/sources/{id}/toggle` son privados.

`GET /api/health` es público y muestra si hay autenticación e IA configuradas. `POST /api/articles/{id}/publish` devuelve error mientras `PUBLISH_MODE=none`; oculta el botón de publicación si no está configurado. Esta publicación apunta a la web editorial, no a publicar el panel de Lovable.

CORS: en Railway configura `CORS_ORIGINS` con el origen exacto del panel de Lovable, por ejemplo `https://mi-proyecto.lovable.app`. No uses `*` para la API privada.

Construye el frontend privado “InfoLinense Desk” para consumir una API FastAPI externa indicada por `VITE_INFOLINENSE_API_URL`. Sustituye `https://TU_BACKEND` por el dominio real de Railway al crearlo. No uses mocks: si la API no responde, enseña el error.

No inventes datos ni simules respuestas. Toda la información debe venir de la API real.

IDENTIDAD
- Diseño editorial moderno, joven, muy visual y profesional.
- Azul InfoLinense #1F5EFF como color principal.
- Lima #C4E910 solo como acento.
- Mucho blanco, fotografía protagonista, bordes suaves, nada de dashboard corporativo genérico.
- Mobile first pero excelente en escritorio.

LOGIN
- Pantalla de acceso con contraseña.
- POST `${API}/api/auth/login` body JSON `{password}`.
- Guardar token en localStorage y retirarlo al salir o recibir 401.
- Todas las llamadas privadas incluyen `Authorization: Bearer ${token}`.
- Si API devuelve 401, volver a login.

MÓDULOS
1 Dashboard
2 Radar
3 Investigación
4 Redacción
5 Fotografías
6 Revisión
7 Kit de redes
8 Plantillas
9 Fuentes
10 Configuración

REGLAS
- Nunca muestres ni pidas OPENAI_API_KEY en el frontend.
- El texto de redes ES el campo body de la noticia y tiene máximo 2.200 caracteres. No crear un copy alternativo.
- Nunca generar fotografías con IA. Si `ai_image_suggestion` tiene contenido, mostrarlo en tarjeta aparte “Sugerencia IA — no generada”.
- La foto real seleccionada debe mostrar origen/licencia.

REVISIÓN
GET `/api/review`.
Cada tarjeta debe mostrar:
- preview `/media/render/{id}.png` usando el host de API. Esta ruta requiere Bearer; descargar como blob mediante fetch autenticado y mostrar con URL.createObjectURL. Nunca pasar el token por query string.
- sección
- headline
- graphic_summary
- body editable con contador /2200
- workflow: “Pieza rápida” o “Investigada”
- score y fuente
- origen/licencia de imagen
- ai_image_suggestion separada

Acciones:
- Guardar y regenerar → PUT `/api/articles/{id}` con las claves `headline`, `graphic_summary` y `body`. No enviar `social_text` como campo editable: el backend lo sincroniza con body.
- Cambiar foto → GET `/api/articles/{id}/photos`, galería y POST `/api/articles/{id}/photo`
- Investigar más → POST `/api/articles/{id}/research-more`
- Aprobar → POST `/api/articles/{id}/approve`
- Descartar → POST `/api/articles/{id}/reject`

KIT REDES
GET `/api/articles/{id}/kit`
Mostrar únicamente:
- PNG 1080×1350 final obtenido mediante fetch con Authorization: Bearer y descarga del blob
- texto completo (`text`) y contador
- Copiar texto
- Descargar imagen
- Publicar web (solo si status approved y `/api/health` informa `publish_mode` distinto de `none`) → POST `/api/articles/{id}/publish`
No mostrar “copy para Instagram”.

RADAR
- Botón “Buscar ahora” → POST `/api/scan`
- Mostrar candidatas y puntuación.
- Etiquetas de relevancia.
- Las menos relevantes que superen el umbral aparecen automáticamente ya terminadas en Revisión; no obligar al usuario a redactarlas manualmente.

PLANTILLAS
GET `/api/templates`, upload PPTX a `/api/templates/upload` multipart con file + name y POST `/api/templates/{id}/activate`.
Las nuevas plantillas deben tener una o 12 diapositivas, con `{{HEADLINE}}` en cada una; admiten `{{SUMMARY}}` y `{{SECTION}}` y un elemento gráfico `PHOTO` para la imagen principal.
Explica que el backend usa el PPTX original y no recrea el diseño con IA.

FUENTES
GET `/api/sources`, POST `/api/sources`, POST `/api/sources/{id}/toggle`.

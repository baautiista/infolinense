# Prompt para la web pública infolinense.com (Lovable)

Proyecto: https://lovable.dev/projects/86779499-193f-434e-8a78-635b130874b4

Copia todo lo que hay debajo de la línea y pégalo en el chat de ese proyecto.

---

Quiero que InfoLinense Desk (mi redacción, en Railway) publique noticias automáticamente en esta web. Conserva el diseño y todo lo que ya funciona.

1. Crea una función de servidor (Edge Function) llamada `publish-article`, accesible por POST desde fuera, sin login de usuario, protegida con un secreto:
   - Lee el secreto de la variable privada `DESK_PUBLISH_SECRET` (guárdala en los secretos del proyecto; nunca en el código del navegador).
   - Rechaza con 401 cualquier petición cuya cabecera `x-infolinense-secret` no coincida exactamente.
   - Acepta CORS solo para POST.

2. La petición llega con este JSON:
   `external_id` (texto único, p. ej. "desk-42"), `slug`, `title`, `subtitle`, `body` (texto plano con párrafos separados por saltos de línea), `body_html`, `section` (una de: OBRAS, CIUDAD, GIBRALTAR, SUCESOS, CULTURA, DEPORTES, COMERCIO, MEDIO AMBIENTE, POLÍTICA, SOCIEDAD, PATRIMONIO, AGENDA), `section_color` y `section_text_color` (hex), `source_url`, `source_name`, `sources` (lista de `{name,url}`), `image_base64` (JPEG en base64, foto principal), `image_content_type`, `social_image_base64` (PNG opcional), `published_at` (ISO), `status` ("published").

3. Qué debe hacer la función:
   - Subir `image_base64` a un bucket de almacenamiento público (por ejemplo `news-images`) con el nombre `<slug>.jpg` y obtener su URL pública. Igual con `social_image_base64` si llega (`<slug>-social.png`).
   - Guardar la noticia en la tabla de noticias que ya usa la web. Si no existe, créala con: id, external_id (único), slug (único), title, subtitle, body, body_html, section, section_color, section_text_color, image_url, social_image_url, source_url, source_name, sources (jsonb), published_at, created_at. Si ya existe una tabla de noticias, adapta los campos a ella en lugar de crear otra.
   - Hacer upsert por `external_id`: si la misma noticia llega dos veces, se actualiza y no se duplica.
   - Responder `{"url": "https://infolinense.com/noticia/<slug>"}` con la URL pública real de la noticia.

4. En la web:
   - La portada y la sección correspondiente muestran la noticia nada más guardarse, ordenadas por `published_at` (más reciente primero).
   - La página de la noticia (`/noticia/:slug`) muestra la etiqueta de sección con su color de fondo y de texto, el titular, la entradilla, la foto, el cuerpo en párrafos y, al final, «Fuente:» con el enlace a `source_url`.

5. Al terminar, dime la URL exacta de la función (algo como `https://<proyecto>.supabase.co/functions/v1/publish-article`) para configurarla en mi servidor.

---

## Después, en Railway (servicio infolinense-api → Variables)

- `PUBLISH_MODE` = `lovable`
- `LOVABLE_WEBHOOK_URL` = la URL de la función que te dé Lovable
- `PUBLISH_WEBHOOK_SECRET` = la misma cadena que pusiste en `DESK_PUBLISH_SECRET` (larga y aleatoria)

Con eso, el botón **Publicar** de cada noticia la aprueba y la sube a infolinense.com.

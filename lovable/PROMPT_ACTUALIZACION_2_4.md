# Prompt para pegar en Lovable (actualización 2.4)

Copia todo lo que hay debajo de la línea y pégalo en el chat del proyecto de Lovable.

---

Actualiza InfoLinense Desk para usar la versión 2.4 de la API (`VITE_INFOLINENSE_API_URL`). Conserva el diseño, el login y todo lo que ya funciona. No uses datos simulados: todo sale de la API. No añadas publicación automática en ningún sitio.

## 1. Botón «Redactar» en cada noticia (Inicio, Radar y Agenda)

Sustituye los botones separados «Investigar / Redactar / Editar borrador» por **un solo botón**:

- Texto: «Abrir borrador» si la noticia trae `article_id` (lo devuelve `GET /api/candidates?status=pending`); si no, «Redactar».
- Al pulsar: `POST /api/candidates/{id}/write?wait=45`.
  - Respuesta 200 con `state: "done"` → abre el editor con `article`. Si `action` es `"drafted"`, muestra «Borrador creado».
  - Respuesta 202 con `state: "working"` → muestra un indicador animado con el texto de `step_text` («Investigando la noticia y sus fuentes…» o «Redactando el borrador…») y consulta `GET /api/candidates/{id}/write` cada 3 segundos hasta que `state` sea `done` o `error`.
  - Error HTTP (502/503/4xx) → muestra el texto de `detail` tal cual (ya incluye el proveedor, el código HTTP y lo que respondió la IA) y un botón **Reintentar** que repite el POST.
- Mientras trabaja, desactiva el botón para evitar dobles clics. Si al cargar la lista una noticia trae `work_state: "working"`, empieza a consultar su estado automáticamente.
- El servidor nunca crea dos borradores para la misma noticia.

## 2. Prioridades y agenda

En cada noticia muestra cinco botones: Urgente (`urgent`), Hoy (`today`), Esta semana (`this_week`), Futura (`future`), No interesa (`no_interest`) → `POST /api/candidates/{id}/triage` con `{priority}`. Resalta el activo (`editorial_priority`).

Junto a una noticia con prioridad, muestra `planned_at` en un campo de fecha y hora. Al cambiarlo envía `{priority, planned_at: "YYYY-MM-DDTHH:MM:00"}` (hora de Madrid). Si `plan_locked` es 1 muestra la etiqueta «hora fijada por ti»; esa hora ya no se moverá cuando entren noticias nuevas. Para devolverla a automático, envía la prioridad sin `planned_at`.

Añade la sección **Agenda**: `GET /api/schedule` → lista ordenada por `planned_at`, agrupada por día, con hora grande, prioridad, etiqueta Edicto/Licitación/Exclusiva, estado (`article_status`) y `plan_reason`. Botón «Recalcular» → `POST /api/schedule/rebuild`.

En cada noticia muestra también fuente (`source_name`), fecha (`published_at`), enlace (`url`) y `local_angle` («Por qué importa aquí»).

## 3. Editor del borrador

Campos editables: Titular (`headline`), Entradilla (`subtitle`), Cuerpo (`body`, contador /2200), Texto para la imagen (`graphic_summary`). Guardar → `PUT /api/articles/{id}` con esas cuatro claves.

- **Siete titulares alternativos**: `headline_options_json` (lista JSON de 7). Muéstralos como botones; al pulsar uno se copia al campo Titular. Botón «Proponer otros 7» → `POST /api/articles/{id}/headlines` → `{headlines: [7]}`.
- **Datos que faltan**: `missing_data_json` (lista) en un aviso amarillo.
- **Fuentes**: `sources_json` (lista de `{name,url}`) como enlaces.
- «Enviar a Revisión» → `POST /api/candidates/{candidate_id}/submit`.

## 4. Fotos

- `GET /api/articles/{id}/photos` → galería. Cada foto: `source`, `author`, `license` y etiqueta verde «Uso permitido» si `publish_safe` es true o roja «Requiere permiso».
- Buscar foto libre: `GET /api/articles/{id}/photos?q=texto` (Wikimedia Commons).
- Elegir foto con uso permitido: `POST /api/articles/{id}/photo` con el objeto de la foto.
- Elegir foto «Requiere permiso»: pide «Permiso o licencia», «Autor» y una casilla «Tengo permiso para usarla»; envía el objeto con `license`, `author` y `confirm_permission: true`.
- Subir foto propia: `POST /api/articles/{id}/photo/upload` (multipart: `file`, `source`, `license`, `author`).
- Muestra siempre la procedencia de la foto elegida (`image_source`, `image_author`, `image_license`).

## 5. Carrusel

Si `carousel_suitable` es 1, muestra el aviso con `carousel_reason`. Botón «Analizar carrusel» → `POST /api/articles/{id}/carousel` → `{suitable, reason, slides:[{title,text,photo_query}]}`. Muestra las diapositivas. Botón «Crear diapositivas en Canva» → `POST /api/articles/{id}/carousel/design` con `{photo_urls:[image_url]}`.

## 6. Panel final «Listo para publicar»

Nueva sección. `GET /api/review` y, para cada noticia, `GET /api/articles/{id}/kit`:
- `copy_text` (titular + entradilla + cuerpo) con botón **Copiar texto**, y `text` con «Copiar solo cuerpo».
- Imagen: si `image_url` existe, es el PNG de Canva; si no, `photo_download_url` (foto original). **Ambas rutas requieren el token**: descárgalas con `fetch` y cabecera `Authorization: Bearer`, conviértelas en Blob y usa `URL.createObjectURL`. Botones «Descargar imagen» y «Descargar foto original».
- Carrusel: `carousel_download_url` (ZIP, también con token).
- `image_credit` debajo de la imagen y `missing_data` como recordatorio.
- Texto fijo: «InfoLinense Desk no publica nada automáticamente.» No muestres botón de publicar.

## 7. Errores de IA y Ajustes

- Cualquier error de la API: muestra `detail` completo, sin sustituirlo por un mensaje genérico. No escribas en el frontend textos como «OpenAI ha limitado temporalmente las peticiones o no queda saldo».
- En Ajustes, la tarjeta de IA lee `GET /api/health`: `ai_provider` (`anthropic` = Claude, `openai` = ChatGPT) y `ai_fallback`.
- Botón «Comprobar la IA» → `GET /api/ai/check` → para cada proveedor muestra nombre, si es principal o respaldo, modelo, `ok`, `message` y la respuesta real (`http_status`, `code`, `provider_message`).

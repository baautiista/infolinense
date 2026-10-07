# Área Campo de Gibraltar Desk 1.1

Mesa de exclusivas de Área Campo de Gibraltar. Misma forma de trabajar que InfoLinense Desk, en rojo, sin publicar en ningún sitio: busca, redacta y te deja todo listo para **copiar y descargar**.

## Novedades 1.1

- **Licitaciones primero.** En Ordenar, la vista principal es «Licitaciones de la comarca»: todas las de los 8 municipios, sus empresas municipales (Emalgesa, Emgisa…), la Mancomunidad/Arcgisa, la APBA, la Junta y el Estado, con filtros por municipio, fase (en plazo, adjudicada, formalizada, contrato menor…), importe, plazo y buscador.
  - Plataforma de Contratación completa: se sigue la cadena del ATOM hasta lo ya leído, así no se escapa ninguna (también **contratos menores**). El municipio sale del lugar de ejecución, el código postal o el organismo.
  - La misma licitación en varias fuentes se une; la adjudicación posterior es otra propuesta (es otra noticia).
  - Marca varias y pulsa **Pieza resumen** («Las licitaciones de la semana en…»).
  - La pieza del día prioriza licitaciones.
- **Carruseles con el estilo de Área.** La portada va siempre primero y el resto combina, según la noticia, 12 tipos de diapositiva: lista con barras, caja roja, pasos con flechas, lista + cifra, ficha de proyecto, pregunta y respuesta, foto anotada, texto + mapa, cifra gigante, tarjetas con iconos, mosaico y documento oficial. Vista previa en el panel, ==resaltado== en rojo, y **Generar imágenes del carrusel** crea los PNG 1080×1350 (van al ZIP). Logos en Ajustes.

## Qué hace

1. **Ordenar** — Radar de todo el Campo de Gibraltar (Algeciras, La Línea, San Roque, Los Barrios, Tarifa, Jimena, Castellar, San Martín del Tesorillo) en bloques: Exclusivas, Obras y urbanismo, Agenda y cultura, Instituciones, Nacionales adaptables y Efemérides. Arriba, la **pieza del día** (una diaria, elegida y redactada sola a las 7:00).
   - **Exclusivas**: Plataforma de Contratación del Sector Público (ATOM de perfiles y de plataformas agregadas, incluida la Junta), Gobierto, BOP Cádiz, BOE, BOJA, tablón de edictos de La Línea, presupuestos y plenos.
   - **Instituciones**: los ocho ayuntamientos, Mancomunidad/ARCGISA, Autoridad Portuaria (APBA), Junta, Gobierno de España, Diputación y Gibraltar (frontera, obras, acuerdos).
   - **Nacionales adaptables**: datos publicados por municipios («mapa de la renta», INE, Agencia Tributaria…) para convertirlos en «Así ha cambiado … en el Campo de Gibraltar».
   - **Efemérides**: calendario editable (Ajustes) con aniversarios redondos destacados.
   - Cada propuesta lleva **EXCLUSIVA** si ningún medio comarcal la ha contado, o «Ya lo tiene Europa Sur / 8Directo…».
   - Lo que **ya ha publicado Diario Área** no aparece (queda en «Ya en Área»). Si es una **actualización** (fase nueva, cifra nueva, días después) sí aparece, marcada.
2. **Redacción** — Al marcar Urgente/Hoy/Esta semana/Más adelante se redacta sola: titular, 5 alternativos, entradilla, texto, **copy de Instagram**, **carrusel** (5-8 diapositivas) y **propuesta de render** («así quedaría») cuando conviene.
3. **Revisar** — Editas todo, reordenas diapositivas, eliges imágenes (✓ y número de diapositiva):
   - **Imágenes del expediente**: descarga los PDF de la licitación/proyecto y saca sus imágenes grandes y los planos.
   - Foto de la fuente, de otros medios, búsqueda en internet y subida propia.
   - **Canva**: rellena la plantilla del carrusel y exporta cada página a PNG.
4. **Copiar** — Botones «Copiar» en cada texto, «Copiar todo», ZIP con imágenes marcadas + `textos.txt`. Al publicar, «Marcar como publicada».

## Despliegue en Railway (servicio nuevo, mismo repositorio)

1. En el proyecto de Railway: **New → GitHub Repo → baautiista/infolinense**. En *Settings → Source → Root Directory* pon `area`.
2. Añade un **volumen** montado en `/data`.
3. Variables:
   - `ADMIN_PASSWORD` (contraseña del panel) y `JWT_SECRET` (cadena aleatoria larga).
   - `DATA_DIR=/data`
   - `GEMINI_API_KEY` (gratis, aistudio.google.com) para redactar con IA. Opcional de pago: `AI_ALLOW_PAID=true` + `ANTHROPIC_API_KEY` u `OPENAI_API_KEY`; con `ANTHROPIC_WEB_SEARCH=true` o `OPENAI_WEB_SEARCH=true` además busca exclusivas en la web.
   - `PUBLIC_BASE_URL=https://<dominio-del-servicio>`
   - Canva (opcional): `CANVA_CLIENT_ID`, `CANVA_CLIENT_SECRET`. URL de retorno en Canva Developers: `https://<dominio>/api/canva/callback`. Permisos: `asset:read asset:write brandtemplate:content:read design:content:read design:content:write design:meta:read`.
4. Genera dominio y abre `https://<dominio>/api/health`.

Otras variables: `SCAN_INTERVAL_MINUTES` (45), `DAILY_PICK_HOUR` (7), `PLACSP_FIRST_PAGES` (12, primera lectura), `PLACSP_MAX_PAGES` (40), `TENDER_WINDOW_DAYS` (20), `AUTO_DRAFT` (true).

## Plantilla de Canva del carrusel

**Por tipos (recomendada):** una página por tipo de diapositiva y los cuadros nombrados `TIPO_CAMPO` (ver `guia/guia_canva_area.png` y Ajustes → Canva). Ejemplos: `PORTADA_TITULAR`, `PORTADA_ENTRADILLA`, `PORTADA_FOTO`, `LISTA_TITULO`, `LISTA_PUNTO1…4`, `FICHA_CIFRA`, `CALLES_CALLE1`/`CALLES_OBRA1`… Para repetir un tipo se duplica la página: `LISTA2_TITULO`. La app rellena solo las páginas que usa el carrusel y exporta esas, en orden. Gráficos fijos en la plantilla; el resaltado de palabras sueltas (==así==) no se puede hacer con Autocompletar, solo en los PNG que genera el panel.

**Numerada (antigua):**

Plantilla de marca con una página por diapositiva y estos nombres en Autocompletar: `TITULO_1`, `TEXTO_1`, `FOTO_1`, `ANTETITULO_1` … (1 = portada), además de `MUNICIPIO` y `NUM`. Se pega el enlace en Ajustes → Canva y el panel muestra qué campos reconoce.

## Estilo

La guía de redacción está en `app/estilo_area.md` y se puede editar.

## Pruebas

`python3 tests/test_area.py` (sin red).

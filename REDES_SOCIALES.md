# Publicar en Instagram, Facebook y TikTok

El panel publica en las redes **solo cuando pulsas «Publicar»**. Usa la imagen hecha en Canva (o el carrusel, si lo has creado) y el texto de la noticia, con estos hashtags al final: `#LaLínea #LaLíneaDeLaConcepción #InfoLinense` (se cambian con `SOCIAL_HASHTAGS`).

Antes de nada, comprueba que en Railway existe `PUBLIC_BASE_URL` con la dirección del panel (`https://…up.railway.app`). Las redes descargan la imagen desde ahí.

---

## 1. Facebook e Instagram (una sola conexión)

**Lo que necesitas:**
- Una **página de Facebook** de InfoLinense de la que seas administrador.
- La cuenta de Instagram tiene que ser **profesional** (Empresa o Creador) y estar **unida a esa página**. Esto se hace en Instagram: Configuración → Cuenta → Compartir en otras apps / Centro de cuentas.

**Pasos:**
1. Entra en https://developers.facebook.com → **Mis apps** → **Crear app** → tipo **Empresa** (Business).
2. Dentro de la app, añade el producto **Inicio de sesión con Facebook para empresas** (o «Facebook Login»).
3. En la configuración de ese producto, en **URI de redireccionamiento de OAuth válidos**, pega la dirección que aparece en el panel (Ajustes → Redes sociales). Termina en `/api/networks/meta/callback`.
4. Añade también el producto **Instagram** (API de Instagram con inicio de sesión de Facebook).
5. En **Configuración → Básica** copia el **Identificador de la app** y la **Clave secreta**.
6. En Railway → Variables, añade:
   - `META_APP_ID` = identificador de la app
   - `META_APP_SECRET` = clave secreta
7. Redespliega. En el panel, ve a **Ajustes → Redes sociales → Conectar Facebook e Instagram**. Acepta todos los permisos y marca tu página.
8. Si administras varias páginas, elige la de InfoLinense en el desplegable.

Mientras la app esté en «modo desarrollo» funciona para ti, porque eres el administrador de la app. No hace falta revisión de Meta para publicar en tu propia página.

---

## 2. TikTok

**Pasos:**
1. Entra en https://developers.tiktok.com → **Manage apps** → **Connect an app**.
2. Añade los productos **Login Kit** y **Content Posting API**. En Content Posting API activa **Direct Post**.
3. Permisos (scopes): `user.info.basic` y `video.publish`.
4. En **Login Kit → Redirect URI**, pega la dirección que aparece en el panel. Termina en `/api/networks/tiktok/callback`.
5. **Verifica la dirección de las imágenes.** En la app de TikTok, en **URL properties**, añade el **prefijo de URL** que aparece en el panel (termina en `/p/`). TikTok te dará un archivo con un nombre como `tiktokAbC123.txt` y un contenido. En Railway añade:
   - `TIKTOK_VERIFY_FILE` = el nombre del archivo (por ejemplo `tiktokAbC123.txt`)
   - `TIKTOK_VERIFY_CONTENT` = el texto que hay dentro del archivo

   Redespliega y pulsa **Verify** en TikTok.
6. Copia el **Client key** y el **Client secret** y añádelos en Railway:
   - `TIKTOK_CLIENT_KEY`
   - `TIKTOK_CLIENT_SECRET`
7. Redespliega. En el panel: **Ajustes → Redes sociales → Conectar TikTok**.

**Importante:** hasta que TikTok revise y apruebe tu app (botón *Submit for review*), sus normas solo permiten publicar en **privado** («Solo yo»). El panel lo hace así y te avisa. Cuando TikTok la apruebe, las publicaciones saldrán públicas sin cambiar nada.

---

## Cómo se usa

- En **4 Publicar** aparece «Publicar también en: Instagram, Facebook, TikTok» con casillas. El panel recuerda lo que marcaste.
- Al pulsar **Publicar** se envía a la web y a las redes marcadas. Cada red muestra ✓ con su enlace, o «reintentar» si falló. Si pasas el dedo o el ratón por encima, ves el motivo.
- Una noticia nunca se publica dos veces en la misma red.
- Si la noticia tiene carrusel exportado, se publica como carrusel: la imagen principal y luego las diapositivas.

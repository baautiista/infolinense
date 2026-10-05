# Publicar en Instagram, Facebook y TikTok

El panel publica en las redes **solo cuando pulsas «Publicar»**. Usa la imagen hecha en Canva (o el carrusel, si lo has creado) y el texto de la noticia, con estos hashtags al final: `#LaLínea #LaLíneaDeLaConcepción #InfoLinense` (se cambian con `SOCIAL_HASHTAGS`).

Antes de nada, comprueba que en Railway existe `PUBLIC_BASE_URL` con la dirección del panel (`https://…up.railway.app`). Las redes descargan la imagen desde ahí.

---

## 1. Instagram (directo, sin página de Facebook)

1. En https://developers.facebook.com abre tu app → **Añadir producto** → **Instagram** → **Configuración de la API con inicio de sesión de Instagram**.
2. En el apartado **3. Configurar el inicio de sesión de la empresa de Instagram**, pega como URL de redireccionamiento la dirección que aparece en el panel (termina en `/api/networks/instagram/callback`).
3. Copia el **Identificador de la app de Instagram** y la **Clave secreta de la app de Instagram** (son distintos de los de Meta) y ponlos en Railway:
   - `INSTAGRAM_APP_ID`
   - `INSTAGRAM_APP_SECRET`
4. Mientras la app esté en modo desarrollo: **Roles de la app → Roles → Añadir personas → Probador de Instagram** y escribe tu usuario de Instagram. Acepta la invitación en Instagram (Configuración → Apps y sitios web → Invitaciones de probador).
5. La cuenta de Instagram tiene que ser **profesional** (Empresa o Creador). No hace falta página de Facebook.
6. En el panel: **Ajustes → Redes sociales → Conectar Instagram**. El acceso dura 60 días y el panel lo renueva solo.

## Facebook (perfil personal)

Meta no permite publicar en perfiles personales de forma automática. En cada noticia hay un botón **«Compartir en Facebook»**: la primera pulsación prepara la imagen y el texto; la segunda abre el menú «Compartir» del móvil (elige Facebook) y deja el texto copiado para pegarlo. En el ordenador descarga la imagen, copia el texto y abre Facebook.

Si algún día creas una página de Facebook, se puede conectar desde Ajustes (apartado «Solo si algún día creas una página») con `META_APP_ID` y `META_APP_SECRET`.

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

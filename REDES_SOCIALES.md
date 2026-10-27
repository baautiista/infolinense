# Publicar en Instagram, Facebook y TikTok

El panel publica en las redes al pulsar «Publicar» o cuando llega la hora programada. Usa la imagen hecha en Canva (o el carrusel, si lo has creado) y el texto de la noticia, con estos hashtags al final: `#LaLínea #LaLíneaDeLaConcepción #InfoLinense` (se cambian con `SOCIAL_HASHTAGS`).

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

TikTok rechazó la app de InfoLinense para publicación directa porque se utiliza como herramienta interna. La conexión pública usa ahora Buffer y su API personal. No necesitas cambiar la cuenta a privada ni mantener la app de TikTok para Developers.

1. Crea una cuenta en https://buffer.com (plan Gratis) y conecta el TikTok público de InfoLinense en https://publish.buffer.com/settings/channels.
2. En https://publish.buffer.com/settings/api crea una **clave API personal**.
3. En el panel entra en **Ajustes → Redes sociales → TikTok**, pega la clave y pulsa **Conectar TikTok con Buffer**. Si la cuenta de Buffer tiene varios TikTok, el panel mostrará los ID de canal disponibles: pega el que corresponda a InfoLinense.
4. Al publicar una noticia o al llegar la hora programada, Desk envía a Buffer su imagen, texto y titular en modo **automático y publicar ahora**. La noticia aparece pendiente hasta que Buffer confirme que TikTok la publicó. Los errores se muestran en la noticia sin repetir envíos pendientes.

También puedes poner `BUFFER_API_KEY` y `BUFFER_TIKTOK_CHANNEL_ID` en Railway para InfoLinense; la conexión desde el panel evita tocar las variables. Buffer Gratis permite hasta diez publicaciones en cola por canal y la API está incluida. Se aplican los límites de publicación de TikTok y Buffer. Mantén la URL pública de las imágenes accesible hasta que Buffer publique.

---

## Cómo se usa

- En **4 Publicar** aparece «Publicar también en: Instagram, Facebook, TikTok» con casillas. El panel recuerda lo que marcaste.
- Al pulsar **Publicar** se envía a la web y a las redes marcadas. Cada red muestra ✓ con su enlace, o «reintentar» si falló. Si pasas el dedo o el ratón por encima, ves el motivo.
- Una noticia pendiente o ya publicada no se vuelve a enviar a la misma red.
- Si la noticia tiene carrusel exportado, se publica como carrusel: la imagen principal y luego las diapositivas.

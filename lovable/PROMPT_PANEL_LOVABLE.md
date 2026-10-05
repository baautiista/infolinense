# Prompt para el panel antiguo de Lovable (infolinense-desk.lovable.app)

El panel completo y actualizado está en el servidor de Railway:
https://infolinense-api-production.up.railway.app/

El proyecto de Lovable del panel se quedó con la versión antigua (comprobaciones de permisos de fotos, pantallas que ya no existen). Para no tener dos paneles distintos, pega esto en el chat de ese proyecto de Lovable:

---

Sustituye toda la aplicación por una sola página que redirija automáticamente a https://infolinense-api-production.up.railway.app/ (con window.location.replace al cargar). Muestra mientras tanto el texto «Abriendo InfoLinense Desk…» y un enlace a esa dirección por si la redirección no funciona. Elimina el resto de pantallas, comprobaciones de licencias o permisos de fotos y llamadas a la API.

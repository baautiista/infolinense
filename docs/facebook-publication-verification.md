# Confirmación de publicaciones de Facebook

## Hallazgos en el código

Desk marcaba `published` al recibir un ID sin leer el estado real. Para una foto
aceptaba `id` como alternativa a `post_id`, confundiendo el recurso con la entrada.
El feed no enviaba `published` explícitamente. La conexión OAuth podía elegir
la primera página con Instagram o la primera disponible si no encontraba el medio.
Un vídeo procesado se consideraba publicado sin comprobar el muro.

Estos defectos están comprobados en el código; no demuestran todavía la causa
concreta de la falta de acceso anónimo a una publicación de producción.

## Casos comunicados

Página esperada: https://www.facebook.com/elcofradelinense

Foto no visible: `1163216032910095`.
Foto visible: `1119644583933907`.
Ambas usan el álbum `180324204532621`. El tipo de enlace no explica la diferencia.
También se aportó una entrada visible con identificador pfbid. No se han borrado,
modificado ni vuelto a publicar estas entradas.

## Cambios

- Fotos individuales y carruseles: recursos sin publicar, seguidos de una entrada
  del feed con `published=true`. Se guarda el ID de esa entrada.
- Texto y enlaces usan el mismo feed con publicación explícita; el editor sigue
  conservando su requisito de imagen de Canva.
- El token debe identificar la página seleccionada y Meta debe confirmar que
  está publicada. Para Cofrade se resuelve además `elcofradelinense` y se compara
  su ID. Se respetan `COFRADE_META_PAGE_ID` / `COFRADE_META_PAGE_TOKEN` y los
  prefijos del resto de medios. No hay selección automática arbitraria en OAuth.
- Solo se confirma una entrada con autor correcto, `is_published=true`,
  `is_hidden=false`, privacidad `EVERYONE`, sin programación ni restricciones
  y presente en `published_posts` (hasta 1000 entradas). Campos ausentes,
  permisos insuficientes o formatos no soportados dejan el estado sin confirmar.
- Los intentos se reservan antes del envío. Se conserva el ID antes de verificar.
  Los intentos previos nunca vuelven a enviar contenido automáticamente, incluso
  si hubo timeout sin ID. «Comprobar estado» y el proceso periódico solo leen.
- Los vídeos requieren la entrada del muro, localizada por el vídeo adjunto;
  que termine el procesamiento no basta. Si Meta no ofrece esa relación, se
  mantiene pendiente de verificación.
- `facebook_delivery` guarda página de destino y evidencia de confirmación,
  sin tokens. No se altera retroactivamente el historial con la auditoría.

## Auditoría pendiente de producción (solo lectura)

El endpoint autenticado `GET /api/networks/facebook/audit` ofrece el diagnóstico.
También se puede ejecutar en el servidor con las conexiones existentes, sin imprimir variables:

```python
import json
from app.facebook_publishing import audit
print(json.dumps(audit(), ensure_ascii=False, indent=2))
```

Revisa las últimas 20 entradas de cada medio y devuelve solo IDs, estados y
los errores de comprobación, nunca las cuentas ni sus tokens. No cambia
publicaciones ni marca entradas del historial. Las dos bases locales de este
entorno no contienen cuentas Meta ni historial Facebook para contrastar.

Para la foto fallida hay que localizar además su entrada del muro y comparar
con la foto visible: autor, estado, privacidad y pertenencia a `published_posts`.
Revisar en el panel Meta el modo de la aplicación (desarrollo/activo), permisos
`pages_manage_posts` y `pages_read_engagement`, y en la página las restricciones
de edad/país y su estado. No son causas confirmadas. No se cambian restricciones
ni el modo de la aplicación automáticamente.

La confirmación con un token de página **no acredita acceso anónimo**. Tras
publicar contenido nuevo autorizado hay que abrir su permalink sin iniciar
sesión. Facebook puede imponer restricciones a nivel de página o aplicación
que no aparecen en el objeto Post. Sin acceso de producción no se puede
prometer visibilidad pública ni dar el incidente por resuelto.

No se han creado publicaciones reales para las pruebas ni desplegado cambios.

Prueba anónima desde este entorno: ambas fotos responden HTTP 200 con formulario
de inicio de sesión. Esto no permite distinguir su visibilidad; un HTTP 200 de
Facebook no acredita que el contenido sea público.

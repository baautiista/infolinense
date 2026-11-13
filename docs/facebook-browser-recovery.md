# Recuperación del lector de Facebook ocupado

## Diagnóstico

El aviso «El navegador está ocupado; espera a que termine la operación actual»
se emitía exclusivamente al llenar la cola de 32 comandos de
`facebook_browser.call`. Las operaciones se ejecutaban en un único hilo. Al
agotarse la espera, `Future.cancel()` no podía cancelar una operación ya en
marcha. Si Playwright quedaba bloqueado, el hilo seguía ocupado y las siguientes
peticiones llenaban la cola, incluidas consultas de estado y capturas.

El bloqueo se reprodujo con una operación detenida y peticiones concurrentes.
No se dispone de la traza del Chromium de producción para identificar qué
navegación o respuesta inició el atasco concreto del usuario.

## Corrección

- Playwright se ejecuta en un proceso hijo dedicado, con una sola operación
  activa. No se guardan peticiones de captura, lectura o escritura en una cola.
- El estado se consulta sin esperar al navegador. Expone si está ocupado, el
  tipo de operación y el tiempo transcurrido, sin URL, texto introducido ni cookies.
- Las operaciones tienen un límite de 60 segundos, o 120 para leer una página.
  Si no responden, se termina únicamente el proceso del navegador y sus hijos,
  identificados por descendencia y tiempo de creación para evitar PIDs reutilizados.
  La siguiente lectura crea otro proceso sobre el mismo perfil guardado.
- `POST /api/facebook/browser/restart`, autenticado, permite recuperar también
  una operación en curso sin borrar la sesión. En Ajustes está el botón
  «Recuperar navegador sin borrar sesión». Desconectar y borrar sigue siendo
  una acción distinta y explícita.
- Los avisos temporales responden HTTP 409 con `Retry-After`. La vista espera
  durante una lectura y reanuda las capturas. Al recuperar, descarta el texto
  pendiente; no lo reproduce en una sesión posterior.
- Las respuestas GraphQL se leen tras `requestfinished`, cuando su cuerpo ha
  terminado, en lugar de esperar un cuerpo posiblemente abierto en `response`.

Se conservan la validación de página/autores/fechas, las tarjetas habituales y
los controles de recursos del navegador. No se cambia la app de publicación,
no se usan servicios de pago ni se garantiza que Facebook entregue toda la semana.

## Validación

Pruebas de concurrencia, timeout, caída y recuperación del proceso; estado
accesible durante el bloqueo; terminación de los hijos propios; autenticación y
respuestas 409; Chromium real reutilizando un perfil con una cookie sintética
persistida; controles de la interfaz probados en Chromium. No se utiliza ninguna
cuenta ni contraseña real de Facebook.

```sh
.venv/bin/python -m unittest tests.test_facebook_browser_runtime tests.test_facebook_browser_controls tests.test_facebook_browser tests.test_facebook_public tests.test_facebook_reader tests.test_facebook_workflow tests.test_facebook_apify tests.test_facebook_confirmation tests.test_facebook_diagnostics -q
```

La búsqueda con la sesión real de producción queda pendiente de desplegar y
comprobar. La recuperación conserva el perfil guardado; si Facebook ha caducado
la sesión o exige una comprobación, el editor deberá completarla en Ajustes.

# Spec 004 — Logging

- **Estado:** aprobada (2026-10-07), con las decisiones de la sección 10
- **Fecha:** 2026-10-07
- **Referencia:** `alcampo-scraper/specs/003-alcampo-scraper-logging` (aprobada), adaptada a los fallos propios de Dia
- **Evidencia:** estado del código tras las specs 001–003; revisiones de las specs 001 y 003

## 1. Contexto y objetivo

Hoy el servicio deja **muy poco rastro**:

- **`LOG_LEVEL` se lee pero no se aplica**: nada configura `logging` (revisión de la 001).
- **Todo `502` es silencioso.** El handler de `UpstreamUnavailableError` responde sin registrar el `reason`, que se pensó "para logs" (001 plan-D7).
- **Los reintentos son invisibles**: ni cada reintento ni su agotamiento dejan huella.
- **Un bug no previsto** acaba en el `500` en texto plano de Starlette, sin contexto.
- **No hay forma de seguir una petición**: las pocas líneas que existen (unidad desconocida, discrepancia de CP, límites, enfriamiento, cierre de sesiones) no dicen a qué petición pertenecen.

Y hay fallos propios de Dia igual de silenciosos:

- **Productos descartados por el mapper** (001 RF-11): si Dia cambiara un campo, se descartarían **todos** y la API respondería `200 []`, como un "no hay resultados" legítimo.
- **Cache corrupta** (001 plan-D10): se trata como miss sin avisar.
- **Sesiones de Dia** (spec 002): se crean, renuevan y descartan sin que conste.
- **Bloqueo de Akamai** (spec 003): hoy deja un `WARNING`, cuando es el evento accionable del episodio.

**Objetivo:** que cualquier error o degradación deje una línea clara, con nivel adecuado y contexto suficiente, y que todas las líneas de una petición se puedan seguir por su request id. Sin dependencias nuevas (constitución #1) y sin filtrar secretos (constitución #12).

## 2. Usuarios y actores

- **Responsable del servicio en producción:** lee los logs en `stderr` para detectar y diagnosticar.
- **Sistema:** emite logs durante el ciclo de vida de la app y de cada petición.

## 3. Historias de usuario

- **H1.** Como responsable del servicio, quiero que cada error (Dia caído, reintentos agotados, bloqueo de Akamai, formato inesperado, bug) deje una línea clara con contexto.
- **H2.** Como responsable del servicio, quiero seguir todas las líneas de una petición aunque haya tráfico concurrente.
- **H3.** Como responsable del servicio, quiero enterarme de las degradaciones silenciosas (productos descartados, cache corrupta, sesiones que cambian, límites, enfriamiento), para detectar cambios en Dia antes que un consumidor.

## 4. Requisitos funcionales (EARS)

### A. Configuración

- **RF-1.** CUANDO arranque la aplicación (`lifespan`), EL sistema DEBERÁ configurar el logging una sola vez: nivel desde `LOG_LEVEL`, salida a `stderr` y formato `%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s`, solo con `logging` estándar.
- **RF-2.** SI `LOG_LEVEL` no es `DEBUG`, `INFO`, `WARNING`, `ERROR` o `CRITICAL` (sin distinguir mayúsculas), ENTONCES la aplicación DEBERÁ fallar al arrancar.

### B. Petición

- **RF-3.** CUANDO llegue una petición HTTP, EL sistema DEBERÁ generarle un request id (`uuid4().hex`) y registrar en `INFO` una línea de **inicio** (método, ruta y parámetros) y otra de **fin** (código y duración en ms), con el mismo id. Incluye las que fallan la validación (`422`).
- **RF-4.** Toda línea emitida durante la petición, también desde módulos que no conocen HTTP (scraper, reintentos, sesiones, puerta, cache), DEBERÁ llevar su request id. Fuera de una petición (arranque, parada), `-`.
- **RF-5.** EL sistema DEBERÁ devolver el request id en la cabecera `X-Request-ID`, también en `404`, `422`, `500` y `502`. El `X-Request-ID` que mande el cliente DEBERÁ ignorarse (D1).

### C. Errores

- **RF-6.** CUANDO una excepción no controlada llegue al límite de la app, EL sistema DEBERÁ registrarla en `ERROR` con traceback y responder `500 {"detail": "Internal server error"}` con un handler propio, sin el mensaje de la excepción en el cuerpo.
- **RF-7.** CUANDO una búsqueda termine en `502` por `UpstreamUnavailableError`, EL sistema DEBERÁ registrar en `ERROR` el `reason`, `postal_code` y `term`, **salvo** en las degradaciones previstas (enfriamiento activo, límite agotado): esas dejan un `WARNING` (D2).
- **RF-8.** CUANDO una búsqueda termine en `404` (CP sin servicio o página fuera de rango), EL sistema DEBERÁ registrar un `INFO` con el motivo: es una respuesta, no un fallo.

### D. Reintentos

- **RF-9.** Antes de cada reintento, EL sistema DEBERÁ registrar un `WARNING` con el intento, el motivo (código o tipo de error de red), la ruta (sin parámetros, D5) y la espera.
- **RF-10.** Al agotarse los reintentos, o ante un `4xx` no reintentable, EL sistema DEBERÁ registrar un `ERROR` con los intentos o el código, y la ruta.

### E. Fallos propios de Dia

- **RF-11.** CUANDO Akamai bloquee una petición, EL sistema DEBERÁ registrar un `ERROR` indicando la ruta y que empieza un enfriamiento de `AKAMAI_COOLDOWN_SECONDS` (o que ya había uno). Sustituye al `WARNING` actual de la puerta (D3).
- **RF-12.** SI la respuesta de Dia no es JSON o no tiene la forma esperada, ENTONCES EL sistema DEBERÁ registrar un `ERROR` con la ruta y el tipo de fallo.
- **RF-13.** CUANDO el mapper descarte productos, EL sistema DEBERÁ registrar **un** `WARNING` por respuesta con cuántos y sus `object_id` (si los tienen).
- **RF-14.** SI el mapper descarta **todos** los productos de una respuesta que traía alguno, ENTONCES EL sistema DEBERÁ registrarlo en `ERROR`, en lugar del `WARNING` de RF-13 (D4).
- **RF-15.** CUANDO una entrada de cache esté corrupta, EL sistema DEBERÁ registrar un `WARNING` con la clave.
- **RF-16.** EL sistema DEBERÁ registrar en `INFO` la creación de una sesión de Dia (CP y motivo: nueva, por edad o por discrepancia) y en `INFO` su retiro (CP y motivo: edad, LRU, discrepancia) (D6).

### F. Seguridad de los logs

- **RF-17.** Todo valor que venga del cliente (`term`, `postal_code`, parámetros, ruta) DEBERÁ registrarse con `%r`, para que saltos de línea y caracteres de control no fabriquen líneas falsas.
- **RF-18.** EL sistema NO DEBERÁ registrar nunca cookies (`session_id`, `_abck`, `bm_sz`, `ak_bmsc`, `bm_sv`, `h163j1mz`), cabeceras completas, ni cuerpos de respuesta de Dia.

## 5. Requisitos no funcionales

- **RNF-1. Sin dependencias nuevas:** `logging` estándar; nada de `structlog` ni similares.
- **RNF-2. Logging síncrono aceptado** (excepción puntual a la constitución #3, como en Mercadona y Alcampo).
- **RNF-3. Tipado estricto:** middleware, filtro y handlers sin `Any`.
- **RNF-4. Un logger por módulo** (`logging.getLogger(__name__)`), como ya se hace.
- **RNF-5. Tests con `caplog`**, sin depender del formato salvo en RF-1.
- **RNF-6. Docs vivas:** README con `LOG_LEVEL`, formato y `X-Request-ID`.

## 6. Contrato

Sin cambios en los cuerpos. Novedades: cabecera de respuesta `X-Request-ID` y el `500` con cuerpo JSON propio.

## 7. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Parámetros inválidos (`422`) | inicio y fin registrados, con `X-Request-ID` (por eso va en un middleware) |
| Cliente envía `X-Request-ID: abc` | se ignora (RF-5) |
| Reintento fallido y luego éxito | solo `WARNING` por intento, sin `ERROR` |
| 100 búsquedas durante un enfriamiento | 100 `WARNING`, ningún `ERROR`; el único `ERROR` es el del bloqueo (RF-11) |
| Búsqueda sin resultados legítima | nada de RF-13/RF-14 |
| Todos los productos descartados | `ERROR` (RF-14); la respuesta sigue siendo `200 []` (D4) |
| `term` con salto de línea | escapado con `%r`, una sola línea (RF-17) |
| Respuesta de Dia con `Set-Cookie` | ninguna línea contiene sus valores (RF-18) |
| `LOG_LEVEL=debug` | válido; `LOG_LEVEL=VERBOSE` → no arranca |
| Redis caído | `500` (fuera de alcance), pero ahora registrado por RF-6 |

## 8. Fuera de alcance

- Logs en JSON, Sentry, Datadog, OpenTelemetry, métricas.
- Escritura a fichero y rotación (se captura `stderr`).
- Cambiar el access log de uvicorn (se desactiva al desplegar si molesta).
- Cambiar el comportamiento ante descarte total (D4).

## 9. Criterios de finalización

- [ ] RF-1…RF-18 cubiertos por tests en verde.
- [ ] Un test de integración demuestra que todas las líneas de una petición que reintenta y acaba en `502` comparten el request id de `X-Request-ID`.
- [ ] Un test demuestra que ninguna línea contiene los valores de las cookies que manda Dia.
- [ ] Un test demuestra que un `term` con salto de línea da una sola línea.
- [ ] `ruff`, `mypy` y `pytest` limpios; README actualizado.
- [ ] Verificación manual: `uvicorn` contra Redis falso o real, 1 búsqueda real y 1 inválida, y comprobar formato, request id e inicio/fin en consola.

## 10. Decisiones (dudas resueltas el 2026-10-07)

Todas con la opción recomendada en el borrador.

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Request id | Generado siempre (`uuid4().hex`), devuelto en `X-Request-ID`; el del cliente se ignora | RF-3, RF-5 |
| D2 | Degradaciones previstas | Enfriamiento y límites agotados → `WARNING` | RF-7 |
| D3 | Bloqueo de Akamai | `ERROR` | RF-11 |
| D4 | Descarte total | Solo `ERROR`; respuesta `200 []` como hoy | RF-14; el cambio de comportamiento queda para otra spec |
| D5 | Peticiones a Dia en el log | Solo la ruta, sin parámetros | RF-9, RF-10, RF-11, RF-12 |
| D6 | Eventos de sesión | `INFO` | RF-16 |

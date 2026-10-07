# Plan 004 — Logging

- **Estado:** aprobado (2026-10-07)
- **Fecha:** 2026-10-07
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D6**; las de este plan, **D1…**.
- **Base:** la implementación ya probada de Alcampo (`app/core/logging.py`, `app/middleware/request_context.py`), adaptada.

## 1. Visión general

```
RequestContextMiddleware (ASGI puro, el más externo)
  request_id = uuid4().hex  → contextvar
  INFO  request started method=GET path='/api/v1/products' params={...}
  ├─ app … cualquier logger.* de cualquier módulo lleva [request_id] (fábrica de LogRecord)
  │   ├─ handlers de dominio: 502 ERROR/WARNING, 404 INFO
  │   └─ excepción no controlada → ERROR con traceback → 500 JSON propio
  ├─ cabecera X-Request-ID en toda respuesta
  INFO  request finished status=200 duration_ms=…
```

## 2. Módulos

| Fichero | Cambio | RF |
|---|---|---|
| `app/core/config.py` | validar `LOG_LEVEL` | RF-2 |
| `app/core/logging.py` | **nuevo**: `configure_logging`, `request_id_var`, fábrica de `LogRecord`, `httpx` a `WARNING` | RF-1, RF-4, RF-18 |
| `app/middleware/request_context.py` | **nuevo**: request id, inicio/fin, `X-Request-ID`, `500` | RF-3…RF-6 |
| `app/exceptions.py` | `UpstreamThrottledError`, madre de `CooldownActiveError` y `OutboundRateLimitedError` | RF-7 |
| `app/main.py` | `configure_logging` en el `lifespan`; middleware; logs en los handlers `502` y `404` | RF-1, RF-7, RF-8 |
| `app/scrapers/retry.py` | `path`; `WARNING` por reintento, `ERROR` al agotar y en `4xx` | RF-9, RF-10 |
| `app/services/outbound.py` | `blocked(path)` → `ERROR` | RF-11 |
| `app/scrapers/dia_search.py`, `dia_session.py` | `ERROR` por cuerpo inesperado; pasan su ruta | RF-12 |
| `app/mappers/product_mapper.py` | `WARNING`/`ERROR` por descartes | RF-13, RF-14 |
| `app/services/search_cache.py` | `WARNING` por entrada corrupta | RF-15 |
| `app/services/postal_code_sessions.py` | `INFO` al crear y retirar sesiones | RF-16 |
| `README.md` | `LOG_LEVEL`, formato, `X-Request-ID` | RNF-6 |

## 3. Decisiones de diseño

**D1 — Request id por fábrica de `LogRecord`, no por `Filter`** (como Alcampo, su plan-D4). Un filtro de handler solo marcaría lo que pasa por nuestro handler; `caplog` no lo vería. La fábrica añade `record.request_id` a todo registro desde un `ContextVar` (`"-"` fuera de una petición).

**D2 — Handler propio, no `logging.basicConfig(force=True)`** (Alcampo plan-D3): `force` quitaría el handler de `caplog`. `configure_logging(level, stream=None)` sustituye solo su handler marcado y se puede llamar varias veces.

**D3 — Formato** `%(asctime)s %(levelname)s %(name)s [%(request_id)s] %(message)s` (spec RF-1).

**D4 — `httpx` a `WARNING`.** El logger `httpx` escribe en `INFO` `HTTP Request: GET https://www.dia.es/…?q=leche&page=…`: la URL completa, con el término del cliente sin escapar y el CP. Contradice spec-D5 y RF-17. Se sube a `WARNING`; nuestras propias líneas (RF-9…RF-12) dan la ruta y el resultado.
- *Descartada:* un filtro que reescriba la URL, como el `PageTokenRedactor` de Alcampo. Allí la línea de httpx era útil para medir el ritmo; aquí la puerta y los reintentos ya registran lo necesario.

**D5 — Middleware ASGI puro, el más externo, y captura de excepciones dentro** (Alcampo spec 006 plan-D2 y spec 003 plan-D2). `BaseHTTPMiddleware` registraba el fin antes de enviar el cuerpo; un `exception_handler(Exception)` corre fuera del middleware, sin request id ni `X-Request-ID`.

**D6 — `UpstreamThrottledError`** (como Alcampo): madre de `CooldownActiveError` y `OutboundRateLimitedError`, hija de `UpstreamUnavailableError`. El handler de `502` registra `WARNING` para ella y `ERROR` para el resto (spec-D2).

**D7 — `send_with_retry(..., path="-")`**: la ruta la pasa quien llama (`SEARCH_PATH`, `SAVE_SHIPPING_ADDRESS_PATH`), no se lee de la petición de httpx, para no arrastrar parámetros (spec-D5).

**D8 — `OutboundGate.blocked(path)`** registra un único `ERROR`: `akamai block path=%s cooldown=started seconds=300` o `cooldown=already_active`. Sustituye al `WARNING` de la 003 (spec-D3).

**D9 — Descartes del mapper en `map_search`**: cuenta los descartados y recoge los `object_id` que se puedan leer del crudo. Ninguno descartado → nada; algunos → `WARNING`; todos y había alguno → `ERROR` (spec-D4). Los ids se registran con `%r` (vienen de Dia, pero mejor no confiar).

**D10 — Motivo de creación de sesión.** El pool recuerda por CP el motivo del último retiro (`age`, `lru`, `mismatch`) y lo usa al crear la siguiente (`reason=renewal:age`, …); sin retiro previo, `reason=new`. `discard` pasa a recibir el motivo (`mismatch`).

## 4. Estrategia de test por RF

| RF | Test | Fichero |
|---|---|---|
| RF-1 | formato real en un `StringIO`; llamar dos veces no duplica líneas; `caplog` sigue viendo los registros | `tests/core/test_logging.py` |
| RF-2 | `LOG_LEVEL=debug` válido; `VERBOSE` → `ValidationError` | `test_config.py` |
| RF-3, RF-5 | inicio y fin con el mismo id; `X-Request-ID` en `200`, `422`, `404`, `502` y `500`; el del cliente se ignora | `tests/middleware/test_request_context.py` |
| RF-4 | una línea de un módulo cualquiera durante la petición lleva el id; fuera, `-` | logging, middleware |
| RF-6 | ruta que lanza `RuntimeError` → `500 {"detail": "Internal server error"}`, `ERROR` con traceback, sin el mensaje en el cuerpo | middleware |
| RF-7 | `UpstreamUnavailableError` → `ERROR` con `reason`, `postal_code`, `term`; `CooldownActiveError`/`OutboundRateLimitedError` → `WARNING` | `test_products_route.py` |
| RF-8 | CP sin servicio y página fuera de rango → `INFO` | route |
| RF-9, RF-10 | `503, 503, 200` → 2 `WARNING` con ruta, intento y espera; agotados → `ERROR`; `404` → `ERROR`; ninguna línea con `q=` | `test_retry.py` |
| RF-11 | bloqueo → 1 `ERROR` con ruta y `cooldown=started`; segundo → `cooldown=already_active` | `test_outbound.py` |
| RF-12 | cuerpo HTML / JSON sin `search_items` / `PUT` con respuesta inesperada → `ERROR` con ruta y tipo | `test_dia_search.py`, `test_dia_session.py` |
| RF-13, RF-14 | 1 roto de 3 → `WARNING` con su id; 3 de 3 → `ERROR`; 0 de 0 → nada | `test_map_search.py` |
| RF-15 | entrada corrupta → `WARNING` con la clave | `test_search_cache.py` |
| RF-16 | creación `reason=new`; por edad `renewal:age`; tras `discard(..., "mismatch")` `renewal:mismatch`; retiro LRU `lru` | `test_postal_code_sessions.py` |
| RF-17 | `term` con `\n` → una sola línea, escapado | middleware, integración |
| RF-18 | respuesta de Dia con `Set-Cookie: session_id=<sintético>` → ningún registro lo contiene; `httpx` no escribe en `INFO` | integración, logging |
| Request id de punta a punta | petición que reintenta y acaba en `502`: todas sus líneas con el id de `X-Request-ID` | integración |

## 5. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | La fábrica de `LogRecord` es global: se instala una sola vez y se encadena con la anterior | Igual que en Alcampo; test de doble llamada |
| R2 | Cambiar `discard(cp, session)` a `discard(cp, session, reason)` toca servicio, pool y sus tests | Parámetro con valor por defecto `"discarded"` |
| R3 | El access log de uvicorn duplica la línea de fin sin request id | Fuera de alcance (spec §8): `--no-access-log` al desplegar |

## 6. Secuencia y entrega

| Bloque | Tareas | Total aprox. |
|---|---|---|
| PR1 Configuración y middleware | T1–T3 | ~380 |
| PR2 Errores, reintentos y bloqueo | T4–T6 | ~320 |
| PR3 Fallos propios de Dia | T7–T10 | ~360 |
| PR4 Cableado, integración y docs | T11–T12 | ~250 |

4 PRs encadenados, cada uno en verde y por debajo de 400 líneas.

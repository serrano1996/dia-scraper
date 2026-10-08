# Tasks 004 — Logging

- **Estado:** aprobado (2026-10-07)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones de diseño citadas como plan-Dn)
- **Entrega:** 4 PRs encadenados (stacked). Cada PR deja la suite en verde.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**.
2. **GREEN:** el código mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .`, `mypy` y `pytest -q`, todo limpio. Marcar `[x]`, proponer el commit y **parar**.

Formato de commit: `<tipo>(004-dia-scraper-logging): <descripción en inglés> (Tn)`.

---

## PR1 — Configuración y middleware

### [x] T1 — Validar `LOG_LEVEL`
- **RED:** `test_config.py`: `debug`, `Info`, `WARNING` válidos y normalizados a mayúsculas; `VERBOSE` y `""` → `ValidationError`.
- **GREEN:** validador en `Settings`.
- **RF:** RF-2

### [x] T2 — `configure_logging`
- **RED:** `tests/core/test_logging.py`: con un `StringIO`, una línea tiene el formato de plan-D3 con `[-]` fuera de petición y `[<id>]` con `request_id_var` puesto; dos llamadas no duplican líneas; el nivel se aplica; `caplog` sigue capturando y ve `request_id`; el logger `httpx` queda en `WARNING`.
- **GREEN:** `app/core/logging.py` (plan-D1, D2, D3, D4).
- **RF:** RF-1, RF-4, RF-18

### [x] T3 — Middleware de petición
- **RED:** `tests/middleware/test_request_context.py`, con una app mínima: inicio y fin en `INFO` con el mismo id y la duración; `X-Request-ID` en `200`, `422` y `500`; un `X-Request-ID` del cliente se ignora; una excepción → `500 {"detail": "Internal server error"}`, `ERROR` con traceback y sin el mensaje en el cuerpo; parámetros con `\n` → escapados con `%r`.
- **GREEN:** `app/middleware/request_context.py` (plan-D5).
- **RF:** RF-3, RF-4, RF-5, RF-6, RF-17. **Fin de PR1.**

---

## PR2 — Errores, reintentos y bloqueo

### [x] T4 — Handlers y `UpstreamThrottledError`
- **RED:** `test_exceptions.py`: jerarquía de plan-D6. `test_products_route.py`: `UpstreamUnavailableError` → `ERROR` con `reason`, `postal_code` y `term`; `CooldownActiveError` y `OutboundRateLimitedError` → `WARNING`; `PostalCodeNotServedError` y `PageOutOfRangeError` → `INFO`; el middleware ya registrado en `create_app` pone `X-Request-ID` en `404` y `502`.
- **GREEN:** `exceptions.py`, `main.py` (middleware y logs en handlers).
- **RF:** RF-5, RF-7, RF-8

### [x] T5 — Logs de reintentos
- **RED:** `test_retry.py` con `caplog`: `503, 503, 200` → 2 `WARNING` con `path`, intento, motivo y espera; agotados → 1 `ERROR`; `404` → 1 `ERROR`; una línea nunca contiene `q=` (la ruta llega sin parámetros).
- **GREEN:** `send_with_retry(..., path=…)`; el scraper y la sesión pasan su ruta (plan-D7).
- **RF:** RF-9, RF-10

### [x] T6 — Bloqueo de Akamai en `ERROR`
- **RED:** `test_outbound.py`: `blocked(path)` → 1 `ERROR` con ruta, `cooldown=started` y segundos; el segundo → `ERROR` con `cooldown=already_active`; ya no hay `WARNING` de activación. `test_retry.py`: la puerta recibe la ruta.
- **GREEN:** `OutboundGate.blocked(path)` y el `Gate` de `retry.py` (plan-D8).
- **RF:** RF-11. **Fin de PR2.**

---

## PR3 — Fallos propios de Dia

### [x] T7 — Cuerpos inesperados
- **RED:** `test_dia_search.py`: HTML → `ERROR` con ruta y `kind=invalid_json`; JSON sin `search_items` → `kind=unexpected_schema`. `test_dia_session.py`: respuesta inesperada al `PUT` → `ERROR` con ruta y estado.
- **GREEN:** logs en `dia_search.py` y `dia_session.py`.
- **RF:** RF-12

### [x] T8 — Descartes del mapper
- **RED:** `test_map_search.py`: 1 roto de 3 → 1 `WARNING` con `discarded=1` y el id; 3 de 3 → 1 `ERROR`; sin resultados → ningún registro; duplicados no cuentan como descartes.
- **GREEN:** `map_search` (plan-D9).
- **RF:** RF-13, RF-14

### [x] T9 — Cache corrupta
- **RED:** `test_search_cache.py`: entrada corrupta → `WARNING` con la clave (`%r`).
- **GREEN:** `search_cache.py`.
- **RF:** RF-15

### [ ] T10 — Eventos de sesión
- **RED:** `test_postal_code_sessions.py`: CP nuevo → `INFO` `reason=new`; por edad → retiro `reason=age` y creación `reason=renewal:age`; LRU → retiro `reason=lru`; `discard(cp, s, reason="mismatch")` → retiro `reason=mismatch` y la siguiente creación `renewal:mismatch`. `test_product_service.py`: el servicio descarta con `reason="mismatch"`.
- **GREEN:** pool y servicio (plan-D10).
- **RF:** RF-16. **Fin de PR3.**

---

## PR4 — Cableado, integración y docs

### [ ] T11 — `lifespan` e integración
- **RED:** `test_main.py`: el `lifespan` llama a `configure_logging` con `LOG_LEVEL`. `tests/integration/test_logging.py`: una búsqueda que reintenta y acaba en `502` → todas sus líneas con el id de `X-Request-ID`; una respuesta de Dia con `Set-Cookie: session_id=<sintético>` → ningún registro contiene el valor; `term` con `\n` → ninguna línea partida; ningún registro de `httpx` en `INFO`.
- **GREEN:** `main.py` (`configure_logging` en el `lifespan`).
- **RF:** RF-1, RF-4, RF-17, RF-18

### [ ] T12 — Docs vivas y verificación manual
- **Hacer:** README (sección "Logs": `LOG_LEVEL`, formato, `X-Request-ID`, niveles por evento). Verificación manual: app real (con fakeredis si no hay Redis), 1 búsqueda real y 1 inválida; anotar las líneas observadas.
- **RF:** RNF-6. **Fin de PR4.**

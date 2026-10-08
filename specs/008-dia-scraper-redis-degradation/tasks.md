# Tasks 008 — Funcionar con Redis caído

- **Estado:** aprobado (2026-10-08)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones de diseño citadas como plan-Dn)
- **Entrega:** 1 PR.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**.
2. **GREEN:** lo mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .`, `mypy` y `pytest -q`, todo limpio. Marcar `[x]`, proponer el commit y **parar**.

Formato de commit: `<tipo>(008-dia-scraper-redis-degradation): <descripción en inglés> (Tn)`.

---

### [x] T1 — Timeouts de Redis
- **RED:** `test_config.py`: `redis_timeout_seconds` (2, `> 0`) y `redis_circuit_open_seconds` (10, `>= 0`). `test_main.py`: `create_redis` lleva `socket_connect_timeout` y `socket_timeout`; contra un socket mudo de `127.0.0.1`, `ping` lanza `TimeoutError` antes de `t + margen` (plan-D1, D8).
- **GREEN:** `config.py`, `create_redis`.
- **RF:** RF-1

### [ ] T2 — Circuit breaker
- **RED:** `tests/services/test_redis_circuit.py` con reloj falso: abre con `RedisError` (`WARNING` con el tipo, sin el mensaje), abierto no llama, prueba tras `open_seconds`, cierra con `INFO` o reabre; un error ajeno a Redis no lo abre; `open_seconds=0` nunca abre pero registra `WARNING` por fallo (plan-D2, D3).
- **GREEN:** `app/services/redis_circuit.py`.
- **RF:** RF-2, RF-3, RF-9

### [ ] T3 — Caches sin Redis
- **RED:** `tests/redis_doubles.py` (plan-D8); `test_search_cache.py` y `test_postal_code_cache.py`: con `BrokenRedis` (`DOWN`, `HUNG`) y con el circuito abierto, `get` → `None`, `is_marked` → `False`, `set`/`mark` sin excepción, `DEBUG` (plan-D4).
- **GREEN:** `search_cache.py`, `postal_code_cache.py` con `circuit` opcional.
- **RF:** RF-4, RF-5

### [ ] T4 — Enfriamiento sin Redis
- **RED:** `test_cooldown.py`: sin Redis, `activate` arranca uno local (`True`, luego `False`) y `is_active` lo ve hasta que vence; uno local sigue activo con Redis de vuelta y sin clave; con Redis sano, igual que hoy (plan-D5).
- **GREEN:** `LocalCooldown` y el respaldo en `cooldown.py`.
- **RF:** RF-6, RF-8

### [ ] T5 — Límites sin Redis
- **RED:** `test_rate_limiter.py`: sin Redis, `limit` admitidas y la siguiente rechazada; una entrada de exactamente `window` segundos ya no cuenta; `release` de un slot local lo libera; `release` con Redis caído no lanza; `limit=0` no toca nada (plan-D6).
- **GREEN:** `LocalRateLimiter` y el respaldo en `rate_limiter.py`.
- **RF:** RF-7

### [ ] T6 — Cableado y `/ready`
- **RED:** `tests/integration/test_redis_degradation.py`: búsqueda `200` con `DOWN` y `HUNG` sin `ERROR`, `/health` `200`; tras el primer fallo la segunda búsqueda no toca Redis (`CountingBrokenRedis`), un único `WARNING` del circuito; sin Redis, con `DIA_RATE_LIMIT=2` (el `PUT` y el `GET` de la primera) la segunda búsqueda distinta da `502`, y tras un `403` de Akamai la siguiente da `502` sin llamar a Dia. `tests/integration/test_ready.py`: `200`/`503` con los cuerpos de RF-10, sin `X-API-Key`, sin rutas de Dia llamadas (plan-D7).
- **GREEN:** `main.py` (circuito en el `lifespan`, pasado a la puerta, los limitadores y `AppResources`; `/ready`), `state.py`, `dependencies.py`.
- **RF:** RF-3, RF-9, RF-10, H1, H2

### [ ] T7 — Docs
- **Hacer:** README: qué pasa sin Redis (caches saltadas, protecciones locales, tráfico `instancias × límite`), `/ready` frente a `/health`, las dos variables. Dar al usuario las líneas de `.env.example` (`REDIS_TIMEOUT_SECONDS=2`, `REDIS_CIRCUIT_OPEN_SECONDS=10`).
- **RF:** RNF-4

### [ ] T8 — Verificación manual con `docker compose`
- **Hacer:** con el daemon, el guion de la spec §9: búsqueda con Redis, `docker compose stop redis`, búsqueda (`200`, tiempo acotado), `/ready` `503`, el `WARNING` del circuito en los logs y ningún `ERROR`; `docker compose start redis`, `/ready` `200`. Con 1–2 búsquedas reales a Dia, despacio. Anotar el resultado aquí.
- **RF:** criterios de finalización

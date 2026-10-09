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

### [x] T2 — Circuit breaker
- **RED:** `tests/services/test_redis_circuit.py` con reloj falso: abre con `RedisError` (`WARNING` con el tipo, sin el mensaje), abierto no llama, prueba tras `open_seconds`, cierra con `INFO` o reabre; un error ajeno a Redis no lo abre; `open_seconds=0` nunca abre pero registra `WARNING` por fallo (plan-D2, D3).
- **GREEN:** `app/services/redis_circuit.py`.
- **RF:** RF-2, RF-3, RF-9

### [x] T3 — Caches sin Redis
- **RED:** `tests/redis_doubles.py` (plan-D8); `test_search_cache.py` y `test_postal_code_cache.py`: con `BrokenRedis` (`DOWN`, `HUNG`) y con el circuito abierto, `get` → `None`, `is_marked` → `False`, `set`/`mark` sin excepción, `DEBUG` (plan-D4).
- **GREEN:** `search_cache.py`, `postal_code_cache.py` con `circuit` opcional.
- **Nota:** `BrokenRedis` lanza al acceder al comando (como el doble de Alcampo), no desde una corrutina: cubre igual comandos y `pipeline()`, porque el circuito llama a la operación dentro de su `try`.
- **RF:** RF-4, RF-5

### [x] T4 — Enfriamiento sin Redis
- **RED:** `test_cooldown.py`: sin Redis, `activate` arranca uno local (`True`, luego `False`) y `is_active` lo ve hasta que vence; uno local sigue activo con Redis de vuelta y sin clave; con Redis sano, igual que hoy (plan-D5).
- **GREEN:** `LocalCooldown` y el respaldo en `cooldown.py`.
- **RF:** RF-6, RF-8

### [x] T5 — Límites sin Redis
- **RED:** `test_rate_limiter.py`: sin Redis, `limit` admitidas y la siguiente rechazada; una entrada de exactamente `window` segundos ya no cuenta; `release` de un slot local lo libera; `release` con Redis caído no lanza; `limit=0` no toca nada (plan-D6).
- **GREEN:** `LocalRateLimiter` y el respaldo en `rate_limiter.py`.
- **RF:** RF-7

### [x] T6 — Cableado y `/ready`
- **RED:** `tests/integration/test_redis_degradation.py`: búsqueda `200` con `DOWN` y `HUNG` sin `ERROR`, `/health` `200`; tras el primer fallo la segunda búsqueda no toca Redis (`CountingBrokenRedis`), un único `WARNING` del circuito; sin Redis, con `DIA_RATE_LIMIT=1` la segunda búsqueda distinta da `502`, y tras un `403` de Akamai la siguiente da `502` sin llamar a Dia. `tests/integration/test_ready.py`: `200`/`503` con los cuerpos de RF-10, sin `X-API-Key`, sin rutas de Dia llamadas (plan-D7).
- **GREEN:** `main.py` (circuito en el `lifespan`, pasado a la puerta, los limitadores y `AppResources`; `/ready`), `state.py`, `dependencies.py`.
- **Nota:** los tests usan el CP 28041, el predeterminado de Dia: su sesión no hace `PUT`, así que cada búsqueda es una sola petición y basta `DIA_RATE_LIMIT=1` (el borrador decía 2 suponiendo un `PUT`). En el RED, cuatro tests de búsqueda ya pasaban: los respaldos de T3–T5 funcionan con el circuito desactivado por defecto; fallaban los que exigen un único circuito compartido y `/ready`.
- **RF:** RF-3, RF-9, RF-10, H1, H2

### [x] T7 — Docs
- **Hacer:** README: qué pasa sin Redis (caches saltadas, protecciones locales, tráfico `instancias × límite`), `/ready` frente a `/health`, las dos variables. Dar al usuario las líneas de `.env.example` (`REDIS_TIMEOUT_SECONDS=2`, `REDIS_CIRCUIT_OPEN_SECONDS=10`).
- **RF:** RNF-4

### [x] T8 — Verificación manual con `docker compose`
- **Hacer:** con el daemon, el guion de la spec §9: búsqueda con Redis, `docker compose stop redis`, búsqueda (`200`, tiempo acotado), `/ready` `503`, el `WARNING` del circuito en los logs y ningún `ERROR`; `docker compose start redis`, `/ready` `200`. Con 1–2 búsquedas reales a Dia, despacio. Anotar el resultado aquí.
- **RF:** criterios de finalización

- **Resultado (2026-10-09, imagen de `a4ac0fe`, CP 28041):**

  | Paso | Resultado |
  |---|---|
  | Búsqueda `leche` con Redis | `200` en 0,74 s, 50 productos; queda `search:28041:leche:1:50`; `/ready` `200` |
  | `docker compose stop redis` y búsqueda `agua` | `200` en 0,48 s; `/ready` `503`; `/health` `200` |
  | Búsqueda `leche` otra vez | `200` en 0,61 s, desde Dia (sin cache) |
  | Logs | un único `WARNING redis circuit open seconds=10 error=ConnectionError`; ningún `ERROR` y ningún `Future exception was never retrieved` (R1 no aparece) |
  | `/ready` pasado el circuito, Redis aún parado | `503` en 2,04 s: `TimeoutError` al resolver `redis`, acotado por `REDIS_TIMEOUT_SECONDS`; el circuito se reabre con su `WARNING` |
  | `docker compose start redis` y `/ready` | `200` (0,04 s); `INFO redis circuit closed` |

  Fueron **3 búsquedas reales** a Dia, una más de las 1–2 previstas: la tercera comprobaba que sin Redis no hay cache. Sin `.env` en el repo, la API recibió `DIA_BASE_URL` y un token aleatorio de un solo uso por un override temporal de compose fuera del repo, borrado al terminar.


---

## Revisión con contexto nuevo (2026-10-09)

Revisión adversarial de `c1f2b47..HEAD` antes del PR: 0 CRITICAL, 4 WARNING, 4 SUGGESTION. Confirmados reproduciéndolos: (1) pasado el periodo del circuito, **todas** las llamadas concurrentes prueban Redis (20 de 20), y contra un Redis colgado cada una paga el timeout, contra RNF-2; (2) si el `ZREM` que devuelve el hueco rechazado falla, `acquire` cae a la ventana local vacía y **admite una petición que Redis había rechazado**.

### [x] T9 — Correcciones de la revisión
- **RED:**
  - `test_redis_circuit.py`: pasado el periodo, solo una llamada prueba Redis y las concurrentes siguen viendo el circuito abierto; una prueba fallida da un solo `WARNING`.
  - `test_rate_limiter.py`: con el `ZREM` de la devolución fallando, la petición sigue rechazada; el rechazo de Redis ya no se lanza dentro de la operación del circuito (una prueba que acaba en "límite agotado" cierra el circuito, porque Redis respondió).
  - `test_cooldown.py`: un enfriamiento activado en Redis sigue activo en el proceso si Redis cae después.
  - `redis_doubles.py`: los comandos fallan al esperarlos (`await`), como el cliente real, y `pipeline()` devuelve un pipeline cuyo `execute` falla.
  - `test_main.py`: el test del socket mudo con un margen holgado (`< 2.5 s` frente a los ~5 s sin timeout), para no fallar en un runner lento.
- **GREEN:** `redis_circuit.py` (una sola prueba a la vez), `rate_limiter.py` (`_acquire_in_redis` devuelve si admite; la devolución va aparte y su fallo no admite), `cooldown.py` (activar en Redis también activa en local).
- **Docs:** README ("solo una petición por periodo paga el timeout"); docstring del limitador: un hueco cuyo pipeline se ejecutó pero cuya respuesta se perdió cuenta en Redis y en local hasta que vence la ventana (aceptado, acotado).
- **Hecho así:**
  - Los dobles más realistas no rompieron ningún test.
  - El test de T4 `test_with_redis_up_nothing_is_kept_locally`, que fijaba lo contrario, pasa a `test_another_instance_sees_only_the_shared_key`.
  - **Aceptado:** el proceso que ve un bloqueo arranca su enfriamiento local aunque Redis diga que otra instancia ya había empezado uno. En ese proceso la pausa puede durar hasta `AKAMAI_COOLDOWN_SECONDS` desde su propio bloqueo, un poco más que la del resto; nunca más corta.
- **Hallazgos no corregidos:**
  - El `aclose` de Redis al apagar no va por el circuito: comprobado que `aclose()` no lanza tras un fallo contra un puerto muerto (`127.0.0.1:1`).
  - El `.venv` local es Python 3.14, mientras que la CI y la imagen usan 3.11: los tests pasan en ambos.

- **CI en GitHub (2026-10-09, run 37902144814, tras el push de `60df3da`):** ✅ `success`, todos los pasos en verde. Con Python 3.11 pasan **648 tests en 7,28 s**, incluidos el del socket mudo y los de concurrencia del circuito. También pasa `docker build`.


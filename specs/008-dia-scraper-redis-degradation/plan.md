# Plan 008 — Funcionar con Redis caído

- **Estado:** aprobado (2026-10-08)
- **Fecha:** 2026-10-08
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D6**; las de este plan, **D1…**.
- **Base:** `alcampo-scraper/app/services/redis_circuit.py`, `waf_cooldown.py` (`LocalCooldown`), `rate_limiter.py` (`LocalRateLimiter`), `app/main.py` (`create_redis`, `/ready`) y `tests/services/redis_doubles.py` (sus specs 007 y 012).

## 1. Ficheros

| Fichero | Cambio | RF |
|---|---|---|
| `app/core/config.py` | `redis_timeout_seconds: float = 2` (`> 0`), `redis_circuit_open_seconds: int = 10` (`>= 0`) | RF-1, RF-3 |
| `app/main.py` | `create_redis` con `socket_connect_timeout` y `socket_timeout`; un `RedisCircuitBreaker` en el `lifespan`, pasado a todo lo que usa Redis; `GET /ready` | RF-1, RF-3, RF-10 |
| `app/services/redis_circuit.py` | **nuevo**: `RedisCircuitBreaker`, `RedisCircuitOpenError(RedisError)` | RF-2, RF-3, RF-9 |
| `app/services/search_cache.py`, `postal_code_cache.py` | `RedisError` → miss / no marcado; escritura omitida | RF-4, RF-5 |
| `app/services/cooldown.py` | `LocalCooldown` + respaldo; lo local se mira primero | RF-6, RF-8 |
| `app/services/rate_limiter.py` | `LocalRateLimiter` + respaldo en `acquire` y `release` | RF-7 |
| `app/core/state.py`, `app/core/dependencies.py` | `AppResources.circuit`; los repositorios por petición lo reciben | RF-3 |
| `tests/redis_doubles.py` | **nuevo**: `BrokenRedis(error)` (cada comando lanza `error`) y `CountingBrokenRedis` (cuenta intentos) | RNF-3 |
| `README.md` | sección "Sin Redis" y `/ready` | RNF-4 |

`.env.example`: no lo toca el agente; el usuario añade las dos líneas que se le den (T7).

## 2. Decisiones de diseño

**D1 — Timeouts del cliente, sin reintentos ocultos** (spec-D1): `redis.from_url(url, socket_connect_timeout=t, socket_timeout=t)`. Comprobado con redis 8.1.0 (el del lock): contra un socket que escucha y nunca responde, `ping()` lanza `redis.exceptions.TimeoutError` (un `RedisError`) a los 0,31 s con `t=0.3`, y un Redis apagado da `ConnectionError`. La conexión no reintenta por defecto (`retry._retries == 0`), así que una operación cuesta como mucho `t`. Un test lo fija con un socket real de `127.0.0.1` (D8): si una versión futura cambia el valor por defecto, el test lo detecta.

**D2 — Un circuito por proceso, envuelto en cada operación** (spec-D2, RF-3): como en Alcampo, `circuit.call(lambda: redis.op(...))`.
- Abierto: lanza `RedisCircuitOpenError` sin tocar Redis.
- Un `RedisError` lo abre durante `open_seconds` (reloj `time.monotonic`, inyectable).
- Pasado ese tiempo, la siguiente llamada prueba: si sale bien, el circuito se cierra con un `INFO`; si falla, se vuelve a abrir.
- Varias pruebas concurrentes son posibles; cada una está acotada por D1.
- `RedisCircuitOpenError` hereda de `RedisError`, así que los respaldos capturan solo `RedisError`.

**D3 — Logs: el circuito es el único que avisa** (spec-D5, RF-9).
- `WARNING redis circuit open seconds=10 error=TimeoutError` al abrirse: el tipo, nunca el mensaje, que puede llevar la URL de Redis con contraseña. Mismo criterio que la spec 004 para los errores sin manejar.
- `INFO redis circuit closed` al cerrarse.
- Cada respaldo registra `DEBUG redis unavailable op=…`.
- Con `REDIS_CIRCUIT_OPEN_SECONDS=0` el circuito nunca se abre, pero registra el `WARNING` de cada fallo: sin él, un Redis caído no dejaría rastro. Diferencia con Alcampo, donde el respaldo también da un `WARNING` en cada fallo real y por eso cada fallo aparece dos veces.

**D4 — Respaldos dentro de cada repositorio** (spec-D3): `ProductService`, `OutboundGate` y `PostalCodeSessions` no cambian; cada clase que habla con Redis captura `RedisError` y degrada.
- `SearchCacheRepository.get` → `None`; `set` → nada.
- `NotServedRepository.is_marked` → `False`; `mark` → nada (spec-D4).
- `AkamaiCooldown`, `RateLimiter`: los respaldos de D5 y D6.

Así, la decisión de qué hacer sin Redis queda junto al dato que se pierde, y los llamantes siguen igual. El parámetro `circuit` es opcional en los constructores y por defecto es un circuito desactivado, de modo que los tests actuales construyen igual.

**D5 — `LocalCooldown`, mirado antes que Redis** (RF-6, RF-8).
- En memoria: `until = now + seconds`; `activate` devuelve `False` si ya está activo, igual que `SET NX`.
- `is_active()` = `local.is_active() or redis.exists(...)` (si Redis falla, solo lo local). Así un enfriamiento iniciado sin Redis sigue respetándose cuando Redis vuelve sin la clave (RF-8).
- `activate()` intenta `SET NX`; con `RedisError`, `local.activate()`.
- Un solo `AkamaiCooldown` por proceso (ya lo es: lo crea el `lifespan` dentro de la puerta), así que el respaldo vive dentro de él.

**D6 — `LocalRateLimiter` por limitador** (RF-7).
- Ventana deslizante sobre una `deque[(t, slot)]`, con la misma frontera que `ZREMRANGEBYSCORE -inf now-window`.
- `acquire`: Redis o, con `RedisError`, la ventana local con el mismo `slot`.
- `release(slot)`: primero lo local (si el slot se tomó ahí, se devuelve ahí) y, si no, `ZREM` por el circuito; con `RedisError`, nada (el slot caduca con la ventana).
- A diferencia de Alcampo, en Dia los dos `RateLimiter` se crean una sola vez en el `lifespan`, no en cada petición: cada uno lleva su propia ventana local, sin pasarla desde fuera.
- `limit == 0` sigue sin tocar nada.

**D7 — `/ready` pública, `PING` por el circuito** (spec-D6, RF-10).
- `200 {"status":"ready","redis":"ok"}` o `503 {"status":"unavailable","redis":"unreachable"}`.
- Con el circuito abierto, responde `503` sin hacer `PING`: no martillea a un Redis caído. Un Redis colgado tarda como mucho `REDIS_TIMEOUT_SECONDS`.
- Nunca llama a Dia: un tercero no debe marcar como no lista a la instancia.
- Fuera del router con `X-API-Key`, como `/health`.
- El `HEALTHCHECK` del `Dockerfile` sigue en `/health`: reiniciar el contenedor no arregla Redis.

**D8 — Dobles de Redis** (RNF-3).
- `tests/redis_doubles.py`: `BrokenRedis(error)` responde a cualquier atributo con una corrutina que lanza `error`, y a `pipeline()` con un contexto cuyo `execute` lanza. Constantes `DOWN = ConnectionError(...)` y `HUNG = TimeoutError(...)` de `redis.exceptions`.
- `CountingBrokenRedis` cuenta intentos (prueba que el circuito abierto no toca Redis).
- Para RF-1, un test con el cliente real contra un socket que escucha sin aceptar: el sistema completa el handshake TCP y nadie responde.

## 3. Estrategia de test

| RF | Test | Fichero |
|---|---|---|
| RF-1 | `create_redis` lleva los dos timeouts; contra un socket mudo, `ping` falla con `TimeoutError` en `≈ t` | `tests/test_main.py` |
| RF-2 | abre tras un `RedisError`; abierto no llama a la operación; tras `open_seconds` prueba y cierra o reabre; errores que no son de Redis pasan sin abrir | `tests/services/test_redis_circuit.py` |
| RF-3 | `open_seconds=0` nunca abre; un solo circuito compartido (integración: tras el primer fallo, la segunda búsqueda no toca Redis) | `test_redis_circuit.py`, `tests/integration/test_redis_degradation.py` |
| RF-4, RF-5 | con `BrokenRedis`, `get` → `None`, `is_marked` → `False`, escrituras sin excepción | `test_search_cache.py`, `test_postal_code_cache.py` |
| RF-6, RF-8 | sin Redis el enfriamiento se activa en local y frena; activo en local con Redis de vuelta y sin clave → sigue activo | `test_cooldown.py` |
| RF-7 | sin Redis el límite se aplica en local con la misma frontera; `release` de un slot local; `limit=0` | `test_rate_limiter.py` |
| RF-9 | `WARNING` al abrir con solo el tipo; `INFO` al cerrar; `DEBUG` en los respaldos; `WARNING` por fallo con el circuito desactivado | `test_redis_circuit.py`, integración |
| RF-10 | `/ready` `200` con fakeredis, `503` con `BrokenRedis`, sin `X-API-Key`, sin tocar Dia | `tests/integration/test_ready.py` |
| H1, H2 | búsqueda `200` con Redis `DOWN` y `HUNG`, sin `ERROR`; sin Redis, el límite global y un bloqueo de Akamai siguen frenando la siguiente petición | `test_redis_degradation.py` |

## 4. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | El `Future exception was never retrieved` de Alcampo (DNS de `redis` con el contenedor parado) | El circuito reduce los intentos a uno cada 10 s; se mira en la verificación manual y, si aparece, se documenta o se corrige en la tarea de la revisión |
| R2 | Varias instancias sin Redis multiplican el tráfico a Dia | Aceptado en la spec (§7); documentado en el README |

## 5. Entrega

Un PR (~350 líneas de código y tests).

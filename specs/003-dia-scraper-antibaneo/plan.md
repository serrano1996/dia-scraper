# Plan 003 — Medidas antibaneo

- **Estado:** borrador, pendiente de revisión
- **Fecha:** 2026-10-07
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D8**; las de este plan, **D1…**.

## 1. Visión general

Todas las peticiones a Dia ya pasan por `send_with_retry` (búsqueda en `DiaSearchScraper`, `PUT` en `DiaSession`). Esta spec le añade una **puerta** que se consulta antes de **cada intento**, reintentos incluidos:

```
send_with_retry(send, gate=…)
  para cada intento:
    gate.admit()          ── enfriamiento activo  → CooldownActiveError      → 502
                          ── límite global agotado → OutboundRateLimitedError → 502
    response = send()
    403 HTML de Akamai → gate.blocked() (activa el enfriamiento, WARNING) → UpstreamBlockedError → 502
    429 + Retry-After ≤ 60 s → esperar eso + jitter;  > 60 s → 502
    5xx / 429 sin Retry-After / red → backoff + jitter

PostalCodeSessions._create(cp)   (solo CPs ≠ 28041: los que hacen PUT)
    session_limiter.acquire()  ── agotado → OutboundRateLimitedError → 502 (WARNING)
    session.set_postal_code(cp)   ── pasa por la puerta como cualquier petición
```

La cache de búsquedas y la negativa se leen **antes** de llegar a la puerta, así que un hit o un `404` conocido se sirven durante un enfriamiento (spec RF-2) sin cambiar el servicio.

## 2. Módulos

| Fichero | Cambio | RF |
|---|---|---|
| `app/core/config.py` | 6 variables nuevas (§3) | RF-1, RF-4, RF-6, RF-7, RF-9 |
| `app/exceptions.py` | `CooldownActiveError`, `OutboundRateLimitedError` (ambas `UpstreamUnavailableError` → `502`) | RF-2, RF-5, RF-7 |
| `app/scrapers/retry.py` | jitter, `Retry-After` con tope, y la puerta en cada intento | RF-1, RF-4, RF-9, RF-10 |
| `app/services/cooldown.py` | **nuevo**: `AkamaiCooldown` (Redis, `SET NX EX`) | RF-1…RF-3 |
| `app/services/rate_limiter.py` | **nuevo**: `RateLimiter`, ventana deslizante en Redis | RF-4…RF-8 |
| `app/services/outbound.py` | **nuevo**: `OutboundGate` (`admit`, `blocked`) | RF-1, RF-2, RF-4, RF-5, RF-11 |
| `app/scrapers/dia_search.py`, `dia_session.py` | reciben la puerta y el `uniform`; se la pasan a `send_with_retry` | RF-1, RF-4 |
| `app/services/postal_code_sessions.py` | límite de sesiones nuevas antes del `PUT` | RF-7, RF-8 |
| `app/core/state.py`, `dependencies.py`, `main.py` | la puerta y el limitador de sesiones, uno por proceso | — |
| `README.md`, `.env.example` | docs vivas | RNF-3 |

## 3. Configuración y Redis

| Variable | Defecto | Regla |
|---|---|---|
| `AKAMAI_COOLDOWN_SECONDS` | 300 | > 0 |
| `DIA_RATE_LIMIT` | 30 | ≥ 0 (0 = sin límite) |
| `DIA_RATE_WINDOW_SECONDS` | 60 | > 0 |
| `NEW_SESSION_LIMIT` | 10 | ≥ 0 (0 = sin límite) |
| `NEW_SESSION_WINDOW_SECONDS` | 600 | > 0 |
| `RETRY_JITTER_MAX_S` | 0.3 | ≥ 0 |

| Clave Redis | Tipo | Vida |
|---|---|---|
| `akamai:cooldown` | string `"1"` | `AKAMAI_COOLDOWN_SECONDS` (`SET NX EX`: no se alarga, spec-D2) |
| `ratelimit:dia` | sorted set (un miembro por petición, puntuado con su instante) | `DIA_RATE_WINDOW_SECONDS` |
| `ratelimit:dia:new_sessions` | sorted set | `NEW_SESSION_WINDOW_SECONDS` |

## 4. Decisiones de diseño

**D1 — La puerta se consulta en cada intento, dentro de `send_with_retry`.** `send_with_retry(..., gate: OutboundGate | None = None)`: `await gate.admit()` antes de cada `send()`, y `await gate.blocked()` antes de lanzar `UpstreamBlockedError`. Así los reintentos cuentan para el límite (RF-4) y un bloqueo en cualquier petición (búsqueda o `PUT`) activa el enfriamiento (RF-1), sin duplicar lógica en el scraper y en la sesión.
- *Descartada:* comprobar la puerta en el servicio. No vería los reintentos ni el `PUT`, que ocurre dentro del pool.

**D2 — Ventana deslizante sobre un sorted set**, como Alcampo (spec 008 plan-D3): `ZREMRANGEBYSCORE` de lo que ha salido de la ventana, `ZCARD`, y `ZADD` si cabe, en un `MULTI`/`EXEC` con `WATCH` para que dos instancias no se cuelen a la vez. Un miembro único por petición (`uuid4`). `limit=0` no toca Redis.
- *Descartada:* ventana fija con `INCR`/`EXPIRE`. Deja pasar el doble del límite en la frontera entre ventanas, justo la ráfaga que se quiere evitar.

**D3 — Enfriamiento con `SET akamai:cooldown 1 NX EX <segundos>`.** `NX` hace que un segundo bloqueo no lo alargue (spec-D2). `is_active` = `EXISTS`.

**D4 — Las excepciones nuevas heredan de `UpstreamUnavailableError`**: el handler de `502` ya las cubre (RNF-4). No se reintentan: `send_with_retry` solo reintenta respuestas y errores de red.

**D5 — El límite de sesiones nuevas vive en el pool** (`PostalCodeSessions(session_limiter=…)`), y se consume en `_create` justo antes del `PUT`, solo para CPs distintos de `28041`. Las renovaciones pasan por `_create`, así que cuentan (RF-8). Durante un enfriamiento el `PUT` lo frena la puerta; el hueco ya consumido del límite de sesiones se acepta (es un hueco de un límite generoso, y un enfriamiento es raro).

**D6 — Jitter y `Retry-After` en `send_with_retry`**, con `uniform` y `now` inyectables (tests deterministas). `Retry-After` solo con `429`; segundos o fecha HTTP; fecha pasada = 0. Si la espera supera 60 s, `UpstreamUnavailableError` sin reintentar (spec RF-10, más estricta que Alcampo, que espera el tope).

**D7 — WARNING en la puerta** (RF-11): `"akamai cooldown activated seconds=%d"` y `"outbound limit reached limit=%s"`. Solo números y nombres de límite: nunca URLs con términos, cabeceras ni cookies.

**D8 — Una puerta y un limitador de sesiones por proceso**, creados en el `lifespan` y guardados en `AppResources`; la factoría de `DiaSession` y el provider del scraper reciben la misma puerta.

## 5. Estrategia de test por RF

| RF | Test | Tipo | Fichero |
|---|---|---|---|
| RF-1 | `403` HTML en búsqueda y en `PUT` → `akamai:cooldown` con TTL ≤ 300 | U + I | `test_retry.py`, integración |
| RF-2 | con enfriamiento: miss → `502` y 0 peticiones; hit → `200`; CP en cache negativa → `404`; CP nuevo → `502` y ningún `PUT` | U + I | `test_outbound.py`, integración |
| RF-3 | segundo bloqueo no cambia el TTL | U | `test_cooldown.py` |
| RF-4 | búsqueda con `503, 200` consume 2 huecos; el `PUT` consume 1 | U + I | `test_retry.py`, integración |
| RF-5 | límite agotado → `OutboundRateLimitedError` sin enviar; WARNING | U | `test_rate_limiter.py`, `test_outbound.py` |
| RF-6 | `limit=0` → sin límite y sin tocar Redis | U | rate limiter |
| RF-7 | `NEW_SESSION_LIMIT=1`: 2.º CP nuevo → `502`; búsqueda de un CP con sesión → `200` | U + I | `test_postal_code_sessions.py`, integración |
| RF-8 | renovar por edad consume hueco | U | sessions |
| RF-9 | esperas = backoff + `uniform(0, 0.3)` (`uniform` falso) | U | retry |
| RF-10 | `429` con `Retry-After: 2` y con fecha HTTP → espera eso + jitter; `Retry-After: 3600` → `502` sin reintento; `Retry-After` inválido → backoff | U | retry |
| RF-11 | WARNING al activar enfriamiento y al agotar cada límite, sin cookies | U | outbound, sessions |
| Varias instancias | dos apps sobre el mismo fakeredis comparten enfriamiento y límite | I | integración |

## 6. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | Los límites por defecto son estimaciones: pueden ser demasiado bajos (`502` evitables) o demasiado altos (no evitan un bloqueo) | Configurables; los WARNING de RF-11 dan los datos para ajustarlos |
| R2 | 5 min de enfriamiento pueden quedarse cortos si Akamai bloquea más | Configurable; el creciente queda preparado como siguiente paso (spec-D2) |
| R3 | Redis caído → `500` (también en la puerta) | Fuera de alcance, como en las specs 001–002 |
| R4 | `WATCH`/`MULTI` en fakeredis | fakeredis soporta transacciones; si algún caso no, se para y se avisa |

## 7. Secuencia y entrega

| Bloque | Tareas | Total aprox. |
|---|---|---|
| PR1 Config, excepciones, jitter y `Retry-After` | T1–T4 | ~330 |
| PR2 Enfriamiento, limitador y puerta | T5–T7 | ~380 |
| PR3 La puerta en las peticiones y el límite de sesiones | T8–T10 | ~300 |
| PR4 Cableado, integración y docs | T11–T13 | ~350 |

4 PRs encadenados, cada uno en verde y por debajo de 400 líneas.

## 8. Qué no cambia

Contrato, servicio de productos, mapper, pool de sesiones (salvo el límite), cache.

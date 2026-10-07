# Plan 002 — Búsqueda con el código postal real

- **Estado:** aprobado (2026-10-07)
- **Fecha:** 2026-10-07
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D9**; las de este plan, **D1…**.

## 1. Visión general

```
GET /api/v1/products?postal_code=08001&term=…
  ▼
ProductService.search(query)
  │ 1. search cache  search:08001:term:page:size ─────────────► hit: devolver (como en la 001)
  │ 2. cache negativa postal_code:not_served:08001 ──────────► sí: PostalCodeNotServedError → 404
  │ 3. sessions.get("08001")
  │      ├─ vigente → DiaSession existente
  │      └─ no hay / caducada → nueva DiaSession (+ PUT save-shipping-address, salvo 28041)
  │            ├─ 204 → se guarda en el pool
  │            ├─ 206 no_service → PostalCodeNotServedError → cache negativa → 404
  │            └─ otro → UpstreamUnavailableError → 502
  │ 4. scraper.search(term, …, client=session.client)
  │ 5. cart.postal_code == "08001"?  no → sessions.discard + repetir 3–4 una vez → si no, 502
  │ 6. mapear, cachear y devolver (como en la 001)
```

El scraper de búsqueda, el mapper, la paginación (`dia_window`) y el contrato **no cambian**. Lo nuevo es de dónde sale el `httpx.AsyncClient`: deja de ser uno por proceso y pasa a ser uno por CP.

## 2. Módulos

| Fichero | Cambio | RF |
|---|---|---|
| `app/core/config.py` | `SESSION_MAX_AGE_SECONDS`, `MAX_SESSIONS`, `POSTAL_CODE_NEGATIVE_CACHE_TTL_SECONDS` | RF-5, RF-13 |
| `app/exceptions.py` | `PostalCodeNotServedError(postal_code)` | RF-4 |
| `app/models/dia.py` | `DiaValidationError` (cuerpo del `206`) | RF-4 |
| `app/scrapers/dia_session.py` | **nuevo**: `DiaSession` (su cliente, su CP, `set_postal_code`) | RF-1, RF-2, RF-4, RF-6 |
| `app/services/in_flight.py` | **nuevo**: `InFlight[T]`, copia del `InFlightSearches` de Alcampo | RF-7 |
| `app/services/postal_code_sessions.py` | **nuevo**: `PostalCodeSessions`, el pool | RF-3, RF-7, RF-13, RF-14 |
| `app/services/postal_code_cache.py` | **nuevo**: `NotServedRepository`, la cache negativa | RF-5 |
| `app/services/product_service.py` | usa el pool y la cache negativa; comprueba `cart.postal_code` | RF-8…RF-12 |
| `app/core/state.py`, `dependencies.py`, `main.py` | el pool sustituye al cliente HTTP único; handler `404` | RF-4, RF-14 |
| `README.md`, `.env.example` | docs vivas | RNF-5 |

## 3. Modelo de datos

```python
# app/models/dia.py
class DiaNoService(BaseModel):  # extra="ignore"
    no_service: str = Field(min_length=1)


class DiaValidationError(BaseModel):  # extra="ignore"; cuerpo real del 206 (fixture)
    type: Literal["VALIDATION_ERROR"]
    message: DiaNoService
```

| Redis | Valor | TTL |
|---|---|---|
| `search:{postal_code}:{term}:{page}:{page_size}` | como en la 001 | `CACHE_TTL_SECONDS` |
| `postal_code:not_served:{postal_code}` | `"1"` | `POSTAL_CODE_NEGATIVE_CACHE_TTL_SECONDS` |

## 4. Decisiones de diseño

**D1 — `DiaSession` es dueña de su `httpx.AsyncClient`.** `DiaSession(client, postal_code)`; el cliente sale de la factoría de la 001 (`create_http_client`, mismas cabeceras de Chrome). Las cookies (`session_id` y las de Akamai) viven en su jar y nunca salen del proceso ni a los logs (RNF-4).
- *Descartada:* un cliente compartido pasando `cookies=` en cada petición. httpx desaconseja las cookies por petición y mezclaría los `Set-Cookie` de sesiones distintas en el mismo jar.

**D2 — `set_postal_code` usa `send_with_retry` y clasifica después.** El `206` es un `2xx`, así que `send_with_retry` lo devuelve. `DiaSession` decide: `204` → ok; `206` cuyo cuerpo valida contra `DiaValidationError` → `PostalCodeNotServedError`; cualquier otra respuesta `2xx` → `UpstreamUnavailableError("unexpected answer to save-shipping-address")`. Los `4xx`, `5xx`, Akamai y errores de red ya los resuelve `send_with_retry` (RF-6).

**D3 — "Un solo vuelo" con `InFlight`, no con `asyncio.Lock`** (cambio menor sobre spec-D3, mismo efecto). Es el `InFlightSearches` de Alcampo (spec 008), copiado y renombrado: el primer llamante para un CP lanza la creación de la sesión y los demás esperan la misma tarea vía `asyncio.shield`, así un cliente que se desconecta no cancela el `PUT` del que dependen los otros. Un `Lock` haría que cada espera cancelada propagase la cancelación al creador.

**D4 — Edad desde la creación, no desde el último uso.** `SESSION_MAX_AGE_SECONDS` cuenta desde el `PUT`. Aunque Dia probablemente renueva `session_id` con cada uso (Fase 0 §2, 🔶), no está verificado; renovar cada 50 min cuesta 1 petición y quita la duda. Si Dia la invalida antes, lo detecta RF-8.

**D5 — LRU por último uso para `MAX_SESSIONS`.** Al crear la sesión número 101 se descarta la que lleva más tiempo sin usarse (`OrderedDict.move_to_end` en cada `get`).

**D6 — Las sesiones descartadas se retiran, no se cierran al momento.** Una búsqueda puede estar usando la sesión que otra petición acaba de descartar (por edad, LRU o RF-9). Se mueven a una lista de retiradas con su instante, y se cierran las que llevan más de `RETIRE_GRACE_SECONDS = 120` (constante: el peor caso de una búsqueda es `RETRY_MAX_ATTEMPTS × HTTP_TIMEOUT_SECONDS` + esperas ≈ 32 s con los valores por defecto) al crear la siguiente. `aclose()` cierra todas, activas y retiradas (RF-14). Es la idea del `retired` de Alcampo, generalizada a un pool con LRU.

**D7 — `28041` crea su sesión sin `PUT`** (spec-D6) pero vive en el pool como cualquier otra, con su edad y su LRU. La constante `DEFAULT_POSTAL_CODE` de la 001 se reutiliza.

**D8 — La comprobación de `cart.postal_code` está en el servicio**, no en el scraper: solo el servicio sabe qué CP pidió el consumidor y puede pedir otra sesión al pool. En la segunda discrepancia: `logger.warning("postal code mismatch expected=%s got=%s", …)` y `UpstreamUnavailableError`.

**D9 — Orden de lecturas en Redis: search cache, luego cache negativa.** Un hit (el caso frecuente) cuesta una lectura, como hoy. Un CP sin servicio nunca tiene entradas de búsqueda, así que el orden no cambia el resultado.

**D10 — `PostalCodeNotServedError` no hereda de `UpstreamUnavailableError`**: es una respuesta, no un fallo (como en Alcampo). Handler propio → `404`.

**D11 — `AppResources` cambia `http_client` por `sessions: PostalCodeSessions`.** El pool se crea en el `lifespan` con una factoría `new_client = lambda: create_http_client(settings)`, inyectable en los tests para usar respx.

## 5. Estrategia de test por RF

| RF | Test | Tipo | Fichero |
|---|---|---|---|
| RF-1 | sesión nueva → 1 `PUT` a `save-shipping-address?new_postal_code=08001`, sin cuerpo, con cabeceras de Chrome | U + I | `tests/scrapers/test_dia_session.py`, integración |
| RF-2 | `204` → la misma sesión sirve la 2.ª búsqueda; 0 `PUT` nuevos | U + I | `test_postal_code_sessions.py`, integración |
| RF-3 | `28041` → 0 `PUT` | U | sessions |
| RF-4 | fixture real del `206` → `PostalCodeNotServedError`; API → `404 {"detail": "Postal code not served by Dia"}` | U + I | session, integración |
| RF-5 | tras un `206`, la 2.ª búsqueda del CP → `404` sin llamar a Dia; TTL de la clave | U + I | `test_postal_code_cache.py`, servicio, integración |
| RF-6 | `PUT` → `503×3`, `403` HTML, `404`, `200` inesperado, `206` con otro cuerpo → `UpstreamUnavailableError`/`UpstreamBlockedError`, nada cacheado | U + I | session, integración |
| RF-7 | 5 `get("08001")` simultáneos → 1 sesión creada; cancelar un llamante no cancela la creación | U | sessions |
| RF-8, RF-9 | 1.ª respuesta con `cart.postal_code=28041` para 08001 → sesión descartada, 2.ª sesión, búsqueda correcta; dos discrepancias → `502` + `WARNING`, nada cacheado | U + I | servicio, integración |
| RF-10 | `warehouse == postal_code` pedido | U | servicio |
| RF-11 | clave `search:08001:leche:1:50`; `28041` y `08001` no comparten entrada | U | servicio |
| RF-12 | hit → 0 sesiones creadas, 0 peticiones | U + I | servicio, integración |
| RF-13 | reloj falso: pasada la edad → sesión nueva; con `MAX_SESSIONS=2`, la 3.ª descarta la menos usada | U | sessions |
| RF-14 | retiradas cerradas tras la gracia, no antes; `aclose` cierra todo; el `lifespan` cierra el pool | U + I | sessions, `test_main.py` |

## 6. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | Muchos CPs distintos → muchos `PUT` y muchas sesiones de Akamai desde una IP | `MAX_SESSIONS` y la cache negativa; el límite por ventana se decide en la spec de anti-baneo (spec-D9). En la Fase 0, 7 `PUT` en 25 min sin bloqueo |
| R2 | Dia invalida sesiones antes de los 50 min | RF-8/RF-9 lo detectan y repiten una vez |
| R3 | Memoria: 100 clientes httpx abiertos | Cada uno es un pool de conexiones pequeño; 100 es asumible y configurable |
| R4 | Un CP "sin servicio" que pasa a tener servicio tarda 1 día en notarse | Aceptado (spec-D5) |
| R5 | Cerrar una sesión retirada en uso si una búsqueda dura más de 120 s | Imposible con los defaults (≈32 s de peor caso); si se suben timeouts o intentos, documentado junto a la constante |

## 7. Secuencia y entrega

| Bloque | Tareas | Código | Tests | Total |
|---|---|---|---|---|
| PR1 Base: config, excepción, `206`, `DiaSession`, cache negativa | T1–T4 | ~110 | ~220 | ~330 |
| PR2 Pool de sesiones | T5–T7 | ~130 | ~250 | ~380 |
| PR3 Servicio | T8–T10 | ~70 | ~230 | ~300 |
| PR4 Cableado, integración y docs | T11–T13 | ~40 | ~220 + docs ~60 | ~320 |
| **Total** | | **~350** | **~920** | **~1330** |

4 PRs encadenados, cada uno en verde y por debajo de 400 líneas.

## 8. Qué no cambia

Scraper de búsqueda, mapper, paginación, contrato de respuesta, política de reintentos.

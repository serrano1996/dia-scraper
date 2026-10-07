# Tasks 003 — Medidas antibaneo

- **Estado:** aprobado (2026-10-07)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones de diseño citadas como plan-Dn)
- **Entrega:** 4 PRs encadenados (stacked). Cada PR deja la suite en verde.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**.
2. **GREEN:** el código mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .`, `mypy` y `pytest -q`, todo limpio. Marcar `[x]`, proponer el commit y **parar**.

Formato de commit: `<tipo>(003-dia-scraper-antibaneo): <descripción en inglés> (Tn)`.

---

## PR1 — Configuración, excepciones, jitter y `Retry-After`

### [x] T1 — Settings
- **RED:** `tests/core/test_config.py`: defaults de las 6 variables de plan §3; el entorno las sobrescribe; `AKAMAI_COOLDOWN_SECONDS`, `DIA_RATE_WINDOW_SECONDS` y `NEW_SESSION_WINDOW_SECONDS` a `0` → `ValidationError`; `DIA_RATE_LIMIT`, `NEW_SESSION_LIMIT` y `RETRY_JITTER_MAX_S` aceptan `0` y rechazan negativos.
- **GREEN:** `app/core/config.py`.
- **RF:** RF-1, RF-4, RF-6, RF-7, RF-9

### [x] T2 — Excepciones
- **RED:** `tests/test_exceptions.py`: `CooldownActiveError` y `OutboundRateLimitedError` son `UpstreamUnavailableError` (y no `UpstreamBlockedError`).
- **GREEN:** `app/exceptions.py` (plan-D4).
- **RF:** RF-2, RF-5, RF-7

### [x] T3 — Jitter
- **RED:** `tests/scrapers/test_retry.py`: con `uniform` falso que devuelve `0.2`, `503, 503, 200` espera `[0.7, 1.2]`; `uniform` recibe `(0, jitter_max)`; con `jitter_max=0` las esperas son las de hoy.
- **GREEN:** `send_with_retry(..., jitter_max=0.0, uniform=random.uniform)` (plan-D6).
- **RF:** RF-9

### [x] T4 — `Retry-After`
- **RED:** en `test_retry.py`: `parse_retry_after` con segundos, fecha HTTP futura, fecha pasada (0), valor inválido (`None`); `429` con `Retry-After: 2` → espera `2 + jitter`; con fecha HTTP → su diferencia; `Retry-After: 3600` → `UpstreamUnavailableError` sin reintento; `429` sin cabecera → backoff; `Retry-After` en un `503` se ignora.
- **GREEN:** `parse_retry_after` y la espera de `429` (plan-D6).
- **RF:** RF-10. **Fin de PR1.**

---

## PR2 — Enfriamiento, limitador y puerta

### [x] T5 — Enfriamiento
- **RED:** `tests/services/test_cooldown.py` (fakeredis): inactivo al principio; `activate()` lo activa con TTL ≤ `AKAMAI_COOLDOWN_SECONDS` y devuelve `True`; un segundo `activate()` devuelve `False` y no cambia el TTL.
- **GREEN:** `app/services/cooldown.py` (plan-D3).
- **RF:** RF-1, RF-3

### [x] T6 — Limitador de ventana deslizante
- **RED:** `tests/services/test_rate_limiter.py` (fakeredis, reloj falso): `limit=2` → 2 `acquire` y el 3.º lanza `OutboundRateLimitedError`; al pasar la ventana vuelve a admitir; una entrada justo de `window` segundos ya no cuenta; claves distintas no se mezclan; `limit=0` nunca limita ni escribe en Redis; dos limitadores sobre el mismo Redis comparten la cuenta.
- **GREEN:** `app/services/rate_limiter.py` (plan-D2).
- **RF:** RF-4, RF-5, RF-6

### [x] T7 — `OutboundGate`
- **RED:** `tests/services/test_outbound.py`: `admit()` con enfriamiento activo → `CooldownActiveError` sin consumir hueco; con límite agotado → `OutboundRateLimitedError` y un WARNING con el nombre del límite; `blocked()` activa el enfriamiento y registra un WARNING con su duración solo la primera vez.
- **GREEN:** `app/services/outbound.py` (plan-D7).
- **RF:** RF-1, RF-2, RF-5, RF-11. **Fin de PR2.**

---

## PR3 — La puerta en las peticiones y el límite de sesiones

### [x] T8 — La puerta en `send_with_retry`
- **RED:** en `test_retry.py`, con una puerta falsa que cuenta: `admit` se llama antes de cada intento (3 con `503, 503, 200`); si `admit` lanza, `send` no se llama; un `403` de Akamai llama a `blocked` y luego lanza `UpstreamBlockedError`; sin puerta, todo como hoy.
- **GREEN:** parámetro `gate` (plan-D1).
- **RF:** RF-1, RF-4

### [x] T9 — Scraper y sesión pasan la puerta
- **RED:** `test_dia_search.py` y `test_dia_session.py`: con una puerta cuyo `admit` lanza `CooldownActiveError`, ni la búsqueda ni el `PUT` llegan a respx; un `403` de Akamai en cualquiera de los dos llama a `blocked`.
- **GREEN:** `DiaSearchScraper(gate=…, jitter_max=…)`, `DiaSession(gate=…)`.
- **RF:** RF-1, RF-2, RF-4

### [x] T10 — Límite de sesiones nuevas en el pool
- **RED:** `test_postal_code_sessions.py`: con `NEW_SESSION_LIMIT=1`, el 2.º CP nuevo → `OutboundRateLimitedError`, sin `PUT`; un CP con sesión vigente no consume ni se ve afectado; `28041` no consume; renovar por edad consume.
- **GREEN:** `PostalCodeSessions(session_limiter=…)` (plan-D5).
- **RF:** RF-7, RF-8. **Fin de PR3.**

---

## PR4 — Cableado, integración y docs

### [x] T11 — Cableado
- **RED:** `tests/test_main.py`: `AppResources` tiene `gate`; el pool usa el limitador de sesiones; una `DiaSession` creada por el pool y el scraper del provider comparten la misma puerta.
- **GREEN:** `state.py`, `dependencies.py`, `main.py` (plan-D8).

### [x] T12 — Integración
- **RED:** `tests/integration/test_antiban.py`:
  - `403` de Akamai → `502`; la siguiente búsqueda no cacheada → `502` con 0 peticiones; una cacheada → `200`; un CP en cache negativa → `404`;
  - `DIA_RATE_LIMIT=2`: la 3.ª búsqueda no cacheada → `502` sin petición;
  - `NEW_SESSION_LIMIT=1`: el 2.º CP nuevo → `502` sin `PUT`, y el 1.º sigue respondiendo;
  - dos apps sobre el mismo fakeredis comparten enfriamiento y límite.
- **Depende:** T11
- **RF:** RF-1…RF-8

### [ ] T13 — Docs vivas
- **Hacer:** README (sección "Protección frente a Akamai": enfriamiento, límites, jitter y `Retry-After`; tabla de configuración); `.env.example` (pedírselo al usuario si el agente no puede escribirlo). **Sin prueba manual de bloqueo** (spec §9): solo una búsqueda real para comprobar que la puerta no estorba el camino normal.
- **Depende:** T12
- **RF:** RNF-3. **Fin de PR4.**

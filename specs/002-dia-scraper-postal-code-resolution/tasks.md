# Tasks 002 — Búsqueda con el código postal real

- **Estado:** aprobado (2026-10-07)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones de diseño citadas como plan-Dn)
- **Entrega:** 4 PRs encadenados (stacked). Cada PR deja la suite en verde.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**.
2. **GREEN:** el código mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .`, `mypy` y `pytest -q`, todo limpio. Marcar `[x]`, proponer el commit y **parar**.

Formato de commit: `<tipo>(002-dia-scraper-postal-code-resolution): <descripción en inglés> (Tn)`.

---

## PR1 — Base

### [x] T1 — Settings nuevos
- **RED:** `tests/core/test_config.py`: defaults `SESSION_MAX_AGE_SECONDS=3000`, `MAX_SESSIONS=100`, `POSTAL_CODE_NEGATIVE_CACHE_TTL_SECONDS=86400`; el entorno los sobrescribe; `0` en cualquiera → `ValidationError`.
- **GREEN:** `app/core/config.py`.
- **RF:** RF-5, RF-13 (spec-D2, spec-D5)

### [x] T2 — Excepción y cuerpo del `206`
- **RED:**
  - `tests/test_exceptions.py`: `PostalCodeNotServedError("35001")` es `DiaScraperError`, **no** `UpstreamUnavailableError`, y expone `.postal_code`.
  - `tests/models/test_dia.py`: la fixture real `dia_save_shipping_address_no_service.json` valida contra `DiaValidationError`; sin `message.no_service`, con `type` distinto o con `no_service` vacío → `ValidationError`.
- **GREEN:** `app/exceptions.py`, `app/models/dia.py` (plan §3, plan-D10).
- **RF:** RF-4

### [x] T3 — `DiaSession.set_postal_code`
- **RED:** `tests/scrapers/test_dia_session.py`, con respx:
  - `204` → 1 `PUT` a `/api/v1/common-aggregator/save-shipping-address` con `new_postal_code=08001`, sin cuerpo; `session.postal_code == "08001"`;
  - el `PUT` lleva las cabeceras de Chrome y las cookies que dejó una respuesta previa (`session_id` sintético);
  - `206` con la fixture real → `PostalCodeNotServedError("08001")`;
  - `206` con otro cuerpo, `200 {}` → `UpstreamUnavailableError`;
  - `503, 503, 204` → éxito tras reintentos (sin esperas reales); `403` HTML → `UpstreamBlockedError` con 1 llamada;
  - `aclose()` cierra su cliente.
- **GREEN:** `app/scrapers/dia_session.py` (plan-D1, plan-D2).
- **Depende:** T2
- **RF:** RF-1, RF-2, RF-4, RF-6

### [x] T4 — Cache negativa
- **RED:** `tests/services/test_postal_code_cache.py` con fakeredis: sin entrada → `False`; `mark("35001", ttl)` → `True` y TTL aplicado en `postal_code:not_served:35001`; otro CP → `False`.
- **GREEN:** `app/services/postal_code_cache.py`.
- **RF:** RF-5 (spec-D5). **Fin de PR1.**

---

## PR2 — Pool de sesiones

### [x] T5 — `InFlight` y creación de sesiones
- **RED:**
  - `tests/services/test_in_flight.py` (adaptado de Alcampo): llamadas simultáneas con la misma clave → 1 ejecución, mismo resultado; la excepción llega a todos; cancelar un llamante no cancela la tarea; tras terminar, la clave se olvida.
  - `tests/services/test_postal_code_sessions.py`, con factoría de sesiones falsa y reloj falso: `get("08001")` crea y fija el CP; un 2.º `get` devuelve la misma sesión sin otro `PUT`; `get("28041")` no hace `PUT`; 5 `get("08001")` simultáneos → 1 sesión; un `PostalCodeNotServedError` o `UpstreamUnavailableError` se propaga, la sesión fallida se cierra y no se guarda.
- **GREEN:** `app/services/in_flight.py`, `app/services/postal_code_sessions.py` (plan-D3, plan-D7).
- **Depende:** T3
- **RF:** RF-2, RF-3, RF-7

### [x] T6 — Edad, LRU y retiro
- **RED:** en `test_postal_code_sessions.py`:
  - pasados `SESSION_MAX_AGE_SECONDS` desde la creación → sesión nueva; la vieja queda retirada, no cerrada;
  - con `max_sessions=2`: `get(A)`, `get(B)`, `get(A)`, `get(C)` → se retira B (la menos usada), no A;
  - `discard(cp, session)` retira esa sesión; si el pool ya tiene otra para ese CP, no la toca;
  - las retiradas se cierran al crear una sesión pasados `RETIRE_GRACE_SECONDS`, no antes;
  - `aclose()` cierra activas y retiradas.
- **GREEN:** completar `PostalCodeSessions` (plan-D4, plan-D5, plan-D6).
- **Depende:** T5
- **RF:** RF-9, RF-13, RF-14

### [x] T7 — Refactor del pool y cobertura de bordes
- **RED:** `MAX_SESSIONS=1` con dos CPs alternos → nunca más de 1 activa; un `discard` de una sesión que ya no está en el pool no falla.
- **GREEN/Refactor:** lo mínimo; revisar la complejidad cognitiva (Sonar) del pool.
- **Depende:** T6
- **RF:** RF-13. **Fin de PR2.**

---

## PR3 — Servicio

### [ ] T8 — El servicio usa la sesión del CP
- **RED:** `tests/services/test_product_service.py`, con un pool falso:
  - la búsqueda usa `session.client` de `sessions.get("08001")`;
  - la clave de cache es `search:08001:leche:1:50`; `28041` y `08001` no comparten entrada;
  - un hit no llama al pool;
  - los tests de la 001 se adaptan: el servicio ya no recibe `http_client`.
- **GREEN:** `ProductService(sessions=…)` (plan-D9, plan-D11).
- **Depende:** T5
- **RF:** RF-10, RF-11, RF-12

### [ ] T9 — Nunca un CP por otro
- **RED:**
  - respuesta con `cart.postal_code="28041"` para `08001` y luego otra correcta → `discard` de la 1.ª sesión, 2 búsquedas, respuesta correcta y cacheada;
  - dos discrepancias → `UpstreamUnavailableError`, `WARNING` con ambos CPs en `caplog`, nada en Redis;
  - `warehouse` es siempre el CP pedido.
- **GREEN:** comprobación en `ProductService` (plan-D8).
- **Depende:** T8
- **RF:** RF-8, RF-9, RF-10 (spec-D7)

### [ ] T10 — CP sin servicio
- **RED:**
  - `sessions.get` lanza `PostalCodeNotServedError` → se propaga y queda marcado en la cache negativa con su TTL;
  - CP marcado → `PostalCodeNotServedError` sin llamar al pool;
  - un `UpstreamUnavailableError` del `PUT` no marca nada.
- **GREEN:** cache negativa en `ProductService`.
- **Depende:** T4, T8
- **RF:** RF-4, RF-5, RF-6. **Fin de PR3.**

---

## PR4 — Cableado, integración y docs

### [ ] T11 — Estado, `lifespan` y handler `404`
- **RED:**
  - `tests/test_main.py`: el `lifespan` crea el pool y lo cierra al apagar (activas y retiradas); `AppResources` ya no tiene `http_client`;
  - `tests/api/test_products_route.py`: `PostalCodeNotServedError` → `404 {"detail": "Postal code not served by Dia"}`.
- **GREEN:** `app/core/state.py`, `dependencies.py`, `main.py` (plan-D11).
- **Depende:** T10
- **RF:** RF-4, RF-14

### [ ] T12 — Integración
- **RED:** `tests/integration/`, con respx para el `PUT` y la búsqueda:
  - CP nuevo → 1 `PUT` + 1 búsqueda; otro término del mismo CP → 1 búsqueda; la misma búsqueda → 0;
  - `28041` → 0 `PUT`;
  - `206` → `404`; repetir → `404` sin peticiones;
  - `cart.postal_code` distinto en la 1.ª respuesta → 2 `PUT`, 2 búsquedas y `200` correcto;
  - `PUT` bloqueado por Akamai → `502`, 1 llamada, nada en Redis;
  - test de paridad de la 001 en verde.
- **Depende:** T11
- **RF:** RF-1…RF-12

### [ ] T13 — Docs vivas y prueba manual
- **Hacer:** README (quitar la limitación del CP por defecto, añadir el `404` y las variables nuevas); `.env.example` (si el agente no puede escribirlo, pedírselo al usuario); prueba manual contra Dia real: `08001` (CP nuevo, `PUT` + búsqueda), `08001` otro término (sin `PUT`), `35001` (`404`) y `35001` otra vez (sin peticiones). Unas 3–4 peticiones reales, anotadas aquí.
- **Depende:** T12
- **RF:** RNF-5, criterios de finalización. **Fin de PR4.**

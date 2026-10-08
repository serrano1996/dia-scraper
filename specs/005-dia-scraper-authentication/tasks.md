# Tasks 005 — Autenticación

- **Estado:** aprobado (2026-10-08)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones de diseño citadas como plan-Dn)
- **Entrega:** 2 PRs encadenados (stacked). Cada PR deja la suite en verde.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**.
2. **GREEN:** el código mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .`, `mypy` y `pytest -q`, todo limpio. Marcar `[x]`, proponer el commit y **parar**.

Formato de commit: `<tipo>(005-dia-scraper-authentication): <descripción en inglés> (Tn)`.

---

## PR1 — Configuración, comprobación y router

### [x] T1 — `API_KEYS`
- **RED:** `test_config.py`: sin variable → `frozenset()`; `" a , ,b "` → `{"a", "b"}`; `"a,b"` no falla (no se lee como JSON); `" , ,"` → vacío; `repr(settings)` no contiene los tokens.
- **GREEN:** `Settings.api_keys` (plan-D1).
- **RF:** RF-8, RF-11

### [x] T2 — `is_valid_api_key`
- **RED:** `tests/core/test_security.py`: coincide el exacto; `None`, `""`, con espacios y con otras mayúsculas → `False`; varios tokens → cualquiera vale; un candidato no ASCII (`"ñ"`) → `False` sin excepción; conjunto vacío → siempre `False`.
- **GREEN:** `app/core/security.py` (plan-D2).
- **RF:** RF-4, RF-5

### [x] T3 — `require_api_key` en el router
- **RED:** `test_products_route.py`, con la app real y su `lifespan` (fakeredis): sin cabecera, vacía e inválida → `401 {"detail": "Invalid or missing API key"}` y `WWW-Authenticate: ApiKey`; token inválido y `postal_code` inválido → `401`, no `422`; token válido → llega al servicio. Los tests de ruta existentes pasan a sustituir `require_api_key` (plan-D7).
- **GREEN:** `require_api_key` y `dependencies=[Security(require_api_key)]` en el router (plan-D3, D4, D5).
- **RF:** RF-1, RF-2, RF-3, RF-7

### [x] T4 — Tests existentes con token
- **RED:** con T3 hecho, los tests de integración y `test_main.py` que llaman a `/api/v1` fallan con `401`.
- **GREEN:** el harness de integración fija `API_KEYS` sintético y la cabecera por defecto; `test_main.py` igual (plan-D7). Sin cambios en `app/`.
- **RF:** — **Fin de PR1.**
- **Cambio al implementar (2026-10-08):** se hace en el mismo commit que T3. Con la dependencia en el router, 34 tests de integración y de `test_main.py` daban `401`; separarlo dejaba un commit con la suite en rojo, contra la regla de cierre de cada tarea.

---

## PR2 — Logs, `/health`, integración y docs

### [x] T5 — Logs del rechazo y arranque sin tokens
- **RED:** rechazo → 1 `WARNING` con `reason=missing|invalid` y `path`, sin el valor; arranque con `API_KEYS` vacía → `WARNING` y `401` en todo `/api/v1`.
- **GREEN:** log en `require_api_key`; `WARNING` en el `lifespan`.
- **RF:** RF-9, RF-10, RF-12

### [ ] T6 — Ocultar parámetros con nombre de secreto
- **RED:** `test_request_context.py`: `?api_key=x&Token=y&KEY=z&term=token` → los tres primeros como `'***'` y `term` intacto; el valor real no aparece en ningún registro.
- **GREEN:** `redact_params` en el middleware, antes del `repr` y del tope (plan-D6).
- **RF:** RF-15

### [ ] T7 — `/health`, OpenAPI e integración
- **RED:** `tests/integration/test_auth.py`: `/health` → `200 {"status": "ok"}` sin token y sin tocar Redis; `/docs` y `/openapi.json` sin token → `200`; sin token: 0 peticiones a Dia y Redis sin claves nuevas, también con un CP nuevo y durante un enfriamiento; `401` con `X-Request-ID`; ni el token recibido ni los configurados en ningún registro (nivel `DEBUG`); el OpenAPI declara `APIKeyHeader` para `/api/v1/products`.
- **GREEN:** `GET /health` en `main.py`.
- **RF:** RF-1, RF-6, RF-13, RF-14, RF-16

### [ ] T8 — Docs vivas y verificación manual
- **Hacer:** README (sección "Autenticación": cabecera, `401`, rotación con varios tokens, cómo generar uno fuerte; `/health`; `API_KEYS` en la tabla de configuración). `.env.example` con `API_KEYS=` (pedírselo al usuario si el agente no puede escribirlo). Verificación manual: sin cabecera, inválida y válida (1 búsqueda real) → `401`/`401`/`200`, y ningún log muestra el token.
- **RF:** RNF-3. **Fin de PR2.**

# Plan 005 — Autenticación

- **Estado:** aprobado (2026-10-08)
- **Fecha:** 2026-10-08
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D6**; las de este plan, **D1…**.
- **Base:** `alcampo-scraper/app/core/security.py` (su spec 004), ya probado.

## 1. Visión general

```
RequestContextMiddleware (spec 004)       ← request id, inicio/fin, X-Request-ID también en el 401
  └─ router /api/v1, dependencies=[Security(require_api_key)]
        require_api_key  ── falta / inválida → WARNING (sin el valor) → 401 + WWW-Authenticate: ApiKey
        │                    (antes de validar ProductQuery: ni 422, ni Redis, ni Dia)
        └─ ruta /products → ProductService (sin cambios)
/health, /docs, /openapi.json, /redoc     ← fuera del router: públicos
```

## 2. Módulos

| Fichero | Cambio | RF |
|---|---|---|
| `app/core/config.py` | `api_keys: Annotated[frozenset[str], NoDecode]`, `repr=False`, validador que corta por comas | RF-8, RF-11 |
| `app/core/security.py` | **nuevo**: `is_valid_api_key`, `require_api_key` | RF-1…RF-5, RF-7, RF-12 |
| `app/api/v1/products.py` | el router declara `dependencies=[Security(require_api_key)]` | RF-1, RF-6, RF-16 |
| `app/main.py` | `WARNING` al arrancar sin tokens; `GET /health` | RF-6, RF-10 |
| `app/middleware/request_context.py` | ocultar parámetros con nombre de secreto | RF-15 |
| `tests/integration/conftest.py`, tests de ruta | token sintético en el harness; override de la dependencia en los tests de ruta sin `lifespan` | — |
| `README.md`, `.env.example` | `API_KEYS` y cómo generar un token | RNF-3 |

## 3. Decisiones de diseño

**D1 — `API_KEYS` como `Annotated[frozenset[str], NoDecode]` con `repr=False`** (Alcampo plan-D1). Sin `NoDecode`, pydantic-settings intenta leer la variable como JSON y `a,b` falla al arrancar. `repr=False` saca los tokens del `repr` de `Settings` (RF-11).

**D2 — `is_valid_api_key` compara como bytes UTF-8 y sin cortocircuito** (Alcampo plan-D3). `secrets.compare_digest` lanza `TypeError` con `str` no ASCII, lo que convertiría una cabecera rara en un `500`; y recorrer todos los tokens sin parar en el primero evita que el tiempo revele cuál coincidió.

**D3 — `APIKeyHeader(auto_error=False)` y el `401` propio** (Alcampo plan-D4): así el cuerpo es idéntico con la cabecera ausente o inválida (RF-3) y lleva `WWW-Authenticate` (RF-7).

**D4 — La dependencia en el router, no en la ruta** (spec-D6): cubre todo lo que se añada bajo `/api/v1` y nada de fuera. FastAPI resuelve las dependencias del router antes que los parámetros de la ruta, así que un token inválido da `401` y no `422` (RF-1). Se comprueba con un test (plan de Alcampo, §2: verificado allí).

**D5 — Los tokens se leen de `resources(app).settings`** (lo que construyó el `lifespan`), no de `get_settings()`, para que los tests que cambian el entorno vean los suyos.

**D6 — Ocultar parámetros en el middleware por nombre** (spec-D1): `api_key`, `apikey`, `x-api-key`, `key`, `token`, sin distinguir mayúsculas, se muestran como `'***'` antes del `repr` y del tope de 500 caracteres de la spec 004.

**D7 — Tests existentes.** El harness de integración fija `API_KEYS=test-key-…` (sintético) y el `TestClient` envía la cabecera por defecto; los tests de ruta, que no ejecutan el `lifespan`, sustituyen `require_api_key` con `dependency_overrides`. Los tests nuevos de autenticación usan la app real con `lifespan`.

## 4. Estrategia de test por RF

| RF | Test | Fichero |
|---|---|---|
| RF-1 | token inválido con `term` inválido → `401` (no `422`); sin token: 0 peticiones respx y 0 claves leídas/escritas en Redis | `tests/integration/test_auth.py` |
| RF-2, RF-3, RF-7 | sin cabecera, vacía e inválida → mismo `401` y `WWW-Authenticate: ApiKey` | integración |
| RF-4, RF-5 | `is_valid_api_key`: exacto, con espacios, con mayúsculas, no ASCII (sin `500`), varios tokens | `tests/core/test_security.py` |
| RF-6 | `/health`, `/docs`, `/openapi.json` sin token → `200` | integración |
| RF-8, RF-11 | `" a , ,b "` → `{"a","b"}`; sin variable → vacío; `repr(settings)` sin los tokens | `test_config.py` |
| RF-9, RF-10 | sin tokens: `401` siempre y `WARNING` al arrancar | integración, `test_main.py` |
| RF-12, RF-14 | rechazo → `WARNING` con ruta y motivo; ni el token recibido ni los configurados en ningún registro | integración |
| RF-13 | `401` con `X-Request-ID` | integración |
| RF-15 | `?api_key=x&Token=y&term=token` → `'***'` en los dos primeros y `token` en el tercero | `test_request_context.py` |
| RF-16 | OpenAPI declara el esquema `APIKeyHeader` para `/api/v1/products` | `test_main.py` |

## 5. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | Olvidar la cabecera en un test existente y que falle por `401` | El harness la pone por defecto (D7) |
| R2 | Un token en la URL se registre antes de ocultarse | La ocultación va en el mismo middleware, antes de formatear (D6) |

## 6. Secuencia y entrega

| Bloque | Tareas | Total aprox. |
|---|---|---|
| PR1 Configuración, comprobación y router | T1–T4 | ~350 |
| PR2 Logs, `/health`, integración y docs | T5–T8 | ~320 |

2 PRs encadenados, cada uno en verde y por debajo de 400 líneas.

# Tasks 001 — MVP de búsqueda de productos

- **Estado:** aprobado (2026-10-07)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones de diseño citadas como plan-Dn)
- **Entrega:** 4 PRs encadenados (stacked). Cada PR deja la suite en verde.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado** (no por un error de import ajeno).
2. **GREEN:** el código mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .`, `mypy` y `pytest -q`, todo limpio. Marcar `[x]`, proponer el commit y **parar**.

Formato de commit: `<tipo>(001-dia-scraper-mvp): <descripción en inglés> (Tn)`.

---

## PR1 — Base, modelos y mapper

### [x] T1 — Entorno y paquete `app`
- **RED:** `tests/test_package.py` importa `app`, `app.api.v1`, `app.core`, `app.models`, `app.mappers`, `app.scrapers`, `app.services` y `app.middleware` → `ModuleNotFoundError`.
- **GREEN:** crear los `__init__.py` vacíos. Crear el venv e instalar con `pip install -e ".[dev]"`.
- **Depende:** —
- **RF:** — (infraestructura; plan R4)
- **Hecho cuando:** la instalación termina sin errores, `pytest -q` pasa 1 test, `ruff check .` y `mypy` salen limpios. Si alguna dependencia falla en el Python local, **parar y avisar** (R4).

### [x] T2 — `Settings`
- **RED:** `tests/core/test_config.py`:
  - sin `DIA_BASE_URL` → `ValidationError`;
  - sin `REDIS_URL` → `ValidationError`;
  - con ambas → defaults `CACHE_TTL_SECONDS=3600`, `RETRY_MAX_ATTEMPTS=3`, `RETRY_BASE_DELAY=0.5`, `HTTP_TIMEOUT_SECONDS=10`, `LOG_LEVEL="INFO"`;
  - las variables de entorno sobrescriben los defaults;
  - `CACHE_TTL_SECONDS=0`, `RETRY_MAX_ATTEMPTS=0` y `HTTP_TIMEOUT_SECONDS=0` → `ValidationError` (deben ser positivos).
  - Todo con `monkeypatch` y `_env_file=None`, para no leer un `.env` local.
- **GREEN:** `app/core/config.py` con `Settings(BaseSettings)` y `get_settings()` con `lru_cache`.
- **Depende:** T1
- **RF:** RF-23
- **Hecho cuando:** todos los casos pasan.

### [x] T3 — Excepciones de dominio
- **RED:** `tests/test_exceptions.py`:
  - `UpstreamUnavailableError("x", status_code=404)` es subclase de `DiaScraperError`, expone `.reason == "x"` y `.status_code == 404`; sin `status_code` → `None`;
  - `UpstreamBlockedError` es subclase de `UpstreamUnavailableError`;
  - `PageOutOfRangeError(3)` es subclase de `DiaScraperError` (no de `UpstreamUnavailableError`) y expone `.page == 3`;
  - el módulo no importa `httpx`.
- **GREEN:** `app/exceptions.py` (plan-D7).
- **Depende:** T1
- **RF:** RF-7, RF-19, RF-20, RF-22
- **Hecho cuando:** todos los casos pasan.

### [x] T4 — Schemas de la API
- **RED:** `tests/models/test_product.py`:
  - `ProductQuery(postal_code=" 28001 ", term="  leche ")` → recortados; `page=1` y `page_size=50` por defecto;
  - `term="   "`, `term` de 101 caracteres → `ValidationError`; 100 caracteres → válido;
  - `postal_code` `"2800"`, `"280011"`, `"28a01"` y `"２８００１"` (ancho completo) → `ValidationError`;
  - `page=0`, `page=21`, `page_size=0`, `page_size=101` → `ValidationError`; `page=20`, `page_size=100` → válidos;
  - `Product(price_format=None)` válido; `Product(image_url=None)` y `Product(category=None)` → `ValidationError`;
  - `ProductSearchResponse` serializa `scraped_at` con sufijo `Z`.
- **GREEN:** `app/models/product.py` (plan §3).
- **Depende:** T1
- **RF:** RF-2, RF-12
- **Hecho cuando:** todos los casos pasan.

### [x] T5 — Schemas crudos de Dia
- **RED:** `tests/models/test_dia.py`:
  - la fixture real `dia_search_leche.json` valida contra `DiaSearchResponse` (`cart.postal_code == "28041"`, `total_items == 417`, `pagination.total_pages == 14`, 3 productos crudos);
  - `dia_search_no_results.json` valida (sin `facets`, `search_items: []`);
  - JSON sin `search_items`, sin `cart` o sin `pagination` → `ValidationError`;
  - el primer producto valida contra `DiaProduct` (`object_id == "504P6"`, `prices.price == 4.98`);
  - `price="abc"`, `price=True`, `price=float("nan")` → `ValidationError`;
  - sin `image` o sin `l2_category_description` → `ValidationError`;
  - un producto Club de `dia_search_jamon_promos.json` sin `strikethrough_price` → `ValidationError`; con él → válido.
- **GREEN:** `app/models/dia.py` (plan-D1, plan-D2).
- **Depende:** T1
- **RF:** RF-11, RF-21
- **Hecho cuando:** todos los casos pasan usando las fixtures reales, sin copiarlas a mano.

### [x] T6 — Formato del precio por unidad
- **RED:** `tests/mappers/test_product_mapper.py::test_format_unit_price_*`:
  - `0.83` + `LITRO` → `"0.83 €/L"`; `0.8` → `"0.80 €/L"`;
  - `KILO` → `kg`, `UNIDAD` → `ud`, `DOCENA` → `docena`, `LAVADO` → `lavado`, `100 ML.` → `100 ml`, `100 GR.` → `100 g`;
  - `BOTELLA` → `None` y un `WARNING` en `caplog` que nombra la unidad.
- **GREEN:** `UNIT_SUFFIXES` y `format_unit_price()` en `app/mappers/product_mapper.py` (plan-D3, plan-D13).
- **Depende:** T5
- **RF:** RF-9, RF-10 (spec-D5)
- **Hecho cuando:** todos los casos pasan.

### [x] T7 — Mapeo de un producto
- **RED:** `test_map_product_*`:
  - 1.er producto de `dia_search_leche.json` → `Product(id="504P6", name="Leche semidesnatada Dia Láctea pack 6 x 1 L", price=4.98, price_format="0.83 €/L", image_url="https://www.dia.es/product_images/504P6/504P6_ISO_0_ES.jpg", category="Leche")`;
  - `dia_search_jamon_promos.json`: producto Club → `price=3.49`, `price_format=None`; promoción para todos → `price=1.49` y su `price_format` normal; producto normal (`274059`) → `price=2.21`;
  - las fixtures `huevos_units`, `detergente_lavado` y `platano_100ml` → el sufijo esperado;
  - `image_url` se construye con el `base_url` recibido (sin doble `/`).
- **GREEN:** `map_product(raw: DiaProduct, *, base_url: str) -> Product`.
- **Depende:** T4, T6
- **RF:** RF-8, RF-10 (spec-D3, spec-D4, spec-D8)
- **Hecho cuando:** todos los casos pasan.

### [x] T8 — Mapeo de la búsqueda (descarte y deduplicación)
- **RED:** `test_map_search_*`, construidos a partir de la fixture real:
  - `dia_search_leche.json` → 3 productos en orden;
  - un `object_id` repetido → sin duplicado, primera aparición conservada;
  - un producto sin `display_name`, otro sin `object_id` y otro con `price="abc"` → descartados, el resto se conserva;
  - `dia_search_no_results.json` → `[]`.
- **GREEN:** `map_search(raw: DiaSearchResponse, *, base_url: str) -> list[Product]`, con validación por producto (plan-D1).
- **Depende:** T7
- **RF:** RF-5, RF-11
- **Hecho cuando:** todos los casos pasan. **Fin de PR1.**

---

## PR2 — Cliente HTTP, reintentos y scraper

### [x] T9 — Factoría del cliente HTTP
- **RED:** `tests/scrapers/test_http_client.py`: `create_http_client(settings)` devuelve un `httpx.AsyncClient` con `base_url` = `DIA_BASE_URL`, `timeout` = `HTTP_TIMEOUT_SECONDS` y todas las cabeceras de plan-D12; la versión mayor de Chrome del `User-Agent` es la misma que la de `sec-ch-ua`; el `User-Agent` es Chrome 155; `Referer` es la constante `https://www.dia.es/` aunque `DIA_BASE_URL` sea otra. Se cierra con `aclose()`.
- **GREEN:** `app/scrapers/http_client.py`.
- **Depende:** T2
- **RF:** RF-4, RNF-3
- **Hecho cuando:** el test pasa.

### [x] T10 — Reintentos ante fallos transitorios
- **RED:** `tests/scrapers/test_retry.py`, con un `send` falso que devuelve una secuencia de `httpx.Response` (o lanza) y un `sleep` falso que registra las esperas:
  - `200` → 1 llamada, 0 esperas;
  - `503, 503, 200` → 3 llamadas, esperas `[0.5, 1.0]`;
  - `429, 200` → reintenta;
  - `httpx.ConnectTimeout`, `200` → reintenta;
  - `503 × 3` con `max_attempts=3` → `UpstreamUnavailableError`, esperas `[0.5, 1.0]` (no espera tras el último).
- **GREEN:** `send_with_retry()` en `app/scrapers/retry.py` (plan-D6).
- **Depende:** T3
- **RF:** RF-17, RF-20
- **Hecho cuando:** todos los casos pasan sin esperas reales.

### [x] T11 — Errores no reintentables y bloqueo de Akamai
- **RED:** en `test_retry.py`:
  - `404` → 1 llamada, `UpstreamUnavailableError` con `status_code == 404`;
  - `403` con `Content-Type: application/json` → igual que `404` (`status_code == 403`, no `UpstreamBlockedError`);
  - `403` con `Content-Type: text/html` y el cuerpo real de Akamai (`<TITLE>Access Denied</TITLE>`) → `UpstreamBlockedError` en el 1.er intento, 0 esperas;
  - transporte agotado → la excepción final es `UpstreamUnavailableError`, no de `httpx` (con `__cause__` encadenado).
- **GREEN:** clasificación completa de plan-D6.
- **Depende:** T10
- **RF:** RF-18, RF-19, RF-20, RF-22 (spec-D7)
- **Hecho cuando:** todos los casos pasan.

### [x] T12 — Scraper de búsqueda
- **RED:** `tests/scrapers/test_dia_search.py`, con `respx`:
  - `search("leche", page=2, page_size=50)` → exactamente 1 llamada a `/api/v1/search-back/search/reduced` con `q=leche`, `page=2`, `page_size=50`; devuelve `DiaSearchResponse` de la fixture real;
  - `search("plátano", …)` → `q` llega codificado y se decodifica a `plátano`;
  - `200` con HTML, `200` con JSON sin `search_items` → `UpstreamUnavailableError`;
  - `403` de Akamai → `UpstreamBlockedError` y 1 sola llamada;
  - `404` HTML (como el de `page ≥ 51`) → `UpstreamUnavailableError`, 1 sola llamada.
- **GREEN:** `DiaSearchScraper` en `app/scrapers/dia_search.py`, con `DEFAULT_POSTAL_CODE = "28041"` (plan-D4).
- **Depende:** T5, T9, T11
- **RF:** RF-3, RF-21, RF-22
- **Hecho cuando:** todos los casos pasan. **Fin de PR2.**

---

## PR3 — Cache y servicio

### [x] T13 — Repositorio de cache
- **RED:** `tests/services/test_search_cache.py`, con `FakeAsyncRedis` nuevo por test:
  - `get` sin entrada → `None`;
  - `set` + `get` → la misma `ProductSearchResponse`; la clave es `search:28041:leche:1:50`;
  - `"LECHE"` y `" leche "` producen la misma clave (plan-D5);
  - el TTL de la clave es `CACHE_TTL_SECONDS`;
  - valor corrupto (`b"{no json"`, o JSON que no valida) → `None` (plan-D10).
- **GREEN:** `SearchCacheRepository` en `app/services/search_cache.py`.
- **Depende:** T4
- **RF:** RF-13, RF-14
- **Hecho cuando:** todos los casos pasan.

### [ ] T14 — Servicio: miss y metadatos
- **RED:** `tests/services/test_product_service.py`, con un scraper falso y fakeredis:
  - miss → llama al scraper con el término recortado (sin pasar a minúsculas), `page` y `page_size`; devuelve los productos mapeados;
  - `search`: `postal_code` y `term` de la petición, `warehouse` = `cart.postal_code` de la respuesta (se prueba con un `cart.postal_code` distinto de `28041`), `strategy_used="api"`, `scraped_at` = reloj inyectado, `total_results=417`, `page`, `page_size`;
  - `total_pages` = `pagination.total_pages`, con tope 20 (`total_pages: 84` → `20`);
  - página 1 sin resultados → `products: []`, `total_results: 0`, `total_pages: 0`, y se cachea;
  - tras un miss, la respuesta queda en Redis.
- **GREEN:** `ProductService.search()` en `app/services/product_service.py` (plan-D9, plan-D11).
- **Depende:** T8, T12, T13
- **RF:** RF-1, RF-6, RF-12, RF-14
- **Hecho cuando:** todos los casos pasan.

### [ ] T15 — Servicio: hit, página fuera de rango y errores
- **RED:** en `test_product_service.py`:
  - hit → 0 llamadas al scraper; `scraped_at` original; `postal_code` y `term` de la petición actual (otra mayúscula, otro CP);
  - página 2 con `search_items: []` → `PageOutOfRangeError(2)` y Redis vacío;
  - página 2 con productos todos rotos → `200` con `products: []` (no es fuera de rango, plan-D8);
  - el scraper lanza `UpstreamUnavailableError` o `UpstreamBlockedError` → se propaga y Redis queda vacío.
- **GREEN:** completar `ProductService`.
- **Depende:** T14
- **RF:** RF-7, RF-13, RF-15, RF-16
- **Hecho cuando:** todos los casos pasan. **Fin de PR3.**

---

## PR4 — API, integración y docs

### [ ] T16 — Estado, providers, ruta y `main`
- **RED:** `tests/api/test_products_route.py` y `tests/test_main.py`, con el servicio sustituido por `dependency_overrides`:
  - `GET /api/v1/products?postal_code=28001&term=leche` → `200` y el cuerpo del servicio falso;
  - el servicio lanza `UpstreamUnavailableError` / `UpstreamBlockedError` → `502 {"detail": "Upstream service unavailable"}`, sin el `reason`;
  - `PageOutOfRangeError` → `404 {"detail": "Page out of range"}`;
  - `create_app()` registra la ruta en `/api/v1/products` y el `lifespan` crea y cierra el cliente HTTP y Redis (factorías parcheadas).
- **GREEN:** `app/core/state.py`, `app/core/dependencies.py`, `app/api/v1/products.py`, `app/main.py` (plan-D14).
- **Depende:** T15
- **RF:** RF-1, RF-2, RF-7, RF-19, RF-20, RNF-1
- **Hecho cuando:** todos los casos pasan y `mypy` sigue limpio.

### [ ] T17 — Harness de integración y camino feliz
- **RED:** `tests/integration/conftest.py` (app real + lifespan + `FakeAsyncRedis` + `respx`, plan-D15) y `tests/integration/test_products_endpoint.py`:
  - miss → `200`, cuerpo valida, 1 llamada respx con `q`, `page`, `page_size` y las cabeceras de Chrome;
  - la misma petición otra vez → hit, **0 llamadas** respx nuevas;
  - `term="LECHE"` tras `term="leche"` → hit;
  - sin resultados → `200`, `products: []`.
- **GREEN:** solo el harness (si el código ya pasa, la tarea demuestra el cableado). Verificar R3 de Alcampo: `respx` no intercepta al `TestClient`.
- **Depende:** T16
- **RF:** RF-1, RF-3, RF-4, RF-6, RF-13
- **Hecho cuando:** todos los casos pasan.

### [ ] T18 — Integración: validación y errores
- **RED:** en `test_products_endpoint.py`:
  - los casos de `422` de RF-2 → 0 llamadas respx y Redis vacío;
  - página 2 vacía → `404 Page out of range`, nada en Redis;
  - `403` de Akamai → `502`, **1 sola** llamada respx;
  - `404` de Dia → `502`, **1 sola** llamada;
  - `503 × RETRY_MAX_ATTEMPTS` → `502` (con `RETRY_BASE_DELAY=0` en el entorno del test);
  - ningún error deja entrada en Redis.
- **GREEN:** —, o el ajuste mínimo que falte.
- **Depende:** T17
- **RF:** RF-2, RF-7, RF-15, RF-18, RF-19, RF-20
- **Hecho cuando:** todos los casos pasan.

### [ ] T19 — Paridad de contrato con Mercadona
- **RED:** `tests/api/test_contract_parity.py` (copiado de Alcampo) contra `tests/fixtures/mercadona_search_response_schema.json`. Debe pasar a la primera si los schemas de T4 son correctos; para confirmar que el test muerde, se comprueba en local que quitar un campo de `SearchMetadata` lo pone en rojo (sin commitear ese cambio).
- **GREEN:** —
- **Depende:** T4
- **RF:** RNF-5 (constitución #13)
- **Hecho cuando:** el test pasa y se ha visto fallar.

### [ ] T20 — Docs vivas y prueba manual
- **Hacer:**
  - `README.md`: endpoint, parámetros, ejemplo de respuesta, errores, variables de entorno, y **limitaciones conocidas**: CP por defecto `28041` hasta la spec 002 (spec-D2), precio sin tarjeta Club (spec-D3, spec-D8), `total_results` aproximado (Fase 0 §1), dependencia de la huella ante Akamai (R1, R2).
  - `.env.example` con todas las variables de RF-23 (si el agente no puede escribirlo, pedírselo al usuario, R6).
  - Arrancar con `uvicorn`, abrir `/docs` y comprobar que muestra `ProductSearchResponse` con sus 9 campos de `search`.
  - Prueba manual contra Dia real: **una o dos** peticiones (`term=leche`, y la misma otra vez para ver el hit). Anotar resultado aquí.
- **Depende:** T18, T19
- **RF:** RNF-6, criterios de finalización de la spec
- **Hecho cuando:** README y `.env.example` actualizados y la prueba manual anotada. **Fin de PR4.**

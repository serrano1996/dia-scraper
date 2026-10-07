# Plan 001 — MVP de búsqueda de productos

- **Estado:** borrador, pendiente de revisión
- **Fecha:** 2026-10-07
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D8** para no confundirlas con las decisiones de diseño de este plan (**D1…**).

## 1. Visión general

Flujo de una petición:

```
GET /api/v1/products?postal_code&term&page&page_size
  │  (validación: ProductQuery en la firma → 422)
  ▼
api/v1/products.py ──► services/product_service.py
                         │ 1. cache.get(postal_code, term, page, page_size) ──► hit: reescribe postal_code y devuelve
                         │ 2. scraper.search(term, page, page_size)          ──► DiaSearchResponse (sobre crudo validado)
                         │ 3. página > 1 y sin productos                      ──► PageOutOfRangeError → 404
                         │ 4. mapper.map_search(raw)                          ──► list[Product] (descarta rotos, deduplica)
                         │ 5. cache.set(...)
                         ▼
                       ProductSearchResponse
Errores: UpstreamUnavailableError (y UpstreamBlockedError) ──► handler ──► 502
         PageOutOfRangeError ──► handler ──► 404
```

Toda la E/S es async. Un único `httpx.AsyncClient` y un único cliente Redis por proceso, creados y cerrados en el `lifespan` (RNF-1).

## 2. Módulos

Todos son nuevos: el repositorio solo tiene el esqueleto.

| Fichero | Responsabilidad | RF |
|---|---|---|
| `app/core/config.py` | `Settings` (pydantic-settings) + `get_settings()` | RF-23 |
| `app/core/dependencies.py` | Provider FastAPI `get_product_service` a partir de `app.state` | RF-1 |
| `app/exceptions.py` | `DiaScraperError` (base), `UpstreamUnavailableError`, `UpstreamBlockedError`, `PageOutOfRangeError` | RF-7, RF-19, RF-20, RF-22 |
| `app/models/product.py` | Schemas de la API: `ProductQuery`, `Product`, `SearchMetadata`, `ProductSearchResponse` | RF-1, RF-2, RF-12 |
| `app/models/dia.py` | Schemas crudos de Dia (sobre de la respuesta + producto) | RF-5, RF-11, RF-21 |
| `app/mappers/product_mapper.py` | Crudo → `Product`; tabla de unidades; precio sin tarjeta; descarte y deduplicación | RF-5, RF-8…RF-11 |
| `app/scrapers/http_client.py` | Factoría `create_http_client(settings) -> httpx.AsyncClient` con las cabeceras de Chrome | RF-4, RNF-3 |
| `app/scrapers/retry.py` | `send_with_retry(...)`: clasifica respuestas, backoff, bloqueo de Akamai | RF-17…RF-20 |
| `app/scrapers/dia_search.py` | `DiaSearchScraper.search(term, page, page_size) -> DiaSearchResponse` | RF-3, RF-21, RF-22 |
| `app/services/search_cache.py` | `SearchCacheRepository`: get/set en Redis con TTL | RF-13…RF-16 |
| `app/services/product_service.py` | Orquesta cache → scraper → mapper → cache; página fuera de rango | RF-1, RF-6, RF-7, RF-12…RF-16 |
| `app/api/v1/products.py` | Ruta `GET /api/v1/products` | RF-1, RF-2 |
| `app/main.py` | `create_app()`, `lifespan`, handlers `502` y `404` | RF-7, RF-19, RF-20, RNF-1 |
| `README.md`, `.env.example` | Docs vivas | RNF-6 |

Tests en espejo (`tests/core/`, `tests/models/`, `tests/mappers/`, `tests/scrapers/`, `tests/services/`, `tests/api/`) más `tests/integration/`.

## 3. Modelo de datos

### API (`app/models/product.py`)

Mismas reglas que el `ProductQuery` actual de Alcampo (que ya iguala a Mercadona):

```python
SearchTerm = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
PostalCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[0-9]{5}$")]
MAX_PAGE = 20
MAX_PAGE_SIZE = 100

class ProductQuery(BaseModel):
    postal_code: PostalCode
    term: SearchTerm
    page: int = Field(default=1, ge=1, le=MAX_PAGE)
    page_size: int = Field(default=50, ge=1, le=MAX_PAGE_SIZE)

class Product(BaseModel):            # frozen
    id: str
    name: str
    price: float
    price_format: str | None
    image_url: str
    category: str

class SearchMetadata(BaseModel):     # frozen
    postal_code: str
    term: str
    warehouse: str
    strategy_used: str
    scraped_at: datetime             # UTC, serializado con "Z"
    total_results: int
    page: int
    page_size: int
    total_pages: int

class ProductSearchResponse(BaseModel):   # frozen
    search: SearchMetadata
    products: list[Product]
```

`[0-9]` y no `\d`: `\d` acepta dígitos Unicode como los de ancho completo (lección de Alcampo, spec 007 plan-D7).

### Crudo de Dia (`app/models/dia.py`)

Todos con `model_config = ConfigDict(extra="ignore")`: cada producto trae 12–20 campos y solo usamos 6.

```python
class DiaPrices(BaseModel):
    price: float
    price_per_unit: float
    measure_unit: str = Field(min_length=1)
    strikethrough_price: float | None = None
    is_club_price: bool = False

class DiaProduct(BaseModel):
    object_id: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    prices: DiaPrices
    image: str = Field(min_length=1)
    l2_category_description: str = Field(min_length=1)

class DiaCart(BaseModel):
    postal_code: str = Field(min_length=1)

class DiaPagination(BaseModel):
    page_number: int
    page_size: int
    total_pages: int

class DiaSearchResponse(BaseModel):
    cart: DiaCart                                   # obligatorio → RF-12 (warehouse), RF-21
    pagination: DiaPagination                       # obligatorio → RF-12, RF-21
    total_items: int                                # obligatorio → RF-12, RF-21
    search_items: list[JsonValue]                   # obligatorio → RF-21; productos validados uno a uno (D1)
```

Los tres precios son en realidad `Price = Annotated[FiniteFloat, BeforeValidator(reject_bool)]` (D2): aceptan `4.98` y `5` (enteros JSON) y rechazan `"abc"`, `true`, `NaN` e `inf`. Un `model_validator` de `DiaPrices` exige `strikethrough_price` cuando `is_club_price` es `true` (RF-11).

### Redis

| Clave | Valor | TTL |
|---|---|---|
| `search:{cp}:{term}:{page}:{page_size}` (p. ej. `search:28041:leche:1:50`) | `ProductSearchResponse` en JSON | `CACHE_TTL_SECONDS` |

`{cp}` es el CP por defecto de la 001 (`DEFAULT_POSTAL_CODE = "28041"`, D4); `{term}` es el término recortado y en minúsculas (D5).

## 4. Decisiones de diseño

**D1 — Validar los productos uno a uno, no en bloque.** El sobre guarda `search_items` como `list[JsonValue]` y el mapper valida cada uno contra `DiaProduct` dentro de un `try/except ValidationError` (igual que Alcampo, su plan 001 D1).
- *Descartada:* `search_items: list[DiaProduct]`. Un solo producto roto haría fallar la búsqueda entera con `502`, en contra de RF-11.

**D2 — Precios como `FiniteFloat` en el crudo.** Dia manda números (Fase 0 §6), así que no hay formato textual que conservar como en Alcampo. `FiniteFloat` rechaza `NaN` e `inf`; un validador `mode="before"` rechaza `bool` (Pydantic convierte `true` en `1.0` en modo laxo).
- *Descartada:* `Decimal`. El contrato es `price: float` en los tres scrapers; convertir a `Decimal` y volver no aporta nada.

**D3 — `price_format` se formatea con `f"{price_per_unit:.2f} €/{suffix}"`.** El número de Dia (`0.83`, `16.99`, `0.07`) pasa a dos decimales con punto, como los importes de texto de Alcampo (`"0.88 €/L"`). `0.8` → `"0.80"`.
- *Descartada:* `str(price_per_unit)`. Daría `"0.8 €/L"` y `"16.99 €/kg"`, incoherente entre productos y con Alcampo.

**D4 — CP de la clave de cache: constante `DEFAULT_POSTAL_CODE = "28041"`**, en `app/scrapers/dia_search.py`, con un comentario que enlaza a la Fase 0 §2. La clave se construye **antes** de llamar a Dia, así que no puede usar `cart.postal_code` de la respuesta. Una sola entrada por término sirve a todos los CPs, como en Alcampo 001 (su plan-D4). `warehouse` sí sale de la respuesta (spec-D6).
- *Descartada:* `search:{postal_code de la petición}:…`. Multiplicaría las peticiones a Dia por el número de CPs sin cambiar el resultado (todas van a 28041 en la 001).
- *Riesgo:* si Dia cambia su CP por defecto, la clave dice `28041` pero `warehouse` dirá el nuevo. No hay fallo; la 002 sustituye la constante por el CP resuelto.

**D5 — `term` en la clave: recortado y en minúsculas (`casefold()`).** Verificado en vivo que Dia no distingue mayúsculas (`LECHE` = `leche`, Fase 0 §1). A Dia se le envía el término recortado **tal como llegó** (sin pasar a minúsculas), y `search.term` es el término recortado, como Mercadona. En un hit, el servicio reescribe `search.term` y `search.postal_code` con los de la petición actual.
- *Descartada:* clave sin normalizar mayúsculas, como Alcampo 001 (su plan-D5). Allí no estaba verificado; aquí sí, y ahorra peticiones.

**D6 — Reintentos en una función pura con `sleep` inyectable.** `send_with_retry(send, *, max_attempts, base_delay, sleep=asyncio.sleep)` clasifica cada resultado:

| Resultado | Acción |
|---|---|
| `403` con `Content-Type` `text/html` | `UpstreamBlockedError` inmediato, **sin reintento** (RF-19, spec-D7) |
| `2xx` | devolver la respuesta |
| `5xx` o `429` | reintentar |
| Otro `4xx` (incluido un `403` que no sea HTML) | `UpstreamUnavailableError(status_code=…)` inmediato (RF-18) |
| `httpx.TransportError` (incluye timeouts) | reintentar |
| Intentos agotados | `UpstreamUnavailableError` (RF-20) |

Espera entre el intento *n* y el *n+1*: `base_delay × 2^(n-1)`. Los tests inyectan un `sleep` falso que registra las esperas.
- *Descartada:* `tenacity`, fuera del stack fijo. *Descartada:* `AsyncHTTPTransport(retries=N)`, solo reintenta conexión.

**D7 — Excepciones: jerarquía mínima, la que hoy cambia comportamiento.** `UpstreamUnavailableError(reason, *, status_code=None)` para todo fallo del upstream; `UpstreamBlockedError` como subclase (sin reintento, y la spec de anti-baneo lo usará para el enfriamiento); `PageOutOfRangeError(page)` aparte, porque no es un fallo sino un `404`. El `reason` es interno y nunca sale en la respuesta.
- *Descartada:* la jerarquía completa de Alcampo hoy (`CooldownActiveError`, `OutboundRateLimitedError`…). Ningún comportamiento de la 001 depende de ellas.

**D8 — Página fuera de rango se decide en el servicio**, no en el scraper: `page > 1` y `search_items` vacío → `PageOutOfRangeError`, antes de mapear y sin cachear (RF-7, RF-15). Se mira `search_items` crudo y no la lista mapeada: si Dia trae productos pero todos están rotos, no es "fuera de rango".
- *Descartada:* comparar `page` con `pagination.total_pages`. `total_items` es aproximado (Fase 0 §1), así que `total_pages` también; el único dato fiable es si la página trae productos.

**D9 — `total_pages = min(pagination.total_pages, MAX_PAGE)`**, `total_results = total_items` tal cual (RF-12). Dia calcula `total_pages` con el `page_size` que le enviamos, que es el de la petición.

**D10 — Cache corrupta = miss.** Si el JSON guardado no valida contra `ProductSearchResponse`, se trata como miss y se sobrescribe (Alcampo plan 001 D8).

**D11 — Reloj inyectable en el servicio.** `ProductService(..., clock: Callable[[], datetime] = lambda: datetime.now(UTC))` para comprobar `scraped_at` (RF-12, RF-16).

**D12 — Cabeceras del cliente HTTP fijas y coherentes** (RF-4), como constantes de `app/scrapers/http_client.py`: un único perfil de Chrome 129 en Windows, exactamente el que pasó en la Fase 0 §5 (`User-Agent`, `sec-ch-ua` con la misma versión, `sec-ch-ua-mobile: ?0`, `sec-ch-ua-platform: "Windows"`, `Sec-Fetch-Dest: empty`, `Sec-Fetch-Mode: cors`, `Sec-Fetch-Site: same-origin`, `Referer: {DIA_BASE_URL}/`, `Accept: application/json, text/plain, */*`, `Accept-Language: es-ES,es;q=0.9`). `timeout=HTTP_TIMEOUT_SECONDS` (10 s), `base_url=DIA_BASE_URL`, HTTP/1.1 (sin `h2`).
- *Descartada:* rotar User-Agent ya. Exige rotar también los client hints de forma coherente (Fase 0, implicación 4); se decide en la spec de anti-baneo.
- *Descartada:* HTTP/2. Pasa igual que HTTP/1.1 (Fase 0 §5) y obligaría a añadir la dependencia `h2`, fuera de la constitución sin discutirlo.

**D13 — Aviso de unidad desconocida con `logging` estándar** (`logger.warning("unknown measure unit %r", unit)`, RF-10). El logging estructurado llega con su spec; aquí basta un logger de módulo, comprobado con `caplog`.

**D14 — Providers en `app/core/dependencies.py` y `AppState` tipado desde el principio** (`app/core/state.py`, como el Alcampo actual): `get_product_service(request)` lee `http_client` y `redis` del estado sin `Any`, lo que `mypy --strict` exige.

**D15 — Tests de integración con `fastapi.testclient.TestClient` como context manager** (lifespan real), parcheando la factoría `create_redis(settings)` para que devuelva un `FakeAsyncRedis()` nuevo por test, y `respx` para Dia. Es lo que ya funciona en Alcampo (`tests/integration/conftest.py`).

**D16 — Test de paridad de contrato** copiado de Alcampo (`tests/api/test_contract_parity.py`) contra `tests/fixtures/mercadona_search_response_schema.json` (ya copiada). Compara forma, no nombres de modelos (RNF-5).

## 5. Estrategia de test por RF

Tipos: **U** = unitario, **I** = integración (app real + lifespan + fakeredis + respx).

| RF | Test | Tipo | Fichero |
|---|---|---|---|
| RF-1 | `GET` válido → `200`, body valida contra `ProductSearchResponse` | I | `tests/integration/test_products_endpoint.py` |
| RF-2 | falta `term`/`postal_code`; `term="   "`, 101 caracteres; `postal_code="2800"`, `"28001a"`, dígitos de ancho completo; `page=0`, `page=21`, `page_size=0`, `page_size=101` → `422`, 0 llamadas respx, Redis vacío | U + I | `tests/models/test_product.py`, integración |
| RF-3 | la ruta respx recibe `q`, `page`, `page_size`; exactamente 1 llamada | U | `tests/scrapers/test_dia_search.py` |
| RF-4 | el cliente lleva todas las cabeceras de D12 y la versión de `User-Agent` coincide con la de `sec-ch-ua` | U | `tests/scrapers/test_http_client.py` |
| RF-5 | fixture real `dia_search_leche.json` → 3 productos en orden; `object_id` repetido → primera aparición | U | `tests/mappers/test_product_mapper.py` |
| RF-6 | fixture real `dia_search_no_results.json`, página 1 → `products: []`, `total_results: 0`, `total_pages: 0` | U + I | servicio, integración |
| RF-7 | página 2 con `search_items: []` → `PageOutOfRangeError`, nada en Redis; en la API → `404 {"detail": "Page out of range"}` | U + I | servicio, integración |
| RF-8 | fixture leche → `Product(id="504P6", name=…, price=4.98, image_url="https://www.dia.es/product_images/504P6/504P6_ISO_0_ES.jpg", category="Leche")` | U | mapper |
| RF-8 | fixture `dia_search_jamon_promos.json`: Club → `price=3.49` (`strikethrough_price`); promoción para todos → `price=1.49` | U | mapper |
| RF-9 | `0.83` + `LITRO` → `"0.83 €/L"`; `0.8` → `"0.80 €/L"`; las 7 unidades de spec-D5 con las fixtures reales (`KILO`, `DOCENA`, `UNIDAD`, `LAVADO`, `100 ML.`, `100 GR.`) | U | mapper |
| RF-10 | `measure_unit="BOTELLA"` → `None` y un `WARNING` en `caplog`; producto Club → `price_format=None` | U | mapper |
| RF-11 | sin `display_name` / sin `object_id` / sin `image` / sin `l2_category_description` / `price="abc"` / `price=true` / Club sin `strikethrough_price` → descartado; el resto se conserva | U | mapper, `tests/models/test_dia.py` |
| RF-12 | `warehouse` = `cart.postal_code` de la respuesta; `strategy_used="api"`; `scraped_at` = reloj; `total_results=417`; `total_pages` con tope 20 | U | `tests/services/test_product_service.py` |
| RF-13 | con entrada en Redis → respuesta de cache y **0 llamadas**; `"LECHE"` y `" leche "` comparten clave | U + I | servicio, `test_search_cache.py`, integración |
| RF-14 | miss → `set` con TTL = `CACHE_TTL_SECONDS`; búsqueda sin resultados también se cachea | U | search_cache, servicio |
| RF-15 | fallo del scraper o `PageOutOfRangeError` → Redis vacío | U | servicio |
| RF-16 | hit → `scraped_at` original; `postal_code` y `term` de la petición actual | U | servicio |
| RF-17 | `503, 503, 200` → 3 llamadas, esperas `[0.5, 1.0]`; `429` y `ConnectTimeout` se reintentan | U | `tests/scrapers/test_retry.py` |
| RF-18 | `404` → 1 llamada, `UpstreamUnavailableError(status_code=404)`; `403` JSON → igual | U | retry |
| RF-19 | `403` HTML de Akamai → `UpstreamBlockedError` en el 1.er intento, 0 esperas; en la API → `502`, 1 sola llamada respx | U + I | retry, integración |
| RF-20 | `503 × RETRY_MAX_ATTEMPTS` → `UpstreamUnavailableError`; API → `502 {"detail": "Upstream service unavailable"}` sin cuerpo de Dia | U + I | retry, integración |
| RF-21 | `200` con HTML (el `404` "Bloqueado" llega como HTML, pero con código 404; aquí se prueba un `200` HTML), JSON sin `search_items`, sin `cart` → `UpstreamUnavailableError` | U | scraper |
| RF-22 | la excepción que sale del scraper nunca es de `httpx` | U | scraper |
| RF-23 | sin `DIA_BASE_URL` o sin `REDIS_URL` → `ValidationError`; defaults correctos; env sobrescribe | U | `tests/core/test_config.py` |
| RNF-5 | forma de la respuesta = forma de Mercadona | U | `tests/api/test_contract_parity.py` |
| D10 | valor corrupto en la clave → miss y sobrescritura | U | search_cache |

Fixtures: las reales de `tests/fixtures/`. Los casos no observados en vivo (unidad desconocida, `object_id` duplicado, producto roto, Club sin `strikethrough_price`) se construyen en el test **a partir** de la fixture real, cambiando solo el campo bajo prueba.

## 6. Riesgos

| # | Riesgo | Impacto | Mitigación |
|---|---|---|---|
| R1 | Akamai endurece Bot Manager (p. ej. exige `_abck` validada por su JS) | Todo `502` | Fuera de nuestro control (constitución #14). `UpstreamBlockedError` lo hace visible y sin reintentos; cache de 1 h |
| R2 | El perfil de Chrome 129 envejece y Akamai empieza a desconfiar de una versión antigua | `403` | Constante única en `http_client.py`, fácil de actualizar; la spec de anti-baneo decidirá la rotación |
| R3 | Dia cambia su CP por defecto | `warehouse` cambia solo (spec-D6); la clave de cache sigue diciendo `28041` (D4) | Sin fallo funcional. La 002 sustituye la constante |
| R4 | Python local 3.14 y objetivo 3.11+; alguna wheel podría faltar | Instalación fallida | T1 instala y ejecuta `pytest`; si falla, venv 3.11/3.12. Alcampo ya instala en este equipo con las mismas cotas |
| R5 | Unidades nuevas de `measure_unit` | `price_format: null` | Degradación aceptada (RF-10) con aviso en el log para detectarla |
| R6 | `.env.example` bloqueado por los permisos del agente (pasó en Alcampo) | RNF-6 incumplido | La tarea de docs pedirá al usuario que lo cree o conceda permiso |

## 7. Secuencia de implementación

Orden por dependencias, de dentro hacia fuera:

1. **Base:** instalación, `config`, `exceptions`.
2. **Modelos:** API y crudo.
3. **Mapper:** unidades, precio sin tarjeta, producto, búsqueda.
4. **HTTP:** factoría del cliente, `retry`, scraper.
5. **Cache:** repositorio.
6. **Servicio:** orquestación y página fuera de rango.
7. **API:** estado, providers, ruta, `main` + `lifespan` + handlers.
8. **Integración:** harness y escenarios end-to-end; test de paridad.
9. **Docs:** README, `.env.example`, `/docs` y prueba manual.

## 8. Estimación y entrega

| Bloque | Código `app/` | Tests | Docs | Total |
|---|---|---|---|---|
| 1–3 Base, modelos, mapper | ~170 | ~230 | — | ~400 |
| 4 HTTP, retry, scraper | ~120 | ~200 | — | ~320 |
| 5–6 Cache y servicio | ~110 | ~200 | — | ~310 |
| 7–9 API, integración, docs | ~110 | ~220 | ~70 | ~400 |
| **Total** | **~510** | **~850** | **~70** | **~1430** |

**Supera las 400 líneas**, así que propongo **4 PRs encadenados** (stacked), uno por fila:

```
main ◄── PR1 base+modelos+mapper ◄── PR2 http+retry+scraper ◄── PR3 cache+servicio ◄── PR4 api+integración+docs
```

- Cada PR deja **toda la suite en verde**.
- PR1–PR3 no exponen ningún endpoint; la funcionalidad visible llega con PR4.
- Alcampo hizo 3 PRs y su tercero salió de ~420 líneas; aquí se parte en dos desde el principio para respetar el límite.

## 9. Qué no cambia

Constitución, `pyproject.toml` (las dependencias ya están declaradas) y fixtures de la Fase 0.

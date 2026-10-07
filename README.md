# dia-scraper

API REST asíncrona (FastAPI) que extrae, procesa y sirve datos de productos de
[Dia online](https://www.dia.es). Ofrece el mismo contrato que `mercadona-scraper` y
`alcampo-scraper` para poder comparar los supermercados sin adaptar el consumidor.

> Estado: MVP (`specs/001-dia-scraper-mvp`). Busca siempre con el código postal por defecto
> de Dia; ver [Limitaciones conocidas](#limitaciones-conocidas).

## Puesta en marcha

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload       # http://127.0.0.1:8000/docs
```

Necesita un Redis accesible en `REDIS_URL`.

## Endpoint

### `GET /api/v1/products`

| Parámetro | Obligatorio | Reglas |
|---|---|---|
| `postal_code` | sí | exactamente 5 dígitos |
| `term` | sí | 1–100 caracteres tras recortar espacios |
| `page` | no | 1–20, por defecto 1 |
| `page_size` | no | 1–100, por defecto 50 |

```bash
curl "http://127.0.0.1:8000/api/v1/products?postal_code=28001&term=leche&page=1&page_size=50"
```

```json
{
  "search": {
    "postal_code": "28001",
    "term": "leche",
    "warehouse": "28041",
    "strategy_used": "api",
    "scraped_at": "2026-10-07T09:00:00Z",
    "total_results": 417,
    "page": 1,
    "page_size": 50,
    "total_pages": 9
  },
  "products": [
    {
      "id": "504P6",
      "name": "Leche semidesnatada Dia Láctea pack 6 x 1 L",
      "price": 4.98,
      "price_format": "0.83 €/L",
      "image_url": "https://www.dia.es/product_images/504P6/504P6_ISO_0_ES.jpg",
      "category": "Leche"
    }
  ]
}
```

| Campo | Significado |
|---|---|
| `search.warehouse` | código postal con el que Dia ha servido la búsqueda (`cart.postal_code`). Dia no expone tienda ni almacén |
| `search.total_results` | total de Dia para la búsqueda. Es una estimación: puede desviarse en ±1 |
| `search.total_pages` | páginas según Dia, con tope 20 |
| `products[].price` | precio que paga cualquier cliente, **sin** tarjeta Club Dia |
| `products[].price_format` | precio por unidad (`L`, `kg`, `ud`, `docena`, `lavado`, `100 ml`, `100 g`); `null` si la unidad es desconocida o el producto tiene oferta Club Dia |
| `products[].category` | categoría de segundo nivel de Dia (p. ej. `"Leche"`) |

### Errores

| Código | Cuándo | Cuerpo |
|---|---|---|
| `422` | parámetros inválidos (no se llama a Dia) | formato de FastAPI |
| `404` | `page` > 1 más allá de la última página | `{"detail": "Page out of range"}` |
| `502` | Dia no responde, falla tras los reintentos, responde algo inesperado o Akamai nos bloquea | `{"detail": "Upstream service unavailable"}` |

Las respuestas correctas, incluidas las búsquedas sin resultados, se cachean en Redis
`CACHE_TTL_SECONDS` por código postal efectivo, término (sin distinguir mayúsculas), página y
tamaño de página. Los errores no se cachean.

## Configuración

Variables de entorno (o `.env`); ver [`.env.example`](.env.example).

| Variable | Por defecto | Descripción |
|---|---|---|
| `DIA_BASE_URL` | — (obligatoria) | `https://www.dia.es` |
| `REDIS_URL` | — (obligatoria) | p. ej. `redis://localhost:6379/0` |
| `CACHE_TTL_SECONDS` | `3600` | vida de cada búsqueda cacheada |
| `RETRY_MAX_ATTEMPTS` | `3` | intentos en total ante `5xx`, `429` o errores de red |
| `RETRY_BASE_DELAY` | `0.5` | espera base entre intentos (backoff exponencial) |
| `HTTP_TIMEOUT_SECONDS` | `10` | timeout de cada petición a Dia |
| `LOG_LEVEL` | `INFO` | nivel de log |

## Limitaciones conocidas

- **Código postal sin efecto todavía.** Toda búsqueda se hace con el código postal por defecto
  de Dia (`28041`, Madrid). `postal_code` se valida y se devuelve, pero no cambia precios ni
  catálogo. Dia sí varía según el código postal (Barcelona tiene otros precios; Sevilla, otro
  catálogo): la resolución real llega en la spec 002.
- **Precio sin tarjeta.** En ofertas Club Dia se devuelve el precio sin tarjeta y
  `price_format: null`, porque el precio por unidad que da Dia corresponde al precio con tarjeta.
- **Total aproximado.** `total_results` es el de Dia, que puede desviarse en una unidad.
- **Dependencia de Akamai.** Dia está detrás de Akamai Bot Manager. La API envía un perfil
  coherente de Chrome 155; si Akamai endurece sus reglas, todas las búsquedas responderán `502`.
  El perfil se revisa cada ~3 meses junto con el de Alcampo (`app/scrapers/http_client.py`).
- **Sin Redis la API falla** (`500`): la degradación ante Redis caído no está en el MVP.

## Desarrollo

```bash
pytest                          # los tests nunca llaman a Dia real (respx + fakeredis)
ruff check . && ruff format .   # obligatorio antes de cada commit
mypy                            # tipos, estricto, sobre app/
```

## Documentación

- [Constitución del proyecto](docs/constitution.md)
- [Fase 0: investigación en vivo de Dia](docs/investigacion/fase-0-dia.md)
- Specs: [`specs/`](specs/)

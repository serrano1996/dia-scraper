# dia-scraper

API REST asíncrona (FastAPI) que extrae, procesa y sirve datos de productos de
[Dia online](https://www.dia.es). Ofrece el mismo contrato que `mercadona-scraper` y
`alcampo-scraper` para poder comparar los supermercados sin adaptar el consumidor.

> Estado: búsqueda con el código postal real (`specs/001-dia-scraper-mvp`,
> `specs/002-dia-scraper-postal-code-resolution`); ver [Limitaciones conocidas](#limitaciones-conocidas).

## Puesta en marcha

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload       # http://127.0.0.1:8000/docs
```

Necesita un Redis accesible en `REDIS_URL` y al menos un token en `API_KEYS`.

### Con Docker

API y Redis en contenedores, sin instalar Python ni Redis:

```bash
cp .env.example .env               # rellena DIA_BASE_URL y API_KEYS
docker compose up --build          # http://127.0.0.1:8000/docs
docker compose logs -f api         # logs, con request id
docker compose down                # parar y borrar los contenedores
```

- El compose pone `REDIS_URL` apuntando a su propio Redis, aunque el `.env` diga otra cosa, y
  arranca la API solo cuando Redis responde.
- Necesita Docker Compose 2.24 o posterior (`docker compose version`): el `.env` es opcional en
  el fichero, y esa sintaxis no existe en versiones anteriores.
- Puerto del host: `API_PORT` (por defecto `8000`), solo en `127.0.0.1`: la API no queda abierta
  al resto de la red. Redis no se publica fuera.
- La API se reinicia sola si cae (`restart: unless-stopped`) y, al pararla, tiene hasta 30 s para
  terminar las peticiones en curso y cerrar las sesiones de Dia y Redis.
- Sin `.env`, la API termina al arrancar con el error de la variable que falta.
- El Redis del compose es **efímero** (sin snapshots ni AOF): al reiniciar se pierden la cache, los códigos postales sin
  servicio recordados, el enfriamiento y los límites. Las sesiones de Dia (en memoria) también.
- **Un worker por contenedor**: las sesiones de Dia viven en la memoria del proceso. Para escalar,
  más contenedores sobre el mismo Redis (comparten límites y enfriamiento; cada uno paga sus
  propios cambios de código postal en Dia).
- La imagen (`python:3.11-slim`) corre con un usuario sin privilegios, sin el access log de
  uvicorn (el middleware ya registra cada petición) y con un `HEALTHCHECK` contra `/health`.
- Sin lockfile: la imagen instala las dependencias dentro de las cotas de `pyproject.toml`, así que
  dos builds en fechas distintas pueden traer versiones distintas.

## Autenticación

Todo lo que cuelga de `/api/v1/` exige la cabecera `X-API-Key` con uno de los tokens de
`API_KEYS`. Se comprueba antes que nada: una petición sin token válido no valida parámetros, no
lee Redis y no llama a Dia, así que nadie de fuera puede gastar los límites hacia Dia ni provocar
un bloqueo de Akamai.

- Sin cabecera, vacía o con un token que no coincide exactamente (sin recortar espacios ni
  ignorar mayúsculas): `401 {"detail": "Invalid or missing API key"}` con
  `WWW-Authenticate: ApiKey`. Siempre la misma respuesta, para no dar pistas.
- `API_KEYS` admite varios tokens separados por comas: para rotar sin cortes, se añade el nuevo,
  se cambian los clientes y se quita el viejo.
- Sin ningún token configurado la API arranca, avisa en el log y responde `401` a todo
  `/api/v1/`.
- Públicos: `/health` (`{"status": "ok"}`, no toca Redis ni Dia), `/docs`, `/redoc` y
  `/openapi.json`.
- Los tokens nunca aparecen en los logs; si alguien manda uno en la URL (`?api_key=…`, `?token=…`,
  `?key=…`), no autentica y en el log sale como `'***'`.

La longitud no se valida: genera tokens largos y aleatorios, por ejemplo con

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

## Endpoint

### `GET /api/v1/products`

| Parámetro | Obligatorio | Reglas |
|---|---|---|
| `postal_code` | sí | exactamente 5 dígitos |
| `term` | sí | 1–100 caracteres tras recortar espacios |
| `page` | no | 1–20, por defecto 1 |
| `page_size` | no | 1–100, por defecto 50 |

```bash
curl -H "X-API-Key: $DIA_API_KEY" "http://127.0.0.1:8000/api/v1/products?postal_code=28041&term=leche&page=1&page_size=50"
```

```json
{
  "search": {
    "postal_code": "28041",
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
| `search.warehouse` | código postal con el que Dia ha servido la búsqueda (`cart.postal_code`): siempre el `postal_code` pedido. Dia no expone tienda ni almacén |
| `search.total_results` | total de Dia para la búsqueda. Es una estimación: puede desviarse en ±1 |
| `search.total_pages` | páginas según Dia, con tope 20 |
| `products[].price` | precio que paga cualquier cliente, **sin** tarjeta Club Dia |
| `products[].price_format` | precio por unidad (`L`, `kg`, `ud`, `docena`, `lavado`, `100 ml`, `100 g`); `null` si la unidad es desconocida o el producto tiene oferta Club Dia |
| `products[].category` | categoría de segundo nivel de Dia (p. ej. `"Leche"`) |

### Errores

| Código | Cuándo | Cuerpo |
|---|---|---|
| `401` | falta `X-API-Key` o no es válida (ver [Autenticación](#autenticación)) | `{"detail": "Invalid or missing API key"}` |
| `422` | parámetros inválidos (no se llama a Dia) | formato de FastAPI |
| `404` | Dia no da servicio en ese código postal, o no existe (Dia no los distingue) | `{"detail": "Postal code not served by Dia"}` |
| `404` | `page` > 1 más allá de la última página | `{"detail": "Page out of range"}` |
| `502` | Dia no responde, falla tras los reintentos, responde algo inesperado, responde con otro código postal o Akamai nos bloquea; también durante un enfriamiento o con un límite de salida agotado (ver abajo) | `{"detail": "Upstream service unavailable"}` |

Las respuestas correctas, incluidas las búsquedas sin resultados, se cachean en Redis
`CACHE_TTL_SECONDS` por código postal, término (sin distinguir mayúsculas), página y tamaño de
página. Los códigos postales sin servicio se recuerdan `POSTAL_CODE_NEGATIVE_CACHE_TTL_SECONDS`.
El resto de errores no se cachea.

### Cómo se usa el código postal

Dia toma el código postal de la **sesión** (cookie), no de la búsqueda. La API mantiene en memoria
una sesión de Dia por código postal: la primera búsqueda de un código postal nuevo cuesta 2
peticiones a Dia (fijar el código postal y buscar); las siguientes, 1 por página. `28041`, el de
la sesión anónima de Dia, no necesita la primera. Cada respuesta de Dia dice con qué código postal
la ha servido: si no es el pedido (la sesión caducó), la API abre otra sesión y repite una vez;
si sigue sin serlo, responde `502`. Nunca devuelve datos de un código postal como si fueran de
otro.

### Protección frente a Akamai

Dia está detrás de Akamai Bot Manager. Toda petición a Dia (búsquedas, cambios de código postal y
reintentos) pasa antes por una puerta común, compartida por todas las instancias a través de Redis:

- **Enfriamiento.** Si Akamai bloquea una petición (`403` HTML), la API deja de llamar a Dia durante
  `AKAMAI_COOLDOWN_SECONDS`. Mientras dura, se sigue sirviendo todo lo que esté en cache (también
  los `404` de códigos postales sin servicio); lo demás responde `502` al momento. Un segundo
  bloqueo no lo alarga.
- **Límite global.** Como mucho `DIA_RATE_LIMIT` peticiones a Dia cada `DIA_RATE_WINDOW_SECONDS`
  (ventana deslizante). Pasado el límite, la petición no sale y responde `502`.
- **Límite de códigos postales nuevos.** Como mucho `NEW_SESSION_LIMIT` sesiones nuevas (cada una
  es un cambio de código postal en Dia) cada `NEW_SESSION_WINDOW_SECONDS`. Los códigos postales que
  ya tienen sesión no se ven afectados.
- **Reintentos irregulares.** Cada espera entre reintentos suma un aleatorio de hasta
  `RETRY_JITTER_MAX_S`. Si Dia responde `429` con `Retry-After`, se espera lo que pide (si pide más
  de 60 s, `502` sin reintentar).

Cada enfriamiento y cada límite agotado deja un `WARNING` en el log. Los límites son una
estimación prudente, no un umbral medido: Dia nunca bloqueó por ritmo en la investigación inicial.
Ajústalos con esos avisos.

## Logs

Texto plano a `stderr`, una línea por evento:

```
2026-10-08 10:33:42,051 INFO app.middleware.request_context [b2db584eb44c493b8a9a8b73cba8b072] request started method=GET path='/api/v1/products' params={'postal_code': '28041', 'term': 'leche'}
```

- **Request id.** Cada petición recibe uno (32 caracteres hex) que acompaña a todas sus líneas,
  de cualquier módulo, y se devuelve en la cabecera `X-Request-ID` (también en `404`, `422`,
  `500` y `502`). El `X-Request-ID` que mande el cliente se ignora. Fuera de una petición vale `-`.
- **Nivel** con `LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`, sin distinguir
  mayúsculas). Un valor desconocido impide arrancar.

| Evento | Nivel |
|---|---|
| Inicio y fin de cada petición (con código y duración) | `INFO` |
| `404` (código postal sin servicio, página fuera de rango) | `INFO` |
| Sesión de Dia creada o retirada, con su motivo | `INFO` |
| Reintento | `WARNING` |
| Enfriamiento activo, límite de salida agotado o búsqueda bloqueada (`502`; el `ERROR` es el del bloqueo) | `WARNING` |
| Productos descartados por formato, cache corrupta, unidad desconocida, discrepancia de código postal | `WARNING` |
| Bloqueo de Akamai (con la ruta y el estado del enfriamiento) | `ERROR` |
| Reintentos agotados, `4xx` no reintentable, respuesta de Dia inesperada, `502` no previsto | `ERROR` |
| **Todos** los productos de una respuesta descartados (probable cambio de formato en Dia) | `ERROR` |
| Error no controlado (`500`): tipo de la excepción y frames, nunca su mensaje | `ERROR` |

Nunca se registran cookies (`session_id` ni las de Akamai), cabeceras completas, cuerpos de las
respuestas de Dia, mensajes de excepciones ni URLs con parámetros: de las peticiones a Dia solo consta la ruta. Los valores
que manda el cliente aparecen escapados (`%r`), así que un salto de línea no puede fabricar una
línea falsa. El access log de uvicorn sigue emitiendo su propia línea por petición, sin request
id; si sobra, `uvicorn app.main:app --no-access-log`.

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
| `LOG_LEVEL` | `INFO` | nivel de log (ver [Logs](#logs)) |
| `SESSION_MAX_AGE_SECONDS` | `3000` | una sesión de Dia se renueva pasado este tiempo desde su creación (la cookie de Dia dura 1 h) |
| `MAX_SESSIONS` | `100` | sesiones de Dia abiertas a la vez; al superarlo se descarta la usada hace más tiempo |
| `POSTAL_CODE_NEGATIVE_CACHE_TTL_SECONDS` | `86400` | cuánto se recuerda que Dia no da servicio en un código postal |
| `AKAMAI_COOLDOWN_SECONDS` | `300` | sin llamar a Dia tras un bloqueo de Akamai |
| `DIA_RATE_LIMIT` | `30` | peticiones a Dia por ventana, entre todas las instancias (`0` = sin límite) |
| `DIA_RATE_WINDOW_SECONDS` | `60` | ventana del límite anterior |
| `NEW_SESSION_LIMIT` | `10` | códigos postales nuevos (sesiones) por ventana (`0` = sin límite) |
| `NEW_SESSION_WINDOW_SECONDS` | `600` | ventana del límite anterior |
| `RETRY_JITTER_MAX_S` | `0.3` | aleatorio máximo sumado a cada espera entre reintentos (`0` = sin aleatorio) |
| `API_KEYS` | vacía (nadie entra) | tokens válidos para `X-API-Key`, separados por comas |

## Limitaciones conocidas

- **Sesiones en memoria.** Las sesiones de Dia viven en el proceso: cada instancia de la API
  tiene las suyas, y un reinicio las pierde (la siguiente búsqueda de cada código postal vuelve a
  costar 2 peticiones).
- **Límites sin medir.** Los valores por defecto de la protección frente a Akamai son una
  estimación: pueden quedarse cortos o sobrar. El enfriamiento no crece con bloqueos repetidos.
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

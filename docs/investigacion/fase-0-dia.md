# Fase 0 — Investigación en vivo de Dia

- **Fecha:** 2026-10-07, 09:01–09:40 UTC (~85 peticiones en 40 min).
- **Método:** peticiones reales con `httpx` (HTTP/2 y HTTP/1.1) desde una IP residencial española, más lectura estática de los bundles JS públicos del front de búsqueda (`/search-front/assets/...`) para descubrir rutas. No se usó navegador.
- **Base URL:** `https://www.dia.es` (front Vue + Vike con SSR, detrás de **Akamai** con **Bot Manager**; búsqueda servida por un backend propio sobre **Algolia**).
- **Leyenda:** ✅ verificado en vivo · 🔶 deducido o parcial · ❌ no verificado.

## Resumen

| # | Pregunta | Respuesta | Estado |
|---|---|---|---|
| 1 | Endpoint de búsqueda | `GET /api/v1/search-back/search/reduced?q=&page=&page_size=`. JSON propio de Dia (backend `search-back`, que consulta Algolia). Paginación **por número de página** con total | ✅ |
| 2 | Credenciales | **Ninguna**. Ni cookies previas, ni CSRF, ni pasar por la home: la primera petición en frío devuelve `200` | ✅ |
| 3 | ¿Influye la localización? | **Sí**: catálogo siempre, y precio en algunas zonas (Barcelona sí, Sevilla no, frente a Madrid). Se fija con **1 petición** (`PUT save-shipping-address`) y vive en la cookie `session_id` | ✅ (ver §3) |
| 4 | CP inexistente / sin servicio | Ambos → `206 {"type": "VALIDATION_ERROR", "message": {"no_service": ...}}`. **Indistinguibles**. La sesión no cambia de CP | ✅ |
| 5 | Anti-bot | **Akamai Bot Manager**: `403 Access Denied` (HTML) si la petición no parece de un Chrome real. Sin bloqueo por ritmo observado (15 peticiones a 1/s) | ✅ (umbral ❌) |
| 6 | Forma del producto | Ver §6. Precios **numéricos**, `price_per_unit` y `measure_unit` explícitos. Fixtures en `tests/fixtures/dia_search_*.json` | ✅ |

## 1. Búsqueda por texto ✅

```
GET /api/v1/search-back/search/reduced?q=leche&page=1&page_size=50
Accept: application/json, text/plain, */*
```

- `200 application/json`. Claves raíz: `cart`, `facets`, `locale`, `login_status`, `pagination`, `query_id`, `search_items`, `sort`, `suggestions`, `total_items`.
- Existe también `GET /api/v1/search-back/search` (la que usa el SSR): mismo contenido **más** `header`, `footer` y `customer`. `reduced` es la que usa la web al paginar y es más ligera (~3 KB menos por página: 26,4 KB frente a 29,2 KB para leche). Usamos `reduced`.
- Los productos están en `search_items[]`, en una sola lista (sin grupos).
- **Paginación por número**, no por cursor: `page` (desde 1) y `page_size`. La respuesta trae `pagination: {page_number, page_size, total_pages}` y `total_items`.
  - Sin `page_size` → 30 por página (lo que usa la web).
  - `page_size=100` → 100 por página, `total_pages: 5` (leche, 417 resultados).
  - `page_size=500` → **los 417 de golpe**, `total_pages: 1`. ❌ No se buscó el tope real.
  - `page` más allá de la última (`page=15` con 14 páginas) → `200` con `search_items: []`.
  - **`page ≥ 51` → `404` HTML** "Bloqueado / Este sitio ha sido bloqueado", con cualquier `page_size` (probados 30 y 100). `page=50` → `200`. Bisecado con 51, 52, 53, 56, 62, 68, 75 y 99 (todos `404`). No es un bloqueo de IP: la petición siguiente es `200`. Tampoco es el límite de 1000 resultados de Algolia (`page=34` × 30 y `page=11` × 100 dan `200 []`). 🔶 Regla del borde o del backend sobre el número de página.
- **Orden por defecto:** `sort: {"field": "rating", "direction": "desc"}`.
- **`total_items` no es exacto**: `huevos` dijo 200 y devolvió 201 productos distintos; `piña`, 46 y 47. Es una estimación de Algolia; se observó un error de ±1.
- **Sin resultados** (`q=xqzwvkjhgf`): `200` con `search_items: []`, `total_items: 0`, `total_pages: 0` y sin `facets` → fixture `dia_search_no_results.json`.
- **Mayúsculas:** `LECHE` y `leche` devuelven el mismo total (417) y los mismos 30 productos en el mismo orden (página 1). ✅ Verificado con un término.
- **Tildes y espacios:** `plátano`, `jamón`, `piña` y `papel higiénico` funcionan codificados en la URL (los añade `httpx` con `params=`). ✅
- La web limita el término a **100 caracteres** y exige un mínimo de 2 (`apiEnvironmentVariables.search`: `minCharsToSearch: 2`, `maxCharsToSearch: 100`). ❌ No se probó qué hace el servidor con más de 100.
- El SSR de `/search?q=` incrusta el mismo JSON en `<script id="vike_pageContext">` (`INITIAL_STATE.header.searchData`). No hace falta: el endpoint JSON es directo y más barato.

## 2. Credenciales y sesión ✅

- **Ninguna credencial.** Un cliente sin cookies que llama directamente a la búsqueda recibe `200`. El servidor crea la sesión al vuelo: `Set-Cookie: session_id=<uuid>` (más las de Akamai `ak_bmsc`, `bm_sz`, `_abck`, `bm_sv` y `h163j1mz`).
- La sesión anónima nace en el **CP `28041`** (Madrid): `cart.postal_code: "28041"`.
- La cabecera de respuesta `session_id` repite el valor de la cookie.
- No hay token CSRF: el `PUT` de cambio de CP (§3) funciona solo con la cookie `session_id`.
- ❌ Duración de la sesión `session_id` no medida.
- `session_id` es un identificador de sesión efímero, no una credencial de cuenta. No se commitea; los tests usarán valores sintéticos.

## 3. Localización ✅

### Fijar el CP: una sola petición

Leído del bundle (`renderer_default.page.client`, acción `reevaluate/REEVALUATE`) y verificado en vivo:

```
PUT /api/v1/common-aggregator/save-shipping-address?new_postal_code=<cp>
(sin cuerpo; con la cookie session_id de la sesión)
```

| Respuesta | Significado | Verificado con |
|---|---|---|
| `204` sin cuerpo | CP aplicado a la sesión | 08001, 41001, 07001 |
| `206` `{"code":206,"message":{"no_service":"No service for supplied postal code"},"type":"VALIDATION_ERROR"}` | CP **sin servicio o inexistente**; la sesión **no** cambia | 35001, 99999, `ABCDE` |
| `200` con conflictos | 🔶 Solo con carrito: la web lo trata como "hay que revisar productos" y luego confirma con `&skip_dry_run=true`. Nuestras sesiones nunca tienen carrito, así que no aplica | ❌ no observado |

- Tras el `204`, la búsqueda **en la misma sesión** devuelve `cart.postal_code` con el nuevo CP y otro catálogo. No hace falta ningún paso más.
- Es una sesión por CP: el CP vive en el servidor, ligado a `session_id`. Dos CPs a la vez = dos sesiones (dos cookie jars).
- **No hay identificador de tienda ni almacén** en ninguna respuesta (búsqueda, home, SSR): lo único que se expone es `cart.postal_code`.

### ¿Cambian los precios? ✅ Sí, según la zona

Búsqueda `agua` completa (`page_size=500`) en una sesión con cada CP, frente a una sesión por defecto (28041):

| CP | Resultados | En común | Precio distinto | Solo en una región |
|---|---|---|---|---|
| 08001 (Barcelona) | 312 / 323 | 291 | **27** (p. ej. `659`: 2,69 € / 2,15 €; `5392P12`: 4,68 € / 4,20 €) | 21 / 32 |
| 41001 (Sevilla) | 318 / 323 | 294 | **0** | 24 / 29 |
| 35001 (Las Palmas) | sin servicio (`206`) | — | — | — |

**Conclusión:** el catálogo cambia siempre con el CP; el precio, solo en algunas zonas. Como Dia no expone la tienda que sirve cada CP, **la unidad de cache tiene que ser el CP efectivo** (no podemos agrupar CPs por tienda como en Alcampo).

- Canarias (35001): sin servicio. Baleares (07001): con servicio.

## 4. CP inexistente o sin servicio ✅

- `99999` (inexistente), `ABCDE` (formato inválido) y `35001` (Canarias, existe pero sin reparto) dan **exactamente la misma respuesta** `206 VALIDATION_ERROR no_service`.
- `206` es un `2xx`: `httpx` lo considera éxito. Hay que detectarlo por código y cuerpo.
- La sesión conserva el CP anterior (28041), así que si se ignorara el `206` se servirían datos de Madrid como si fueran del CP pedido.
- El bundle tiene además `CHECK_SERVICE_NEKTRIA` (comprobación previa de servicio en el formulario de dirección). ❌ No probado: el `PUT` ya da la respuesta.

## 5. Protección anti-bot ✅ (umbral ❌)

**Qué se observa** cuando salta:

```
HTTP/1.1 403 Forbidden
Content-Type: text/html
<TITLE>Access Denied</TITLE> ... Reference #18.xxxx ... errors.edgesuite.net
```

Es la página estándar de Akamai. Llega en la **primera** petición si el cliente no parece un navegador. Bisección (una petición cada 20 s, misma IP, sin cookies):

| Cabeceras enviadas | Resultado |
|---|---|
| `curl` (Schannel) con todas las cabeceras de Chrome | `403` |
| `httpx` con su User-Agent por defecto | `403` |
| User-Agent de Chrome + `Accept` | `403` |
| … + `Accept-Language` (+ `Referer`) | `403` |
| User-Agent de Chrome + `Accept` + `sec-ch-ua`, `sec-ch-ua-mobile`, `sec-ch-ua-platform` | `200` |
| User-Agent de Chrome + `Accept` + `Sec-Fetch-Dest/Mode/Site` | `200` |
| Todas las anteriores | `200` (HTTP/2 y HTTP/1.1) |

- **Regla:** un User-Agent de Chrome **sin** cabeceras propias de Chrome (client hints o `Sec-Fetch-*`) delata al bot. Hay que enviar un conjunto **coherente** con el User-Agent.
- `curl` de Windows (Schannel) falla incluso con todas las cabeceras: Akamai también mira la huella TLS. La de `httpx` (OpenSSL) pasa.
- **Ritmo:** 12 peticiones a 3 s y 15 a 1 s (términos distintos, sin cookies previas en la segunda) → todas `200`. No se forzó más para no quemar la IP. Akamai devuelve las cookies `_abck`/`bm_sv`, que su JS de sensor actualizaría en un navegador; sin ejecutar ese JS seguimos recibiendo `200`.
- **No hay `429`, `Retry-After` ni `RateLimit-*`** en ninguna respuesta.
- 🔶 Riesgo: Akamai Bot Manager puede endurecer la regla (exigir la cookie `_abck` validada, que requiere ejecutar su JS) sin aviso. Superarlo exigiría navegador, fuera de la constitución.

## 6. Forma del producto ✅

Extracto de `search_items[0]` (fixture completa en `tests/fixtures/dia_search_leche.json`):

```json
{
  "brand": "Dia Láctea",
  "display_name": "Leche semidesnatada Dia Láctea pack 6 x 1 L",
  "image": "/product_images/504P6/504P6_ISO_0_ES.jpg",
  "l1_category_description": "Huevos, leche y mantequilla",
  "l2_category_description": "Leche",
  "object_id": "504P6",
  "sku_id": "504P6",
  "prices": {
    "currency": "EUR",
    "price": 4.98,
    "price_per_unit": 0.83,
    "measure_unit": "LITRO",
    "strikethrough_price": 4.98,
    "discount_percentage": 0,
    "is_promo_price": false,
    "is_club_price": false
  },
  "units_in_stock": 1017,
  "url": "/huevos-leche-y-mantequilla/leche/p/504P6"
}
```

Campos en 930 productos de 7 búsquedas: siempre presentes `object_id`, `sku_id` (igual a `object_id` en los 930), `display_name`, `image`, `l1/l2_category_description`, `prices` (con sus 8 claves), `url`, `units_in_stock`, `units_in_cart`, `brand_type`; `brand` falta en 8. Opcionales: `allergens`, `promotions`, `headband_promotion`, `stamp_*`, `product_info`, `average_weight`, `weight_in_grams`.

| Campo de la API | Origen en Dia | Nota |
|---|---|---|
| `id` | `object_id` (`"504P6"`) | igual a `sku_id` en todo lo observado |
| `name` | `display_name` | |
| `price` | `prices.price` | **número** (no string). Ver precios Club abajo |
| `price_format` | `prices.price_per_unit` + `prices.measure_unit` | tabla de unidades abajo |
| `image_url` | `https://www.dia.es` + `image` | la ruta es **relativa**; la URL absoluta responde `200` (sirve AVIF aunque acabe en `.jpg`) |
| `category` | `l2_category_description` (o `l1`) | a decidir en la spec |

### Unidades de `measure_unit` (930 productos)

| Valor | Productos | Ejemplo (`price_per_unit`) |
|---|---|---|
| `LITRO` | 667 | leche, cerveza |
| `KILO` | 177 | jamón, queso, detergente en polvo |
| `UNIDAD` | 38 | cápsulas de detergente, pañuelos |
| `LAVADO` | 33 | detergente líquido (0,07 €/lavado) |
| `DOCENA` | 10 | huevos |
| `100 ML.` | 3 | cosmética |
| `100 GR.` | 2 | puré |

❌ El bundle no enumera los valores posibles: puede haber más.

### Precios en promoción y Club Dia ✅

Fixture `dia_search_jamon_promos.json`:

| Caso | `price` | `strikethrough_price` | `is_promo_price` | `is_club_price` |
|---|---|---|---|---|
| Normal | 3,40 | 3,40 | false | false |
| Promoción para todos (jamón cocido) | 1,49 | 1,99 | true | false |
| **Promoción solo Club Dia** (jamón serrano) | 2,79 | 3,49 | true | **true** |

- Con `is_club_price: true`, **`price` es el precio con tarjeta Club Dia**: un cliente sin tarjeta paga `strikethrough_price`. `promotions[].only_club_dia: true` lo confirma.
- `price_per_unit` sigue a `price` (precio Club incluido).
- Esto afecta a la comparación con otros supermercados: hay que decidir qué precio es "el precio".

## Fixtures guardadas (`tests/fixtures/`)

| Fichero | Contenido |
|---|---|
| `dia_search_leche.json` | búsqueda real "leche" (`reduced`, página 1), recortada a 3 productos |
| `dia_search_no_results.json` | búsqueda real sin resultados |
| `dia_search_jamon_promos.json` | 4 productos reales: normal, promoción, promoción Club Dia y `100 GR.` |
| `dia_search_huevos_units.json` | `DOCENA` y `UNIDAD` |
| `dia_search_detergente_lavado.json` | `LAVADO` |
| `dia_search_platano_100ml.json` | `100 ML.` |
| `dia_save_shipping_address_no_service.json` | cuerpo del `206` de CP sin servicio |

En las fixtures recortadas, `pagination` y `total_items` son los originales (no cuadran con el número de productos recortados). No contienen `session_id` ni cookies (comprobado con `grep`). `query_id` es un hash de la consulta, no una credencial.

## Implicaciones para las specs

1. **001:** buscar sin sesión ni pasos previos es viable: **1 petición por búsqueda**. Como Dia pagina por número y da total, la paridad de contrato con Mercadona (`page`, `page_size`, `total_pages`, `total_results`) sale casi gratis: **no hace falta repetir el camino de Alcampo** (MVP sin paginación y luego spec 009).
2. **001:** el cliente HTTP debe enviar un conjunto de cabeceras de Chrome coherente desde el principio, o no funciona nada (Akamai, §5). En Alcampo esto llegó en la spec 002; aquí es requisito del MVP.
3. **Localización:** mucho más barata que en Alcampo (1 `PUT` frente a 8 peticiones), pero exige una sesión (cookie jar) por CP. La clave de cache debe ir por CP efectivo. El `206` hay que traducirlo a `404`.
4. **Anti-baneo:** el riesgo no es el ritmo (no observado) sino la huella: User-Agent, client hints y TLS deben cuadrar. Rotar User-Agents sin rotar las demás cabeceras empeora las cosas.
5. **`price`:** decidir entre precio Club Dia y precio sin tarjeta (§6).

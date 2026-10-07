# Spec 001 — MVP de búsqueda de productos

- **Estado:** aprobada (2026-10-07), con las decisiones de la sección 10
- **Fecha:** 2026-10-07
- **Evidencia:** [Fase 0](../../docs/investigacion/fase-0-dia.md)

## 1. Contexto y objetivo

Ya existen APIs para Mercadona y Alcampo con **el mismo contrato de respuesta**. Queremos su hermana para Dia, para que un consumidor compare los tres supermercados sin tocar su código.

Esta spec entrega lo mínimo útil: buscar productos por texto en Dia y devolverlos normalizados, **con el contrato actual completo** (paginación incluida), cache Redis y un manejo de errores de red predecible.

**Diferencia consciente con Alcampo.** Alcampo hizo un MVP sin paginación (su spec 001) y tuvo que alcanzar el contrato de Mercadona ocho specs después (su spec 009), porque su paginación era por cursor. Dia pagina **por número y da el total** (Fase 0 §1): cada página es una sola petición. Repetir aquel rodeo sería trabajo tirado, así que la paridad de contrato entra ya en esta spec (D1).

**Limitación consciente del MVP.** La Fase 0 demostró que Dia cambia catálogo y, en algunas zonas, precio según el código postal (§3). En esta spec **toda búsqueda se hace con el CP por defecto de la sesión anónima** (`28041`, Madrid). `postal_code` se acepta, se valida y se devuelve, pero **no influye todavía** en precios ni catálogo. La resolución real llega en una spec posterior (D2), como hicieron Mercadona y Alcampo.

## 2. Usuarios y actores

- **Consumidor de la API:** un servicio o script que compara precios entre supermercados. Llama a `GET /api/v1/products`.
- **Dia (upstream):** la web `www.dia.es`, con Akamai Bot Manager delante.
- **Redis:** cache de resultados.

## 3. Historias de usuario

- **H1.** Como consumidor, quiero buscar productos de Dia por texto y recibirlos en el mismo formato que Mercadona y Alcampo, para compararlos sin adaptar mi código.
- **H2.** Como consumidor, quiero recorrer todos los resultados por páginas y saber cuántos hay.
- **H3.** Como consumidor, quiero que una búsqueda sin resultados me devuelva una lista vacía y no un error, para distinguir "no hay" de "algo falló".
- **H4.** Como consumidor, quiero un error claro y estable (`502`) cuando Dia no está disponible o me bloquea, para reintentar más tarde o degradar.
- **H5.** Como operador, quiero que las búsquedas repetidas salgan de cache, para no golpear Dia ni llamar la atención de Akamai.

## 4. Requisitos funcionales (EARS)

### Parámetros

- **RF-1.** CUANDO el consumidor llame a `GET /api/v1/products?postal_code=<str>&term=<str>[&page=<int>][&page_size=<int>]` con parámetros válidos, EL sistema DEBERÁ responder `200` con un cuerpo `ProductSearchResponse` (§6).
- **RF-2.** EL sistema DEBERÁ validar los parámetros con un modelo Pydantic declarado en la firma de la ruta (`Annotated[ProductQuery, Query()]`), con las mismas reglas que Alcampo y Mercadona: `postal_code` exactamente 5 dígitos ASCII tras recortar espacios; `term` de 1 a 100 caracteres tras recortar; `page` entero de 1 a `MAX_PAGE` (20), por defecto 1; `page_size` entero de 1 a 100, por defecto 50. Fuera de estas reglas → `422` sin llamar a Dia ni a Redis.

### Búsqueda

- **RF-3.** CUANDO se consulte Dia, EL sistema DEBERÁ hacer **una sola** petición (sin contar reintentos) a `GET {DIA_BASE_URL}/api/v1/search-back/search/reduced` con `q=<term normalizado>`, `page` y `page_size`. Con `page_size` ≥ 30, son los de la petición. Dia nunca devuelve menos de 30 productos por página (Fase 0 §1), así que con `page_size` < 30 EL sistema DEBERÁ pedir a Dia el menor múltiplo de `page_size` que sea ≥ 30, la página de Dia que contiene la pedida, y quedarse solo con los `page_size` productos de esa página (D9). No hay recorrido ni cursor.
- **RF-4.** EL sistema DEBERÁ enviar en **todas** las peticiones a Dia un conjunto de cabeceras coherente con un Chrome de escritorio: `User-Agent`, `Accept`, `Accept-Language`, `sec-ch-ua`, `sec-ch-ua-mobile`, `sec-ch-ua-platform`, `Sec-Fetch-Dest`, `Sec-Fetch-Mode`, `Sec-Fetch-Site` y `Referer`, con la versión de Chrome del `User-Agent` igual a la de `sec-ch-ua` (Fase 0 §5). Sin ellas, Akamai responde `403` a la primera petición.
- **RF-5.** EL sistema DEBERÁ devolver los productos de `search_items[]` en el orden en que llegan (orden por defecto de Dia, `rating` descendente). SI un `object_id` aparece más de una vez, ENTONCES DEBERÁ conservar solo la primera aparición.
- **RF-6.** CUANDO Dia devuelva `search_items` vacío en la página 1, EL sistema DEBERÁ responder `200` con `products: []`, `total_results: 0` y `total_pages: 0`.
- **RF-7.** CUANDO Dia devuelva `search_items` vacío en una página mayor que 1, EL sistema DEBERÁ responder `404 {"detail": "Page out of range"}` sin cachear nada, como Mercadona y Alcampo.

### Mapeo del producto

- **RF-8.** EL sistema DEBERÁ mapear cada producto crudo a `Product` así: `id` ← `object_id`; `name` ← `display_name`; `image_url` ← `DIA_BASE_URL` + `image` (la ruta llega relativa); `category` ← `l2_category_description` (D4); `price` ← `prices.strikethrough_price` SI `prices.is_club_price` es `true`, y `prices.price` en otro caso: el precio que paga un cliente sin tarjeta Club Dia (D3).
- **RF-9.** EL sistema DEBERÁ construir `price_format` como `"<importe> €/<unidad>"`, con el importe de `prices.price_per_unit` formateado con dos decimales y punto decimal (`0.83`), y la unidad según la tabla de D5. Ejemplo: `{"price_per_unit": 0.83, "measure_unit": "LITRO"}` → `"0.83 €/L"`.
- **RF-10.** SI `measure_unit` no está en la tabla de D5, ENTONCES EL sistema DEBERÁ devolver `price_format: null` sin fallar la validación, y registrar un aviso con la unidad desconocida. SI `prices.is_club_price` es `true`, ENTONCES `price_format` DEBERÁ ser `null`: el `price_per_unit` de Dia se calcula sobre el precio Club, que no es el de `price` (D8).
- **RF-11.** SI un producto individual no puede mapearse porque le falta un campo obligatorio (`object_id`, `display_name`, `prices.price`, `prices.price_per_unit`, `prices.measure_unit`, `image`, `l2_category_description`; y `prices.strikethrough_price` cuando `is_club_price` es `true`) o un precio no es numérico, ENTONCES EL sistema DEBERÁ descartarlo y seguir con el resto, en vez de fallar toda la respuesta.

### Metadatos de la búsqueda

- **RF-12.** EL sistema DEBERÁ rellenar `search` con: `postal_code` de la petición; `term` normalizado (recortado), como Mercadona; `warehouse` = `cart.postal_code` de la respuesta de Dia, el CP con el que Dia ha servido la búsqueda (D6); `strategy_used: "api"`; `scraped_at` = instante UTC de la consulta a Dia; `total_results` = `total_items` de Dia; `page` y `page_size` de la petición; `total_pages` = `min(ceil(total_items / page_size), MAX_PAGE)`, calculado con el `page_size` de la petición (D9).

### Cache

- **RF-13.** CUANDO llegue una búsqueda, EL sistema DEBERÁ consultar Redis antes que Dia, con la clave `search:{postal_code efectivo}:{term normalizado en minúsculas}:{page}:{page_size}`. SI hay entrada, ENTONCES DEBERÁ devolverla sin llamar a Dia.
- **RF-14.** CUANDO una consulta a Dia tenga éxito (con o sin resultados), EL sistema DEBERÁ guardar la respuesta en Redis con TTL `CACHE_TTL_SECONDS` (por defecto 3600).
- **RF-15.** EL sistema NO DEBERÁ cachear respuestas de error ni `404` de página fuera de rango.
- **RF-16.** CUANDO se sirva desde cache, `scraped_at` DEBERÁ conservar el instante de la consulta original a Dia, y `postal_code` el de la petición actual.

### Errores y reintentos

- **RF-17.** CUANDO Dia responda `5xx` o `429`, o haya un error de transporte (timeout, conexión), EL sistema DEBERÁ reintentar hasta `RETRY_MAX_ATTEMPTS` intentos en total, con backoff exponencial (`RETRY_BASE_DELAY × 2^(n-1)`).
- **RF-18.** CUANDO Dia responda cualquier otro `4xx`, EL sistema NO DEBERÁ reintentar.
- **RF-19.** CUANDO Dia responda `403` con `Content-Type: text/html` (bloqueo de Akamai), EL sistema DEBERÁ lanzar `UpstreamBlockedError`, **sin reintentar**, y la API DEBERÁ responder `502` (D7).
- **RF-20.** SI se agotan los reintentos, o Dia responde un `4xx` no reintentable, ENTONCES EL sistema DEBERÁ lanzar `UpstreamUnavailableError`, y la API DEBERÁ responder `502` con un `detail` propio (sin reenviar el cuerpo de Dia).
- **RF-21.** SI Dia responde `2xx` con un cuerpo que no es JSON o no encaja con el schema crudo (p. ej. sin `search_items`), ENTONCES EL sistema DEBERÁ responder `502`.
- **RF-22.** Ningún tipo de `httpx` DEBERÁ cruzar la capa de servicio: los scrapers traducen a excepciones de `app/exceptions.py`.

### Configuración

- **RF-23.** EL sistema DEBERÁ leer `DIA_BASE_URL`, `REDIS_URL`, `CACHE_TTL_SECONDS`, `RETRY_MAX_ATTEMPTS`, `RETRY_BASE_DELAY`, `HTTP_TIMEOUT_SECONDS` y `LOG_LEVEL` con pydantic-settings. SI falta una obligatoria (`DIA_BASE_URL`, `REDIS_URL`), ENTONCES la aplicación DEBERÁ fallar al arrancar.

## 5. Requisitos no funcionales

- **RNF-1. Async:** toda E/S (HTTP y Redis) es `async`. Un único `httpx.AsyncClient` y un único cliente Redis por proceso, creados y cerrados en el `lifespan`.
- **RNF-2. Carga sobre Dia:** **1 petición HTTP por página no cacheada** (más reintentos). Sin pasar por la home ni por pasos de sesión.
- **RNF-3. Latencia:** con cache hit, p95 < 50 ms en local. Sin cache no hay objetivo (depende de Dia); timeout HTTP de 10 s por intento por defecto.
- **RNF-4. Tipado:** type hints en toda función pública; `Any` prohibido; `mypy --strict` limpio sobre `app/`.
- **RNF-5. Tests:** nunca llaman a Dia real (`respx`) ni a Redis real (`fakeredis`, instancia nueva por test). Las fixtures de producto son las reales de la Fase 0. Hay un test de paridad que compara el esquema OpenAPI de la respuesta con el de Mercadona (como el `test_contract_parity.py` de Alcampo).
- **RNF-6. Docs vivas:** `/docs` muestra `ProductSearchResponse` real; `README.md` y `.env.example` actualizados.
- **RNF-7. Seguridad:** no se loguean cookies de Dia (`session_id`, `_abck`, `bm_sz`, `ak_bmsc`, `bm_sv`) ni cabeceras completas.

## 6. Contrato de respuesta

Idéntico al de Mercadona y Alcampo:

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

Errores: `422` (validación, formato FastAPI), `404 {"detail": "Page out of range"}` y `502 {"detail": "Upstream service unavailable"}`.

## 7. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| `term` sin resultados | `200`, `products: []`, `total_pages: 0` (RF-6) |
| `term` con espacios alrededor | se recorta antes de consultar y de construir la clave de cache |
| `term` > 100 caracteres (tras recortar) | `422` (RF-2) |
| `term` de 1 carácter | se acepta (como Mercadona) y se envía a Dia. ❌ La web exige 2; no verificado qué hace el servidor con 1 |
| `term` con tildes, `ñ`, espacios internos | se envía codificado en la URL. ✅ Verificado (`plátano`, `jamón`, `piña`, `papel higiénico`) |
| `page` > última página (≤ 20) | Dia `200 []` → `404 Page out of range` (RF-7) |
| `page` > 20 | `422` sin llamar a Dia (RF-2). Además protege del `404` HTML que Dia da desde `page=51` |
| `total_items` aproximado (±1, Fase 0 §1) | se devuelve tal cual: es el mejor total disponible |
| Producto en promoción Club Dia | `price` = `strikethrough_price`, `price_format: null` (D3, D8) |
| Promoción para todos (`is_promo_price` sin `is_club_price`) | `price` = `prices.price` (el precio rebajado) y `price_format` normal |
| `measure_unit` desconocida | `price_format: null` + aviso en el log (RF-10) |
| Producto sin `image` o sin categoría | se descarta (RF-11). ❌ No observado en vivo (930/930 los tienen) |
| Bloqueo de Akamai (`403` HTML) | `502` sin reintento (RF-19) |
| Dia `404` (p. ej. el de `page ≥ 51`) | `502` sin reintento (RF-18, RF-20) |
| Dia `200` con HTML o JSON inesperado | `502` (RF-21) |
| Redis caído | **fuera de alcance en 001**: hoy la petición falla con `500` |

## 8. Fuera de alcance

- Resolución real de `postal_code` (D2).
- Rotación de User-Agent, `Retry-After`, jitter y enfriamiento tras bloqueo.
- Logging estructurado con request id y handler global de `500`.
- Autenticación `X-API-Key`, Docker, CI, lockfiles.
- Superar Akamai Bot Manager (constitución #14).
- Filtros, orden, categorías y detalle de producto.

## 9. Criterios de finalización

- [ ] Todos los RF tienen al menos un test que falla sin la implementación y pasa con ella.
- [ ] Hay un test de integración con la app real (lifespan + fakeredis + respx) que cubre hit, miss, lista vacía, página fuera de rango y `502`.
- [ ] Un test demuestra que en un cache hit **no** sale ninguna petición HTTP.
- [ ] Un test demuestra que un `403` de Akamai y un `404` de Dia se piden **una sola vez**.
- [ ] Un test compara el esquema de la respuesta con el de Mercadona.
- [ ] `ruff check .`, `ruff format --check .`, `mypy` y `pytest -q` limpios.
- [ ] `/docs` muestra el schema real; `README.md` y `.env.example` actualizados.
- [ ] Prueba manual contra Dia real (una o dos peticiones) documentada.

## 10. Decisiones (dudas resueltas el 2026-10-07)

Todas con la opción recomendada en el borrador.

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | ¿Paginación y contrato completo ya en 001? | Sí: `page`, `page_size`, `total_pages` y `total_results` real, como Mercadona | Sin spec de paridad posterior. 1 petición por página (RF-2, RF-3, RF-12) |
| D2 | ¿Usar `postal_code` de verdad ya en 001? | No: CP por defecto de Dia (`28041`); la resolución real llega en la spec 002 | Hasta la 002, un CP de Barcelona recibe precios y catálogo de Madrid. Se documenta en el README |
| D3 | `price` con oferta Club Dia | El que paga cualquiera: `strikethrough_price` si `is_club_price`, si no `price` | Compara a igualdad con Mercadona y Alcampo. El precio Club no se expone (RF-8) |
| D4 | `category` | `l2_category_description` | La más específica, como el último nivel de Alcampo (RF-8) |
| D5 | Tabla de unidades | `LITRO→L`, `KILO→kg`, `UNIDAD→ud`, `DOCENA→docena`, `LAVADO→lavado`, `100 ML.→100 ml`, `100 GR.→100 g`; cualquier otra → `null` | Todas vistas en vivo (Fase 0 §6). Una unidad nueva degrada a `null` con aviso (RF-9, RF-10) |
| D6 | `warehouse` | `cart.postal_code` de la respuesta de Dia | Dia no expone tienda; el CP efectivo es lo que decide el catálogo y no se desfasa (RF-12) |
| D7 | Bloqueo de Akamai (`403` HTML) | `UpstreamBlockedError` → `502` inmediato, sin reintento | No se castiga más la reputación de la IP (RF-19) |
| D8 | `price_format` con oferta Club Dia | `null` | No se inventa un precio por unidad que Dia no da (RF-10) |
| D9 | `page_size` < 30 (descubierto en la prueba manual de la T20, 2026-10-07: Dia devuelve como mínimo 30) | Recortar de nuestro lado: pedir a Dia un múltiplo del `page_size` ≥ 30 y servir solo el trozo pedido | Se mantiene el contrato de Mercadona (1–100) y 1 petición por página (RF-3, RF-12). Descartadas: subir el mínimo a 30 (rompe la paridad) y pedir siempre 30 (2–4 peticiones por página grande) |

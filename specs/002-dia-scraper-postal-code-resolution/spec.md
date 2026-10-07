# Spec 002 — Búsqueda con el código postal real

- **Estado:** aprobada (2026-10-07), con las decisiones de la sección 10
- **Fecha:** 2026-10-07
- **Evidencia:** [Fase 0](../../docs/investigacion/fase-0-dia.md) §2–§4
- **Referencia:** `alcampo-scraper/specs/007-alcampo-scraper-warehouse-resolution`, adaptada a Dia (allí resolver cuesta 8 peticiones; aquí, 1)

## 1. Contexto y objetivo

Hoy toda búsqueda se hace con el CP por defecto de la sesión anónima de Dia (`28041`, Madrid), sea cual sea el `postal_code` (spec 001, spec-D2). La Fase 0 (§3) demostró que Dia **sí** cambia según el CP:

- Barcelona (08001) frente a Madrid, término "agua" (todos los resultados): 27 de 291 productos comunes con **precio distinto**, y 21/32 productos solo en una de las dos.
- Sevilla (41001): mismos precios, pero 24/29 productos solo en una de las dos (**catálogo distinto**).
- Canarias (35001): **sin servicio**.

Así que hoy un cliente de Barcelona recibe precios de Madrid, y uno de Las Palmas recibe resultados de una tienda que no le sirve.

**En Dia, fijar el CP es barato** (Fase 0 §3):

1. Una sola petición: `PUT /api/v1/common-aggregator/save-shipping-address?new_postal_code=<cp>`, sin cuerpo, con la cookie `session_id` de la sesión.
2. `204` → el CP queda en la sesión **del servidor**. `206 {"type": "VALIDATION_ERROR", "message": {"no_service": ...}}` → CP sin servicio **o inexistente** (indistinguibles); la sesión no cambia.
3. La búsqueda no acepta el CP como parámetro: lo toma de la sesión. **Una sesión (cookie jar) por CP.**
4. La sesión caduca: `session_id` vence a la hora (🔶 probablemente renovada con cada uso).
5. **Toda respuesta de búsqueda dice con qué CP se ha servido** (`cart.postal_code`). Es la forma de no servir nunca un CP por otro.
6. **No hay identificador de tienda**: no podemos agrupar CPs que comparten tienda. La unidad de cache es el CP.

**Objetivo:** que cada búsqueda use el `postal_code` pedido, con su precio y su catálogo; que un CP sin servicio sea un `404` claro; y que el servicio **nunca** devuelva datos de un CP etiquetados como otro.

## 2. Usuarios y actores

- **Consumidor de la API:** envía un `postal_code` real y espera los precios y el catálogo de esa zona.
- **Responsable del servicio:** quiere que el número de peticiones a Dia siga siendo bajo con muchos CPs distintos.
- **Dia (upstream)** y **Redis**, como en la spec 001.

## 3. Historias de usuario

- **H1.** Como consumidor, quiero precios y catálogo del código postal que envío, no los de Madrid.
- **H2.** Como consumidor, quiero que un código postal sin servicio (o inexistente) se responda con `404`, para distinguirlo de un fallo de Dia (`502`).
- **H3.** Como responsable del servicio, quiero que buscar en un CP ya usado no cueste más peticiones que hoy.
- **H4.** Como responsable del servicio, quiero que el servicio **nunca** devuelva datos de un CP etiquetados como otro, aunque Dia renueve o pierda la sesión.

## 4. Requisitos funcionales (EARS)

### A. Sesión por código postal

- **RF-1.** CUANDO llegue una búsqueda no cacheada para un `postal_code` sin sesión vigente, EL sistema DEBERÁ crear una sesión nueva (cookie jar propio) y fijar en ella el CP con `PUT {DIA_BASE_URL}/api/v1/common-aggregator/save-shipping-address?new_postal_code=<cp>`, sin cuerpo y con las cabeceras de Chrome de la spec 001 (RF-4).
- **RF-2.** SI Dia responde `204`, ENTONCES EL sistema DEBERÁ usar esa sesión para la búsqueda y para las siguientes búsquedas de ese CP mientras siga vigente (D2).
- **RF-3.** SI el `postal_code` pedido es el CP por defecto de Dia (`28041`), ENTONCES EL sistema NO DEBERÁ llamar al `PUT`: la sesión anónima ya nace con él (D6).
- **RF-4.** SI Dia responde `206` con `type: "VALIDATION_ERROR"` y `message.no_service`, ENTONCES EL sistema DEBERÁ lanzar `PostalCodeNotServedError`, y la API DEBERÁ responder `404 {"detail": "Postal code not served by Dia"}`, sin reenviar nada de Dia (D4).
- **RF-5.** EL sistema DEBERÁ cachear en Redis la respuesta negativa de RF-4 con TTL `POSTAL_CODE_NEGATIVE_CACHE_TTL_SECONDS` (D5), y responder `404` sin llamar a Dia mientras esté vigente.
- **RF-6.** SI el `PUT` responde cualquier otra cosa (otro `2xx`, `206` sin `no_service`, `4xx`, `5xx`, bloqueo de Akamai), ENTONCES EL sistema DEBERÁ aplicar la misma política de reintentos y errores de la spec 001 (RF-17…RF-20) y responder `502` sin cachear nada.
- **RF-7.** Varias búsquedas simultáneas sin sesión para el **mismo** CP DEBERÁN compartir un único `PUT` (D3).

### B. Nunca un CP por otro

- **RF-8.** CUANDO llegue la respuesta de una búsqueda, EL sistema DEBERÁ comprobar que `cart.postal_code` es el CP de la sesión usada.
- **RF-9.** SI no coincide (la sesión caducó o Dia la reinició), ENTONCES EL sistema DEBERÁ descartar esa sesión, crear otra (RF-1) y repetir la búsqueda **una sola vez**. SI vuelve a no coincidir, ENTONCES DEBERÁ responder `502` y registrar un `WARNING` con ambos CPs (D7).
- **RF-10.** `search.warehouse` DEBERÁ seguir siendo el `cart.postal_code` de la respuesta (spec 001 spec-D6), que tras RF-8 es siempre el `postal_code` pedido.

### C. Cache

- **RF-11.** La clave de cache de búsquedas DEBERÁ usar el `postal_code` pedido en lugar de la constante `28041`: `search:{postal_code}:{term}:{page}:{page_size}` (D8).
- **RF-12.** Un cache hit NO DEBERÁ crear sesión ni llamar a Dia (como hoy).

### D. Ciclo de vida de las sesiones

- **RF-13.** Las sesiones DEBERÁN vivir en memoria del proceso, como mucho `SESSION_MAX_AGE_SECONDS` desde su creación (por debajo de la hora de `session_id`), y como mucho `MAX_SESSIONS` a la vez; al superar el máximo se descarta la usada hace más tiempo (D2).
- **RF-14.** Cada sesión DEBERÁ cerrarse (su `httpx.AsyncClient`) al descartarse y al apagar la aplicación.

## 5. Requisitos no funcionales

- **RNF-1. Carga sobre Dia:** CP ya usado, sin cache → 1 petición por página (como hoy). CP nuevo → 2 (`PUT` + búsqueda). Nunca más de 2 por página, salvo la repetición única de RF-9 y los reintentos.
- **RNF-2. Sin dependencias nuevas** (constitución #1).
- **RNF-3. Tests sin red** (respx, fakeredis), con la fixture real `dia_save_shipping_address_no_service.json` y sesiones con `session_id` sintéticos.
- **RNF-4. Seguridad:** no se loguea `session_id` ni ninguna cookie (constitución #12).
- **RNF-5. Docs vivas:** README (se quita la limitación del CP por defecto) y `.env.example` con las variables nuevas.
- **RNF-6. Contrato:** sin cambios de forma (test de paridad de la 001 en verde). Solo es nuevo el `404` de CP sin servicio, que Mercadona y Alcampo ya tienen.

## 6. Contrato

Sin cambios en la respuesta `200`. Error nuevo:

```json
404 {"detail": "Postal code not served by Dia"}
```

## 7. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| `postal_code=28041` | sin `PUT` (RF-3) |
| CP con servicio, primera vez | `PUT` `204` + búsqueda (2 peticiones) |
| Mismo CP, otra página o término | 1 petición, misma sesión |
| CP sin servicio (35001) o inexistente (99999) | `404`, cacheado (RF-4, RF-5) |
| Dos búsquedas a la vez de un CP nuevo | un solo `PUT` (RF-7) |
| Sesión caducada: `cart.postal_code` vuelve como `28041` | sesión nueva y una repetición (RF-9) |
| `PUT` bloqueado por Akamai | `502`, sin reintento (spec 001 RF-19) |
| Más de `MAX_SESSIONS` CPs activos | se descarta la menos usada (RF-13) |
| Redis caído | **fuera de alcance** (D1): sigue dando `500` |

## 8. Fuera de alcance

- Degradación sin Redis y timeouts de Redis (D1).
- Límite de resoluciones nuevas por ventana (D9).
- Agrupar CPs que comparten tienda: Dia no expone tienda.
- Rotación de User-Agent y enfriamiento tras bloqueo.

## 9. Criterios de finalización

- [ ] Todos los RF tienen al menos un test que falla sin la implementación y pasa con ella.
- [ ] Un test de integración demuestra 2 peticiones para un CP nuevo y 1 para el siguiente término del mismo CP.
- [ ] Un test demuestra que un `cart.postal_code` distinto nunca llega al consumidor.
- [ ] Test de paridad de la 001 en verde.
- [ ] `ruff`, `mypy` y `pytest` limpios; README y `.env.example` actualizados.
- [ ] Prueba manual contra Dia real con 08001 y 35001 (3–4 peticiones), documentada.

## 10. Decisiones (dudas resueltas el 2026-10-07)

Todas con la opción recomendada en el borrador.

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Degradación sin Redis | Fuera: spec propia | Redis caído sigue dando `500` |
| D2 | Almacén de sesiones | Pool en memoria, una por CP; `SESSION_MAX_AGE_SECONDS=3000` (50 min) y `MAX_SESSIONS=100`, descartando la usada hace más tiempo | 1 petición por página en CPs ya usados (RF-13) |
| D3 | `PUT` compartido entre búsquedas simultáneas | Sí | Un CP nuevo cuesta un `PUT` aunque lleguen 10 búsquedas a la vez (RF-7) |
| D4 | Texto del `404` | `"Postal code not served by Dia"` | Como Alcampo (RF-4) |
| D5 | TTL de la cache negativa | `POSTAL_CODE_NEGATIVE_CACHE_TTL_SECONDS=86400` (1 día) | Un CP que empiece a tener servicio tarda hasta un día en notarse (RF-5) |
| D6 | `28041` sin `PUT` | Sí | La sesión anónima ya nace en `28041` (RF-3) |
| D7 | `cart.postal_code` distinto | Sesión nueva y una repetición; si persiste, `502` + `WARNING` | Nunca un CP por otro, sin `502` evitables (RF-9) |
| D8 | Clave de cache | `search:{postal_code pedido}:…` | Una entrada por CP (RF-11) |
| D9 | Límite de `PUT` nuevos | No en la 002 | Se revisa en la spec de anti-baneo |

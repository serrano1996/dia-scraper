# Spec 008 — Funcionar con Redis caído

- **Estado:** aprobada (2026-10-08), con las decisiones de la sección 10
- **Fecha:** 2026-10-08
- **Referencia:** `alcampo-scraper/specs/007-alcampo-scraper-warehouse-resolution` §E (RF-14, RF-15, RF-18 y su enmienda del circuit breaker) y `specs/012-alcampo-scraper-operational-robustness` (`/ready`), con sus implementaciones (`redis_circuit.py`, respaldos locales)

## 1. Contexto y objetivo

Redis guarda cinco cosas en Dia, y hoy todas son imprescindibles:

| Uso | Spec | Si Redis falla hoy |
|---|---|---|
| Cache de búsquedas (`search:*`) | 001 | `500` |
| Cache de CPs sin servicio (`postal_code:not_served:*`) | 002 | `500` |
| Enfriamiento tras un bloqueo (`akamai:cooldown`) | 003 | `500` |
| Límite global de peticiones (`ratelimit:dia`) | 003 | `500` |
| Límite de CPs nuevos (`ratelimit:dia:new_sessions`) | 003 | `500` |

Y dos agravantes:

- **Sin timeouts.** `create_redis` es `redis.from_url(url)` sin `socket_timeout` ni `socket_connect_timeout`: un Redis **colgado** (no caído) deja cada búsqueda esperando sin límite, mientras `/health` sigue en `200`.
- **Nadie sabe que Redis falla:** `/health` no lo mira (a propósito, spec 005) y no hay otra sonda.

Alcampo resolvió lo mismo en dos pasos y aprendió por el camino (su spec 007, enmienda del 2026-09-30): **solo con timeouts, cada búsqueda sin Redis tardaba ~9 s** (≈4 operaciones × 2 s), y con el contenedor parado asyncio registraba falsos `ERROR` (`Future exception was never retrieved`). La solución fue un **circuit breaker**: tras un fallo, Redis se salta unos segundos y se usan los respaldos directamente.

**Objetivo:** que un Redis caído o colgado **degrade** el servicio en lugar de tumbarlo: búsquedas que siguen respondiendo (sin cache), protecciones frente a Akamai que siguen activas (en memoria del proceso), tiempos acotados, y una sonda `/ready` que diga que Redis falta.

## 2. Usuarios y actores

- **Consumidor de la API:** sigue recibiendo resultados aunque Redis falle.
- **Responsable del servicio:** se entera de que Redis falla (logs y `/ready`) y no sufre un bloqueo de Akamai por haber perdido los límites.

## 3. Historias de usuario

- **H1.** Como consumidor, quiero seguir buscando aunque Redis esté caído o colgado.
- **H2.** Como responsable del servicio, quiero que, sin Redis, los límites y el enfriamiento sigan protegiéndonos de Akamai.
- **H3.** Como responsable del servicio, quiero que una búsqueda sin Redis no tarde más que con Redis, salvo un timeout al detectarlo.
- **H4.** Como responsable del servicio, quiero una sonda que diga si la instancia tiene Redis.

## 4. Requisitos funcionales (EARS)

### A. Timeouts

- **RF-1.** El cliente Redis DEBERÁ tener timeout de conexión y de operación de `REDIS_TIMEOUT_SECONDS` (por defecto 2, D1).

### B. Circuit breaker

- **RF-2.** CUANDO una operación con Redis falle (error o timeout), EL sistema DEBERÁ dejar de intentar Redis durante `REDIS_CIRCUIT_OPEN_SECONDS` (por defecto 10, D2) y usar directamente los respaldos de la sección C. Pasado ese tiempo, la siguiente operación prueba Redis: si responde, el circuito se cierra.
- **RF-3.** Un solo circuito por proceso para todos los usos de Redis, creado en el `lifespan`. `REDIS_CIRCUIT_OPEN_SECONDS=0` lo desactiva.

### C. Respaldos (D3)

- **RF-4.** **Cache de búsquedas:** sin Redis, una lectura es un miss y una escritura se omite. La búsqueda responde `200` si Dia responde.
- **RF-5.** **Cache de CPs sin servicio:** sin Redis, un CP se trata como no marcado (se vuelve a preguntar a Dia) y la marca se omite (D4).
- **RF-6.** **Enfriamiento:** sin Redis, DEBERÁ usarse un enfriamiento en memoria del proceso con las mismas reglas (duración fija, no se alarga).
- **RF-7.** **Límites:** sin Redis, cada límite (global y de CPs nuevos) DEBERÁ usar una ventana deslizante en memoria del proceso con el mismo límite y la misma ventana. La coordinación entre instancias se pierde; cada proceso sigue limitado.
- **RF-8.** Un enfriamiento activado en memoria NO DEBERÁ perderse al volver Redis: mientras dure, la puerta lo respeta aunque Redis diga que no hay (D3).

### D. Visibilidad

- **RF-9.** EL sistema DEBERÁ registrar un `WARNING` al abrirse el circuito (con el tipo de error) y un `INFO` al cerrarse; mientras está abierto, cada uso del respaldo NO DEBERÁ registrar nada por encima de `DEBUG` (D5).
- **RF-10.** EL sistema DEBERÁ exponer `GET /ready`, público como `/health`: `200 {"status": "ready", "redis": "ok"}` si Redis responde a un `PING` (a través del circuito), y `503 {"status": "unavailable", "redis": "unreachable"}` si no (D6). `/health` no cambia.

## 5. Requisitos no funcionales

- **RNF-1. Sin dependencias nuevas.**
- **RNF-2. Tiempos acotados:** con Redis caído, la primera búsqueda tarda como mucho un `REDIS_TIMEOUT_SECONDS` más; las siguientes, durante el circuito abierto, nada más que sin Redis.
- **RNF-3. Tests sin Redis real:** un doble de Redis que falla o se cuelga a voluntad, y un reloj inyectable para el circuito.
- **RNF-4. Docs vivas:** README (qué pasa sin Redis, `/ready`) y `.env.example` con las dos variables nuevas.

## 6. Contrato

Sin cambios en `/api/v1/products`. Nuevo `GET /ready` (público).

## 7. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Redis caído al arrancar | la app arranca (el cliente no conecta hasta usarse); búsquedas `200` sin cache; `/ready` `503` |
| Redis colgado | la primera operación espera `REDIS_TIMEOUT_SECONDS`, abre el circuito; el resto, sin esperar |
| Redis vuelve | como mucho `REDIS_CIRCUIT_OPEN_SECONDS` después se usa otra vez; las entradas de cache de antes siguen valiendo |
| Bloqueo de Akamai sin Redis | enfriamiento en memoria del proceso (las otras instancias no lo ven) |
| Varias instancias sin Redis | cada una con sus límites: el tráfico total hacia Dia puede ser `instancias × DIA_RATE_LIMIT` |
| CP sin servicio sin Redis | se vuelve a preguntar a Dia (un `PUT` por búsqueda, limitado por los límites locales) |
| `/health` sin Redis | `200` (no lo mira) |

## 8. Fuera de alcance

- Redis en alta disponibilidad (Sentinel, Cluster).
- Cache en memoria del proceso como respaldo de la de Redis (D3).
- Reintentos de operaciones de Redis.

## 9. Criterios de finalización

- [ ] Todos los RF con tests en verde.
- [ ] Un test demuestra que, con Redis colgado, una búsqueda responde en torno a `REDIS_TIMEOUT_SECONDS` y las siguientes sin esperar.
- [ ] Un test demuestra que, sin Redis, el límite global y el enfriamiento siguen frenando peticiones.
- [ ] `ruff`, `mypy`, `pytest` y la CI en verde; README y `.env.example` actualizados.
- [ ] **Verificación manual** con `docker compose`: búsqueda con Redis, `docker compose stop redis`, búsqueda otra vez (`200` sin cache, tiempo acotado, `/ready` `503`, `WARNING` del circuito), `docker compose start redis` y `/ready` `200`. Con 1–2 búsquedas reales a Dia.

## 10. Decisiones (dudas resueltas el 2026-10-08)

Todas con la opción recomendada en el borrador.

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Timeout de Redis | `REDIS_TIMEOUT_SECONDS=2` | RF-1 |
| D2 | Circuit breaker | Desde el principio, `REDIS_CIRCUIT_OPEN_SECONDS=10` | RF-2, RF-3 |
| D3 | Respaldos | Las caches se saltan; enfriamiento y límites en memoria del proceso; sin cache en memoria | RF-4…RF-8 |
| D4 | CP sin servicio sin Redis | Se vuelve a preguntar a Dia | RF-5 |
| D5 | Ruido en los logs | `WARNING` al abrir, `INFO` al cerrar; `DEBUG` por uso del respaldo | RF-9 |
| D6 | `/ready` | Ahora, en esta spec | RF-10 |

# Spec 003 — Medidas antibaneo

- **Estado:** aprobada (2026-10-07), con las decisiones de la sección 10
- **Fecha:** 2026-10-07
- **Evidencia:** [Fase 0 §5](../../docs/investigacion/fase-0-dia.md)
- **Referencia:** `alcampo-scraper` specs 002 (antibaneo), 008 (protección de salida) y 010 (ritmo), adaptadas a lo que se sabe de Akamai en Dia

## 1. Contexto y objetivo

Lo que ya hay (specs 001 y 002):

- Un perfil de Chrome 155 coherente en todas las peticiones (`User-Agent`, client hints, `Sec-Fetch-*`): sin él, Akamai responde `403` a la primera.
- Reintentos con backoff exponencial **exacto** (`0.5 s`, `1 s`) ante `5xx`, `429` y errores de red.
- El `403` HTML de Akamai → `UpstreamBlockedError` → `502`, sin reintento.
- Cache de 1 h por CP y búsqueda, y un solo `PUT` aunque lleguen varias búsquedas de un CP nuevo a la vez.

Los huecos:

1. **Insistir tras un bloqueo.** Cuando Akamai nos bloquea, cada búsqueda no cacheada vuelve a llamar a Dia y recibe otro `403`. Si el bloqueo es por IP, eso lo alarga.
2. **Sin techo de tráfico.** Muchos clientes con búsquedas distintas, o muchas instancias, pueden mandar a Dia ráfagas sin límite. En la Fase 0, 15 peticiones a 1 por segundo no dispararon nada, pero no sabemos dónde está el umbral.
3. **Sin techo de CPs nuevos.** Cada CP nuevo es un `PUT` y una sesión nueva de Akamai desde la misma IP (spec 002, spec-D9, aplazado aquí).
4. **Esperas perfectamente regulares** entre reintentos, un patrón fácil de reconocer.

**Lo que dice la Fase 0 y conviene no perder de vista.** En Dia **nunca se vio un `429`, un `Retry-After` ni un bloqueo por ritmo**: el único bloqueo observado fue por **huella** (cabeceras incoherentes), y es inmediato. No sabemos cuánto dura un bloqueo de Akamai ni qué lo dispara por volumen. Los límites de esta spec son **prudencia sin umbral medido**: se configuran por entorno y se ajustarán con lo que se vea en producción.

**Objetivo:** dejar de insistir tras un bloqueo, poner techo al tráfico hacia Dia y a los CPs nuevos, y quitar la regularidad de los reintentos, sin cambiar el stack (constitución #1) ni intentar superar Akamai (constitución #14).

## 2. Usuarios y actores

- **Responsable del servicio:** quiere que Akamai no marque la IP y, si la marca, que el servicio no lo empeore.
- **Consumidor de la API:** sigue recibiendo lo que esté en cache; lo demás, `502` inmediatos mientras dure un enfriamiento o se agote un límite.

## 3. Historias de usuario

- **H1.** Como responsable del servicio, quiero que tras un bloqueo de Akamai el servicio deje de llamar a Dia un tiempo, sin dejar de servir la cache.
- **H2.** Como responsable del servicio, quiero un techo global de peticiones a Dia, compartido por todas las instancias, para que ningún pico de clientes se convierta en una ráfaga.
- **H3.** Como responsable del servicio, quiero un techo de CPs nuevos por ventana, porque cada uno abre una sesión nueva frente a Akamai.

## 4. Requisitos funcionales (EARS)

### A. Enfriamiento tras un bloqueo

- **RF-1.** CUANDO cualquier petición a Dia (búsqueda o `PUT`) termine en `UpstreamBlockedError`, EL sistema DEBERÁ activar un enfriamiento global en Redis durante `AKAMAI_COOLDOWN_SECONDS` (D1), compartido por todas las instancias.
- **RF-2.** MIENTRAS el enfriamiento esté activo, una búsqueda que no esté en cache DEBERÁ responder `502` sin llamar a Dia, y un cache hit DEBERÁ servirse como siempre. Tampoco se crean sesiones nuevas.
- **RF-3.** Un bloqueo durante un enfriamiento ya activo NO DEBERÁ alargarlo (D2).

### B. Límite global de salida

- **RF-4.** EL sistema DEBERÁ contar en Redis todas las peticiones a Dia (búsquedas, `PUT` y reintentos), en una ventana de `DIA_RATE_WINDOW_SECONDS`, compartida por todas las instancias.
- **RF-5.** SI una petición superaría `DIA_RATE_LIMIT` en la ventana, ENTONCES NO DEBERÁ enviarse: la búsqueda responde `502` al momento (D3).
- **RF-6.** `DIA_RATE_LIMIT=0` DEBERÁ desactivar el límite.

### C. Límite de códigos postales nuevos

- **RF-7.** EL sistema DEBERÁ limitar los `PUT` a `NEW_SESSION_LIMIT` por ventana de `NEW_SESSION_WINDOW_SECONDS`, aparte del límite global (D4). Agotado, una búsqueda de un CP **sin sesión** responde `502` al momento; las de CPs con sesión vigente no se ven afectadas.
- **RF-8.** Renovar la sesión de un CP (edad o discrepancia de la spec 002) también cuenta como `PUT`.

### D. Reintentos menos regulares

- **RF-9.** La espera entre reintentos DEBERÁ ser `RETRY_BASE_DELAY × 2^(n-1)` más un aleatorio uniforme en `[0, RETRY_JITTER_MAX_S]` (D5).
- **RF-10.** CUANDO Dia responda `429` con `Retry-After` (en segundos o fecha HTTP), EL sistema DEBERÁ esperar ese tiempo en lugar del backoff, con un tope de 60 s; por encima del tope, no reintentar y responder `502` (D6).

### E. Visibilidad

- **RF-11.** EL sistema DEBERÁ registrar un `WARNING` al activar un enfriamiento (con su duración) y al agotar un límite (con cuál), sin cabeceras ni cookies (constitución #12). El logging completo llega en su spec; esto es lo mínimo para que un bloqueo no pase desapercibido (revisión de la 001).

## 5. Requisitos no funcionales

- **RNF-1. Sin dependencias nuevas.** Contadores y enfriamiento con `INCR`/`EXPIRE`/`SET NX EX` de Redis.
- **RNF-2. Tests sin red**, con fakeredis y un `uniform` y un reloj inyectables.
- **RNF-3. Docs vivas:** README (sección "Protección frente a Akamai") y `.env.example` con las variables nuevas.
- **RNF-4. Contrato:** sin cambios. Los `502` nuevos usan el mismo cuerpo de siempre.

## 6. Contrato

Sin cambios. Enfriamiento y límites agotados responden `502 {"detail": "Upstream service unavailable"}`.

## 7. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Bloqueo en una búsqueda | `502` y enfriamiento activado (RF-1) |
| Bloqueo en un `PUT` | igual (RF-1) |
| Búsqueda cacheada durante el enfriamiento | `200` desde cache (RF-2) |
| CP en cache negativa durante el enfriamiento | `404` como siempre: no llama a Dia |
| Dos instancias | comparten enfriamiento y contadores (Redis) |
| Reintento de un `503` con el límite justo agotado | no se envía; `502` (RF-4, RF-5) |
| `429` con `Retry-After: 3600` | sin reintento, `502` (RF-10) |
| Redis caído | **fuera de alcance**: sigue dando `500` (spec de degradación) |

## 8. Fuera de alcance

- Rotación de User-Agent (D7).
- Espaciado mínimo entre peticiones, como la spec 010 de Alcampo (D8).
- Degradación sin Redis.
- Logging estructurado completo y request id.
- Superar Akamai (constitución #14).

## 9. Criterios de finalización

- [ ] Todos los RF tienen al menos un test que falla sin la implementación y pasa con ella.
- [ ] Un test de integración demuestra que, tras un bloqueo, la siguiente búsqueda no cacheada no llama a Dia y una cacheada sí se sirve.
- [ ] Un test demuestra que dos "instancias" (dos apps sobre el mismo fakeredis) comparten límite y enfriamiento.
- [ ] `ruff`, `mypy` y `pytest` limpios; README y `.env.example` actualizados.
- [ ] Sin prueba manual de bloqueo: provocar un bloqueo real de Akamai a propósito pondría en riesgo la IP. Se documenta así.

## 10. Decisiones (dudas resueltas el 2026-10-07)

Todas con la opción recomendada en el borrador.

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Duración del enfriamiento | `AKAMAI_COOLDOWN_SECONDS=300` | 5 min de `502` para lo no cacheado tras un bloqueo (RF-1) |
| D2 | ¿Alargar el enfriamiento? | No: fijo | Si se ven bloqueos encadenados, se pasa al creciente de Alcampo (RF-3) |
| D3 | Límite global | `DIA_RATE_LIMIT=30` por `DIA_RATE_WINDOW_SECONDS=60` | Estimación sin umbral medido; configurable (RF-4, RF-5) |
| D4 | Límite de CPs nuevos | `NEW_SESSION_LIMIT=10` por `NEW_SESSION_WINDOW_SECONDS=600` | Un CP nuevo de más en la ventana responde `502` (RF-7) |
| D5 | Jitter | `RETRY_JITTER_MAX_S=0.3` | RF-9 |
| D6 | `Retry-After` | Sí, tope 60 s | RF-10 |
| D7 | Rotación de User-Agent | No | Un único perfil de Chrome coherente |
| D8 | Espaciado mínimo | No en la 003 | Se revisa si aparecen bloqueos por ráfagas |

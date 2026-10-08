# Spec 005 — Autenticación

- **Estado:** aprobada (2026-10-08), con las decisiones de la sección 10
- **Fecha:** 2026-10-08
- **Referencia:** `alcampo-scraper/specs/004-alcampo-scraper-authentication` (aprobada), adaptada a Dia

## 1. Contexto y objetivo

Hoy `GET /api/v1/products` no pide ninguna credencial: cualquiera que conozca la URL puede usarla. En Dia eso cuesta más que en una API normal:

- **Tráfico ajeno contra Akamai.** Cada búsqueda no cacheada de un tercero es una petición real a Dia desde nuestra IP, y cada código postal nuevo, un `PUT` y una sesión nueva. Un tercero puede agotar nuestro límite global (spec 003) o el de códigos postales nuevos y dejarnos con `502`, o provocar un bloqueo de Akamai y su enfriamiento.
- **Sin forma de cortar a un cliente concreto** sin cambiar la URL o apagar el servicio.

**Objetivo:** que solo nuestras aplicaciones, con un token compartido, puedan usar la API. Control de acceso de servicio a servicio, sin cuentas ni dependencias nuevas (constitución #1).

## 2. Usuarios y actores

- **Aplicación cliente autorizada:** conoce un token válido y lo envía en cada petición.
- **Tercero no autorizado:** sin token válido; se rechaza siempre.
- **Responsable del servicio:** configura, rota y revoca tokens con una variable de entorno, sin desplegar código.

## 3. Historias de usuario

- **H1.** Como responsable del servicio, quiero que la API rechace cualquier petición sin un token válido, para que ningún tercero gaste nuestros límites hacia Dia ni provoque un bloqueo de Akamai.
- **H2.** Como responsable del servicio, quiero rotar o revocar un token sin desplegar código.
- **H3.** Como responsable del servicio, quiero ver los intentos rechazados sin que los logs muestren ningún token.

## 4. Requisitos funcionales (EARS)

### A. Control de acceso

- **RF-1.** CUANDO llegue una petición a cualquier endpoint bajo `/api/v1/`, EL sistema DEBERÁ exigir la cabecera `X-API-Key` con un valor igual a alguno de los tokens configurados, **antes** de validar parámetros, leer Redis (cache, cache negativa, enfriamiento, límites) o llamar a Dia.
- **RF-2.** SI `X-API-Key` falta o está vacía, ENTONCES EL sistema DEBERÁ responder `401 {"detail": "Invalid or missing API key"}` sin ejecutar la lógica del endpoint.
- **RF-3.** SI `X-API-Key` no coincide con ningún token, ENTONCES EL sistema DEBERÁ responder **exactamente el mismo** `401` que en RF-2.
- **RF-4.** La comparación DEBERÁ hacerse en tiempo constante (`secrets.compare_digest`) contra cada token configurado.
- **RF-5.** Se compara el valor **exacto**: sin `strip()` ni cambios de mayúsculas.
- **RF-6.** Fuera de `/api/v1/` (`/docs`, `/openapi.json`, `/redoc` y `/health` si existe, D5) todo DEBERÁ seguir siendo público.
- **RF-7.** Todo `401` DEBERÁ llevar `WWW-Authenticate: ApiKey` (RFC 9110).

### B. Configuración

- **RF-8.** Los tokens DEBERÁN leerse de `API_KEYS`, separados por comas, recortando espacios de cada entrada y descartando las vacías (`" a , ,b "` → `{"a", "b"}`). Nunca en el código.
- **RF-9.** SI `API_KEYS` no está o queda vacía tras limpiarla, ENTONCES EL sistema DEBERÁ **fallar cerrado**: arranca, pero toda petición a `/api/v1/` recibe `401`.
- **RF-10.** CUANDO arranque sin tokens, EL sistema DEBERÁ registrar un `WARNING` diciendo que se rechazará todo `/api/v1/` (D2).
- **RF-11.** `API_KEYS` NO DEBERÁ aparecer en el `repr` de `Settings` ni en ningún log.

### C. Logging (con la spec 004)

- **RF-12.** CUANDO se rechace una petición por autenticación, EL sistema DEBERÁ registrar un `WARNING` con la ruta y si la cabecera faltaba o era inválida, **sin el valor**.
- **RF-13.** Los `401` DEBERÁN llevar `X-Request-ID` y sus líneas de inicio y fin, como cualquier respuesta (spec 004).
- **RF-14.** Ningún log DEBERÁ contener el valor de `X-API-Key` ni los tokens de `API_KEYS`.
- **RF-15.** En la línea de inicio de petición, el valor de todo parámetro de consulta cuyo nombre (sin distinguir mayúsculas) sea `api_key`, `apikey`, `x-api-key`, `key` o `token` DEBERÁ sustituirse por `'***'`: un token mandado por error en la URL no llega a los logs (D1). Ese parámetro no autentica. *Ampliado tras la revisión (T9):* se oculta todo parámetro cuyo nombre normalizado (sin espacios, en minúsculas, `-` como `_`) contenga `key`, `token`, `secret`, `auth` o `pass`; la lista de cinco nombres dejaba pasar `api-key`, `access_token`, `password` o `Authorization`.

### D. Documentación

- **RF-16.** El esquema OpenAPI (`/docs`) DEBERÁ mostrar que `/api/v1/` exige `X-API-Key`.

## 5. Requisitos no funcionales

- **RNF-1. Sin dependencias nuevas:** `fastapi.security` y `secrets`.
- **RNF-2. Tipado estricto,** sin `Any`.
- **RNF-3. Docs vivas:** README y `.env.example` con `API_KEYS`, y cómo generar un token fuerte (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), porque la longitud no se valida (D4).
- **RNF-4. Tests con tokens sintéticos** (constitución #12).

## 6. Contrato

Sin cambios en `200`, `404`, `422` y `502`. Nuevo: `401 {"detail": "Invalid or missing API key"}` con `WWW-Authenticate: ApiKey`.

## 7. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| `X-API-Key:` vacía | `401`, igual que si faltara |
| Token válido con espacio delante o detrás, u otras mayúsculas | `401` (RF-5) |
| Varios tokens (rotación sin cortes) | cualquiera vale |
| `API_KEYS=" , ,"` | equivale a vacía: nadie entra y `WARNING` al arrancar |
| Token inválido y `term` inválido | `401`, no `422` (RF-1) |
| Token inválido durante un enfriamiento o con un límite agotado | `401`, no `502`: ni se mira Redis |
| Token inválido con un CP nuevo | `401` y ningún `PUT` ni sesión nueva |
| `/docs`, `/openapi.json` sin token | `200` |
| `?api_key=…` en la URL | no autentica (`401`) y aparece como `'***'` en el log |
| `?Token=abc`, `?KEY=abc` | también se ocultan (el nombre no distingue mayúsculas) |
| `?term=token` | no se oculta: se mira el **nombre**, no el valor |
| `API_KEYS=abc` (débil) | se acepta (D4) |

## 8. Fuera de alcance

- OAuth2, JWT, cuentas de usuario.
- Cuotas o límites por token (los límites hacia Dia son los de la spec 003, globales).
- Gestión de tokens por API o panel.
- Bloqueo tras N intentos fallidos.
- HTTPS (lo pone el proxy o la infraestructura).

## 9. Criterios de finalización

- [ ] RF-1…RF-16 cubiertos por tests en verde.
- [ ] Un test demuestra que una petición sin token válido no llama a Dia ni lee Redis.
- [ ] Un test demuestra que ni el token recibido ni los configurados aparecen en ningún log.
- [ ] `ruff`, `mypy` y `pytest` limpios; README y `.env.example` actualizados.
- [ ] Verificación manual: sin cabecera, con una inválida y con una válida (esta con 1 búsqueda real): `401`/`401`/`200`, y ningún log muestra el token.

## 10. Decisiones (dudas resueltas el 2026-10-08)

Todas con la opción recomendada en el borrador.

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Token en la URL | Ocultarlo como `'***'` por nombre de parámetro | RF-15; toca el middleware de la spec 004 |
| D2 | Sin tokens | Arranca, rechaza todo y avisa | RF-9, RF-10 |
| D3 | `WWW-Authenticate` | `ApiKey` en todo `401` | RF-7 |
| D4 | Tokens débiles | No se valida la longitud; el README explica cómo generar uno fuerte | RNF-3 |
| D5 | `/health` | Se añade, público, sin tocar Redis ni Dia | RF-6 |
| D6 | Mecanismo | Dependencia `Security(APIKeyHeader)` en el router de `/api/v1` | RF-6, RF-16 |

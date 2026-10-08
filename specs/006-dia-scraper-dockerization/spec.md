# Spec 006 — Dockerización

- **Estado:** aprobada (2026-10-08), con las decisiones de la sección 10
- **Fecha:** 2026-10-08
- **Referencia:** `alcampo-scraper/specs/005-alcampo-scraper-dockerization` (aprobada) y sus ficheros actuales (`Dockerfile`, `docker-compose.yml`, `.dockerignore`), adaptados a Dia

## 1. Contexto y objetivo

Hoy la API solo corre desde un venv local y necesita un Redis a mano. Todas las verificaciones manuales de las specs 001–005 sustituyeron Redis por fakeredis porque no había ninguno.

Tres particularidades de Dia condicionan esta spec:

- **Akamai decide por la huella** (Fase 0 §5): cabeceras y TLS. Las pruebas se han hecho con Python 3.14 de Windows; la imagen usará Python 3.11 de Debian, con otro OpenSSL. Hasta probarlo desde el contenedor, no sabemos si Akamai lo acepta (D6).
- **Las sesiones de Dia viven en la memoria del proceso** (spec 002): con varios workers de uvicorn, cada uno tendría su pool y cada CP costaría un `PUT` por worker. Los límites y el enfriamiento sí se comparten por Redis (spec 003).
- **Sin Redis la búsqueda falla con `500`** (la degradación sigue fuera de alcance): el compose tiene que arrancar la API solo con Redis listo.

**Objetivo:** empaquetar la API en una imagen reproducible y levantarla con un Redis local en un comando, **sin tocar `app/`**: `Settings` ya lee todo de variables de entorno.

## 2. Usuarios y actores

- **Desarrollador:** levanta API y Redis con un comando, sin instalar Python ni Redis.
- **Responsable del despliegue:** usa la imagen como artefacto.
- **Docker (daemon u orquestador):** consulta la sonda de vida.

## 3. Historias de usuario

- **H1.** Como desarrollador, quiero construir y levantar API y Redis con un comando.
- **H2.** Como responsable del despliegue, quiero que la imagen falle rápido y claro si falta configuración obligatoria.
- **H3.** Como responsable del despliegue, quiero que Docker sepa si la API está viva sin generar tráfico hacia Dia.

## 4. Requisitos funcionales (EARS)

### A. Imagen

- **RF-1.** EL sistema DEBERÁ proveer un `Dockerfile` multi-etapa sobre `python:3.11-slim` (D1) que instale solo las dependencias de producción de `pyproject.toml` (sin el grupo `dev`) y sirva la API con `uvicorn`, sin cambios en `app/`.
- **RF-2.** El proceso DEBERÁ ejecutarse con un usuario sin privilegios con uid numérico, nunca `root`; el código instalado DEBERÁ ser de `root` (el proceso no puede modificarlo).
- **RF-3.** El `CMD` DEBERÁ arrancar `uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log` (D3) con **un solo worker** (D7), en forma *exec* para que reciba `SIGTERM` y el `lifespan` cierre sesiones y Redis.
- **RF-4.** EL sistema DEBERÁ proveer un `.dockerignore` en forma de **lista de permitidos** (D5): solo `pyproject.toml` y `app/` entran en el contexto, sin bytecode. `.env`, `tests/`, `specs/`, `docs/`, `.git/`, `.venv/` y cualquier fichero nuevo quedan fuera por defecto.
- **RF-5.** La imagen final NO DEBERÁ llevar herramientas de compilación, cachés de `pip`, `tests/` ni ningún `.env`.

### B. Configuración

- **RF-6.** Toda la configuración DEBERÁ llegar por variables de entorno o `.env`, sin valores fijados en la imagen. Ningún token de `API_KEYS` en `Dockerfile` ni compose.
- **RF-7.** SI el contenedor arranca sin `DIA_BASE_URL` o sin `REDIS_URL`, ENTONCES DEBERÁ terminar con código distinto de 0 y el `ValidationError` de `Settings` en `docker logs`.

### C. Composición local

- **RF-8.** EL sistema DEBERÁ proveer un `docker-compose.yml` con la API y un Redis en la misma red, con `REDIS_URL` apuntando al servicio Redis aunque el `.env` diga otra cosa.
- **RF-9.** La API DEBERÁ arrancar solo cuando Redis acepte conexiones: `healthcheck` con `redis-cli ping` y `depends_on: condition: service_healthy` (D2).
- **RF-10.** El puerto del host DEBERÁ ser configurable (`API_PORT`, por defecto `8000`); Redis no publica puerto.
- **RF-11.** El compose NO DEBERÁ fallar si no existe `.env`; la API terminará por RF-7 con un error claro.

### D. Sonda de vida y logs

- **RF-12.** El `Dockerfile` DEBERÁ declarar un `HEALTHCHECK` contra `GET /health` (público, sin Redis ni Dia, spec 005) con Python y `httpx` (sin `curl`), a `127.0.0.1` y sin proxy del entorno (`trust_env=False`).
- **RF-13.** Los logs DEBERÁN salir por `stderr` sin buffer (`PYTHONUNBUFFERED=1`), con el formato y el request id de la spec 004.

## 5. Requisitos no funcionales

- **RNF-1. Sin cambios en `app/`.**
- **RNF-2. Sin dependencias nuevas de Python.**
- **RNF-3. Nunca secretos en la imagen** (constitución #12).
- **RNF-4. Tests de infraestructura sin daemon:** se comprueban `Dockerfile`, compose y `.dockerignore` leyendo los ficheros (como `alcampo-scraper/tests/infra/`); el CLI `docker` solo si está instalado, y nunca `docker compose config` sobre el `.env` real (imprimiría los tokens).
- **RNF-5. Docs vivas:** README (construir, levantar, variables, logs) y `AGENTS.md` (comandos).

## 6. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Recién clonado, sin `.env` | `build` funciona; `up` → la API termina con el `ValidationError` |
| `API_KEYS` vacía | arranca, `WARNING` y `401` en todo `/api/v1` (spec 005) |
| `.env` con `REDIS_URL=redis://localhost…` | el compose lo sustituye |
| Redis tarda | la API espera (RF-9) |
| Redis cae con la API levantada | búsquedas `500`; `/health` sigue `200` (limitación conocida) |
| Reiniciar el compose | se pierden cache, cache negativa, enfriamiento y límites (Redis efímero), y las sesiones de Dia (memoria) |
| Akamai rechaza la huella del contenedor | todo `502` con `ERROR akamai block`; se ve en la verificación manual (D6) |
| Cada sonda del `HEALTHCHECK` | 2 líneas `INFO` (D4) |

## 7. Fuera de alcance

- Kubernetes, CI/CD, registry, HTTPS.
- Redis persistente o gestionado en producción.
- **Lockfile** con versiones y hashes: hoy `pyproject.toml` acota versiones mayores, no las fija (Alcampo lo hizo en su spec 014).
- Varios workers o réplicas (D7).
- Degradación sin Redis y `/ready`.

## 8. Criterios de finalización

- [ ] Tests de infraestructura en verde (RNF-4); `ruff`, `mypy` y `pytest` limpios.
- [ ] README y `AGENTS.md` actualizados.
- [ ] **Verificación manual con el daemon de Docker arrancado:** `build`; imagen sin `tests/` ni `.env` ni dependencias `dev`; proceso no `root`; sin variables → código ≠ 0; `compose up` → `healthy`, `/health` y `/docs` `200`, `/api/v1/products` `401` sin token y `200` con uno sintético en **1 búsqueda real a Dia desde el contenedor** (D6); la misma búsqueda otra vez sale de la cache; `compose logs api` con request id y sin tokens.

## 9. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | Akamai rechaza la huella TLS de Python 3.11/OpenSSL de Debian | Se detecta en la verificación manual; si pasa, se para y se decide (otra base, otra versión) antes de cerrar la spec |
| R2 | El daemon de Docker no está arrancado en este equipo (visto en la spec 001) | La verificación manual la necesita; los tests de infraestructura no |

## 10. Decisiones (dudas resueltas el 2026-10-08)

Todas con la opción recomendada en el borrador.

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | Imagen base | `python:3.11-slim` | RF-1; prueba real de 3.11 |
| D2 | Orden API ↔ Redis | `healthcheck` en Redis y `service_healthy` | RF-9 |
| D3 | Access log | `--no-access-log` | RF-3 |
| D4 | Ruido del `HEALTHCHECK` | Se aceptan sus 2 líneas `INFO` | Sin tocar `app/` |
| D5 | `.dockerignore` | Lista de permitidos | RF-4 |
| D6 | Búsqueda real desde el contenedor | 1 en la verificación manual | R1 |
| D7 | Workers | 1 por contenedor | RF-3 |

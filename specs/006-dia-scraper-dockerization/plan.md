# Plan 006 — Dockerización

- **Estado:** aprobado (2026-10-08)
- **Fecha:** 2026-10-08
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D7**; las de este plan, **D1…**.
- **Base:** los ficheros actuales de Alcampo (`Dockerfile`, `docker-compose.yml`, `.dockerignore`, `tests/infra/`), sin su lockfile.

## 1. Ficheros

| Fichero | Contenido | RF |
|---|---|---|
| `.dockerignore` | `*`, luego `!pyproject.toml`, `!app/`, y fuera `**/__pycache__/`, `**/*.py[cod]` | RF-4 |
| `Dockerfile` | `builder` (venv en `/opt/venv`, `pip install --no-cache-dir .`) y `runtime` (solo el venv, uid 10001, `HEALTHCHECK`, `CMD` exec) | RF-1, RF-2, RF-3, RF-5, RF-12, RF-13 |
| `docker-compose.yml` | `api` (build, `env_file` opcional, `REDIS_URL` del compose, `API_PORT`, `depends_on: service_healthy`) y `redis` (`redis:7-alpine`, `healthcheck`, sin puerto, sin volumen) | RF-8…RF-11 |
| `tests/infra/test_dockerignore.py`, `test_dockerfile.py`, `test_compose.py` | contrato de los tres ficheros | RNF-4 |
| `README.md`, `AGENTS.md` | Docker: construir, levantar, variables, logs | RNF-5 |

## 2. Decisiones de diseño

**D1 — El código viaja dentro del venv.** El `builder` instala el proyecto (`pip install .`) en `/opt/venv`; el wheel ya lleva `app/`, así que el `runtime` copia solo el venv. Ambas etapas usan la misma base: el `python` del venv es un enlace al de la imagen.

**D2 — Sin lockfile:** `pip install .` resuelve las dependencias dentro de las cotas de `pyproject.toml` (mayor siguiente excluida). Dos builds en fechas distintas pueden traer versiones distintas: limitación documentada (spec §7).

**D3 — Usuario `10001` numérico y venv de `root`** (Alcampo plan-D2): un orquestador con `runAsNonRoot` puede comprobarlo, y el proceso no puede modificar su propio código.

**D4 — `HEALTHCHECK` con Python y `httpx`**, contra `127.0.0.1` (no `localhost`, que puede resolver a `::1`, y uvicorn escucha en IPv4) y con `trust_env=False` (un `HTTP_PROXY` del despliegue no debe interceptar la sonda).

**D5 — Tests de infraestructura sin daemon** (Alcampo plan-D7): `Dockerfile` y `.dockerignore` se leen como texto; el compose lo resuelve `docker compose config` (CLI, sin daemon) sobre **una copia en un directorio temporal**, para que el `.env` real nunca se lea (sus tokens saldrían en la salida). Se saltan si no está el CLI.

**D6 — `PYTHONUNBUFFERED=1` y `PYTHONDONTWRITEBYTECODE=1`** en el `runtime` (RF-13).

## 3. Estrategia de test

| RF | Test | Fichero |
|---|---|---|
| RF-4 | primera regla `*`; solo se readmiten `pyproject.toml` y `app/`; bytecode excluido después de `!app/` | `test_dockerignore.py` |
| RF-1, RF-5 | dos etapas sobre `python:3.11-slim`; `pip install` sin extras `[dev]` y con `--no-cache-dir` | `test_dockerfile.py` |
| RF-2 | último `USER` del `runtime` no es `root` ni `0` | `test_dockerfile.py` |
| RF-3 | `CMD` en forma exec: `uvicorn app.main:app`, `0.0.0.0`, `8000`, `--no-access-log`, sin `--reload` ni `--workers` | `test_dockerfile.py` |
| RF-6 | ni `API_KEYS` ni `.env` en el `Dockerfile` ni en el compose | ambos |
| RF-12 | `HEALTHCHECK` contra `127.0.0.1:8000/health` con `trust_env=False` | `test_dockerfile.py` |
| RF-13 | `PYTHONUNBUFFERED=1` | `test_dockerfile.py` |
| RF-8, RF-9 | `REDIS_URL` del compose; `service_healthy`; `ping` | `test_compose.py` |
| RF-10 | puerto `8000` por defecto; Redis sin puerto | `test_compose.py` |
| RF-11 | `config` funciona sin `.env`; un `.env` sintético se carga pero no cambia `REDIS_URL` | `test_compose.py` |
| RF-7 y lo demás | verificación manual con el daemon | T4 |

## 4. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | Akamai rechaza la huella TLS del contenedor | La verificación manual hace 1 búsqueda real; si da `502` con `ERROR akamai block`, se para y se decide |
| R2 | El daemon no está arrancado | Los tests de T1–T3 no lo necesitan; T4 sí, y lo arranca el usuario |

## 5. Entrega

Un solo PR (~300 líneas: tres ficheros, sus tests y docs).

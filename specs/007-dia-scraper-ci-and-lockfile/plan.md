# Plan 007 — CI y dependencias fijadas

- **Estado:** borrador, pendiente de revisión
- **Fecha:** 2026-10-08
- **Spec:** [spec.md](spec.md) (aprobada). Sus decisiones se citan como **spec-D1…spec-D5**; las de este plan, **D1…**.
- **Base:** `alcampo-scraper/scripts/lock.sh`, `.github/workflows/ci.yml`, `tests/infra/test_lockfiles.py`, `test_ci_workflow.py` (sus specs 006 y 014).

## 1. Ficheros

| Fichero | Cambio | RF |
|---|---|---|
| `scripts/lock.sh` | **nuevo**: `pip-compile` 7.6.1 en `python:3.11-slim`, `--generate-hashes --allow-unsafe --strip-extras`, prod y dev; `--upgrade` opcional | RF-1, RF-2, RF-3 |
| `requirements.lock`, `requirements-dev.lock` | **nuevos**, generados (nunca a mano) | RF-1 |
| `Dockerfile` | instala `requirements.lock` con `--require-hashes` en su propia capa, luego `pip install --no-deps .` | RF-4 |
| `.dockerignore` | readmite `requirements.lock` | RF-4 |
| `.github/workflows/ci.yml` | **nuevo** | RF-6…RF-10 |
| `tests/infra/test_lockfiles.py`, `test_ci_workflow.py` | **nuevos**; `test_dockerfile.py` y `test_dockerignore.py` actualizados | RNF-2 |
| `README.md`, `AGENTS.md` | instalar desde los locks, regenerarlos, Windows | RF-11 |

## 2. Decisiones de diseño

**D1 — Locks generados en la plataforma real** (spec-D2): un contenedor `python:3.11-slim` desechable resuelve para Linux y Python 3.11, con `uvloop` incluido. `pip-tools` va fijado (7.6.1), para que el generador tampoco cambie solo. En Git Bash de Windows, `lock.sh` monta la ruta con `pwd -W` y `MSYS_NO_PATHCONV=1` (como en Alcampo).

**D2 — `--allow-unsafe`**: incluye `pip`/`setuptools` si aparecen como dependencias, necesarios para que `--require-hashes` no falle por un paquete sin fijar.

**D3 — Capa propia para el lock en el `Dockerfile`**: `COPY requirements.lock` + `pip install --require-hashes` antes de copiar el código, así un cambio de código no reinstala dependencias. Después `pip install --no-deps .`: nada se resuelve otra vez.

**D4 — La CI instala dev desde el lock, el paquete con `--no-deps -e .` y luego `pip check`**: si `pyproject.toml` declara algo que el lock no tiene, `pip check` falla (RF-5).

**D5 — `cache: pip` con `cache-dependency-path: requirements-dev.lock`**: la caché sigue al lock.

**D6 — Tests de locks sin red**: leen `pyproject.toml` (`tomllib`) y los locks (`packaging`): cada dependencia directa está, dentro de su cota, con hash; toda línea fijada lleva hash; el lock de producción no lleva herramientas de dev.

## 3. Estrategia de test

| RF | Test | Fichero |
|---|---|---|
| RF-1, RF-2 | cada dependencia directa fijada en su cota y con hash, en los dos locks; todas las líneas con hash; prod sin herramientas dev | `test_lockfiles.py` |
| RF-4 | el `Dockerfile` instala `--require-hashes -r requirements.lock` antes de `--no-deps .`; `.dockerignore` readmite el lock | `test_dockerfile.py`, `test_dockerignore.py` |
| RF-5, RF-6 | la CI instala desde `requirements-dev.lock` con hashes, `--no-deps -e .`, `pip check`, y ejecuta `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q`, `docker build` | `test_ci_workflow.py` |
| RF-7 | `permissions: contents: read` | `test_ci_workflow.py` |
| RF-8 | `ubuntu-24.04`, `checkout@v7`, `setup-python@v7`, Python `3.11` | `test_ci_workflow.py` |
| RF-10 | ningún `secrets.` en el workflow | `test_ci_workflow.py` |

## 4. Entrega

Un PR (~200 líneas de código y tests, más los dos locks generados, que no se revisan línea a línea).

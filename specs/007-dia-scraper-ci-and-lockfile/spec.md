# Spec 007 — CI y dependencias fijadas

- **Estado:** aprobada (2026-10-08), con las decisiones de la sección 10
- **Fecha:** 2026-10-08
- **Referencia:** `alcampo-scraper` specs 006 (refactor y CI) y 014 (lockfile y CI al día), y sus ficheros actuales (`.github/workflows/ci.yml`, `scripts/lock.sh`, `tests/infra/test_ci_workflow.py`, `test_lockfiles.py`)

## 1. Contexto y objetivo

De lo que Alcampo resolvió en su spec 006, Dia ya lo tiene casi todo desde el principio: `AppState` tipado, `mypy` estricto en `app/`, middleware ASGI puro, cotas `<siguiente major` en las dependencias y avisos tratados como error en `pytest`. Quedan dos huecos:

- **Sin CI.** `ruff`, `mypy`, `pytest` y el build de Docker solo se ejecutan en una máquina, cuando alguien se acuerda. El repositorio ya tiene remoto en GitHub.
- **Nada está fijado.** El `Dockerfile` instala `pip install .` y el entorno local `pip install -e ".[dev]"`: lo último que cumpla cada cota ese día, transitivas incluidas (starlette, anyio, pydantic-core…), sin hashes. Dos builds del mismo commit pueden llevar versiones distintas (README, spec 006 §7).

Una particularidad heredada de Alcampo: `uvicorn[standard]` trae `uvloop`, que no existe en Windows, así que un lock generado en Windows no vale para la imagen ni para la CI (Linux).

**Objetivo:** que cada push compruebe lo mismo que el cierre de cada tarea, y que la imagen y la CI instalen exactamente las versiones revisadas, con hashes, actualizables con un comando.

## 2. Usuarios y actores

- **Desarrollador:** recibe el fallo de la CI en vez de descubrirlo en ejecución; actualiza dependencias cuando decide.
- **Responsable del servicio:** dos builds del mismo commit son iguales.
- **Cliente de la API:** no nota nada.

## 3. Historias de usuario

- **H1.** Como desarrollador, quiero que cada push ejecute lint, formato, tipos, tests y el build de Docker.
- **H2.** Como responsable del servicio, quiero que dos builds del mismo commit instalen las mismas dependencias, verificadas por hash.
- **H3.** Como desarrollador, quiero actualizar dependencias con un comando documentado y ver el cambio en el diff.

## 4. Requisitos funcionales (EARS)

### A. Lockfiles

- **RF-1.** El repositorio DEBERÁ incluir `requirements.lock` (producción) y `requirements-dev.lock` (producción + `dev`), con versión exacta y hash de **todas** las dependencias, transitivas incluidas, generados desde `pyproject.toml`.
- **RF-2.** Los lockfiles DEBERÁN generarse para Linux y Python 3.11, la plataforma de la imagen y de la CI (D2).
- **RF-3.** `scripts/lock.sh` DEBERÁ regenerarlos (manteniendo versiones; con `--upgrade`, a lo último de cada cota), con el generador fijado a una versión (D2).
- **RF-4.** El `Dockerfile` DEBERÁ instalar las dependencias desde `requirements.lock` con `--require-hashes`, y después el paquete con `--no-deps`.
- **RF-5.** SI `pyproject.toml` declara una dependencia que el lock no tiene, ENTONCES la CI DEBERÁ fallar (`pip check` tras instalar).

### B. CI

- **RF-6.** EL repositorio DEBERÁ incluir `.github/workflows/ci.yml` que, en cada `push` y `pull_request`, ejecute con Python 3.11: instalar desde `requirements-dev.lock` con hashes, `pip check`, `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q` y `docker build`.
- **RF-7.** La CI NO DEBERÁ publicar nada (imagen, paquete) ni tener permisos de escritura: `permissions: contents: read`.
- **RF-8.** El runner y las acciones DEBERÁN ir fijados (`ubuntu-24.04`, `actions/checkout` y `actions/setup-python` en su versión mayor actual), sin `latest` (D3).
- **RF-9.** La CI NO DEBERÁ llamar a Dia ni a un Redis real: los tests ya usan respx y fakeredis; los del compose usan solo el CLI `docker`.
- **RF-10.** La CI NO DEBERÁ necesitar secretos: ni `API_KEYS` ni ningún otro.

### C. Documentación

- **RF-11.** README y `AGENTS.md` DEBERÁN explicar cómo instalar desde los locks en local y cómo regenerarlos; el README deja de listar la imagen no reproducible como limitación.

## 5. Requisitos no funcionales

- **RNF-1. Sin dependencias nuevas de la app.** El generador de locks corre en un contenedor desechable, no se instala en el proyecto.
- **RNF-2. Tests de infraestructura** que leen `ci.yml` y los locks como texto (como Alcampo): la CI hace lo que dice el cierre de cada tarea; cada dependencia de `pyproject.toml` está en el lock con hash; el `Dockerfile` instala desde el lock.
- **RNF-3. Sin cambios en `app/`.**

## 6. Casos límite

| Caso | Comportamiento esperado |
|---|---|
| Se añade una dependencia a `pyproject.toml` sin regenerar el lock | la CI falla en `pip check`; el test de locks también |
| Desarrollo en Windows | `pip install -r requirements-dev.lock` puede fallar por `uvloop` (solo Linux): se documenta instalar con `pip install -e ".[dev]"` en Windows o usar el contenedor (D4) |
| PyPI retira una versión fijada | el build falla; se regenera el lock (decisión explícita) |
| `docker` en la CI | disponible en los runners de Ubuntu; los tests del compose se ejecutan |
| La CI en un fork sin acceso | solo lectura: no hay nada que filtrar |

## 7. Fuera de alcance

- Publicar la imagen en un registry y despliegue continuo.
- Renovación automática de dependencias (Dependabot, Renovate).
- Análisis de seguridad de dependencias (`pip-audit`).
- Fijar la imagen base por digest.

## 8. Criterios de finalización

- [ ] Locks generados y comprometidos; tests de infraestructura en verde.
- [ ] `docker build` desde el lock funciona (con el daemon).
- [ ] La CI pasa en GitHub tras el push del usuario (el agente no hace push).
- [ ] README y `AGENTS.md` actualizados.

## 9. Riesgos

| # | Riesgo | Mitigación |
|---|---|---|
| R1 | El lock de Linux no instala en Windows (`uvloop`) | D4 |
| R2 | La CI tarda en el primer run (sin caché de pip) | `actions/setup-python` con `cache: pip` sobre `requirements-dev.lock` |

## 10. Decisiones (dudas resueltas el 2026-10-08)

Todas con la opción recomendada en el borrador.

| # | Duda | Decisión | Consecuencia |
|---|---|---|---|
| D1 | CI y lockfile | Una sola spec | La CI instala desde el lock desde el primer día |
| D2 | Generador | `pip-compile` (pip-tools fijado) en un contenedor `python:3.11-slim`, con `scripts/lock.sh` | RF-2, RF-3; regenerar necesita Docker |
| D3 | Versiones de la CI | `ubuntu-24.04`, `checkout@v7`, `setup-python@v7` | RF-8 |
| D4 | Windows | Se documenta `pip install -e ".[dev]"`; la CI es la referencia | Sin tercer lock |
| D5 | `docker build` en la CI | Sí, sin publicar | RF-6 |

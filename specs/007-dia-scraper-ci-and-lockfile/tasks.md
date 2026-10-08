# Tasks 007 — CI y dependencias fijadas

- **Estado:** aprobado (2026-10-08)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones de diseño citadas como plan-Dn)
- **Entrega:** 1 PR.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**.
2. **GREEN:** lo mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .`, `mypy` y `pytest -q`, todo limpio. Marcar `[x]`, proponer el commit y **parar**.

Formato de commit: `<tipo>(007-dia-scraper-ci-and-lockfile): <descripción en inglés> (Tn)`.

---

### [x] T1 — Lockfiles
- **RED:** `tests/infra/test_lockfiles.py` (plan-D6) falla: no hay locks.
- **GREEN:** `scripts/lock.sh` (plan-D1, D2) y ejecutarlo (necesita Docker y acceso a PyPI) para generar `requirements.lock` y `requirements-dev.lock`.
- **RF:** RF-1, RF-2, RF-3

### [x] T2 — La imagen instala desde el lock
- **RED:** `test_dockerfile.py`: `--require-hashes -r requirements.lock` antes de `--no-deps .`; `test_dockerignore.py`: se readmite `requirements.lock`.
- **GREEN:** `Dockerfile` (plan-D3) y `.dockerignore`. Comprobar con el daemon que `docker build` funciona.
- **RF:** RF-4

### [x] T3 — CI
- **RED:** `tests/infra/test_ci_workflow.py` falla: no hay workflow.
- **GREEN:** `.github/workflows/ci.yml` (plan-D4, D5).
- **RF:** RF-5…RF-10

### [x] T4 — Docs
- **Hacer:** README (instalar desde los locks en Linux/macOS, `pip install -e ".[dev]"` en Windows, `scripts/lock.sh` y `--upgrade`, quitar la limitación "sin lockfile", la CI) y `AGENTS.md` (comandos). La comprobación de que la CI pasa en GitHub queda para después del push del usuario.
- **RF:** RF-11

- **Pendiente tras el push del usuario:** comprobar que el primer run de la CI en GitHub pasa (el agente no hace push).

---

## Revisión con contexto nuevo (2026-10-08)

Revisión adversarial de `1fdba7d..HEAD` antes del PR: 0 CRITICAL, 5 WARNING, 4 SUGGESTION. Confirmado el principal: ningún lock fijaba `setuptools`, y `pip install --no-deps .` (imagen) y `-e .` (CI) lo descargaban de PyPI sin hash para construir el paquete en un entorno aislado, contra RF-4.

### [x] T5 — Correcciones de la revisión
- **RED:** `requirements-build.lock` fija con hash lo que pide `[build-system].requires`; la imagen y la CI lo instalan con `--require-hashes` y construyen con `--no-build-isolation`; `.dockerignore` lo readmite.
- **GREEN:** `scripts/lock.sh` genera el tercer lock (de `[build-system].requires`), solo acepta `--upgrade` (otra cosa iría dentro de `sh -c`) y limpia `*.egg-info`/`build` aunque falle; `Dockerfile`, `ci.yml`, `.dockerignore`. Las referencias a "spec 014" de `test_ci_workflow.py` pasan a "spec 007". `AGENTS.md`: "Linux" (los hashes son de wheels de Linux), no "Linux/macOS".
- **Comprobado con el daemon:** la imagen se construye sin aislamiento (`setuptools 84.0.0` del lock); los pasos de instalación de la CI, tal cual, en un `python:3.11-slim` limpio: instalación con hashes, `pip check`, `ruff`, `mypy` y 591 tests (sin los del compose, que necesitan el CLI `docker`, presente en el runner).
- **Documentado, no corregido:** una dependencia quitada de `pyproject.toml` sigue en el lock hasta regenerarlo (detectarlo exigiría regenerar el lock en cada run de la CI, ~2,5 min). Acciones fijadas por etiqueta (`@v7`), no por SHA.

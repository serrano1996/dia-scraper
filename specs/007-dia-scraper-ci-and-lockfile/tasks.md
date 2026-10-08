# Tasks 007 — CI y dependencias fijadas

- **Estado:** borrador, pendiente de revisión
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones de diseño citadas como plan-Dn)
- **Entrega:** 1 PR.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**.
2. **GREEN:** lo mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .`, `mypy` y `pytest -q`, todo limpio. Marcar `[x]`, proponer el commit y **parar**.

Formato de commit: `<tipo>(007-dia-scraper-ci-and-lockfile): <descripción en inglés> (Tn)`.

---

### [ ] T1 — Lockfiles
- **RED:** `tests/infra/test_lockfiles.py` (plan-D6) falla: no hay locks.
- **GREEN:** `scripts/lock.sh` (plan-D1, D2) y ejecutarlo (necesita Docker y acceso a PyPI) para generar `requirements.lock` y `requirements-dev.lock`.
- **RF:** RF-1, RF-2, RF-3

### [ ] T2 — La imagen instala desde el lock
- **RED:** `test_dockerfile.py`: `--require-hashes -r requirements.lock` antes de `--no-deps .`; `test_dockerignore.py`: se readmite `requirements.lock`.
- **GREEN:** `Dockerfile` (plan-D3) y `.dockerignore`. Comprobar con el daemon que `docker build` funciona.
- **RF:** RF-4

### [ ] T3 — CI
- **RED:** `tests/infra/test_ci_workflow.py` falla: no hay workflow.
- **GREEN:** `.github/workflows/ci.yml` (plan-D4, D5).
- **RF:** RF-5…RF-10

### [ ] T4 — Docs
- **Hacer:** README (instalar desde los locks en Linux/macOS, `pip install -e ".[dev]"` en Windows, `scripts/lock.sh` y `--upgrade`, quitar la limitación "sin lockfile", la CI) y `AGENTS.md` (comandos). La comprobación de que la CI pasa en GitHub queda para después del push del usuario.
- **RF:** RF-11

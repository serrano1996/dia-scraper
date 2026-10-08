# Tasks 006 — Dockerización

- **Estado:** aprobado (2026-10-08)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md) (decisiones de diseño citadas como plan-Dn)
- **Entrega:** 1 PR.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**.
2. **GREEN:** el fichero mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .`, `mypy` y `pytest -q`, todo limpio. Marcar `[x]`, proponer el commit y **parar**.

Formato de commit: `<tipo>(006-dia-scraper-dockerization): <descripción en inglés> (Tn)`.

---

### [x] T1 — `.dockerignore`
- **RED:** `tests/infra/test_dockerignore.py`: primera regla `*`; solo `!pyproject.toml` y `!app/`; `**/__pycache__/` y `**/*.py[cod]` después de `!app/`.
- **GREEN:** `.dockerignore`.
- **RF:** RF-4

### [x] T2 — `Dockerfile`
- **RED:** `tests/infra/test_dockerfile.py`: los casos de plan §3 (dos etapas sobre `python:3.11-slim`, `pip install` sin `[dev]` y con `--no-cache-dir`, usuario no `root`, `CMD` exec con `--no-access-log` y sin `--reload` ni `--workers`, `HEALTHCHECK` a `127.0.0.1:8000/health` con `trust_env=False`, `PYTHONUNBUFFERED=1`, sin `API_KEYS` ni `.env`).
- **GREEN:** `Dockerfile` (plan-D1, D3, D4, D6).
- **RF:** RF-1, RF-2, RF-3, RF-5, RF-6, RF-12, RF-13

### [ ] T3 — `docker-compose.yml`
- **RED:** `tests/infra/test_compose.py` (con `docker compose config` sobre una copia, plan-D5): `REDIS_URL=redis://redis:6379/0`; `service_healthy`; `ping`; puerto `8000`; Redis sin puerto; sin `.env` funciona; un `.env` sintético se carga y no cambia `REDIS_URL`; sin `API_KEYS` en el fichero.
- **GREEN:** `docker-compose.yml`.
- **RF:** RF-6, RF-8, RF-9, RF-10, RF-11

### [ ] T4 — Docs y verificación manual
- **Hacer:** README (sección "Docker": `docker compose up --build`, `API_PORT`, que el compose pone `REDIS_URL`, `docker compose logs -f api`, Redis efímero, un worker por contenedor, sin lockfile) y `AGENTS.md` (comandos, y el aviso de no ejecutar `docker compose config` en la raíz: imprime los tokens del `.env`).
- **Verificación manual (con el daemon arrancado por el usuario):** `docker build`; la imagen no lleva `tests/`, `.env` ni `pytest`; `docker run` sin variables → código ≠ 0 y `ValidationError`; `docker compose up` → `healthy`; `/health` y `/docs` `200`; `/api/v1/products` `401` sin token y `200` con uno sintético (**1 búsqueda real a Dia desde el contenedor**, spec-D6); la misma búsqueda otra vez → desde cache; `docker compose logs api` con request id y sin tokens. Si Dia responde `502` con `ERROR akamai block`, **parar y avisar** (plan R1).
- **RF:** RF-7, RNF-5

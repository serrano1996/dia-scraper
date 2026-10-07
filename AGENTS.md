# AGENTS.md — dia-scraper

API REST asíncrona (FastAPI) que extrae, procesa y sirve productos de Dia
(`https://www.dia.es`, detrás de Akamai Bot Manager). Hermana de `mercadona-scraper` y
`alcampo-scraper`: mismo contrato de respuesta.

**Reglas del proyecto:** [docs/constitution.md](docs/constitution.md). Léelas antes de tocar nada.

## Comandos

```bash
pip install -e ".[dev]"          # instalar
uvicorn app.main:app --reload    # arrancar en local (http://127.0.0.1:8000/docs)
pytest                           # tests (nunca llaman a Dia real)
ruff check . && ruff format .    # lint + formato (obligatorio antes de cada commit)
mypy                             # tipos, estricto, sobre app/ (obligatorio antes de cada commit)
```

## Proceso

- SDD estricto: `specs/NNN-dia-scraper-<nombre>/{spec,plan,tasks}.md`, aprobados antes de codificar.
- TDD estricto (RED → GREEN → refactor). Una tarea = un commit; al cerrar cada tarea:
  `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q`, marcar la tarea y proponer el commit. Parar.
- Investigación en vivo de Dia: [docs/investigacion/fase-0-dia.md](docs/investigacion/fase-0-dia.md).
  **Cuidado:** Akamai responde `403` si las cabeceras no son coherentes con Chrome (client hints o
  `Sec-Fetch-*`), y `curl` de Windows falla siempre por su huella TLS: sondea con `httpx` y despacio.

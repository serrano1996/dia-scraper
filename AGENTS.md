# AGENTS.md — dia-scraper

API REST asíncrona (FastAPI) que extrae, procesa y sirve productos de Dia
(`https://www.dia.es`, detrás de Akamai Bot Manager). Hermana de `mercadona-scraper` y
`alcampo-scraper`: mismo contrato de respuesta.

**Reglas del proyecto:** [docs/constitution.md](docs/constitution.md). Léelas antes de tocar nada.

## Comandos

```bash
pip install -e ".[dev]"          # instalar (Linux: mejor desde los locks, ver README)
scripts/lock.sh [--upgrade]      # regenerar los locks (Docker); nunca editarlos a mano
uvicorn app.main:app --reload    # arrancar en local (http://127.0.0.1:8000/docs)
pytest                           # tests (nunca llaman a Dia real)
ruff check . && ruff format .    # lint + formato (obligatorio antes de cada commit)
mypy                             # tipos, estricto, sobre app/ (obligatorio antes de cada commit)
docker compose up --build        # API + Redis en contenedores (necesita .env)
docker compose down              # parar y borrar los contenedores
```

- `tests/infra/test_compose.py` usa el CLI `docker` (no el daemon) y se salta si no está instalado.
- **Nunca** ejecutes `docker compose config` en la raíz del repo para inspeccionarlo: copia el
  contenido del `.env` (tokens de `API_KEYS` incluidos) en la salida. Los tests lo hacen sobre
  una copia en un directorio temporal.

## Proceso

- SDD estricto: `specs/NNN-dia-scraper-<nombre>/{spec,plan,tasks}.md`, aprobados antes de codificar.
- TDD estricto (RED → GREEN → refactor). Una tarea = un commit; al cerrar cada tarea:
  `ruff check .`, `ruff format --check .`, `mypy`, `pytest -q`, marcar la tarea y proponer el commit. Parar.
- Investigación en vivo de Dia: [docs/investigacion/fase-0-dia.md](docs/investigacion/fase-0-dia.md).
  **Cuidado:** Akamai responde `403` si las cabeceras no son coherentes con Chrome (client hints o
  `Sec-Fetch-*`), y `curl` de Windows falla siempre por su huella TLS: sondea con `httpx` y despacio.

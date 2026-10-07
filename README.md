# dia-scraper

API REST asíncrona (FastAPI) que extrae, procesa y sirve datos de productos de
[Dia online](https://www.dia.es). Ofrece el mismo contrato que `mercadona-scraper` y
`alcampo-scraper` para poder comparar los supermercados sin adaptar el consumidor.

> Estado: esqueleto. La primera feature (`specs/001-dia-scraper-mvp`) tiene la spec aprobada; plan y tareas en revisión.

## Puesta en marcha

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
cp .env.example .env
uvicorn app.main:app --reload
```

## Desarrollo

```bash
pytest                          # los tests nunca llaman a Dia real (respx + fakeredis)
ruff check . && ruff format .   # obligatorio antes de cada commit
mypy                            # tipos, estricto, sobre app/
```

## Documentación

- [Constitución del proyecto](docs/constitution.md)
- [Fase 0: investigación en vivo de Dia](docs/investigacion/fase-0-dia.md)
- Specs: [`specs/`](specs/)

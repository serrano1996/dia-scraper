# Plan 009 — Fallos encontrados por la revisión de alcampo-scraper

- **Estado:** aprobado (2026-10-09)
- **Fecha:** 2026-10-09
- **Spec:** [spec.md](spec.md).
- **Base:** `alcampo-scraper` T11 (commit `cd2669a`).

## 1. Ficheros

| Fichero | Cambio | RF |
|---|---|---|
| `app/middleware/request_context.py` | `_is_secret_name`: `unicodedata.normalize("NFKC", name).casefold()` y quita `[-_.\s]` con un regex compilado | RF-1 |
| `scripts/lock.sh` | `[ "$#" -gt 1 ]` → uso y `exit 2`; la comprobación pasa delante de `cd "$(dirname …)"` | RF-2 |
| `tests/middleware/test_request_context.py` | casos `to.ken`, `pa_ss`, `a pi key`, `ＫＥＹ` (como escapes Unicode, por RUF001), `K.E-Y` | RF-1 |
| `tests/infra/test_lockfiles.py` | `lock.sh --upgrade extra` y una opción desconocida → 2, con `PATH` vacío | RF-2, RNF-2 |
| `tests/services/test_redis_circuit.py` | `started = asyncio.Event()` en vez de `sleep(0)` | RF-3 |
| `README.md` | limitación conocida (F4) y la normalización de la redacción | RF-4 |

## 2. Decisiones

**D1 — La comprobación de `lock.sh`, antes de cualquier comando externo:** hoy va después de `cd "$(dirname "$0")/.."`. Con el `PATH` vacío del test, `dirname` falla primero, y en un sistema raro el script podría hacer algo antes de validar. La validación usa solo builtins (`[`, `case`, `echo`, `exit`).

## 3. Tareas

Ver [tasks.md](tasks.md). Un PR pequeño (~80 líneas).

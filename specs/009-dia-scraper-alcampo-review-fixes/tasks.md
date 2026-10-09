# Tasks 009 — Fallos encontrados por la revisión de alcampo-scraper

- **Estado:** aprobado (2026-10-09)
- **Spec:** [spec.md](spec.md) · **Plan:** [plan.md](plan.md)
- **Entrega:** 1 PR.

## Reglas de cada tarea

1. **RED:** se escribe el test, se ejecuta y se confirma que **falla por el motivo esperado**.
2. **GREEN:** lo mínimo para que pase.
3. **Refactor**, sin cambiar el comportamiento.
4. Cierre: `ruff check .`, `ruff format --check .`, `mypy` y `pytest -q`, todo limpio. Marcar `[x]`, proponer el commit y **parar**.

Formato de commit: `<tipo>(009-dia-scraper-alcampo-review-fixes): <descripción en inglés> (Tn)`.

---

### [x] T1 — Redacción con la normalización completa (F1)
- **RED:** `test_request_context.py`: `to.ken`, `pa_ss`, `ＫＥＹ`, `K.E-Y` → `***`.
- **GREEN:** `_is_secret_name` (plan §1).
- **RF:** RF-1

### [x] T2 — `lock.sh` rechaza argumentos de más (F2)
- **RED:** `test_lockfiles.py`: `--upgrade extra` y `--upgrade; false` → 2, con `PATH` vacío (RNF-2).
- **GREEN:** `lock.sh` (plan-D1).
- **RF:** RF-2

### [x] T3 — Test del circuito determinista y docs (F3, F4)
- **Hacer:** `Event` en el test del circuito (refactor de test: debe seguir en verde, y en rojo si se quita el indicador `_probing`); README: la normalización de la redacción y la limitación conocida de uvicorn.
- **RF:** RF-3, RF-4
- **Comprobado:** con el `Event`, el test pasa; quitando el indicador `_probing` del circuito falla (mutación deshecha). README: la redacción por subcadena y separadores (la frase seguía nombrando solo tres nombres exactos) y la limitación de uvicorn.

- **CI en GitHub (2026-10-09, run 37916896489, tras el push de `913d6ba`):** ✅ `success`, todos los pasos en verde. Con Python 3.11 pasan **655 tests en 9,78 s**, sin saltarse ninguno: los de `lock.sh` con `PATH` vacío también corren en Linux.

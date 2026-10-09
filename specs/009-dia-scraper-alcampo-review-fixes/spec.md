# Spec 009 — Fallos encontrados por la revisión de alcampo-scraper

- **Estado:** aprobado (2026-10-09)
- **Fecha:** 2026-10-09
- **Referencia:** `alcampo-scraper` spec 015, revisión con contexto nuevo (T11): W1, W2, S3 y S5. Alcampo había copiado de Dia la redacción, `lock.sh` y el circuito, así que los mismos fallos están aquí.

## 1. Contexto y objetivo

Reproducidos en Dia el 2026-10-09 (`5eb7720`):

| # | Fallo | Evidencia | Efecto |
|---|---|---|---|
| F1 | La redacción de parámetros solo normaliza `-` → `_` | `redact_params([("to.ken", …), ("pa_ss", …), ("ＫＥＹ", …)])` los deja en claro (`api.key` sí se tapa) | un secreto con un nombre raro acaba en los logs |
| F2 | `lock.sh` solo mira `$1` | `lock.sh --upgrade extra` pasa la comprobación y sigue hasta `docker` | inocuo hoy (`$2` no se usa), pero la comprobación promete más de lo que hace |
| F3 | Un test del circuito depende del planificador | `test_while_a_probe_runs_the_circuit_stays_open_for_others` usa `asyncio.sleep(0)` para que la prueba haya empezado | puede volverse intermitente con otro bucle de eventos |
| F4 | Un error con la respuesta ya empezada | el middleware relanza (`raise`, `request_context.py:87`) y uvicorn registra `Exception in ASGI application` con el traceback **y el mensaje** | el único camino por el que el mensaje de una excepción llega a los logs |

**Objetivo:** corregir F1–F3 y documentar F4 como límite conocido, igual que en Alcampo.

## 2. Requisitos funcionales (EARS)

- **RF-1.** Un parámetro DEBERÁ redactarse si su nombre, normalizado con NFKC, `casefold()` y sin `-`, `_`, `.` ni espacios, contiene `key`, `token`, `secret`, `auth` o `pass`.
- **RF-2.** `scripts/lock.sh` DEBERÁ rechazar con código 2 más de un argumento, antes de ejecutar cualquier comando externo.
- **RF-3.** El test del circuito DEBERÁ esperar a que la prueba haya empezado mediante un `asyncio.Event`.
- **RF-4.** README: el traceback de uvicorn en un error con la respuesta ya empezada, en "Limitaciones conocidas".

## 3. Requisitos no funcionales

- **RNF-1.** Sin dependencias nuevas ni cambios de contrato.
- **RNF-2.** El test de `lock.sh` corre con un `PATH` vacío: una versión que no rechace la opción no puede llegar a Docker (lección de Alcampo T6).

## 4. Fuera de alcance

- Evitar el traceback de uvicorn (F4): tragarse la excepción ocultaría al servidor que la respuesta quedó a medias.
- Las sugerencias de Alcampo que no aplican a Dia: Dia no tiene enfriamiento creciente (S1) y su `lock.sh` ya borraba `build/` (S4).

## 5. Criterios de finalización

- [ ] F1–F3 con un test RED que los reproduce y en verde tras el cambio; F4 documentado.
- [ ] `ruff`, `mypy`, `pytest` y la CI en verde.

## 6. Decisiones

Sin dudas abiertas: se aplican las decisiones ya tomadas en Alcampo spec 015 (su spec-D2 y su revisión T11).

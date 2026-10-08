# syntax=docker/dockerfile:1

# Spec 006. Both stages use the same base on purpose: the venv's python is a
# symlink to the base image's interpreter, so it must exist in the runtime too.
# 3.11 is the minimum declared in pyproject.toml (spec-D1).

# ---- builder: production dependencies only, into a self-contained venv ----
FROM python:3.11-slim AS builder

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /build
# Exact versions checked by hash (spec 007 RF-4), in their own layer: it is
# rebuilt only when the lock changes, not on every code change (plan-D3).
COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock
COPY pyproject.toml README.md ./
COPY app/ ./app/
# The project alone (`--no-deps`): nothing is resolved again. Plain `.`, no
# [dev] extra: pytest, ruff, respx and fakeredis never get in. The wheel already
# contains app/, so the code travels inside the venv (spec 006 plan-D1).
RUN pip install --no-cache-dir --no-deps .

# ---- runtime: only the venv, run by an unprivileged user ----
FROM python:3.11-slim

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Numeric uid so orchestrators enforcing runAsNonRoot can check it (plan-D3).
# The venv stays owned by root: the process cannot modify its own code.
RUN useradd --system --uid 10001 --no-create-home app
COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
USER 10001
EXPOSE 8000

# Python + httpx (already installed) instead of curl (RNF-2). 127.0.0.1, not
# localhost: localhost may resolve to ::1 and uvicorn listens on IPv4 only.
# trust_env=False: a deploy-time HTTP_PROXY must not intercept the probe (plan-D4).
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD ["python", "-c", "import httpx, sys; sys.exit(httpx.get('http://127.0.0.1:8000/health', timeout=3, trust_env=False).status_code != 200)"]

# Exec form: uvicorn is PID 1 and gets SIGTERM, so the lifespan closes the Dia
# sessions and Redis. One worker: the session pool lives in memory (spec-D7).
# No access log: the request middleware already logs every request with its
# request id, and uvicorn's would print the client's term unescaped (spec-D3).
# 20 s for in-flight requests on shutdown, within compose's 30 s grace (review T5).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log", "--timeout-graceful-shutdown", "20"]

"""Contract of the Dockerfile (spec 006, plan-D5).

Only security and contract properties are checked here; the real build is
verified by hand with the Docker daemon (task T4).
"""

import json
from pathlib import Path

DOCKERFILE = Path(__file__).parents[2] / "Dockerfile"
BASE_IMAGE = "python:3.11-slim"
CONTINUATION = chr(92)  # a backslash, written without one


def raw_text() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def instructions() -> list[tuple[str, str]]:
    """`(KEYWORD, arguments)` per instruction, with line continuations joined."""
    result: list[tuple[str, str]] = []
    pending = ""
    for line in raw_text().splitlines():
        stripped = line.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if stripped.endswith(CONTINUATION):
            pending += stripped[:-1] + " "
            continue
        keyword, _, args = (pending + stripped).partition(" ")
        result.append((keyword.upper(), " ".join(args.split())))
        pending = ""
    return result


def args_of(keyword: str) -> list[str]:
    return [args for kw, args in instructions() if kw == keyword]


def after_last_from() -> list[tuple[str, str]]:
    current = instructions()
    last = max(i for i, (kw, _) in enumerate(current) if kw == "FROM")
    return current[last + 1 :]


def test_two_stages_on_the_minimum_supported_python() -> None:
    builder, runtime = args_of("FROM")  # exactly two stages (spec-D1, plan-D1)

    assert builder.split()[0] == BASE_IMAGE
    assert builder.split()[1:] == ["AS", "builder"]
    assert runtime.split()[0] == BASE_IMAGE


def test_installs_the_project_without_dev_dependencies_nor_pip_cache() -> None:
    installs = [args for args in args_of("RUN") if "pip install" in args]

    assert installs
    assert not any("[" in args for args in installs)  # never `.[dev]`
    assert all("--no-cache-dir" in args for args in installs)


def test_the_runtime_copies_only_the_venv() -> None:
    copies = [args for kw, args in after_last_from() if kw == "COPY"]

    assert copies == ["--from=builder /opt/venv /opt/venv"]


def test_runs_as_an_unprivileged_numeric_user() -> None:
    users = [args for kw, args in after_last_from() if kw == "USER"]

    assert users
    user = users[-1].split(":")[0]
    assert user.isdigit()
    assert user != "0"


def test_serves_the_app_with_one_worker_without_reload_nor_access_log() -> None:
    [cmd] = args_of("CMD")
    argv = json.loads(cmd)  # exec form: uvicorn is PID 1 and receives SIGTERM

    assert argv[:2] == ["uvicorn", "app.main:app"]
    assert argv[argv.index("--host") + 1] == "0.0.0.0"
    assert argv[argv.index("--port") + 1] == "8000"
    assert "--no-access-log" in argv
    assert "--reload" not in argv
    assert "--workers" not in argv  # one worker: the session pool is in memory (spec-D7)
    # Time for in-flight Dia searches before the lifespan closes sessions (review T5).
    assert argv[argv.index("--timeout-graceful-shutdown") + 1] == "20"


def test_healthcheck_probes_health_on_ipv4_loopback_without_proxy() -> None:
    [healthcheck] = args_of("HEALTHCHECK")

    assert "127.0.0.1:8000/health" in healthcheck
    assert "trust_env=False" in healthcheck
    assert "curl" not in healthcheck
    assert "status_code != 200" in healthcheck  # healthy only on a 200


def test_logs_are_unbuffered() -> None:
    env = " ".join(args for kw, args in after_last_from() if kw == "ENV")

    assert "PYTHONUNBUFFERED=1" in env


def test_no_secrets_are_referenced() -> None:
    text = raw_text()

    assert "API_KEYS" not in text
    assert ".env" not in text


def test_the_user_created_is_the_one_that_runs() -> None:
    [useradd] = [args for kw, args in after_last_from() if kw == "RUN" and "useradd" in args]
    [user] = [args for kw, args in after_last_from() if kw == "USER"]

    assert f"--uid {user}" in useradd


def test_the_builder_copies_the_package_readme() -> None:
    copies = [args for kw, args in instructions() if kw == "COPY" and "--from" not in args]

    assert any("README.md" in args for args in copies)


def test_installs_the_pinned_dependencies_then_the_project_alone() -> None:
    # Spec 007 RF-4, plan-D3: exact versions checked by hash, then the package
    # without resolving anything again.
    installs = " && ".join(args for args in args_of("RUN") if "pip install" in args)

    assert "--require-hashes -r requirements.lock -r requirements-build.lock" in installs
    # Built with the hashed setuptools already installed, nothing fetched (review T5).
    assert "--no-deps --no-build-isolation ." in installs
    assert installs.index("requirements.lock") < installs.index("--no-deps")


def test_the_lock_is_installed_before_the_code_is_copied() -> None:
    # Its own layer: a code change does not reinstall the dependencies (plan-D3).
    steps = [(kw, args) for kw, args in instructions()]
    lock_install = next(
        i for i, (kw, a) in enumerate(steps) if kw == "RUN" and "requirements.lock" in a
    )
    code_copy = next(i for i, (kw, a) in enumerate(steps) if kw == "COPY" and "app/" in a)

    assert lock_install < code_copy

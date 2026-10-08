"""Contract of docker-compose.yml (spec 006, plan-D5).

There is no YAML parser among the dependencies (constitution #1), so Docker
itself resolves the file: `docker compose config` needs the CLI but not the
daemon. It runs on a copy in a temporary directory, so the real `.env` (and its
secrets) is never read, which also proves the file works without one (RF-11).
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

COMPOSE_FILE = Path(__file__).parents[2] / "docker-compose.yml"

pytestmark = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker CLI not installed (daemon not needed)"
)


def resolve(directory: Path) -> dict:
    """`docker compose config` of a copy of the file placed in `directory`."""
    copy = directory / "docker-compose.yml"
    shutil.copyfile(COMPOSE_FILE, copy)
    # Defaults only: a local API_PORT must not change what is being tested.
    env = {k: v for k, v in os.environ.items() if k != "API_PORT" and not k.startswith("COMPOSE_")}

    result = subprocess.run(
        ["docker", "compose", "-f", str(copy), "config", "--format", "json"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def config(tmp_path_factory: pytest.TempPathFactory) -> dict:
    """Resolved with no `.env` next to the copy: it must still work (RF-11)."""
    return resolve(tmp_path_factory.mktemp("compose"))


def test_api_uses_the_compose_redis(config: dict) -> None:
    assert config["services"]["api"]["environment"]["REDIS_URL"] == "redis://redis:6379/0"


def test_api_waits_until_redis_is_healthy(config: dict) -> None:
    services = config["services"]

    assert services["api"]["depends_on"]["redis"]["condition"] == "service_healthy"
    assert "ping" in services["redis"]["healthcheck"]["test"]


def test_api_is_published_on_port_8000_by_default(config: dict) -> None:
    ports = {(str(p["published"]), p["target"]) for p in config["services"]["api"]["ports"]}

    assert ports == {("8000", 8000)}


def test_env_file_is_loaded_but_cannot_override_the_redis_url(tmp_path: Path) -> None:
    # `config` inlines the env_file into `environment` (and drops the key), so
    # a synthetic .env shows both that it is loaded and who wins (RF-6, RF-8).
    (tmp_path / ".env").write_text(
        "LOG_LEVEL=DEBUG\nREDIS_URL=redis://localhost:6379/0\n", encoding="utf-8"
    )

    environment = resolve(tmp_path)["services"]["api"]["environment"]

    assert environment["LOG_LEVEL"] == "DEBUG"
    assert environment["REDIS_URL"] == "redis://redis:6379/0"


def test_no_secrets_are_written_in_the_file(config: dict) -> None:
    assert "API_KEYS" not in config["services"]["api"]["environment"]


def test_redis_is_not_published_to_the_host(config: dict) -> None:
    assert "ports" not in config["services"]["redis"]

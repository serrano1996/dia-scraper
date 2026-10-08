"""Contract of the CI workflow (spec 007 RF-5..RF-10).

It runs on GitHub (serrano1996/dia-scraper) on every push; this test pins
what it runs, and the same commands run locally.
"""

from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")  # comes with uvicorn[standard]

WORKFLOW = Path(__file__).parents[2] / ".github" / "workflows" / "ci.yml"
REQUIRED_COMMANDS = [
    "ruff check .",
    "ruff format --check .",
    "mypy",
    "pytest -q",
    "docker build",
]


@pytest.fixture(scope="module")
def workflow() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def commands(workflow: dict) -> list[str]:
    return [step.get("run", "") for job in workflow["jobs"].values() for step in job["steps"]]


def test_runs_on_every_push_and_pull_request(workflow: dict) -> None:
    triggers = workflow.get("on", workflow.get(True))  # YAML 1.1 reads `on` as True
    assert {"push", "pull_request"} <= set(triggers)


def test_uses_the_minimum_supported_python(workflow: dict) -> None:
    versions = [
        step["with"]["python-version"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses", "").startswith("actions/setup-python")
    ]
    assert versions == ["3.11"]


@pytest.mark.parametrize("command", REQUIRED_COMMANDS)
def test_runs_every_check_that_closes_a_task(workflow: dict, command: str) -> None:
    assert any(command in run for run in commands(workflow))


def test_installs_the_pinned_dev_dependencies(workflow: dict) -> None:
    # Spec 007 RF-4, RF-5: the dev lock with hashes, the package alone, then `pip check`
    # fails if pyproject.toml declares something the lock lacks (plan-D3).
    runs = "\n".join(commands(workflow))

    assert (
        "pip install --require-hashes -r requirements-dev.lock -r requirements-build.lock" in runs
    )
    # The editable build uses the hashed setuptools, nothing fetched (review T5).
    assert "pip install --no-deps --no-build-isolation -e ." in runs
    assert "pip check" in runs
    assert runs.index("requirements-dev.lock") < runs.index("--no-build-isolation -e .")
    assert runs.index("--no-build-isolation -e .") < runs.index("pip check")


def test_uses_current_actions_on_a_fixed_runner(workflow: dict) -> None:
    # Spec 007 RF-8: v7 (Node 24), and no silent jump to the next Ubuntu.
    uses = [step.get("uses", "") for job in workflow["jobs"].values() for step in job["steps"]]

    assert "actions/checkout@v7" in uses
    assert "actions/setup-python@v7" in uses
    assert [job["runs-on"] for job in workflow["jobs"].values()] == ["ubuntu-24.04"]


def test_has_read_only_permissions(workflow: dict) -> None:
    # Spec 007 RF-7: nothing is published, nothing is written.
    assert workflow["permissions"] == {"contents": "read"}


def test_needs_no_secrets(workflow: dict) -> None:
    # Spec 007 RF-10: the tests never call Dia nor a real Redis.
    assert "secrets." not in WORKFLOW.read_text(encoding="utf-8")

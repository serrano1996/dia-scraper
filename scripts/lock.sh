#!/usr/bin/env sh
# Spec 007: regenerate requirements.lock and requirements-dev.lock from pyproject.toml.
#
#   scripts/lock.sh            keep the pinned versions; add or drop what changed
#   scripts/lock.sh --upgrade  move every package to the newest version in its range
#
# Runs in a throwaway python:3.11-slim container: the locks must resolve for the
# image and the CI (Linux, Python 3.11), not for the machine running this
# (spec-D2, plan-D1). Contacts PyPI only. Needs Docker.
set -eu

cd "$(dirname "$0")/.."

# Git Bash on Windows rewrites /src-like paths and needs a Windows path to mount.
if pwd -W >/dev/null 2>&1; then
    HERE="$(pwd -W)"
    export MSYS_NO_PATHCONV=1
else
    HERE="$(pwd)"
fi

PIP_TOOLS_VERSION="7.6.1"  # the generator is pinned too
COMPILE="pip-compile --quiet --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url $*"

docker run --rm -v "$HERE:/src" -w /src python:3.11-slim sh -c "
    pip install --quiet --disable-pip-version-check pip-tools==$PIP_TOOLS_VERSION &&
    $COMPILE --output-file requirements.lock pyproject.toml &&
    $COMPILE --extra dev --output-file requirements-dev.lock pyproject.toml &&
    rm -rf dia_scraper.egg-info build
"

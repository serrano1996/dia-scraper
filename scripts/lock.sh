#!/usr/bin/env sh
# Spec 007: regenerate the lockfiles from pyproject.toml.
#
#   scripts/lock.sh            keep the pinned versions; add or drop what changed
#   scripts/lock.sh --upgrade  move every package to the newest version in its range
#
# requirements.lock        production dependencies
# requirements-dev.lock    production + the dev extra
# requirements-build.lock  the build backend ([build-system].requires), so that
#                          building the package fetches nothing unhashed (review T5)
#
# Runs in a throwaway python:3.11-slim container: the locks must resolve for the
# image and the CI (Linux, Python 3.11), not for the machine running this
# (spec-D2, plan-D1). Contacts PyPI only. Needs Docker.
set -eu

# Only the one option we know, checked before any other command and with shell
# builtins only: anything else would end up inside `sh -c` (spec 009 RF-2).
if [ "$#" -gt 1 ]; then
    echo "usage: scripts/lock.sh [--upgrade]" >&2; exit 2
fi
case "${1:-}" in
    "" | --upgrade) UPGRADE="${1:-}" ;;
    *) echo "usage: scripts/lock.sh [--upgrade]" >&2; exit 2 ;;
esac

cd "$(dirname "$0")/.."

# Git Bash on Windows rewrites /src-like paths and needs a Windows path to mount.
if pwd -W >/dev/null 2>&1; then
    HERE="$(pwd -W)"
    export MSYS_NO_PATHCONV=1
else
    HERE="$(pwd)"
fi

PIP_TOOLS_VERSION="7.6.1"  # the generator is pinned too
COMPILE="pip-compile --quiet --generate-hashes --allow-unsafe --strip-extras --no-emit-index-url $UPGRADE"

docker run --rm -v "$HERE:/src" -w /src python:3.11-slim sh -c "
    status=0
    {
        pip install --quiet --disable-pip-version-check pip-tools==$PIP_TOOLS_VERSION &&
        $COMPILE --output-file requirements.lock pyproject.toml &&
        $COMPILE --extra dev --output-file requirements-dev.lock pyproject.toml &&
        python -c 'import tomllib; print(*tomllib.load(open(\"pyproject.toml\", \"rb\"))[\"build-system\"][\"requires\"], sep=chr(10))' > /tmp/build.in &&
        $COMPILE --output-file requirements-build.lock /tmp/build.in
    } || status=\$?
    # Always, even after a failure: pip-compile leaves these behind in the mount.
    rm -rf dia_scraper.egg-info build
    exit \$status
"

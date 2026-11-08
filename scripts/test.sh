#!/bin/sh
# Lint and the full suite, exactly as CI runs them. From anywhere in the repo:
#   scripts/test.sh            ruff, then every test, browser tests included
#   scripts/test.sh browser    once: fetch the pinned Chromium and its system libraries (asks for sudo)
# Extra arguments go to pytest, so scripts/test.sh -k keyboard runs one test.
set -eu
cd "$(dirname "$0")/.."
PLAYWRIGHT="playwright==1.63.0"   # the one version, here and in CI, so the browser is the same too
RUFF="ruff==0.17.0"               # pinned like the rules in pyproject.toml, so a new ruff can't fail CI on its own
# the release Field's route through Polarizer is written against (parallax/fieldproxy.py), for the one test
# that runs the real program. parallax/ never imports Polarizer; this only puts its command beside the tests
POLARIZER="https://github.com/mpwilso/polarizer/releases/download/v0.6.2/polarizer-0.6.2-py3-none-any.whl"
PY="uv run --no-project --python 3.12"

if [ "${1:-}" = "browser" ]; then
    exec $PY --with "$PLAYWRIGHT" python -m playwright install --with-deps chromium
fi
$PY --with "$RUFF" ruff check .
# under a 4 GB memory cap (parallax/memcap.py): a runaway test fails this run with a plain message
# instead of taking the machine down. tests/test_memcap.py checks the cap is on when run from here.
export PARALLAX_TEST_SH=1
exec $PY --with pytest --with "$PLAYWRIGHT" --with "$POLARIZER" --with-editable . python -m parallax.memcap 4G -- \
    python -m pytest -q -rs -p no:cacheprovider "$@"

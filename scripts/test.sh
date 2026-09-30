#!/bin/sh
# Lint and the full suite, exactly as CI runs them. From anywhere in the repo:
#   scripts/test.sh            ruff, then every test, browser tests included
#   scripts/test.sh browser    once: fetch the pinned Chromium and its system libraries (asks for sudo)
# Extra arguments go to pytest, so scripts/test.sh -k keyboard runs one test.
set -eu
cd "$(dirname "$0")/.."
PLAYWRIGHT="playwright==1.63.0"   # the one version, here and in CI, so the browser is the same too
PY="uv run --no-project --python 3.12"

if [ "${1:-}" = "browser" ]; then
    exec $PY --with "$PLAYWRIGHT" python -m playwright install --with-deps chromium
fi
$PY --with ruff ruff check .
# under a 4 GB memory cap (parallax/memcap.py): a runaway test fails this run with a plain message
# instead of taking the machine down. tests/test_memcap.py checks the cap is on when run from here.
export PARALLAX_TEST_SH=1
exec $PY --with pytest --with "$PLAYWRIGHT" --with-editable . python -m parallax.memcap 4G -- \
    python -m pytest -q -rs -p no:cacheprovider "$@"

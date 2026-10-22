#!/usr/bin/env bash
# Run every kit test: personal-value scan, channel (node:test), Python tests (pytest), the workflow checker's
# tests and the clean-install test. Needs python3 with pytest (or set PYTEST), node 18+, git, jq.
# Exit 0 only when everything passes.
set -u
KIT="$(cd "$(dirname "$0")/.." && pwd -P)"
PYTEST="${PYTEST:-python3 -m pytest}"
fail=0
run() { echo "== $1"; shift; "$@" || { echo "== FAILED: $*"; fail=1; }; }
cd "$KIT"
run "scan_private" python3 scan_private.py
if [ ! -d foreman/channel/node_modules ]; then
  run "channel deps" bash -c 'cd foreman/channel && npm ci --no-audit --no-fund'
fi
run "channel (node:test)" bash -c 'cd foreman/channel && npm test --silent'
run "python tests" $PYTEST -q tests
run "workflow checker" bash -c 'cd workflow && python3 -m unittest -q test_check_task'
run "clean install" bash tests/test_clean_install.sh
[ "$fail" = 0 ] && echo "== ALL PASSED" || echo "== SOME TESTS FAILED"
exit "$fail"

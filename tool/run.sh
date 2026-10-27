#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ${RUNNER_OS:-} == Windows ]]; then
  exec .tool-cache/python/python.exe tool/ci.py "$@"
fi
exec devbox run -- python3 tool/ci.py "$@"

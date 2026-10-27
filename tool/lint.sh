#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# actionlint 1.7.12 predates concurrency.queue. Assert the exact supported
# policy before excluding only that obsolete schema diagnostic.
awk '
  /^  queue:/ { total++; if ($0 != "  queue: max") exit 1 }
  /^  cancel-in-progress:/ { if ($0 != "  cancel-in-progress: false") exit 1; cancel++ }
  END { if (total != 1 || cancel != 1) exit 1 }
' .github/workflows/release.yml
actionlint -ignore '^unexpected key "queue" for "concurrency" section\.' .github/workflows/release.yml
shellcheck tool/*.sh

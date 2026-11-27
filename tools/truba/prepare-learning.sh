#!/bin/bash
# Install only into a dedicated RustMech TRUBA runtime on the compute node.
set -euo pipefail
python="$1"
shift
environment="$("$python" - <<'PY'
from pathlib import Path
import sys

target = Path(sys.prefix).resolve()
base = (Path.home() / ".local/share/RustMech").resolve()
if (target.parent != base or not target.name.startswith("remote-")
        or sys.prefix == sys.base_prefix):
    raise SystemExit("Install training packages manually for a custom TRUBA runtime.")
print(target)
PY
)"
exec 9>"$environment/learning-install.lock"
flock 9
for package in "$@"; do
    if [[ "$package" == torch\>* ]]; then
        "$python" -m pip install --disable-pip-version-check --only-binary=:all: \
            --timeout 45 --retries 1 --index-url https://download.pytorch.org/whl/cpu "$package"
    else
        "$python" -m pip install --disable-pip-version-check --only-binary=:all: \
            --timeout 45 --retries 1 "$package"
    fi
done

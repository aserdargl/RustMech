#!/bin/bash
# Optional local Linux verification; never contacts TRUBA or modifies system packages.
set -euo pipefail
project="$(cd "$(dirname "$0")/.." && pwd)"
verification="$project/build/remote-linux-verification"
mkdir -p "$verification/engine"
tar -xzf "$verification/engine.tar.gz" -C "$verification/engine"
bootstrap="$HOME/.local/share/RustMech/verification-tools"
mkdir -p "$bootstrap"
if [ ! -x "$bootstrap/uv-x86_64-unknown-linux-gnu/uv" ]; then
  curl -fL --retry 1 https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz -o "$bootstrap/uv.tar.gz"
  tar -xzf "$bootstrap/uv.tar.gz" -C "$bootstrap"
fi
export UV_PYTHON_INSTALL_DIR="$bootstrap/pythons"
"$bootstrap/uv-x86_64-unknown-linux-gnu/uv" python install 3.12
python_bin="$(dirname "$("$bootstrap/uv-x86_64-unknown-linux-gnu/uv" python find 3.12)")"
export PATH="$python_bin:$PATH"
cd "$verification"
bash engine/tools/truba/prepare-remote.sh "$HOME/.local/share/RustMech/remote-verification-py312/bin/python" "$(cat native.sha)"

#!/bin/bash
set -euo pipefail
project="$(cd "$(dirname "$0")/.." && pwd)"
set +u
source /usr/lib/openfoam/openfoam2606/etc/bashrc >/dev/null 2>&1 || true
set -u
command -v chtMultiRegionFoam >/dev/null
export RUSTMECH_CORE_LIBRARY="$HOME/.local/share/RustMech/remote-verification-py312/native/librustmech_core.so"
export QT_API=pyside6 QT_QPA_PLATFORM=offscreen MPLBACKEND=Agg
export OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=1
"$HOME/.local/share/RustMech/remote-verification-py312/bin/python" "${1:-$project/tools/verify_remote_worker_linux.py}"

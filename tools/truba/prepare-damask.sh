#!/bin/bash
# Install only into the same private runtime validated by prepare-remote.sh.
set -euo pipefail
runtime_python="$1"
environment="$(dirname "$(dirname "$runtime_python")")"
bootstrap="$environment/damask-bootstrap"
mkdir -p "$bootstrap"
exec 9>"$bootstrap/install.lock"
flock 9
printf '%s\n' 'Preparing private DAMASK FFT runtime' > progress.txt
if ! "$runtime_python" -c 'import h5py' 2>/dev/null; then
    "$runtime_python" -m pip install --only-binary=:all: --timeout 30 --retries 1 'h5py>=3.11,<4'
fi

for solver in damask_grid DAMASK_grid "$environment/damask/bin/damask_grid" "$environment/damask/bin/DAMASK_grid"; do
    if command -v "$solver" >/dev/null 2>&1 && "$solver" -h >/dev/null 2>&1; then
        exit 0
    fi
done

if [ ! -x "$bootstrap/bin/micromamba" ]; then
    curl --fail --location --proto '=https' --retry 2 --max-time 120 \
        https://micro.mamba.pm/api/micromamba/linux-64/2.5.0 -o "$bootstrap/micromamba.tar.bz2"
    tar -xjf "$bootstrap/micromamba.tar.bz2" -C "$bootstrap" bin/micromamba
    rm -f -- "$bootstrap/micromamba.tar.bz2"
fi
action=create
if [ -f "$environment/damask/conda-meta/history" ]; then action=install; fi
MAMBA_ROOT_PREFIX="$bootstrap" "$bootstrap/bin/micromamba" "$action" --yes --no-rc \
    --prefix "$environment/damask" --override-channels -c conda-forge 'damask-grid=3.1.0' mpich

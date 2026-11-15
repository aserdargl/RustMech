#!/bin/bash -l
set -euo pipefail
module load comp/python/3.12.0
export OMP_NUM_THREADS="${SLURM_CPUS_PER_TASK:-1}"
export OPENBLAS_NUM_THREADS=1
exec "$HOME/.local/share/RustMech/kratos-10.4.4/bin/python" "$@"

#!/usr/bin/env bash
#SBATCH --job-name=pyfem-sea
#SBATCH --time=3-00:00:00
#SBATCH --ntasks=1
set -euo pipefail

# Run with sbatch from the uploaded project directory on shared storage.
if (( $# < 3 )); then
    echo "Usage: sbatch train.slurm DATASET RUN_DIR MAX_SEGMENTS [training options]" >&2
    exit 2
fi
dataset=$(realpath "$1")
run_dir=$(realpath -m "$2")
remaining=$3
shift 3
[[ "$remaining" =~ ^[1-9][0-9]*$ ]] || exit 2
mkdir -p "$run_dir"
exec 9>"$run_dir/.training.lock"
flock -n 9 || { echo "Training already active for $run_dir" >&2; exit 1; }

status=0
if [[ -n "${PYFEM_TRAIN_IMAGE:-}" ]]; then
    apptainer exec --bind "$PWD,$(dirname "$dataset"),$run_dir" "$PYFEM_TRAIN_IMAGE"         python train.py "$dataset" "$run_dir" "$@" || status=$?
else
    "${PYFEM_TRAIN_PYTHON:-python3}" train.py "$dataset" "$run_dir" "$@" || status=$?
fi

if (( status != 75 )); then
    exit "$status"
fi
if (( remaining == 1 )); then
    echo "Segment limit reached. Checkpoint saved in $run_dir." >&2
    exit 75
fi

options=(--dependency="afterok:$SLURM_JOB_ID" --partition="$SLURM_JOB_PARTITION"
         --chdir="$PWD" --time="${PYFEM_TRAIN_WALLTIME:-3-00:00:00}"
         --cpus-per-task="${SLURM_CPUS_PER_TASK:-1}")
if [[ -n "${SLURM_JOB_ACCOUNT:-}" ]]; then
    options+=(--account="$SLURM_JOB_ACCOUNT")
fi
sbatch --parsable "${options[@]}" "$PWD/train.slurm"     "$dataset" "$run_dir" "$((remaining - 1))" "$@"

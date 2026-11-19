"""Slurm-side continuation protocol for explicitly restartable ESEN inputs."""

import shlex


# Used on both the login and compute nodes. Keep the same lock while looking
# for, submitting, or cancelling allocations belonging to this run.
CHAIN_FUNCTIONS = r'''
active_jobs() {
    squeue -h -u "$(id -un)" -n "$job_name" --states=all -o '%A|%T' |
        awk -F'|' '$2 ~ /^(PENDING|RUNNING|COMPLETING|CONFIGURING|SUSPENDED|STOPPED|RESIZING|REQUEUE_HOLD|REQUEUED|REQUEUE_FED|SIGNALING|STAGE_OUT)$/ {print $1}'
}

submit_segment() {
    local job
    job=$(sbatch --parsable --job-name="$job_name" run.slurm "$@") || return
    job=${job%%;*}
    [[ "$job" =~ ^[0-9]+$ ]] || return 2
    printf '%s\n' "$job" > job.id.tmp && mv -f -- job.id.tmp job.id || return
    printf 'JOB_ID=%s\n' "$job"
}
'''


def control_script(remote, job_name, action):
    setup = f"cd {shlex.quote(remote)} || exit\njob_name={shlex.quote(job_name)}\n"
    return setup + CHAIN_FUNCTIONS + 'set -euo pipefail\nexec 9>chain.lock\nflock -w 25 9\n' + action


RESUME = r'''
test ! -e completed || { echo 'This run has already completed.' >&2; exit 1; }
ids=$(active_jobs)
test -z "$ids" || { echo 'This run already has an active Slurm job. Refresh its status.' >&2; exit 1; }
test -n "$(find checkpoints -type f -name 'step.*' -size +0c -print -quit)" || {
    echo 'No LAMMPS checkpoint is available for this run.' >&2; exit 1;
}
rm -f -- cancelled
submit_segment resume
'''

CANCEL = r'''
touch cancelled
ids=$(active_jobs)
failed=0
while IFS= read -r job; do
    [[ "$job" =~ ^[0-9]+$ ]] || continue
    if ! scancel "$job"; then
        if ! remaining=$(active_jobs) || grep -Fxq -- "$job" <<< "$remaining"; then failed=1; fi
    fi
done <<< "$ids"
exit "$failed"
'''


def batch_body(initial_input, resume_input, launcher, ranks, target, work="work"):
    checkpoint_root = "/".join([".."] * len(work.split("/")))
    return (f'initial_input={shlex.quote(initial_input)}\nresume_input={shlex.quote(resume_input)}\n'
            f'work={shlex.quote(work)}\ncheckpoint_root={checkpoint_root}\n'
            f'launch=({launcher} -n {ranks} {target})\n' + CHAIN_FUNCTIONS + r'''
set -u
base=$PWD
job_name=$SLURM_JOB_NAME
job_id=$SLURM_JOB_ID
segment=$job_id-${SLURM_RESTART_COUNT:-0}
mode=${1:-initial}
parent=${2:-}
baseline=${3:--1}
[[ "$baseline" =~ ^(-1|0|[1-9][0-9]{0,17})$ ]] || exit 2
mkdir -p checkpoints

checkpoints_after() {
    local file step
    for file in checkpoints/*/step.*; do
        [[ -s "$file" && -f "$file" && ! -L "$file" ]] || continue
        step=${file##*.}
        [[ "$step" =~ ^(0|[1-9][0-9]{0,17})$ ]] || continue
        if (( step > $1 )); then printf '%s %s\n' "$step" "$file"; fi
    done | sort -k1,1nr
}

find_checkpoint() {
    local step file
    while read -r step file; do
        if (cd -- "$work" && "${launch[@]}" -log none -screen none -restart2info "$checkpoint_root/$file"); then
            checkpoint=$checkpoint_root/$file
            checkpoint_step=$step
            return 0
        fi
    done < <(checkpoints_after "$1")
    return 1
}

queue_successor() (
    exec 9>chain.lock
    flock -w 25 9 || exit
    [[ ! -e completed && ! -e cancelled ]] || exit 0
    local ids active
    ids=$(active_jobs) || exit
    while IFS= read -r active; do
        [[ -z "$active" || "$active" == "$job_id" ]] || exit 0
    done <<< "$ids"
    if [[ -z "$(checkpoints_after "$checkpoint_step")" ]]; then
        echo 'No newer LAMMPS checkpoint; this allocation will keep running.' >&2
        exit 0
    fi
    submit_segment handover "$job_id" "$checkpoint_step"
)

retire_parent() {
    # A successor that actually got resources verifies a checkpoint before
    # interrupting the predecessor. Wait for Slurm to finish cleaning up.
    local ids
    exec 9>chain.lock
    flock -w 25 9 || return 1
    if [[ -e completed || -e cancelled ]]; then exit 0; fi
    ids=$(active_jobs) || return 1
    if grep -Fxq -- "$parent" <<< "$ids"; then
        if ! scancel "$parent"; then
            ids=$(active_jobs) || return 1
            if grep -Fxq -- "$parent" <<< "$ids"; then return 1; fi
        fi
    fi
    exec 9>&-
    while :; do
        ids=$(active_jobs) || return 1
        if ! grep -Fxq -- "$parent" <<< "$ids"; then return 0; fi
        sleep 5
    done
}

[[ ! -e completed && ! -e cancelled ]] || exit 0
checkpoint=none
checkpoint_step=-1
input=$initial_input
if [[ "$mode" != initial || ${SLURM_RESTART_COUNT:-0} != 0 ]]; then
    if ! find_checkpoint "$baseline"; then
        echo 'No readable LAMMPS checkpoint with new progress.' >&2
        exit 1
    fi
    if [[ "$mode" == handover ]]; then
        [[ "$parent" =~ ^[0-9]+$ && "$parent" != "$job_id" ]] || exit 2
        retire_parent
        [[ ! -e completed && ! -e cancelled ]] || exit 0
        find_checkpoint "$baseline" || exit 1
    fi
    input=$resume_input
    printf 'ESEN restart step %s (%s)\n' "$checkpoint_step" "$checkpoint"
fi

# Hold this lock through the solver lifetime, including if its batch shell
# is interrupted. A second allocation cannot write to work at the same time.
exec 8>solver.lock
flock -w 120 8
[[ ! -e completed && ! -e cancelled ]] || exit 0
mkdir -p "checkpoints/$segment"
handover_due=0
trap 'handover_due=1' USR1
cd -- "$work"
"${launch[@]}" -nonbuf -log "log.$segment.lammps" -in "$input" \
    -var esen_checkpoint "$checkpoint" -var esen_checkpoint_dir "$checkpoint_root/checkpoints/$segment" \
    -var esen_segment "$segment" &
solver=$!
status=0
while :; do
    wait "$solver" && status=0 || status=$?
    if (( ! handover_due )); then break; fi
    handover_due=0
    if kill -0 "$solver" 2>/dev/null; then
        (cd "$base" && queue_successor) || echo 'Could not queue the replacement allocation.' >&2
    fi
done
cd "$base"
if (( status == 0 )); then
    exec 9>chain.lock
    flock -w 25 9
    touch completed
    ids=$(active_jobs) || exit 0
    while IFS= read -r active; do
        if [[ "$active" =~ ^[0-9]+$ && "$active" != "$job_id" ]]; then scancel "$active" || true; fi
    done <<< "$ids"
fi
exit "$status"
''')

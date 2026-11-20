"""Single-node ARF resource policy and read-only Slurm limit discovery.

Documented baseline: https://docs.truba.gov.tr/1-kaynaklar/arf/arf_kuyruk_bilgisi.html
Policy reference: https://docs.truba.gov.tr/2-temel_bilgiler/hesaplama_kumeleri.html
Scheduler defaults (DefMemPerCPU) are not minimum memory requirements.
"""
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import re

MIN_CPUS = 1
MIN_MEMORY_MB = 1  # Smallest positive Slurm MiB request; not a site policy.


@dataclass(frozen=True)
class QueueLimits:
    name: str
    label: str
    min_cpus: int
    max_cpus: int
    max_memory_mb: int
    max_seconds: int = 72 * 3600
    max_memory_per_cpu: int = 0
    available: bool = True
    verified: bool = False
    node_capacities: tuple = ()
    min_gpus: int = 0
    max_gpus: int = 0
    min_memory_mb: int | None = None
    default_memory_per_cpu: int = 0
    default_memory_mb: int = 0
    idle_capacities: tuple = ()

    def suggested_memory(self, cpus):
        requested = self.default_memory_per_cpu * cpus or self.default_memory_mb or 4096
        return max(self.min_memory_mb or 1, min(requested, self.memory_ceiling(cpus)))

    def memory_ceiling(self, cpus):
        ceiling = self.max_memory_mb
        if self.node_capacities:
            ceiling = min(ceiling, max((m for c, m in self.node_capacities if c >= cpus), default=0))
        return min(ceiling, cpus * self.max_memory_per_cpu) if self.max_memory_per_cpu else ceiling


BASELINES = {
    'debug': QueueLimits('debug', 'Debug · short tests', 1, 112, 384 * 1024, 4 * 3600, max_gpus=4),
    'hamsi': QueueLimits('hamsi', 'Hamsi', 28, 56, 192 * 1024, max_memory_per_cpu=3400, default_memory_per_cpu=3400),
    'barbun': QueueLimits('barbun', 'Barbun', 20, 40, 384 * 1024, max_memory_per_cpu=9500, default_memory_per_cpu=8500),
    'orfoz': QueueLimits('orfoz', 'Orfoz', 56, 112, 256 * 1024, max_memory_per_cpu=2295, default_memory_per_cpu=2000),
    'smp': QueueLimits('smp', 'Orkinos · smp', 4, 224, 4096 * 1024, max_memory_per_cpu=18400, default_memory_per_cpu=17000),
    'barbun-cuda': QueueLimits('barbun-cuda', 'Barbun · P100 GPU', 20, 40, 384 * 1024, min_gpus=1, max_gpus=2, max_memory_per_cpu=9500, default_memory_per_cpu=8500),
    'akya-cuda': QueueLimits('akya-cuda', 'Akya · V100 GPU', 10, 40, 384 * 1024, min_gpus=1, max_gpus=4, max_memory_per_cpu=9500, default_memory_per_cpu=8500),
    'palamut-cuda': QueueLimits('palamut-cuda', 'Palamut · A100 GPU', 16, 128, 1024 * 1024, min_gpus=1, max_gpus=8, max_memory_per_cpu=16384, default_memory_per_cpu=7500),
    'kolyoz-cuda': QueueLimits('kolyoz-cuda', 'Kolyoz · H100 / H200 GPU', 16, 64, 1024 * 1024, min_gpus=1, max_gpus=4, max_memory_per_cpu=16384, default_memory_per_cpu=16384),
}
AUTO_QUEUES = ('hamsi', 'barbun', 'orfoz')


def seconds(value):
    days, value = value.split('-', 1) if '-' in value else ('0', value)
    parts = [int(x) for x in value.split(':')]
    if len(parts) == 1:
        return int(days) * 86400 + parts[0] * 60
    if len(parts) == 2:
        parts.insert(0, 0)
    h, m, s = parts
    return int(days) * 86400 + h * 3600 + m * 60 + s


def candidates(partition, cpus, memory_mb, walltime, catalog=None, *, gpus=0):
    catalog = BASELINES if catalog is None else catalog
    names = AUTO_QUEUES if partition == 'auto' else tuple(partition.split(','))
    result = []
    for name in names:
        limit = catalog.get(name)
        if (limit and limit.available and limit.min_cpus <= cpus <= limit.max_cpus
                and (limit.min_memory_mb or 1) <= memory_mb <= limit.memory_ceiling(cpus)
                and limit.min_gpus <= gpus <= limit.max_gpus
                and 0 < seconds(walltime) <= limit.max_seconds):
            result.append(name)
    if partition != 'auto' and len(result) != len(names):
        raise ValueError('CPU, RAM or wall time exceeds the selected queue limits. Refresh queue limits and adjust the request.')
    if not result:
        raise ValueError('No eligible queue for this CPU, GPU, RAM and wall time request.')
    return ','.join(result)


def _fields(line):
    return dict(re.findall(r'(\w+)=([^\s]*)', line))


def _number(value):
    if not value or value in ('UNLIMITED', 'INFINITE', 'N/A', '(null)', '0'):
        return 0
    match = re.fullmatch(r'(\d+)([KMGT]?)', value)
    if not match:
        return 0
    return int(int(match[1]) * {'': 1, 'K': 1 / 1024, 'M': 1, 'G': 1024, 'T': 1024**2}[match[2]])


def _gpu_count(node):
    total = re.search(r'(?:^|,)gres/gpu=(\d+)(?:,|$)', node.get('CfgTRES', ''))
    if total:
        return int(total[1])
    return sum(int(n) for n in re.findall(r'gpu(?::[^:,()]+)?:(\d+)', node.get('Gres', '')))


def parse_catalog(partitions, nodes, *, live_node_capacity=False):
    """Use configured capacity, never transient free RAM/idle CPU as a limit."""
    node_rows = [_fields(line) for line in nodes.splitlines() if 'NodeName=' in line]
    result = {}
    for line in partitions.splitlines():
        row = _fields(line)
        name = row.get('PartitionName')
        if name not in BASELINES:
            continue
        base = BASELINES[name]
        members = [n for n in node_rows if name in n.get('Partitions', '').split(',')]
        if not members:
            continue
        # Prefer effective CPU count and scheduler-configured RAM, reserving OS memory.
        capacities = [(int(n.get('CPUEfctv', n.get('CPUTot', '0'))),
                       int(n.get('RealMemory', '0')) - int(n.get('MemSpecLimit', '0'))) for n in members]
        idle = tuple((max(0, c - int(n.get('CPUAlloc', '0'))), max(0, m - int(n.get('AllocMem', '0'))))
                     for n, (c, m) in zip(members, capacities)
                     if not any(state in n.get('State', '') for state in ('DOWN', 'DRAIN', 'FAIL', 'RESERVED')))
        max_cpu = max(c for c, m in capacities)
        mem = max(m for c, m in capacities)
        if not live_node_capacity:
            max_cpu = min(base.max_cpus, max_cpu)
            mem = min(base.max_memory_mb, mem)
        cpu_cap = _number(row.get('MaxCPUsPerNode'))
        mem_cap = _number(row.get('MaxMemPerNode'))
        if cpu_cap:
            max_cpu = min(max_cpu, cpu_cap)
        if mem_cap:
            mem = min(mem, mem_cap)
        duration = row.get('MaxTime', 'UNLIMITED')
        max_seconds = base.max_seconds if duration == 'UNLIMITED' else min(base.max_seconds, seconds(duration))
        result[name] = replace(base, max_cpus=max_cpu, max_memory_mb=mem,
                               max_seconds=max_seconds,
                               max_memory_per_cpu=_number(row.get('MaxMemPerCPU')) if 'MaxMemPerCPU' in row else base.max_memory_per_cpu,
                               default_memory_per_cpu=_number(row['DefMemPerCPU']) if 'DefMemPerCPU' in row else (0 if 'DefMemPerNode' in row else base.default_memory_per_cpu),
                               default_memory_mb=(_number(row.get('DefMemPerNode')) or mem) if 'DefMemPerNode' in row or row.get('DefMemPerCPU') in ('0', 'UNLIMITED') else 0,
                               idle_capacities=idle,
                               max_gpus=min(base.max_gpus, max((_gpu_count(n) for n in members), default=0)),
                               available=row.get('State') == 'UP' and max_cpu >= base.min_cpus,
                               verified=True, node_capacities=tuple(capacities))
    if not result:
        raise RuntimeError('Slurm queue limits could not be read. No resource request was submitted.')
    return result


def fetch_catalog(context, auth=None, *, live_node_capacity=False):
    from truba_remote import RemoteTransport
    transport = RemoteTransport(context, auth)
    reply = transport.command("set -e\nscontrol show partition -o\nprintf '\\nRUSTMECH_NODES\\n'\nscontrol show node -o")
    partitions, separator, nodes = reply.partition('\nRUSTMECH_NODES\n')
    if not separator:
        raise RuntimeError('Slurm returned incomplete queue limits. No request was submitted.')
    return parse_catalog(partitions, nodes, live_node_capacity=live_node_capacity)


def automatic_options(context, catalog, *, min_cpus=1):
    """Choose a CPU queue and its resource defaults, preferring an idle fit.

    Free capacity is only a snapshot. Slurm remains the scheduling authority;
    no particular node is pinned and no immediate-start promise is made.
    """
    options = []
    for order, name in enumerate(AUTO_QUEUES):
        limit = catalog.get(name)
        if not limit or not limit.available or limit.min_gpus:
            continue
        cpus = max(limit.min_cpus, min_cpus)
        if cpus > limit.max_cpus:
            continue
        ram = limit.suggested_memory(cpus)
        try:
            candidates(name, cpus, ram, context.walltime, catalog)
        except ValueError:
            continue
        idle_fit = any(c >= cpus and m >= ram for c, m in limit.idle_capacities)
        options.append(((not idle_fit, cpus, order), replace(context, partition=name, cores=cpus, memory_mb=ram, gpus=0)))
    if not options:
        raise ValueError('No supported CPU queue can satisfy this analysis and wall time. Refresh limits or select a queue manually.')
    return [value for score, value in sorted(options, key=lambda item: item[0])]


def automatic_context(context, catalog, *, min_cpus=1):
    return automatic_options(context, catalog, min_cpus=min_cpus)[0]


def convergence_context(context, catalog, solver_cpus, max_jobs):
    """Size independent convergence jobs from the configured CPU and RAM budget."""
    if context.gpus:
        raise ValueError('Mesh convergence requires a CPU queue on TRUBA.')
    if context.partition == 'auto':
        partition = automatic_context(context, catalog, min_cpus=solver_cpus).partition
    else:
        partition = context.partition
    limit = catalog.get(partition)
    baseline = BASELINES.get(partition)
    if limit is None or baseline is None or limit.min_gpus:
        raise ValueError('Mesh convergence requires an available CPU queue on TRUBA.')
    cpus = max(solver_cpus, limit.min_cpus, baseline.min_cpus)
    minimum_memory = max(limit.min_memory_mb or 1, baseline.min_memory_mb or 1)
    slots = max(1, min(max_jobs, context.cores // cpus, context.memory_mb // minimum_memory))
    memory = min(context.memory_mb // slots, limit.memory_ceiling(cpus), baseline.memory_ceiling(cpus))
    candidates(partition, cpus, memory, context.walltime, catalog)
    candidates(partition, cpus, memory, context.walltime)
    return replace(context, partition=partition, cores=cpus, memory_mb=memory,
                   parallel_jobs=slots, session_id='')


def test_submission(context, auth=None):
    """Ask Slurm (including job-submit plugins) to validate; never allocate."""
    import shlex
    from truba_remote import RemoteTransport, slurm_script
    from truba_jobs import SAFE_NAME
    transport = RemoteTransport(context, auth)
    if not SAFE_NAME.fullmatch(transport.username):
        raise ValueError('Invalid TRUBA username.')
    scratch = shlex.quote(f'/arf/scratch/{transport.username}/RustMech')
    script = slurm_script(context, transport.username,
                          dict(operation='probe', source_sha256='0' * 64, native_sha256='0' * 64))
    # Site job-submit plugins validate the submitter's working directory even
    # for --test-only. Match the real submission's scratch location.
    reply = transport.command('set -e\nexport LC_ALL=C TZ=UTC\nmkdir -p -- ' + scratch + '\ncd -- ' + scratch +
                              '\nprintf %s ' + shlex.quote(script) + ' | sbatch --test-only 2>&1')
    # Slurm writes its estimate to stderr. UTC makes comparisons independent
    # of the workstation and cluster time zones. Missing estimates are normal.
    match = re.search(r'\bto start at (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})', reply)
    if match:
        try:
            return datetime.fromisoformat(match[1]).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            pass
    return None


def allocate_with_limits(context, auth=None):
    """Resolve Auto and recheck live limits at the explicit Allocate action."""
    from truba_session import create_session
    catalog = fetch_catalog(context, auth)
    if context.partition == 'auto':
        options = automatic_options(context, catalog, min_cpus=context.cores)
        last_error = None
        accepted = []
        for order, option in enumerate(options):
            limit = catalog[option.partition]
            full_node = replace(option, cores=limit.max_cpus,
                                memory_mb=limit.suggested_memory(limit.max_cpus))
            # Validate without submitting. Only try a whole node if the site's
            # job-submit plugin rejects the smaller request.
            for choice in dict.fromkeys((option, full_node)):
                try:
                    estimate = test_submission(choice, auth)
                except RuntimeError as error:
                    last_error = error
                    continue
                # A numeric prediction wins over unknown availability. If no
                # predictions exist, retain automatic_options' idle-fit order.
                known = isinstance(estimate, (int, float))
                accepted.append(((not known, estimate if known else 0, order), choice))
                break
        if not accepted:
            raise RuntimeError('Slurm rejected the automatic proposals; no allocation was submitted. ' + str(last_error))
        _, chosen = min(accepted, key=lambda item: item[0])
        return create_session(chosen, auth)
    if context.gpus or any(BASELINES[name].min_gpus for name in context.partition.split(',')):
        raise ValueError('The current RustMech solvers use CPUs. GPU queues require a GPU-enabled solver and cannot be allocated here.')
    partition = candidates(context.partition, context.cores, context.memory_mb, context.walltime, catalog, gpus=context.gpus)
    test_submission(context, auth)
    return create_session(replace(context, partition=partition), auth)

"""Queue policy, scheduler memory caps and one-submission Auto routing."""
from dataclasses import replace
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

from truba_resources import (BASELINES, candidates, parse_catalog, allocate_with_limits, automatic_context,
                             convergence_context, planned_convergence_context,
                             test_submission as validate_submission)
from truba_remote import RemoteContext, slurm_script


class QueuePolicyTests(unittest.TestCase):
    def test_documented_cpu_and_node_limits(self):
        self.assertEqual((BASELINES['hamsi'].min_cpus, BASELINES['hamsi'].max_cpus), (28, 56))
        self.assertEqual(BASELINES['barbun'].min_cpus, 20)
        self.assertEqual(BASELINES['orfoz'].min_cpus, 56)
        self.assertEqual(BASELINES['smp'].max_cpus, 224)
        for queue, cores, ram in [('hamsi', 27, 131072), ('hamsi', 64, 131072),
                                  ('barbun', 41, 131072), ('orfoz', 55, 131072),
                                  ('smp', 225, 131072), ('hamsi', 28, 200 * 1024)]:
            with self.subTest(queue=queue, cores=cores), self.assertRaises(ValueError):
                candidates(queue, cores, ram, '01:00:00')

    def test_auto_only_includes_queues_matching_the_same_request(self):
        self.assertEqual(candidates('auto', 28, 64000, '24:00:00'), 'hamsi,barbun')
        self.assertEqual(candidates('auto', 56, 128000, '24:00:00'), 'hamsi,orfoz')
        self.assertEqual(candidates('auto', 28, 256 * 1024, '24:00:00'), 'barbun')
        with self.assertRaises(ValueError):
            candidates('auto', 8, 131072, '01:00:00')
        with self.assertRaises(ValueError):
            candidates('debug', 8, 131072, '05:00:00')

    def test_live_caps_and_memory_per_cpu_are_enforced(self):
        catalog = parse_catalog(
            'PartitionName=hamsi State=UP MaxTime=3-00:00:00 MaxCPUsPerNode=56 MaxMemPerCPU=3500 DefMemPerCPU=2000',
            'NodeName=hamsi1 Partitions=hamsi CPUEfctv=56 RealMemory=190000 MemSpecLimit=2000')
        limit = catalog['hamsi']
        self.assertTrue(limit.verified)
        self.assertEqual(limit.max_memory_mb, 188000)
        self.assertEqual(limit.memory_ceiling(28), 98000)
        with self.assertRaises(ValueError):
            candidates('hamsi', 28, 131072, '01:00:00', catalog)
        self.assertEqual(candidates('hamsi', 40, 131072, '01:00:00', catalog), 'hamsi')

    def test_heterogeneous_debug_does_not_combine_two_node_capacities(self):
        catalog = parse_catalog('PartitionName=debug State=UP MaxTime=04:00:00',
            'NodeName=orfoz1 Partitions=debug CPUEfctv=112 RealMemory=256000\n'
            'NodeName=barbun1 Partitions=debug CPUEfctv=40 RealMemory=384000')
        with self.assertRaises(ValueError):
            candidates('debug', 112, 300000, '01:00:00', catalog)

    def test_defaults_are_not_mistaken_for_memory_minima_and_down_is_excluded(self):
        catalog = parse_catalog('PartitionName=smp State=UP MaxTime=3-00:00:00 DefMemPerCPU=17000\n'
                                'PartitionName=hamsi State=DOWN MaxTime=3-00:00:00',
                                'NodeName=orkinos1 Partitions=smp CPUTot=224 RealMemory=4100000\n'
                                'NodeName=hamsi1 Partitions=hamsi CPUTot=56 RealMemory=190000')
        self.assertEqual(candidates('smp', 8, 131072, '01:00:00', catalog), 'smp')
        with self.assertRaises(ValueError):
            candidates('auto', 28, 131072, '01:00:00', catalog)

    def test_auto_resolves_live_limits_resources_and_submits_once(self):
        context = RemoteContext(partition='auto')
        with patch('truba_resources.fetch_catalog', return_value=BASELINES), patch('truba_resources.test_submission', return_value=None), patch('truba_session.create_session') as submit:
            allocate_with_limits(context, 'auth')
        submit.assert_called_once_with(replace(context, partition='barbun', cores=20, memory_mb=170000), 'auth')

    def test_auto_prefers_idle_fit_and_honours_solver_cpu_requirement(self):
        catalog = dict(BASELINES)
        catalog['hamsi'] = replace(catalog['hamsi'], idle_capacities=((56, 190000),))
        chosen = automatic_context(RemoteContext(partition='auto'), catalog)
        self.assertEqual((chosen.partition, chosen.cores, chosen.memory_mb), ('hamsi', 28, 95200))
        chosen = automatic_context(RemoteContext(partition='auto'), catalog, min_cpus=64)
        self.assertEqual((chosen.partition, chosen.cores), ('orfoz', 64))

    def test_convergence_sizes_each_script_and_batch_within_queue_limits(self):
        context = RemoteContext(partition='barbun', cores=40, memory_mb=128 * 1024)
        for solver_cpus, cpus, slots in ((8, 40, 2), (16, 40, 2), (32, 40, 1)):
            with self.subTest(solver_cpus=solver_cpus):
                plan = convergence_context(context, BASELINES, solver_cpus, max_jobs=5)
                self.assertEqual((plan.cores, plan.parallel_jobs, plan.memory_mb),
                                 (cpus, slots, context.memory_mb))
                script = slurm_script(plan, 'tester', dict(operation='convergence_level',
                                      settings={'cores': solver_cpus}, source_sha256='0' * 64))
                self.assertIn(f'#SBATCH --cpus-per-task={cpus}\n', script)
                self.assertIn(f'OMP_NUM_THREADS={solver_cpus} RUSTMECH_THREADS={solver_cpus} ', script)
                self.assertIn('#SBATCH --time=00:30:00\n', script)
        with self.assertRaises(ValueError):
            convergence_context(context, BASELINES, 41, max_jobs=5)
        auto = convergence_context(replace(context, partition='auto'), BASELINES, 64, max_jobs=5)
        self.assertEqual((auto.partition, auto.cores, auto.parallel_jobs), ('orfoz', 112, 1))

    def test_single_convergence_level_uses_full_node_in_both_submission_paths(self):
        context = RemoteContext(partition='barbun', cores=20, memory_mb=128 * 1024)
        catalog = dict(BASELINES)
        for live_max in (40, 32):
            catalog['barbun'] = replace(BASELINES['barbun'], max_cpus=live_max)
            auto = convergence_context(context, catalog, solver_cpus=16, max_jobs=1)
            planned = planned_convergence_context(context, catalog, 'barbun', 16, 1, context.memory_mb)
            for script in (auto, planned):
                self.assertEqual((script.cores, script.memory_mb, script.parallel_jobs),
                                 (live_max, context.memory_mb, 1))

    def test_convergence_preserves_requested_time_and_checks_live_queue_limit(self):
        catalog = dict(BASELINES)
        catalog['barbun'] = replace(catalog['barbun'], max_seconds=7 * 3600 + 59)
        for partition in ('auto', 'barbun'):
            with self.subTest(partition=partition):
                context = RemoteContext(partition=partition, cores=40, memory_mb=80000, walltime='01:15:00')
                plan = convergence_context(context, catalog, solver_cpus=16, max_jobs=2)
                self.assertEqual((plan.partition, plan.walltime), ('barbun', '01:15:00'))
        with self.assertRaises(ValueError):
            convergence_context(replace(context, walltime='07:01:00'), catalog, solver_cpus=16, max_jobs=2)

    def test_convergence_requests_a_full_hamsi_node_with_the_selected_solver_threads(self):
        catalog = dict(BASELINES)
        catalog['hamsi'] = replace(catalog['hamsi'], idle_capacities=((56, 191000),))
        for partition in ('hamsi', 'auto'):
            with self.subTest(partition=partition):
                context = RemoteContext(partition=partition, cores=28, memory_mb=95200)
                plan = convergence_context(context, catalog, solver_cpus=16, max_jobs=5)
                self.assertEqual((plan.partition, plan.cores, plan.parallel_jobs), ('hamsi', 56, 2))
                script = slurm_script(plan, 'tester', dict(operation='convergence_level',
                                      settings={'cores': 16}, source_sha256='0' * 64))
                self.assertIn('#SBATCH --cpus-per-task=56\n', script)
                self.assertIn('OMP_NUM_THREADS=16 RUSTMECH_THREADS=16 ', script)

    def test_auto_retries_whole_node_if_job_submit_plugin_rejects_half(self):
        with patch('truba_resources.fetch_catalog', return_value=BASELINES), patch('truba_resources.test_submission', side_effect=[RuntimeError('Use whole node'), None, None, None]), patch('truba_session.create_session') as submit:
            allocate_with_limits(RemoteContext(partition='auto'))
        self.assertEqual(submit.call_args.args[0].cores, 40)
        self.assertEqual(submit.call_count, 1)

    def test_gpu_limits_can_be_validated_but_cpu_workers_cannot_reserve_them(self):
        self.assertEqual(candidates('barbun-cuda', 20, 64000, '01:00:00', gpus=1), 'barbun-cuda')
        with self.assertRaises(ValueError):
            candidates('barbun-cuda', 20, 64000, '01:00:00', gpus=3)
        with patch('truba_resources.fetch_catalog', return_value=BASELINES), patch('truba_session.create_session') as submit:
            with self.assertRaises(ValueError):
                allocate_with_limits(RemoteContext(partition='barbun-cuda', cores=20, gpus=1, memory_mb=64000))
        submit.assert_not_called()

    def test_preflight_uses_test_only_without_allocating(self):
        with patch('truba_remote.RemoteTransport') as transport:
            transport.return_value.username = 'tester'
            transport.return_value.command.return_value = ''
            validate_submission(RemoteContext())
        command = transport.return_value.command.call_args.args[0]
        self.assertIn('sbatch --test-only', command)
        self.assertNotIn('sbatch --parsable', command)
        self.assertIn('mkdir -p -- /arf/scratch/tester/RustMech\ncd -- /arf/scratch/tester/RustMech\n', command)
        self.assertIn('LC_ALL=C TZ=UTC', command)
        self.assertIn('sbatch --test-only 2>&1', command)
        self.assertLess(command.index('cd -- '), command.index('sbatch --test-only'))

    def test_job_submit_rejection_never_creates_manual_or_auto_session(self):
        for context in (RemoteContext(), RemoteContext(partition='auto')):
            with patch('truba_resources.fetch_catalog', return_value=BASELINES), patch('truba_resources.test_submission', side_effect=RuntimeError('Rejected')), patch('truba_session.create_session') as submit:
                with self.assertRaises(RuntimeError):
                    allocate_with_limits(context)
                submit.assert_not_called()

    def test_live_gpu_capacity_and_site_default_memory_are_not_minima(self):
        catalog = parse_catalog('PartitionName=barbun-cuda State=UP MaxTime=3-00:00:00 DefMemPerCPU=8500 MaxMemPerCPU=9500',
                                'NodeName=barbun1 Partitions=barbun-cuda CPUTot=40 RealMemory=380000 CfgTRES=cpu=40,mem=380000M,gres/gpu=2')
        self.assertEqual(catalog['barbun-cuda'].max_gpus, 2)
        self.assertIsNone(catalog['barbun-cuda'].min_memory_mb)
        self.assertEqual(candidates('barbun-cuda', 20, 1536, '01:00:00', catalog, gpus=1), 'barbun-cuda')

    def test_auto_compares_all_cpu_queue_estimates_and_submits_only_earliest(self):
        catalog = dict(BASELINES)
        catalog['hamsi'] = replace(catalog['hamsi'], idle_capacities=((56, 190000),))
        estimates = {'hamsi': 300, 'barbun': 100, 'orfoz': 200}
        checked = []
        def estimate(context, auth):
            checked.append(context)
            return estimates[context.partition]
        with patch('truba_resources.fetch_catalog', return_value=catalog), patch(
                'truba_resources.test_submission', side_effect=estimate), patch(
                'truba_session.create_session') as submit:
            allocate_with_limits(RemoteContext(partition='auto'), 'auth')
        self.assertEqual({c.partition for c in checked}, set(estimates))
        self.assertTrue(all(c.gpus == 0 for c in checked))
        submit.assert_called_once()
        self.assertEqual(submit.call_args.args[0].partition, 'barbun')

    def test_known_start_estimate_is_used_when_other_queues_have_no_prediction(self):
        def estimate(context, auth):
            return 200 if context.partition == 'orfoz' else None
        with patch('truba_resources.fetch_catalog', return_value=BASELINES), patch(
                'truba_resources.test_submission', side_effect=estimate), patch(
                'truba_session.create_session') as submit:
            allocate_with_limits(RemoteContext(partition='auto'))
        submit.assert_called_once()
        self.assertEqual(submit.call_args.args[0].partition, 'orfoz')

    def test_unknown_estimates_fall_back_to_idle_fit_and_rejection_is_excluded(self):
        catalog = dict(BASELINES)
        catalog['hamsi'] = replace(catalog['hamsi'], idle_capacities=((56, 190000),))
        def estimate(context, auth):
            if context.partition == 'hamsi':
                raise RuntimeError('Account cannot use this queue')
            return None
        with patch('truba_resources.fetch_catalog', return_value=catalog), patch(
                'truba_resources.test_submission', side_effect=estimate), patch(
                'truba_session.create_session') as submit:
            allocate_with_limits(RemoteContext(partition='auto'))
        submit.assert_called_once()
        self.assertEqual(submit.call_args.args[0].partition, 'barbun')

    def test_preflight_parses_stderr_estimate_and_missing_or_invalid_dates(self):
        for value, expected in (
                ('sbatch: Job 123 to start at 2026-11-17T10:30:00 using 28 processors',
                 datetime(2026, 11, 17, 10, 30, tzinfo=timezone.utc).timestamp()),
                ('sbatch: Job 123 to start at 2026-99-17T10:30:00', None),
                ('sbatch: Job 123 can run', None)):
            with self.subTest(value=value), patch('truba_remote.RemoteTransport') as transport:
                transport.return_value.username = 'tester'
                transport.return_value.command.return_value = value
                self.assertEqual(validate_submission(RemoteContext()), expected)

    def test_failed_live_refresh_never_submits(self):
        with patch('truba_resources.fetch_catalog', side_effect=RuntimeError('Offline')), patch('truba_session.create_session') as submit:
            with self.assertRaises(RuntimeError):
                allocate_with_limits(RemoteContext())
        submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()

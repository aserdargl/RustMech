"""Queue policy, scheduler memory caps and one-submission Auto routing."""
from dataclasses import replace
import unittest
from unittest.mock import patch

from truba_resources import BASELINES, candidates, parse_catalog, allocate_with_limits, automatic_context, test_submission as validate_submission
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
        with patch('truba_resources.fetch_catalog', return_value=BASELINES), patch('truba_resources.test_submission'), patch('truba_session.create_session') as submit:
            allocate_with_limits(context, 'auth')
        submit.assert_called_once_with(replace(context, partition='barbun', cores=20, memory_mb=170000), 'auth')

    def test_auto_prefers_idle_fit_and_honours_solver_cpu_requirement(self):
        catalog = dict(BASELINES)
        catalog['hamsi'] = replace(catalog['hamsi'], idle_capacities=((56, 190000),))
        chosen = automatic_context(RemoteContext(partition='auto'), catalog)
        self.assertEqual((chosen.partition, chosen.cores, chosen.memory_mb), ('hamsi', 28, 95200))
        chosen = automatic_context(RemoteContext(partition='auto'), catalog, min_cpus=64)
        self.assertEqual((chosen.partition, chosen.cores), ('orfoz', 64))

    def test_auto_retries_whole_node_if_job_submit_plugin_rejects_half(self):
        with patch('truba_resources.fetch_catalog', return_value=BASELINES), patch('truba_resources.test_submission', side_effect=[RuntimeError('Use whole node'), None]), patch('truba_session.create_session') as submit:
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
            validate_submission(RemoteContext())
        command = transport.return_value.command.call_args.args[0]
        self.assertIn('sbatch --test-only', command)
        self.assertNotIn('sbatch --parsable', command)

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

    def test_failed_live_refresh_never_submits(self):
        with patch('truba_resources.fetch_catalog', side_effect=RuntimeError('Offline')), patch('truba_session.create_session') as submit:
            with self.assertRaises(RuntimeError):
                allocate_with_limits(RemoteContext())
        submit.assert_not_called()


if __name__ == '__main__':
    unittest.main()

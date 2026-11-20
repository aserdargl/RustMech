"""Remote job protocol, archive/provenance checks, and actual isolated mesh worker."""
from dataclasses import asdict,replace
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from model import ROOT,Settings
from truba_remote import (RemoteContext,analysis_code,make_source_bundle,run_remote_job,run_remote_level,run_remote_level_pair,safe_extract,sync_background_analysis,
                          slurm_script,load_remote_preview,RemoteTransport)


class FakeTransport:
    corrupt_hash = False
    wrong_request = False
    instances = []

    def __init__(self,context,auth=None,record=None):
        self.username = "tester"
        self.delegate = object()
        self.request = json.loads((Path(record["local_directory"])/"request.json").read_text()) if record else None
        self.server_job = record.get("job_id") if record else None
        self.put_names = []
        self.submissions = 0
        self.instances.append(self)

    def command(self,script):
        if "RUSTMECH_JOB_ID" in script:
            if not self.server_job:
                from remote_operations import ANALYSIS_OPERATIONS
                independent = self.request.get("background") or self.request.get("operation") in ANALYSIS_OPERATIONS
                self.server_job = str(320 + len(self.instances)) if independent else "321"
                self.submissions += 1
            return "RUSTMECH_JOB_ID="+self.server_job+"\n"
        if "RUSTMECH_QUEUE" in script: return "RUSTMECH_QUEUE=\n"
        if "RUSTMECH_ACCOUNTING" in script: return "RUSTMECH_ACCOUNTING=COMPLETED|0:0\n"
        if "RUSTMECH_RESULT_SHA" in script:
            return "RUSTMECH_RESULT_SHA="+("0"*64 if self.corrupt_hash else self.archive_hash)+"  result.tar.gz\n"
        return ""

    def put(self,local,remote):
        self.put_names.append(Path(local).name)
        if Path(local).name=="request.json": self.request = json.loads(Path(local).read_text())

    def get(self,remote,local):
        if remote.endswith("result.tar.gz"):
            result = dict(request_id="wrong" if self.wrong_request else self.request["request_id"],
                          source_sha256=self.request["source_sha256"],slurm_job_id=self.server_job,
                          operation=self.request["operation"],remote_output="/fake/output",capabilities={"mesh":True})
            data = json.dumps(result).encode()
            with tarfile.open(local,"w:gz") as archive:
                member = tarfile.TarInfo("output/result.json")
                member.size = len(data)
                archive.addfile(member,io.BytesIO(data))
            with open(local,"rb") as stream: self.archive_hash = hashlib.file_digest(stream,"sha256").hexdigest()
        else: Path(local).write_text("")


class RemoteProtocolTests(unittest.TestCase):
    def test_convergence_peak_ram_uses_each_slurm_step_and_ignores_other_jobs(self):
        from truba_remote import convergence_peak_ram

        accounting = ('321|RM-JOB|\n321.batch|batch|1024M\n'
                      '321.9|mesh-2|3145728K\n321.4|mesh-1|2.5G\n'
                      '321.5|mesh-1|200M\n999.1|mesh-1|200G\n')
        self.assertEqual(convergence_peak_ram(accounting, '321', 2), [2560, 3072])
        self.assertEqual(convergence_peak_ram(accounting, '321', 1), [1024])
        self.assertEqual(convergence_peak_ram('321.1|mesh-1|\n', '321', 2), [None, None])

    def test_convergence_pair_shares_resources_and_keeps_a_successful_sibling(self):
        from convergence_execution import Level
        from truba_remote import SlurmOutOfMemory

        first = Settings(solver_backend='kratos', cores=16, mesh_resolution=.2)
        second = replace(first, mesh_resolution=.15)
        context = RemoteContext(partition='hamsi', cores=56, memory_mb=160001)
        result = {'levels': [{'level': asdict(Level(first, status='completed'))}, {'out_of_memory': True}]}
        with patch('truba_remote.run_remote_job', return_value=(result, self.root)) as execute:
            levels = run_remote_level_pair([first, second], context, 1000, [0])
        steps = execute.call_args.args[0]['convergence_steps']
        self.assertEqual([(step['settings']['cores'], step['memory_mb']) for step in steps],
                         [(28, 80000), (28, 80001)])
        self.assertEqual(levels[0].status, 'completed')
        self.assertIsInstance(levels[1], SlurmOutOfMemory)
        self.assertEqual(levels[1].memory_mb, 80001)

    def test_convergence_worker_launches_both_slurm_steps_before_waiting(self):
        import remote_worker

        output = self.root / 'output'
        request = dict(operation='convergence_level', remote_context={'cores': 56, 'memory_mb': 120000},
                       convergence_steps=[{'settings': {'cores': 28}, 'memory_mb': 60000}] * 2)
        processes = []

        def launch(args, *, cwd, env, stdout, stderr):
            i = len(processes)
            self.assertIn('--exact', args)
            self.assertIn('--cpus-per-task=28', args)
            self.assertIn('--mem=60000M', args)
            self.assertEqual(env['OMP_NUM_THREADS'], '28')
            child = json.loads((cwd / 'request.json').read_text())
            self.assertNotIn('convergence_steps', child)
            self.assertEqual(child['remote_context']['memory_mb'], 60000)
            if i:
                stderr.write('srun: error: task 0: Out Of Memory\n')
            else:
                remote_worker._write(cwd / 'output' / 'result.json', {'level': {'status': 'completed'}})
            process = MagicMock()
            process.__enter__.return_value = process
            def wait():
                self.assertEqual(len(processes), 2)
                return 1 if i else 0
            process.wait.side_effect = wait
            processes.append(process)
            return process

        with patch('remote_worker.subprocess.Popen', side_effect=launch), patch('remote_worker._stage'):
            result = remote_worker._run_convergence_pair(request, output)
        self.assertEqual(result['levels'][0], {'level': {'status': 'completed'}})
        self.assertTrue(result['levels'][1]['out_of_memory'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        FakeTransport.instances=[]
        FakeTransport.corrupt_hash=False
        FakeTransport.wrong_request=False
        self.patches = [patch("truba_remote.RemoteTransport",FakeTransport),
                        patch("truba_remote.state_directory",return_value=self.root/"state")]
        for item in self.patches: item.start()

    def tearDown(self):
        for item in self.patches: item.stop()
        self.temp.cleanup()

    def test_context_rejects_shell_injection_and_unsafe_runtime(self):
        for values in (dict(partition="debug; whoami"),dict(runtime_python="/usr/bin/python"),
                       dict(runtime_python="~/../other/bin/python"),dict(foam_module="foo\nbar"),
                       dict(cores=True),dict(walltime="00:99:00")):
            with self.subTest(values=values),self.assertRaises(ValueError): replace(RemoteContext(),**values).validate()

    def test_convergence_level_uses_its_share_of_the_resource_session(self):
        settings = Settings(solver_backend="kratos", cores=20, automatic_cores=True)
        context = RemoteContext(cores=20, memory_mb=10000, parallel_jobs=5, session_id="321")
        with patch("truba_remote.run_remote_job", return_value=(None, self.root)) as submit:
            self.assertIsNone(run_remote_level(settings, context, 1000, [0]))
        request, submitted_context, *_ = submit.call_args.args
        self.assertEqual(request["settings"]["cores"], 4)
        self.assertFalse(request["settings"]["automatic_cores"])
        self.assertEqual(submitted_context, context)
        self.assertIn("OMP_NUM_THREADS=4 RUSTMECH_THREADS=4", slurm_script(
            context, "tester", dict(request, source_sha256="0" * 64, native_sha256="0" * 64)))

    def test_direct_convergence_level_requests_full_per_job_resources(self):
        settings = Settings(solver_backend="kratos", cores=8)
        context = RemoteContext(cores=8, memory_mb=4000, parallel_jobs=5)
        with patch("truba_remote.run_remote_job", return_value=(None, self.root)) as submit:
            run_remote_level(settings, context, 1000, [0])
        request, submitted_context, *_ = submit.call_args.args
        self.assertFalse(submitted_context.session_id)
        self.assertEqual(request["settings"]["cores"], 8)
        script = slurm_script(context, "tester", dict(request, source_sha256="0" * 64, native_sha256="0" * 64))
        self.assertIn("#SBATCH --cpus-per-task=8", script)
        self.assertIn("#SBATCH --mem=4000M", script)
        self.assertIn("OMP_NUM_THREADS=8 RUSTMECH_THREADS=8", script)

    def test_direct_convergence_submits_a_named_script_and_records_the_slurm_id(self):
        class SubmissionTransport(FakeTransport):
            def command(self, script):
                if "sbatch --parsable" in script:
                    self.submission_script = script
                return super().command(script)

        request = {"operation": "convergence_level", "settings": asdict(Settings(solver_backend="kratos", cores=16))}
        with patch("truba_remote.RemoteTransport", SubmissionTransport):
            _, directory = run_remote_job(request, RemoteContext(cores=20), directory=self.root / "level")
        record = json.loads((directory / "remote_job.json").read_text())
        name = f"{analysis_code(record)}.slurm"
        transport = FakeTransport.instances[-1]
        self.assertIn(f"sbatch --parsable {name}", transport.submission_script)
        self.assertIn(name, transport.put_names)
        self.assertEqual(record["script_name"], name)
        self.assertEqual(record["solver_cores"], 16)
        self.assertEqual(record["job_id"], "321")

    def test_convergence_classifies_slurm_memory_errors_before_retrieving_archive(self):
        from truba_remote import SlurmOutOfMemory

        for accounting, stderr in (("FAILED|1:0", "Detected 1 oom_kill event\nsrun: Out Of Memory"),
                                   ("OUT_OF_MEMORY|0:125", "")):
            with self.subTest(accounting=accounting):
                class MemoryFailureTransport(FakeTransport):
                    def command(self, script):
                        if "RUSTMECH_ACCOUNTING" in script:
                            return f"RUSTMECH_ACCOUNTING={accounting}\n"
                        return super().command(script)

                    def get(self, remote, local):
                        if remote.endswith("result.tar.gz"):
                            raise AssertionError("Must not download an unusable result archive")
                        Path(local).write_text(stderr if remote.endswith("slurm.err") else "")

                request = {"operation": "convergence_level", "settings": asdict(Settings(solver_backend="kratos"))}
                folder = self.root / accounting.split("|")[0]
                with patch("truba_remote.RemoteTransport", MemoryFailureTransport), self.assertRaises(SlurmOutOfMemory):
                    run_remote_job(request, RemoteContext(memory_mb=68000, cores=20), directory=folder)
                record = json.loads((folder / "remote_job.json").read_text())
                self.assertEqual(record["state"], "OUT_OF_MEMORY")
                self.assertEqual(record["slurm_status"], accounting)
                self.assertEqual(record["failure_memory_mb"], 68000)

    def test_submission_download_checksum_and_resume_do_not_resubmit(self):
        result,directory = run_remote_job(dict(operation="probe"),RemoteContext(),directory=self.root/"job")
        self.assertTrue(result["capabilities"]["mesh"])
        first = FakeTransport.instances[-1]
        self.assertEqual(first.submissions,1)
        self.assertEqual(first.put_names,["engine.tar.gz","request.json","run.slurm"])
        record = json.loads((directory/"remote_job.json").read_text())
        self.assertEqual(record["state"],"downloaded")
        command = FakeTransport.command

        def refuse_submission_lookup(transport, script):
            self.assertNotIn("RUSTMECH_JOB_ID", script)
            return command(transport, script)

        with patch.object(FakeTransport, "command", refuse_submission_lookup):
            run_remote_job({},RemoteContext(),resume_record=record)
        self.assertEqual(FakeTransport.instances[-1].submissions,0)
        self.assertEqual(FakeTransport.instances[-1].put_names,[])

    def test_convergence_submits_separately_named_slurm_scripts(self):
        request = dict(operation="convergence_level", settings=dict(solver_backend="kratos"))
        records = [run_remote_job(request, RemoteContext(), directory=self.root / str(i), submit_only=True)
                   for i in range(2)]
        self.assertNotEqual(records[0]["job_id"], records[1]["job_id"])
        self.assertNotEqual(records[0]["script_name"], records[1]["script_name"])
        for record, transport in zip(records, FakeTransport.instances[-2:]):
            name = record["script_name"]
            script = Path(record["local_directory"]) / name
            self.assertIn("#SBATCH --job-name=" + script.stem, script.read_text())
            self.assertIn(name, transport.put_names)

    def test_solves_get_independent_jobs_even_with_an_existing_session(self):
        context = RemoteContext(partition='barbun', cores=40, memory_mb=80000,
                                parallel_jobs=2, session_id='900')
        records = []
        for operation in ('mechanical', 'fft_solve', 'flow_solve', 'thermal_solve', 'doe_solve', 'convergence_level'):
            with self.subTest(operation=operation):
                request = dict(operation=operation, settings=dict(solver_backend='kratos', cores=16))
                directory = self.root / operation
                run_remote_job(request, context, directory=directory)
                record = json.loads((directory / 'remote_job.json').read_text())
                self.assertFalse(record['context']['session_id'])
                self.assertNotIn('session', record)
                self.assertNotEqual(record['job_id'], '900')
                script = directory / record['script_name']
                self.assertIn(script.name, FakeTransport.instances[-1].put_names)
                if operation == 'doe_solve':
                    self.assertIn('#SBATCH --cpus-per-task=20', script.read_text())
                records.append(record)
        self.assertEqual(len({record['job_id'] for record in records}), 6)
        self.assertEqual(len({record['script_name'] for record in records}), 6)

    def test_background_analyses_upload_inputs_and_poll_independently(self):
        import base64
        geometry = self.root / "specimen.vtp"
        geometry.write_bytes(b"test geometry")
        settings = dict(solver_backend="kratos", imported_geometry_path=str(geometry))
        request = dict(operation="mechanical", settings=settings)
        context = RemoteContext(study_name="A çalışması")
        first = run_remote_job(request, context, directory=self.root / "first", submit_only=True)
        second = run_remote_job(request, context, directory=self.root / "second", submit_only=True)
        self.assertNotEqual(analysis_code(first), analysis_code(second))
        self.assertNotEqual(first["job_id"], second["job_id"])
        self.assertEqual(first["study_name"], second["study_name"])
        self.assertEqual(first["study_name"], "A çalışması")
        self.assertEqual(first["state"], "submitted")
        self.assertEqual(base64.b64decode(FakeTransport.instances[0].request["imported_geometry_vtp"]), geometry.read_bytes())
        self.assertIn("#SBATCH --job-name=" + analysis_code(first),
                      (self.root / 'first' / first['script_name']).read_text())
        self.assertFalse((self.root / "first/output/result.json").exists())

        command = FakeTransport.command
        def queue(transport, script):
            if "RUSTMECH_QUEUE" in script:
                if transport.request["request_id"] == first["request_id"]:
                    return "RUSTMECH_QUEUE=RUNNING|None\n"
                return "RUSTMECH_QUEUE=COMPLETED|None\n"
            return command(transport, script)

        with patch.object(FakeTransport, "command", queue):
            self.assertIsNone(sync_background_analysis(first, None))
            result, folder = sync_background_analysis(second, None)
        self.assertEqual(json.loads((self.root / "first/remote_job.json").read_text())["state"], "RUNNING|None")
        self.assertEqual(json.loads((folder / "remote_job.json").read_text())["state"], "downloaded")
        self.assertEqual(result["request_id"], second["request_id"])
        self.assertTrue(all(transport.submissions == 0 and not transport.put_names for transport in FakeTransport.instances[2:]))

    def test_batch_creates_results_in_selected_folder_before_submission(self):
        commands = []
        command = FakeTransport.command

        def capture(transport, script):
            commands.append(script)
            return command(transport, script)

        context = RemoteContext(partition='smp', cores=4)
        request = dict(operation='doe_solve', settings=dict(solver_backend='kratos'),
                       staged_geometry=dict(username='tester', sha256='a' * 64, directory='study/first batch'))
        directory = self.root / 'batch-job'
        with patch.object(FakeTransport, 'command', capture):
            run_remote_job(request, context, directory=directory)

        self.assertIn('realpath -e -- "$project"/\'study/first batch\'', commands[0])
        self.assertIn('results="$batch/results"', commands[0])
        record = json.loads((directory / 'remote_job.json').read_text())
        self.assertIn(f"sbatch --parsable {record['script_name']}", commands[1])
        script = (directory / record['script_name']).read_text()
        self.assertIn('#SBATCH --partition=smp', script)
        self.assertIn('results="$batch/results"', script)
        self.assertIn('RUSTMECH_BATCH_OUTPUT="$results/${PWD##*/}"', script)

    def test_mismatched_request_or_transfer_never_becomes_downloaded(self):
        for key in ("corrupt_hash","wrong_request"):
            setattr(FakeTransport,key,True)
            directory = self.root/key
            with self.assertRaises(RuntimeError):
                run_remote_job(dict(operation="probe"),RemoteContext(),directory=directory)
            record = json.loads((directory/"remote_job.json").read_text())
            self.assertNotEqual(record["state"],"downloaded")
            setattr(FakeTransport,key,False)

    def test_cancelled_marker_stops_before_submission(self):
        marker = self.root/"marker.json"
        marker.with_suffix(".cancel").touch()
        with patch("truba_remote.cancel_remote") as cancel:
            from concurrent.futures import CancelledError
            with self.assertRaises(CancelledError):
                run_remote_job(dict(operation="probe"),RemoteContext(),marker=marker,directory=self.root/"cancel")
            cancel.assert_called_once()
        self.assertEqual(FakeTransport.instances[-1].submissions,0)

    def test_job_list_cancellation_is_not_overwritten_by_monitor(self):
        from concurrent.futures import CancelledError
        from truba_jobs import write_record
        original = FakeTransport.command
        directory = self.root / 'external-cancel'

        def command(transport, script):
            if 'RUSTMECH_QUEUE' in script:
                path = directory / 'remote_job.json'
                record = json.loads(path.read_text())
                write_record(path, dict(record, state='cancelled'))
                return 'RUSTMECH_QUEUE=RUNNING|None\n'
            return original(transport, script)

        with patch.object(FakeTransport, 'command', command):
            with self.assertRaises(CancelledError):
                run_remote_job(dict(operation='probe'), RemoteContext(), directory=directory)
        self.assertEqual(json.loads((directory / 'remote_job.json').read_text())['state'], 'cancelled')

    def test_archive_rejects_traversal_and_links(self):
        for name,link in (("../outside",False),("/absolute",False),("output/link",True)):
            archive_path = self.root/"bad.tar.gz"
            with tarfile.open(archive_path,"w:gz") as archive:
                member = tarfile.TarInfo(name)
                if link: member.type,member.linkname = tarfile.SYMTYPE,"/tmp/outside"
                archive.addfile(member)
            with self.subTest(name=name),self.assertRaises(ValueError): safe_extract(archive_path,self.root/"extract")

    def test_winscp_transmits_multiline_program_as_one_batch_line(self):
        from unittest.mock import MagicMock
        transport=object.__new__(RemoteTransport)
        transport.delegate=MagicMock()
        script="printf 'one\\n'\nprintf 'two\\n'"
        transport.command(script)
        commands=transport.delegate.execute.call_args.args[0]
        self.assertEqual(len(commands),1)
        self.assertNotIn("\n",commands[0])
        self.assertIn("base64 -d",commands[0])
        self.assertTrue(commands[0].startswith("call env TERM=dumb bash -lc "))

    def test_ssh_sets_term_before_login_startup(self):
        from unittest.mock import MagicMock
        from truba_jobs import SSHJobs
        import shlex
        transport = object.__new__(RemoteTransport)
        transport.delegate = MagicMock(spec=SSHJobs)
        client = transport.delegate.connect.return_value.__enter__.return_value
        out, err = MagicMock(), MagicMock()
        out.read.return_value = b'ok'
        err.read.return_value = b''
        out.channel.recv_exit_status.return_value = 0
        client.exec_command.return_value = (None, out, err)
        script = "printf '%s' 'hello world'"
        self.assertEqual(transport.command(script), 'ok')
        client.exec_command.assert_called_once_with(
            'env TERM=dumb bash -lc ' + shlex.quote(script), timeout=60)

    def test_bundle_excludes_results_settings_and_binary_windows_kernels(self):
        path = self.root/"source.tar.gz"
        sha,native = make_source_bundle(path)
        self.assertEqual(len(sha),64)
        self.assertEqual(len(native),64)
        with tarfile.open(path) as archive:
            names = archive.getnames()
        self.assertIn("remote_worker.py",names)
        for module in ("mechanical_analysis", "mechanical_results", "convergence_execution", "doe_execution"):
            self.assertIn(f"{module}.py", names)
        self.assertIn("tools/truba/prepare-remote.sh",names)
        self.assertFalse(any("settings.json" in name or name.endswith(".dll") or name.startswith("results/") for name in names))
        script = slurm_script(RemoteContext(),"tester",dict(operation="preview",source_sha256=sha,native_sha256=native))
        self.assertIn("#SBATCH --cpus-per-task=1", script)
        self.assertIn("#SBATCH --mem=4096M", script)
        self.assertIn("OMP_NUM_THREADS=1", script)
        self.assertIn("#SBATCH",script)
        self.assertIn("remote_worker.py",script)
        self.assertNotIn("sbatch",script)


class ComputeWorkerTests(unittest.TestCase):
    def test_real_preview_worker_generates_mesh_and_serializes_vtk_without_desktop(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            native = directory/"runtime/native"
            native.mkdir(parents=True)
            library = native/"librustmech_core.so"
            # Windows exercises the exact worker with its matching native ABI;
            # this does not claim to be a live TRUBA/SLURM integration test.
            source = ROOT/"native/bin"/("rustmech_core.dll" if os.name=="nt" else "librustmech_core.so")
            if not source.exists(): self.skipTest("Native test kernel is not built")
            shutil.copy2(source,library)
            stamp = "a"*64
            library.with_suffix(".json").write_text(json.dumps(dict(native_sha256=stamp)))
            settings = Settings(size=1,cell_size=1,mesh_resolution=.2,solid_fraction=40)
            request = dict(operation="preview",settings=asdict(settings),request_id="worker-test",source_sha256="b"*64,native_sha256=stamp)
            (directory/"request.json").write_text(json.dumps(request))
            env = {**os.environ,"RUSTMECH_CORE_LIBRARY":str(library),"SLURM_JOB_ID":"99","QT_QPA_PLATFORM":"offscreen"}
            result = subprocess.run([sys.executable,str(ROOT/"remote_worker.py"),"request.json"],cwd=directory,env=env,capture_output=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stderr.decode(errors="replace"))
            preview = load_remote_preview(directory/"output/preview")
            self.assertGreater(preview.mesh.grid.n_cells,0)
            self.assertGreater(preview.surface.n_cells,0)
            self.assertAlmostEqual(preview.mesh.grid.bounds[5],1)
            self.assertEqual(json.loads((directory/"output/result.json").read_text())["slurm_job_id"],"99")


if __name__=="__main__": unittest.main()

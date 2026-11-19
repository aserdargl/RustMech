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
from unittest.mock import patch

from model import ROOT,Settings
from truba_remote import (RemoteContext,analysis_code,make_source_bundle,run_remote_job,safe_extract,sync_background_analysis,
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
                self.server_job = str(320 + len(self.instances)) if self.request.get("background") else "321"
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

    def test_submission_download_checksum_and_resume_do_not_resubmit(self):
        result,directory = run_remote_job(dict(operation="probe"),RemoteContext(),directory=self.root/"job")
        self.assertTrue(result["capabilities"]["mesh"])
        first = FakeTransport.instances[-1]
        self.assertEqual(first.submissions,1)
        self.assertEqual(first.put_names,["engine.tar.gz","request.json","run.slurm"])
        record = json.loads((directory/"remote_job.json").read_text())
        self.assertEqual(record["state"],"downloaded")
        run_remote_job({},RemoteContext(),resume_record=record)
        self.assertEqual(FakeTransport.instances[-1].submissions,0)
        self.assertEqual(FakeTransport.instances[-1].put_names,[])

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
        self.assertIn("#SBATCH --job-name=" + analysis_code(first), (self.root / "first/run.slurm").read_text())
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

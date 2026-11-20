"""Focused regression coverage for ESEN file loading and job lifecycle."""

import base64
from concurrent.futures import Future
from io import BytesIO
import os
from pathlib import Path, PurePosixPath
from stat import S_IFDIR, S_IFREG
import sys
import tarfile
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import ANY, patch

from PySide6.QtCore import QDateTime, QItemSelectionModel, QMimeData, QObject, QPoint, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QPushButton, QScrollArea, QSplitter

import esen_jobs as jobs
from esen_ai_prompt import ESEN_AI_PROMPT
from esen_workspace import EsenWorkspace
from truba_jobs import RemoteAuth
from ui_language import LANGUAGE_KEY, LanguageManager
from window_state import WindowStateStore


class FakeTruba(QObject):
    connection_changed = Signal(bool)
    auth = None

    def analysis_auth(self):
        return self.auth


class EsenTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.history = self.root / "history"
        self.entry = "run example.txt"
        (self.source / self.entry).write_text("# LAMMPS example\n", encoding="utf-8")
        (self.source / "data").mkdir()
        (self.source / "data/cell.lmp").write_text("cell", encoding="utf-8")
        self.files = (self.source, self.entry, ("data/cell.lmp",), False)
        self.auth = RemoteAuth("test-account", "unused")

    def new_job(self, target="remote"):
        return jobs.new_job(self.source, self.entry, target, 2, parent=self.history)

    def script(self):
        return jobs.remote_script(self.entry, 2, "debug", self.auth.username, 512,
                                  "00:02:00", jobs.DEFAULT_MODULE, "lmp", "mpirun")

    def test_restartable_submission_uses_the_seventieth_hour_of_a_72_hour_job(self):
        args = self.entry, 56, "orfoz", self.auth.username, 512
        options = dict(module="", executable="lmp", launcher="mpirun", resume_input="restart case.in")
        script = jobs.remote_script(*args, walltime="72:00:00", **options)
        self.assertIn("#SBATCH --signal=B:USR1@7200\n", script)
        self.assertIn("resume_input='restart case.in'", script)
        self.assertIn('#SBATCH --requeue', script)
        self.assertNotIn("#SBATCH --signal", jobs.remote_script(*args, walltime="01:00:00", **options))
        self.assertNotIn("#SBATCH --requeue", self.script())

    def test_restartable_refresh_tracks_the_successor_and_resubmits_the_same_directory(self):
        directory, record = self.new_job()
        jobs.save_job(directory, record, username=self.auth.username, resume_input="restart.in", job_id="123")
        with patch("esen_jobs.RemoteTransport") as factory:
            transport = factory.return_value
            transport.command.side_effect = [
                "JOB_ID=456\nAUTO_RESUMED=0\ncompleted=0\ncancelled=0\nHAS_CHECKPOINT=1\n"
                "QUEUE=\nACCT=TIMEOUT|0:0|\nstdout.log=\nstderr.log=\n",
                "JOB_ID=789\n",
            ]
            jobs.refresh_remote(directory, record, self.auth)
            self.assertEqual((record["job_id"], record["state"], record["has_checkpoint"]), ("456", "Failed", True))
            jobs.resume_remote(directory, record, self.auth)
            command = transport.command.call_args.args[0]
            self.assertIn(directory.name, command)
            self.assertIn("submit_segment resume", command)
            self.assertEqual((record["job_id"], record["state"]), ("789", "Submitted"))
            transport.put.assert_not_called()

    def test_automatic_timeout_resume_remains_in_background_tracking(self):
        app = QApplication.instance() or QApplication([])
        directory, record = self.new_job()
        jobs.save_job(directory, record, username=self.auth.username, resume_input="restart.in",
                      job_id="123", state="Running", elapsed_seconds=259200, slurm={"remaining": "0:00"})
        truba = FakeTruba()
        truba.auth = self.auth
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(truba, history=self.history)
        self.addCleanup(workspace.dispose)

        with (patch("esen_jobs.RemoteTransport") as factory, patch("esen_jobs.remote_files") as collect):
            transport = factory.return_value
            previous_log = base64.b64encode(b"ESEN_STEP=1000\n").decode("ascii")
            transport.command.return_value = (
                "JOB_ID=456\nAUTO_RESUMED=1\ncompleted=0\ncancelled=0\nHAS_CHECKPOINT=1\n"
                f"QUEUE=\nACCT=\nstdout.log={previous_log}\nstderr.log=\n"
            )
            workspace._poll_remote()
            workspace.remote_task[1].result(timeout=5)
            workspace._tick()
            collect.assert_not_called()

        record = jobs.read_job(directory)
        self.assertEqual((record["job_id"], record["state"]), ("456", "Submitted"))
        self.assertEqual((record["elapsed_seconds"], record["script_step"], record["slurm"]), (None, "", {}))
        self.assertEqual(workspace._active_truba_jobs[directory], self.auth.username)
        self.assertIn("Continuation submitted.", record["detail"])

    def test_local_results_do_not_replace_saved_inputs(self):
        directory, record = self.new_job("local")
        command = [sys.executable, "-c",
                   "from pathlib import Path; Path('data/cell.lmp').write_text('result'); print('finished')"]
        jobs.run_local(directory, record, self.files, command, Event())
        self.assertEqual(jobs.read_job(directory)["state"], "Completed")
        self.assertGreaterEqual(jobs.read_job(directory)["elapsed_seconds"], 0)
        self.assertEqual((directory / "work/data/cell.lmp").read_text(), "result")
        self.assertEqual((directory / "inputs/data/cell.lmp").read_text(), "cell")
        self.assertIn("finished", jobs.log_tail(directory / "stdout.log"))

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_local_memory_cap_is_shared_with_child_processes(self):
        directory, record = self.new_job("local")
        child = "blocks = [bytearray(32 * 1024**2) for _ in range(2)]"
        code = (
            "import subprocess, sys; own = bytearray(64 * 1024**2); "
            f"child = subprocess.run([sys.executable, '-c', {child!r}], capture_output=True); "
            "assert b'MemoryError' in child.stderr, child; print('shared cap enforced')"
        )
        jobs.run_local(directory, record, self.files, [sys.executable, "-c", code], Event(), memory_mib=128)
        result = jobs.read_job(directory)
        self.assertEqual(result["state"], "Completed",
                         result.get("detail", "") + jobs.log_tail(directory / "stderr.log"))
        self.assertIn("shared cap enforced", jobs.log_tail(directory / "stdout.log"))

    def test_path_validation(self):
        for value in ("../../file", "AUX.log", "C:/data", "a\\b", "foo./bar"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                jobs.relative_file(value)

    def test_attach_run_and_download_published_result(self):
        source = "/arf/scratch/test-account/ESEN/CFG/campaign-1/NiTi/TK"
        directory, remote = jobs.remote_run_directory(self.history, source + "/", self.auth)
        self.assertEqual(remote, source)
        self.assertEqual(jobs.remote_run_directory(self.history, source, self.auth)[0], directory)
        result = "results/temperature-500/NiTi_thermal_conductivity.png"
        files = base64.b64encode((result + "\0").encode()).decode()
        with patch("esen_jobs.RemoteTransport") as factory:
            transport = factory.return_value
            transport.command.side_effect = [f"FILES={files}\n", ""]
            jobs.attach_remote_run(directory, source, self.auth)
            record = jobs.read_job(directory)
            self.assertEqual(record["state"], "Results available")
            self.assertEqual(record["name"], "campaign-1 · NiTi · TK")
            self.assertEqual(record["files"], [result])
            list_command = transport.command.call_args.args[0]
            self.assertIn("find -P results", list_command)
            self.assertIn('case "$base" in "$scratch"/*)', list_command)
            transport.get.side_effect = lambda remote, local: local.write_bytes(b"plot")
            jobs.download_file(directory, record, result, self.auth)
            self.assertEqual((directory / "downloads" / result).read_bytes(), b"plot")
            self.assertEqual(transport.get.call_args.args[0], f"{source}/{result}")
            with self.assertRaisesRegex(ValueError, "Cancel externally"):
                jobs.refresh_remote(directory, record, self.auth, cancel=True)

    def test_reject_external_run_outside_connected_scratch(self):
        for path in (
            "/arf/scratch/other/ESEN/CFG/run",
            "/arf/scratch/test-account/../other/job",
            "/arf/home/test-account/job",
        ):
            with self.subTest(path=path), self.assertRaises(ValueError):
                jobs.remote_run_directory(self.history, path, self.auth)

    def test_remote_submit_uploads_relative_files_and_sets_mpi_tasks(self):
        directory, record = self.new_job()
        with patch("esen_jobs.RemoteTransport") as factory:
            transport = factory.return_value
            transport.command.side_effect = ["", "", "JOB_ID=123\n"]

            def inspect_upload(path, target):
                if target.endswith(".tar.gz"):
                    with tarfile.open(path) as bundle:
                        self.assertIn("./data/cell.lmp", bundle.getnames())
                        self.assertIn(f"./{self.entry}", bundle.getnames())
                else:
                    text = path.read_text()
                    self.assertIn("#SBATCH --ntasks=2\n#SBATCH --cpus-per-task=1", text)
                    self.assertIn("exec mpirun -n 2 lmp -nonbuf -in 'run example.txt'", text)

            transport.put.side_effect = inspect_upload
            jobs.submit_remote(directory, record, self.files, self.script(), self.auth)
            self.assertEqual(jobs.read_job(directory)["job_id"], "123")
            self.assertEqual(transport.put.call_count, 2)
            self.assertIn(f"--job-name=ESEN-{directory.name}", transport.command.call_args.args[0])

    def test_folder_upload_preserves_tree_and_jobs_use_saved_inputs(self):
        destination = "Project 1/New inputs"
        for name in ("study/sub", "study/empty", "Results/previous"):
            (self.source / name).mkdir(parents=True)
        (self.source / "study/sub/in.run").touch()
        (self.source / "study/sub/restart.in").touch()
        (self.source / "Results/previous/output.txt").touch()
        def inspect_upload(auth, relative, snapshot):
            self.assertEqual(auth, self.auth)
            self.assertEqual(relative, PurePosixPath(destination))
            self.assertCountEqual([path.relative_to(snapshot).as_posix() for path in snapshot.rglob("*")], [
                "data", "data/cell.lmp", self.entry, "study", "study/sub",
                "study/sub/in.run", "study/sub/restart.in", "study/empty",
                "Results", "Results/previous", "Results/previous/output.txt",
            ])

        with patch("esen_jobs.upload_tree", side_effect=inspect_upload) as transfer:
            upload_id = jobs.upload_folder(self.history, self.source, self.auth, directory=destination)
            transfer.assert_called_once()

        (self.source / "data/cell.lmp").write_text("changed", encoding="utf-8")
        with patch("esen_jobs.RemoteTransport") as factory:
            transport = factory.return_value
            transport.command.return_value = "JOB_ID=123\n"

            def inspect_archive(path, remote):
                if remote.endswith("/inputs.tar.gz"):
                    with tarfile.open(path) as bundle:
                        self.assertEqual(bundle.extractfile("./data/cell.lmp").read(), b"cell")
                        self.assertIn("./study/sub/in.run", bundle.getnames())

            transport.put.side_effect = inspect_archive
            settings = dict(partition="debug", account=self.auth.username,
                            module="", executable="lmp", launcher="srun", resume_input="restart.in")
            for _ in range(2):
                directory, _ = jobs.new_job(
                    self.source, "study/sub/in.run", "remote", None, parent=self.history,
                    folder_upload=jobs.FolderUpload(upload_id, self.auth.username, destination, direct=True),
                )
                record = jobs.read_job(directory)
                snapshot = jobs.job_inputs(directory, record)
                with (patch("esen_jobs.fetch_catalog"),
                      patch("esen_jobs.full_node_resources", return_value=(2, 512, "04:00:00"))):
                    jobs.submit_full_node(directory, record, (snapshot / "study/sub", "in.run", (), True),
                                          settings, self.auth)
                self.assertEqual(jobs.read_job(directory)["job_id"], "123")
                script = (directory / "run.slurm").read_text(encoding="utf-8")
                self.assertIn("work=work/study/sub\ncheckpoint_root=../../..\n", script)
                self.assertIn("resume_input=restart.in", script)
                self.assertFalse((directory / "inputs").exists())
            self.assertEqual(transport.put.call_count, 4)
            self.assertNotIn("esen-folders", str(transport.command.call_args_list))

    def test_slurm_sync_reuses_remote_snapshot_and_uploads_only_changed_files(self):
        run = self.source / "run_pilot.slurm"
        run.write_text("#!/bin/bash\n#SBATCH --ntasks=56\n", encoding="utf-8")
        (self.source / "obsolete.txt").touch()
        with patch("esen_jobs.RemoteTransport") as factory:
            transport = factory.return_value
            transport.command.return_value = ""
            upload_id = jobs.sync_slurm_folder(self.history, self.source, self.auth, [run.name])
            transport.put.assert_called_once()
            transport.reset_mock()
            transport.command.return_value = "READY=1\n"
            self.assertEqual(jobs.sync_slurm_folder(self.history, self.source, self.auth, [run.name]), upload_id)
            transport.put.assert_not_called()
            run.write_text("#!/bin/bash\n#SBATCH --ntasks=112\n", encoding="utf-8")
            (self.source / "obsolete.txt").unlink()
            transport.command.side_effect = ["", "READY=1\n", "", "", ""]

            def inspect_delta(path, _remote, *, callback):
                size = path.stat().st_size
                callback(size // 2, size)
                callback(size, size)
                with tarfile.open(path) as bundle:
                    self.assertEqual(bundle.extractfile("copy.list").read(),
                                     b"data/cell.lmp\0run example.txt\0")
                    self.assertCountEqual(bundle.getnames(), [
                        "copy.list", "inputs/data", "inputs/run_pilot.slurm",
                    ])

            transport.put.side_effect = inspect_delta
            updates = []
            revised = jobs.sync_slurm_folder(
                self.history, self.source, self.auth, [run.name],
                progress=lambda sent, total: updates.append((sent, total)),
            )
            size = updates[0][1]
            self.assertEqual(updates, [(0, size), (size // 2, size), (size, size)])
            self.assertNotEqual(upload_id, revised)
            self.assertTrue((jobs.upload_snapshot(self.history, upload_id) / "obsolete.txt").exists())
            self.assertFalse((jobs.upload_snapshot(self.history, revised) / "obsolete.txt").exists())
            self.assertIn("cp --reflink=auto --parents", transport.command.call_args_list[-2].args[0])

    def test_slurm_submission_preserves_directives_and_uses_script_folder(self):
        name = "data/run_pilot.slurm"
        script = "#!/bin/bash\n#SBATCH --ntasks=56\nmpirun -np \"$SLURM_NTASKS\" lmp -in model.in\n"
        upload_id = "a" * 32
        snapshot = jobs.upload_snapshot(self.history, upload_id)
        (snapshot / "data").mkdir(parents=True)
        (snapshot / name).write_bytes(b"\xef\xbb\xbf" + script.replace("\n", "\r\n").encode())
        directory, record = jobs.new_job(
            self.source, name, "remote", None, parent=self.history,
            folder_upload=(upload_id, self.auth.username),
        )
        files = (snapshot / "data", "run_pilot.slurm", (), True)
        with (patch("esen_jobs.RemoteTransport") as factory,
              patch("esen_jobs.fetch_catalog") as catalog):
            transport = factory.return_value
            transport.command.side_effect = ["", "JOB_ID=123\n"]
            jobs.submit_full_node(directory, record, files, None, self.auth)
            catalog.assert_not_called()
            self.assertEqual((directory / "run.slurm").read_bytes(), script.encode())
            submission = transport.command.call_args.args[0]
            self.assertIn("/work/data && sbatch --parsable", submission)
            self.assertIn(f"--job-name=ESEN-{directory.name}", submission)
            self.assertIn("/stderr.log ", submission)
            self.assertIn(f"/{directory.name}/submit.err", submission)
            self.assertNotIn("--ntasks=", submission)
            self.assertEqual(jobs.read_job(directory)["job_id"], "123")
            transport.command.side_effect = None
            encoded = base64.b64encode(b"data/stress.csv\0").decode()
            transport.command.return_value = f"FILES={encoded}\nLOG=stdout.log\n"
            jobs.remote_files(directory, record, self.auth)
            self.assertIn("cmp -s", transport.command.call_args.args[0])
            self.assertEqual(jobs.read_job(directory)["files"], ["stdout.log", "work/data/stress.csv"])

    def test_full_node_submission_uses_live_partition_and_matching_node_memory(self):
        directory, record = self.new_job()
        settings = dict(partition="smp", account=self.auth.username,
                        module=jobs.DEFAULT_MODULE, executable="lmp", launcher="srun")
        catalog_reply = (
            "PartitionName=smp State=UP MaxCPUsPerNode=256 MaxMemPerCPU=12000 MaxTime=2-12:30:00\n"
            "RUSTMECH_NODES\n"
            "NodeName=larger-cpu Partitions=smp CPUTot=288 CPUEfctv=256 RealMemory=4000000 MemSpecLimit=1000\n"
            "NodeName=larger-ram Partitions=smp CPUTot=224 RealMemory=4500000"
        )
        with patch("truba_remote.RemoteTransport") as factory, patch("esen_jobs.submit_remote") as submit:
            factory.return_value.command.return_value = catalog_reply
            jobs.submit_full_node(directory, record, self.files, settings, self.auth)
        script = submit.call_args.args[3]
        self.assertIn("#SBATCH --ntasks=256", script)
        self.assertIn("#SBATCH --mem=3072000M", script)
        self.assertIn("#SBATCH --time=60:30:00", script)
        self.assertIn("exec srun -n 256", script)
        self.assertEqual(jobs.read_job(directory)["ranks"], 256)

    def test_lost_submission_reply_is_recovered_without_resubmitting(self):
        directory, record = self.new_job()
        with patch("esen_jobs.RemoteTransport") as factory:
            transport = factory.return_value
            transport.command.side_effect = ["", "", TimeoutError("SSH reply lost")]
            jobs.submit_remote(directory, record, self.files, self.script(), self.auth)
            self.assertEqual(jobs.read_job(directory)["state"], "Submitting")
            record = jobs.read_job(directory)
            log = base64.b64encode(b"LAMMPS done\n").decode()
            files = base64.b64encode(b"sub/result.data\0").decode()
            transport.command.reset_mock()
            transport.command.side_effect = [
                "JOB=123\n", f"QUEUE=\nACCT=COMPLETED|0:0\nstdout.log={log}\nstderr.log=\n",
                f"FILES={files}\nLOG=stdout.log\n", "",
            ]
            jobs.refresh_remote(directory, record, self.auth)
            jobs.remote_files(directory, record, self.auth)
            transport.get.side_effect = lambda _remote, local: local.write_bytes(b"result")
            jobs.download_file(directory, record, "work/sub/result.data", self.auth)
            self.assertNotIn("sbatch", "\n".join(call.args[0] for call in transport.command.call_args_list))
            self.assertEqual(record["state"], "Completed")
            self.assertEqual(record["job_id"], "123")
            self.assertEqual(jobs.log_tail(directory / "stdout.log"), "LAMMPS done\n")
            self.assertEqual((directory / "downloads/work/sub/result.data").read_bytes(), b"result")

    def test_truba_slurm_selection_stages_remote_files_before_submission(self):
        name = "run test.slurm"
        directory, record = jobs.new_job(None, name, "remote", None, parent=self.history,
                                         truba_input_folder="runs/model one")
        with patch("esen_jobs.RemoteTransport") as factory, patch("esen_jobs.fetch_catalog") as catalog:
            transport = factory.return_value
            transport.get.side_effect = lambda source, path: path.write_bytes(b"#!/bin/bash\r\necho done\r\n")
            transport.command.side_effect = ["", "JOB_ID=123\n"]
            jobs.submit_full_node(directory, record, (None, name, (), True), {}, self.auth)
        self.assertEqual(jobs.read_job(directory)["job_id"], "123")
        self.assertEqual((directory / "run.slurm").read_bytes(), b"#!/bin/bash\necho done\n")
        stage, submit = (call.args[0] for call in transport.command.call_args_list)
        self.assertIn('cd -P -- "$root"\'/runs/model one\'', stage)
        self.assertIn("test -f 'run test.slurm'", stage)
        self.assertIn("--chdir=", submit)
        self.assertTrue(transport.get.call_args.args[0].endswith("/work/run test.slurm"))
        self.assertTrue(transport.put.call_args.args[1].endswith("/run.slurm"))
        catalog.assert_not_called()

    def test_truba_run_list_checks_every_path_in_order(self):
        folder = PurePosixPath("research/jobs")
        contents = b"\xef\xbb\xbf# selected jobs\r\nsecond/run.slurm\r\n\r\nfirst/run test.slurm\r\n"
        expected = [PurePosixPath("second/run.slurm"), PurePosixPath("first/run test.slurm")]
        with (patch("esen_jobs.read_regular_file", return_value=contents) as read,
              patch("esen_jobs.require_regular_files") as check):
            self.assertEqual(jobs.read_truba_run_list(self.auth, folder), expected)
            read.assert_called_once_with(self.auth, folder, jobs.RUN_LIST, limit=jobs.RUN_LIST_LIMIT)
            check.assert_called_once_with(self.auth, folder, expected)
        for invalid in (b"../run.slurm", b"/run.slurm", b"C:/run.slurm", b"in.model",
                        b"# empty", b"first/run.slurm\nFIRST/run.slurm"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                jobs.run_list_entries(invalid)

    def test_truba_lammps_selection_uses_the_configured_queue_without_uploading_inputs(self):
        name = "in.remote"
        directory, record = jobs.new_job(None, name, "remote", None, parent=self.history,
                                         truba_input_folder=".")
        settings = dict(partition="debug", account=self.auth.username, module="", executable="lmp",
                        launcher="srun", resume_input="data/in.restart")
        with (patch("esen_jobs.RemoteTransport") as factory,
              patch("esen_jobs.fetch_catalog", return_value=jobs.BASELINES)):
            transport = factory.return_value
            transport.command.side_effect = ["", "JOB_ID=456\n"]
            jobs.submit_full_node(directory, record, (None, name, (), True), settings, self.auth)
        self.assertEqual(jobs.read_job(directory)["job_id"], "456")
        self.assertIn("#SBATCH --partition=debug", (directory / "run.slurm").read_text())
        self.assertIn("test -f data/in.restart", transport.command.call_args_list[0].args[0])
        transport.get.assert_not_called()
        self.assertEqual(transport.put.call_count, 1)
        self.assertTrue(transport.put.call_args.args[1].endswith("/run.slurm"))

    def test_completed_remote_results_publish_atomically_beside_inputs(self):
        directory, record = self.new_job()
        jobs.snapshot_inputs(directory, *self.files)
        jobs.save_job(directory, record, state="Completed", username=self.auth.username)
        output = self.source / "Results" / f"ESEN-{directory.name}"

        def transfer(_, destination):
            with tarfile.open(destination, "w:gz") as archive:
                for name, data in {
                    f"work/{self.entry}": (self.source / self.entry).read_bytes(),
                    "work/data/cell.lmp": b"modified input",
                    "work/sub/result.data": b"result",
                    "stdout.log": b"LAMMPS done\n",
                }.items():
                    member = tarfile.TarInfo(name)
                    member.size = len(data)
                    archive.addfile(member, BytesIO(data))

        with patch("esen_jobs.RemoteTransport") as factory:
            transport = factory.return_value
            transport.get.side_effect = OSError("Connection interrupted")
            with self.assertRaisesRegex(OSError, "Connection interrupted"):
                jobs.export_remote_results(directory, record, self.auth)
            self.assertFalse(output.exists())
            self.assertEqual(list(output.parent.iterdir()), [])
            self.assertIn("rm -f -- .esen-results-", transport.command.call_args.args[0])

            transport.get.side_effect = transfer
            jobs.export_remote_results(directory, record, self.auth)
            jobs.export_remote_results(directory, record, self.auth)
            self.assertEqual(transport.get.call_count, 2)

        self.assertFalse((output / "work" / self.entry).exists())
        self.assertEqual((output / "work/data/cell.lmp").read_bytes(), b"modified input")
        self.assertEqual((output / "work/sub/result.data").read_bytes(), b"result")
        self.assertEqual((output / "stdout.log").read_bytes(), b"LAMMPS done\n")
        (output.parent / "reference.dat").write_bytes(b"input under Results")
        next_job, _ = self.new_job()
        snapshot = jobs.snapshot_inputs(next_job, self.source, self.entry, (), True)
        self.assertFalse((snapshot / "Results" / output.name).exists())
        self.assertTrue((snapshot / "Results/reference.dat").is_file())
        reused, reused_record = jobs.new_job(snapshot, self.entry, "remote", None, parent=self.history)
        self.assertEqual(jobs.result_directory(reused, reused_record).parent, output.parent)

    def test_slurm_runtime_excludes_queue_time_and_saves_the_last_reported_step(self):
        directory, record = self.new_job()
        jobs.save_job(directory, record, username=self.auth.username, job_id="456", state="Submitted")
        step = base64.b64encode(b"ESEN_STEP=Minimization\nESEN_STEP=Equilibration\n").decode()
        with patch("esen_jobs.RemoteTransport") as factory:
            transport = factory.return_value
            transport.command.side_effect = [
                "QUEUE=PENDING|0:00|2026-11-20T05:00:00|48:00:00|40|1|orfoz|Resources\nACCT=PENDING|0:0|0\nstdout.log=\nstderr.log=\n",
                f"QUEUE=RUNNING|1-02:03:04|2026-11-20T05:00:00|21:56:56|40|1|orfoz|node\nACCT=RUNNING|0:0|12\nstdout.log={step}\nstderr.log=\n",
                "QUEUE=\nACCT=COMPLETED|0:0|94000|2026-11-20T05:00:00|40|1|orfoz\nstdout.log=\nstderr.log=\n",
            ]
            jobs.refresh_remote(directory, record, self.auth)
            self.assertEqual((record["state"], record["elapsed_seconds"]), ("Queued", 0))
            self.assertEqual(record["slurm"], dict(cpus="40", nodes="1", partition="orfoz"))
            jobs.refresh_remote(directory, record, self.auth)
            self.assertEqual((record["state"], record["elapsed_seconds"]), ("Running", 93784))
            self.assertEqual(record["slurm"]["remaining"], "21:56:56")
            jobs.refresh_remote(directory, record, self.auth)
        saved = jobs.read_job(directory)
        self.assertEqual((saved["state"], saved["elapsed_seconds"]), ("Completed", 94000))
        self.assertEqual(saved["slurm"]["start"], "2026-11-20T05:00:00")
        self.assertEqual(saved["script_step"], "Equilibration")

    def test_selected_job_displays_runtime_and_reported_step(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        directory, record = self.new_job()
        jobs.save_job(directory, record, state="Completed", elapsed_seconds=94000,
                      script_step="Equilibration")
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()
        self.assertEqual(workspace.history.item(0, 9).text(), "26:06:40")
        self.assertIn("26:06:40", workspace.job_progress.text())
        self.assertIn("Equilibration", workspace.job_progress.text())
        self.assertTrue(workspace.job_progress.isVisible())

    def test_history_tables_share_details_and_only_show_queued_and_running_on_right(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        submitted, queued, running, completed, preparing = (self.new_job() for _ in range(5))
        for job, state, job_id in (
            (submitted, "Submitted", "111"), (queued, "Queued", "222"),
            (running, "Running", "333"), (completed, "Completed", "444"),
        ):
            jobs.save_job(*job, state=state, job_id=job_id, username=self.auth.username)
        jobs.save_job(*running, elapsed_seconds=63,
                      slurm=dict(start="2026-11-20T05:00:00", remaining="10:12", cpus="40", nodes="2",
                                 partition="orfoz"))
        (running[0] / "stdout.log").write_text("running job log", encoding="utf-8")
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()
        future = Future()
        workspace.remote_task = preparing[0], future
        workspace._update_finished_job(*preparing)
        self.assertEqual(workspace.history.rowCount(), 5)
        self.assertEqual({workspace.history.item(row, 1).text() for row in range(5)},
                         {"Preparing", "Submitted", "Queued", "Running", "Completed"})
        proxy = workspace.active_jobs.model()
        self.assertEqual(proxy.rowCount(), 2)
        self.assertEqual([proxy.headerData(row, Qt.Orientation.Vertical) for row in range(2)], [1, 2])
        row = next(row for row in range(2) if proxy.index(row, 0).data() == "333")
        self.assertEqual([proxy.index(row, column).data() for column in (4, 5, 6, 7, 9, 10)],
                         [self.entry, "40", "2", "orfoz", "00:01:03", "00:10:12"])
        self.assertNotEqual(proxy.index(row, 8).data(), "—")
        workspace.active_jobs.scrollTo(proxy.index(row, 0))
        position = workspace.active_jobs.visualRect(proxy.index(row, 0)).center()
        QTest.mouseClick(workspace.active_jobs.viewport(), Qt.MouseButton.LeftButton, pos=position)
        self.assertEqual(workspace._selected_directory(), running[0])
        self.assertIn("running job log", workspace.log.toPlainText())
        jobs.save_job(*preparing, state="Submitting")
        workspace._tick()
        preparing_row = next(row for row in range(workspace.history.rowCount())
                             if workspace.history.item(row, 0).data(Qt.ItemDataRole.UserRole) == str(preparing[0]))
        self.assertEqual(workspace.history.item(preparing_row, 1).text(), "Submitting")
        jobs.save_job(*running, state="Completed")
        workspace._show_selected()
        self.assertEqual(proxy.rowCount(), 1)
        self.assertEqual(proxy.index(0, 0).data(), "222")
        future.set_result(None)

    def test_cancel_waits_for_slurm_to_confirm(self):
        directory, record = self.new_job()
        jobs.save_job(directory, record, username=self.auth.username, job_id="456", state="Running")
        with patch("esen_jobs.RemoteTransport") as factory:
            transport = factory.return_value
            transport.command.side_effect = [
                "", "QUEUE=COMPLETING|00:10|2026-11-20T05:00:00|2:00:00|40|1|orfoz|node\nACCT=RUNNING|0:0\nstdout.log=\nstderr.log=\n",
                "QUEUE=\nACCT=CANCELLED by 42|0:15\nstdout.log=\nstderr.log=\n",
            ]
            jobs.refresh_remote(directory, record, self.auth, cancel=True)
            self.assertEqual(record["state"], "Cancellation requested")
            jobs.refresh_remote(directory, record, self.auth)
            self.assertEqual(record["state"], "Cancelled")
            self.assertEqual(transport.command.call_args_list[0].args[0], "scancel 456")

    def test_left_commands_stay_on_one_toolbar_in_both_languages(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        preferences.setValue(LANGUAGE_KEY, "tr")
        language = LanguageManager(app, preferences)
        self.addCleanup(language.shutdown)
        with patch.object(app, "_rustmech_language", language, create=True), \
                patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
            self.addCleanup(workspace.dispose)
            workspace.show()
            for locale in ("tr", "en"):
                with self.subTest(locale=locale):
                    language.set_language(locale)
                    app.processEvents()
                    self.assertFalse(workspace.batch_folder.text())
                    self.assertFalse(workspace.batch_folder.icon().isNull())
                    menu = workspace.batch_folder.menu()
                    language.localize(menu)
                    self.assertEqual(menu.actions()[0].text(),
                                     "Klasördeki işleri tara" if locale == "tr" else "Scan folder for jobs")
                    self.assertEqual(menu.actions(), [workspace.scan_folder_action, workspace.job_folder_action])
                    self.assertEqual(workspace.upload.text(), "Send Truba")
                    self.assertEqual(workspace.sync.text(), "Eşitle" if locale == "tr" else "Sync")
                    icons = (workspace.input_back, workspace.input_forward, workspace.input_up,
                             workspace.batch_folder, workspace.load_files, workspace.pick_script,
                             workspace.use_as_script, workspace.remove_inputs, workspace.move_up,
                             workspace.move_down)
                    buttons = (*icons, workspace.upload, workspace.sync, workspace.copy_ai,
                               workspace.select_history, workspace.delete_job, workspace.use_inputs,
                               workspace.refresh, workspace.resume, workspace.stop, workspace.attach)
                    toolbar = workspace.findChild(QScrollArea, "esenLeftActions")
                    self.assertEqual(set(toolbar.widget().findChildren(QPushButton)), set(buttons))
                    visible = [button for button in buttons if button.isVisible()]
                    self.assertEqual(len({button.y() for button in visible}), 1)
                    self.assertEqual([button.x() for button in visible],
                                     sorted({button.x() for button in visible}))
                    self.assertEqual({button.height() for button in visible}, {25})
                    left = workspace.findChild(QSplitter, "esenInputsAndJobs")
                    self.assertFalse(left.findChildren(QPushButton))
                    self.assertLess(workspace.input_filter.x(), workspace.input_sort.x())
                    self.assertFalse(any(label.text() in {"Job settings", "İş ayarları"}
                                         for label in workspace.findChildren(QLabel)))
                    self.assertEqual(workspace.sync.width(), workspace.upload.width())
                    self.assertEqual({button.width() for button in icons}, {25})
                    target = workspace.target
                    label = next(label for label in workspace.findChildren(QLabel) if label.buddy() is target)
                    self.assertEqual(target.x(), workspace.partition.x())
                    self.assertLessEqual(abs(label.geometry().center().y() - target.geometry().center().y()), 1)
                    browser = workspace.truba_browser
                    date_header = "Değiştirilme tarihi" if locale == "tr" else "Date modified"
                    self.assertEqual(workspace.inputs.headerItem().text(2), date_header)
                    self.assertEqual(browser.files.headerItem().text(2), date_header)
                    file_commands = (browser.up, browser.open_folder, browser.refresh, browser.slurm_inputs,
                                     browser.create, browser.upload, browser.download, browser.select_files,
                                     browser.delete_files, browser.group_files)
                    tools = (*file_commands, browser.filter)
                    self.assertEqual(len({control.y() for control in tools}), 1)
                    self.assertEqual([control.x() for control in tools], sorted({control.x() for control in tools}))
                    self.assertEqual({button.width() for button in file_commands}, {25})
                    for button in file_commands:
                        self.assertFalse(button.text())
                        self.assertFalse(button.icon().isNull())
                        self.assertTrue(button.accessibleName())
                    self.assertTrue(browser.address.isHidden())
                    self.assertTrue(browser.folder_name.isHidden())
                    history_actions = (workspace.select_history, workspace.delete_job)
                    history_commands = (workspace.use_inputs, workspace.refresh, workspace.resume, workspace.stop)
                    self.assertEqual(workspace.active_history_only.text(),
                                     "Sadece aktif işleri göster" if locale == "tr" else "Show only active jobs")
                    self.assertEqual(len({button.y() for button in history_actions}), 1)
                    self.assertEqual(len({button.width() for button in history_actions}), 1)
                    self.assertEqual(len({button.width() for button in history_commands}), 1)
                    results = (workspace.open_file, workspace.download,
                               workspace.open_folder, workspace.open_results_folder)
                    width = max(button.sizeHint().width() for button in results) + 2
                    self.assertEqual({button.width() for button in results}, {width})

    def test_input_filter_and_sort_preserve_checks_and_manual_slurm_order(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        names = ("z.slurm", "b.DATA", "a.in", "c.slurm")
        for name in names:
            (self.source / name).write_text("#!/bin/bash\n", encoding="utf-8")
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
        self.addCleanup(workspace.dispose)
        workspace._load_files([str(self.source / name) for name in names])
        workspace.show()
        app.processEvents()
        items = {item.path: item for item in workspace.inputs.items()}
        self.assertEqual(list(items), ["z.slurm", "c.slurm", "a.in", "b.DATA"])
        for name in ("z.slurm", "c.slurm"):
            items[name].setCheckState(0, Qt.CheckState.Checked)
        workspace.inputs.setCurrentItem(items["c.slurm"])
        workspace.move_up.click()
        self.assertEqual(list(workspace._folder_jobs()), ["c.slurm", "z.slurm"])
        manual_order = [item.path for item in workspace.inputs.items()]
        self.assertEqual(manual_order, ["c.slurm", "z.slurm", "a.in", "b.DATA"])

        for sort_mode, expected in (("name", ["a.in", "b.DATA", "c.slurm", "z.slurm"]),
                                    ("format", ["b.DATA", "a.in", "c.slurm", "z.slurm"])):
            workspace.input_sort.setCurrentIndex(workspace.input_sort.findData(sort_mode))
            self.assertEqual([item.path for item in workspace.inputs.items()], expected)
        workspace.input_filter.setText(".data")
        self.assertEqual([item.path for item in workspace.inputs.items() if not item.isHidden()], ["b.DATA"])
        self.assertEqual(list(workspace._folder_jobs()), ["c.slurm", "z.slurm"])
        self.assertFalse(workspace.move_up.isEnabled())

        workspace.input_filter.clear()
        workspace.input_sort.setCurrentIndex(workspace.input_sort.findData("job"))
        self.assertEqual([item.path for item in workspace.inputs.items()], manual_order)

    def test_copy_ai_copies_instructions_without_inputs_or_connection(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()

        clipboard = app.clipboard()
        self.addCleanup(clipboard.setText, clipboard.text())
        clipboard.setText("previous text")
        self.assertEqual(workspace.copy_ai.text(), "Copy AI")
        self.assertFalse(workspace.copy_ai.autoDefault())
        QTest.mouseClick(workspace.copy_ai, Qt.MouseButton.LeftButton)

        self.assertEqual(clipboard.text(), ESEN_AI_PROMPT)
        self.assertTrue(workspace.isVisible())
        self.assertFalse(self.history.exists())

    def test_load_files_then_choose_script_and_run(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        preferences.setValue("esen/target", "local")
        preferences.setValue("esen/partition", "debug")
        preferences.setValue("esen/walltime", "00:01:00")
        files = [str(self.source / name) for name in (self.entry, "data/cell.lmp")]
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()
        self.assertEqual(workspace.target.currentData(), "remote")
        self.assertEqual(workspace.partition.currentData(), "barbun")
        self.assertTrue(workspace.partition.isVisible())
        self.assertFalse(hasattr(workspace, "walltime"))
        self.assertTrue(workspace.connection.isVisible())
        self.assertFalse(workspace.account.isVisible())
        self.assertFalse(workspace.ranks.isVisible())

        with patch("esen_workspace.QFileDialog.getOpenFileNames", return_value=(files, "")):
            QTest.mouseClick(workspace.load_files, Qt.MouseButton.LeftButton)
        self.assertEqual(workspace.input_root, self.source)
        self.assertFalse(workspace.start.isEnabled())
        self.assertEqual(workspace.history.rowCount(), 0)
        QTest.mouseClick(workspace.use_as_script, Qt.MouseButton.LeftButton)
        self.assertEqual(workspace.entry, self.entry)
        self.assertEqual(workspace.extra, ["data/cell.lmp"])
        self.assertIn("#SBATCH --partition=barbun", workspace.preview.toPlainText())
        self.assertIn("#SBATCH --ntasks=", workspace.preview.toPlainText())
        workspace.partition.setCurrentIndex(workspace.partition.findData("debug"))
        self.assertIn("#SBATCH --time=04:00:00", workspace.preview.toPlainText())
        workspace._save_profiles()
        self.assertEqual(preferences.value("esen/queue"), "debug")
        workspace.partition.setCurrentIndex(workspace.partition.findData("barbun"))
        workspace._load_profiles()
        self.assertEqual(workspace.partition.currentData(), "debug")
        header = workspace.truba_settings
        QTest.mouseClick(header, Qt.MouseButton.LeftButton, pos=header.header_rect().center())
        self.assertTrue(workspace.account.isVisible())
        workspace.account.setText("custom-account")
        workspace.account.setFocus()
        QTest.keyClick(workspace.account, Qt.Key.Key_Return)
        header.setFocus()
        QTest.keyClick(header, Qt.Key.Key_Space)
        self.assertFalse(workspace.account.isVisible())
        self.assertIn("#SBATCH --account=custom-account", workspace.preview.toPlainText())
        self.assertTrue(workspace.isVisible())
        self.assertEqual(workspace.history.rowCount(), 0)

        workspace.target.setCurrentIndex(workspace.target.findData("local"))
        self.assertFalse(header.isVisible())
        self.assertFalse(workspace.partition.isVisible())
        self.assertTrue(workspace.ranks.isVisible())

        with patch("esen_jobs.run_local") as run, patch("esen_jobs.local_command", return_value=["lmp"]):
            workspace.local_memory.setValue(1536)
            workspace._preview()
            QTest.mouseClick(workspace.start, Qt.MouseButton.LeftButton)
            workspace.local_task[1].result(timeout=5)
            self.assertEqual(run.call_args.args[2], self.files)
            self.assertEqual(run.call_args.kwargs["memory_mib"], 1536)
        workspace.target.setCurrentIndex(workspace.target.findData("remote"))
        self.assertFalse(workspace.ranks.isVisible())
        self.assertFalse(workspace.local_memory.isVisible())
        self.assertIn("#SBATCH --ntasks=", workspace.preview.toPlainText())
        self.assertNotIn("#SBATCH --mem=1536M", workspace.preview.toPlainText())
        workspace.target.setCurrentIndex(workspace.target.findData("local"))
        self.assertTrue(workspace.local_memory.isVisible())
        self.assertEqual(workspace.local_memory.value(), 1536)

    def test_truba_badge_follows_connection_and_jobs_while_viewing_other_history(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        self.new_job(target="local")
        truba = FakeTruba()
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(truba, history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()
        self.assertTrue(workspace.connection.isVisible())
        self.assertFalse(workspace.truba_badge.isVisible())

        truba.auth = self.auth
        truba.connection_changed.emit(True)
        self.assertFalse(workspace.connection.isVisible())
        self.assertTrue(workspace.truba_badge.isVisible())
        self.assertEqual(workspace.truba_badge.text(), "Connected")
        workspace._load_files([str(self.source / self.entry)])
        submitted = Event()

        def submit(directory, record, *_):
            self.assertTrue(submitted.wait(timeout=5))
            jobs.save_job(directory, record, state="Submitted", job_id="456", username=self.auth.username)

        with patch("esen_jobs.submit_full_node", side_effect=submit):
            workspace.start.click()
            directory, future = workspace.remote_task
            submitted.set()
            self.assertEqual(workspace.truba_badge.text(), "Working")
            future.result(timeout=5)
            workspace._tick()
        workspace.history.setCurrentCell(1, 0)
        self.assertEqual(workspace.record["target"], "local")
        self.assertEqual(workspace.truba_badge.text(), "Working")

        def complete(directory, record, *_, **__):
            jobs.save_job(directory, record, state="Completed")

        workspace.hide()
        with (patch("esen_jobs.refresh_remote", side_effect=complete) as refresh,
              patch("esen_jobs.remote_files"), patch("esen_jobs.export_remote_results") as export):
            workspace._poll_remote()
            workspace.remote_task[1].result(timeout=5)
            workspace._tick()
        self.assertEqual(refresh.call_args.args[0], directory)
        self.assertEqual(export.call_args.args[0], directory)
        workspace.show()
        workspace._tick()
        self.assertEqual(workspace.truba_badge.text(), "Connected")

        workspace.target.setCurrentIndex(workspace.target.findData("local"))
        self.assertFalse(workspace.truba_badge.isVisible())
        truba.auth = None
        truba.connection_changed.emit(False)
        workspace.target.setCurrentIndex(workspace.target.findData("remote"))
        self.assertTrue(workspace.connection.isVisible())
        self.assertFalse(workspace.truba_badge.isVisible())

    def test_open_results_folder_becomes_available_after_local_download(self):
        app = QApplication.instance() or QApplication([])
        directory, record = self.new_job()
        jobs.save_job(directory, record, state="Completed", username=self.auth.username)
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()
        button = workspace.open_results_folder
        self.assertFalse(button.isEnabled())

        result_folder = jobs.result_directory(directory, record)
        result_folder.mkdir(parents=True)
        workspace._tick()
        self.assertTrue(button.isEnabled())
        with patch("esen_workspace.QDesktopServices.openUrl") as open_url:
            QTest.mouseClick(button, Qt.MouseButton.LeftButton)
            open_url.assert_called_once_with(QUrl.fromLocalFile(str(result_folder)))

        workspace.history.setCurrentCell(-1, -1)
        self.assertFalse(button.isEnabled())

    def test_drop_selects_unique_conventional_script_and_preserves_paths(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
        self.addCleanup(workspace.dispose)
        script = self.source / "in.cell"
        script.write_text("# LAMMPS example\n", encoding="utf-8")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path)) for path in (self.source / "data/cell.lmp", script)])
        args = (QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier)
        workspace.show()
        app.processEvents()
        viewport = workspace.inputs.viewport()
        enter = QDragEnterEvent(*args)
        app.sendEvent(viewport, enter)
        self.assertTrue(enter.isAccepted())
        drop = QDropEvent(*args)
        app.sendEvent(viewport, drop)
        self.assertTrue(drop.isAccepted())
        self.assertEqual(workspace.entry, "in.cell")
        self.assertEqual(workspace.extra, ["data/cell.lmp"])
        self.assertEqual(workspace.history.rowCount(), 0)

    def test_workspace_reopens_with_maximized_geometry_and_splitter(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        store = WindowStateStore(app, preferences)
        self.addCleanup(app.removeEventFilter, store)
        truba = FakeTruba()

        with patch("esen_workspace.QSettings", return_value=preferences):
            first = EsenWorkspace(truba, history=self.history)
        self.addCleanup(first.dispose)
        first.show()
        first.local_memory.setValue(2048)
        first.resize(720, 680)
        first.move(25, 25)
        app.processEvents()
        first.showMaximized()
        app.processEvents()
        splitter = store.splitters(first)[0]
        splitter.setSizes([340, 430])
        expected = splitter.sizes()
        results = next(split for split in store.splitters(first) if split.objectName() == "esenLogsAndActiveJobs")
        results.moveSplitter(results.sizes()[0] - 45, 1)
        expected_results = results.sizes()
        normal = first.normalGeometry()
        first.close()
        with patch("esen_workspace.QSettings", return_value=preferences):
            second = EsenWorkspace(truba, history=self.history)
        self.addCleanup(second.dispose)
        second.show()
        app.processEvents()
        self.assertEqual(second.local_memory.value(), 2048)
        self.assertTrue(second.isMaximized())
        self.assertEqual(second.normalGeometry(), normal)
        actual = store.splitters(second)[0].sizes()
        self.assertAlmostEqual(actual[0] / sum(actual), expected[0] / sum(expected), delta=0.03)
        reopened_results = next(split for split in store.splitters(second) if split.objectName() == results.objectName())
        self.assertEqual(reopened_results.sizes(), expected_results)

    def load_batch(self, workspace):
        with patch("esen_workspace.QFileDialog.getExistingDirectory", return_value=str(self.source)):
            workspace.scan_folder_action.trigger()
        workspace.folder_scan[0].result(timeout=5)
        workspace._tick()

    def test_job_settings_browse_folders_without_changing_checked_jobs(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        nested = self.source / "data" / "nested"
        nested.mkdir()
        (nested / "in.deep").touch()
        (self.source / "in.root").touch()
        timestamp = 1_700_000_000
        os.utime(nested / "in.deep", (timestamp, timestamp))
        expected_date = QDateTime.fromSecsSinceEpoch(timestamp).toString("yyyy-MM-dd HH:mm")
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.target.setCurrentIndex(workspace.target.findData("local"))
        self.load_batch(workspace)
        app.processEvents()
        tree = workspace.inputs

        def open_folder(name):
            tree.setCurrentItem(tree.folders[name])
            QTest.keyClick(tree, Qt.Key.Key_Return)

        self.assertEqual(tree.headerItem().text(2), "Date modified")
        self.assertFalse(workspace.input_back.isEnabled())
        open_folder("data")
        open_folder("data/nested")
        self.assertEqual(workspace.input_path.text(), str(nested.resolve()))
        self.assertEqual(tree.itemFromIndex(tree.rootIndex()), tree.folders["data/nested"])
        self.assertEqual(tree.itemFromIndex(tree.rootIndex()).child(0).text(2), expected_date)
        workspace.input_back.click()
        self.assertEqual(workspace.input_path.text(), str(nested.parent.resolve()))
        workspace.input_forward.click()
        self.assertEqual(workspace.input_path.text(), str(nested.resolve()))
        workspace.input_back.click()
        workspace.input_up.click()
        self.assertFalse(tree.rootIndex().isValid())
        self.assertFalse(workspace.input_forward.isEnabled())
        self.assertCountEqual(workspace._folder_jobs(), ["in.root", "data/nested/in.deep"])

    def test_folder_loads_directly_and_runs_checked_local_scripts_in_order(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        sub = self.source / "study"
        empty = self.source / "study" / "empty"
        ignored = self.source / "Results" / "previous"
        for folder in (sub, ignored):
            folder.mkdir(parents=True)
            (folder / "run.IN").touch()
        empty.mkdir()
        for name in ("in.second", "in.first", "in.skip"):
            (self.source / name).touch()
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()
        workspace.target.setCurrentIndex(workspace.target.findData("local"))

        def run(directory, record, files, invocation, _stop, *, memory_mib):
            self.assertEqual(invocation, ["lmp", files[1]])
            self.assertEqual(files[2:], ((), True))
            self.assertEqual(memory_mib, 1536)
            state = "Failed" if record["name"] == "in.first" else "Completed"
            jobs.save_job(directory, record, state=state)

        with (patch("esen_jobs.run_local", side_effect=run) as launch,
              patch("esen_jobs.local_command", side_effect=lambda entry, *_: ["lmp", entry])):
            self.load_batch(workspace)
            self.assertEqual(workspace.history.rowCount(), 0)
            items = {item.path: item for item in workspace.inputs.items()}
            self.assertEqual(list(items), ["in.first", "in.second", "in.skip", self.entry,
                                           "data/cell.lmp", "study/run.IN"])
            study = items["study/run.IN"].parent()
            self.assertEqual(study.text(0), "study")
            self.assertEqual(workspace.inputs.folders["study/empty"].parent(), study)
            self.assertEqual(items["study/run.IN"].text(0), "run.IN")
            workspace.input_filter.setText("run.IN")
            self.assertFalse(study.isHidden())
            self.assertTrue(items["in.first"].isHidden())
            workspace.input_filter.clear()
            for name in (self.entry, "data/cell.lmp"):
                self.assertFalse(items[name].flags() & Qt.ItemFlag.ItemIsUserCheckable)
            self.assertIn("6 files · 4 scripts selected", workspace.input_note.text())
            items["in.skip"].setCheckState(0, Qt.CheckState.Unchecked)
            workspace.local_memory.setValue(1536)
            workspace.start.click()
            self.assertFalse(workspace.start.isEnabled())
            self.assertEqual(len(workspace.batch_queue), 2)
            workspace.local_memory.setValue(2048)
            for expected_count in range(1, 4):
                workspace.local_task[1].result(timeout=5)
                self.assertEqual(launch.call_count, expected_count)
                workspace._tick()
            self.assertEqual([call.args[2][:2] for call in launch.call_args_list],
                             [(self.source.resolve(), "in.first"), (self.source.resolve(), "in.second"),
                              (sub.resolve(), "run.IN")])
        self.assertTrue(workspace.start.isEnabled())
        self.assertEqual(workspace.history.rowCount(), 3)
        self.assertEqual(workspace.history.item(2, 1).text(), "Failed")

    def test_truba_folder_waits_for_slurm_completion_before_submitting_next_script(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        first = self.source / "01"
        second = self.source / "02"
        for folder in (second, first):
            folder.mkdir()
            (folder / "in.run").touch()
        (first / "in.solver").touch()
        truba = FakeTruba()
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(truba, history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()
        self.load_batch(workspace)
        self.assertFalse(workspace.start.isEnabled())
        truba.auth = self.auth
        truba.connection_changed.emit(True)
        self.assertFalse(workspace.start.isEnabled())
        self.assertTrue(workspace.upload.isEnabled())
        with patch("esen_jobs.upload_tree") as transfer:
            transfer.side_effect = RuntimeError("transfer interrupted")
            workspace.upload.click()
            with self.assertRaisesRegex(RuntimeError, "transfer interrupted"):
                workspace.upload_task[0].result(timeout=5)
            workspace._tick()
            self.assertIn("transfer interrupted", workspace.input_note.text())
            self.assertFalse(workspace.start.isEnabled())
            transfer.side_effect = None
            workspace.upload.click()
            workspace.upload_task[0].result(timeout=5)
            workspace._tick()
            workspace.folder_scan[0].result(timeout=5)
            workspace._tick()
        self.assertFalse(workspace.start.isEnabled())
        for item in workspace.inputs.items():
            if item.path in {"01/in.solver", "02/in.run"}:
                item.setCheckState(0, Qt.CheckState.Checked)
        self.assertTrue(workspace.start.isEnabled())

        def submit(directory, record, files, settings, auth):
            self.assertEqual(auth, self.auth)
            self.assertNotIn("walltime", settings)
            self.assertEqual(record["folder_upload"], workspace.folder_upload[0])
            jobs.save_job(directory, record, state="Submitted", job_id="789", username=auth.username)

        def complete(directory, record, _auth, *, cancel):
            self.assertFalse(cancel)
            jobs.save_job(directory, record, state="Completed")

        with (patch("esen_jobs.submit_full_node", side_effect=submit) as launch,
              patch("esen_jobs.remote_files"), patch("esen_jobs.export_remote_results")):
            workspace.start.click()
            workspace.remote_task[1].result(timeout=5)
            workspace._tick()
            self.assertIsNone(workspace.remote_task)
            self.assertEqual(launch.call_count, 1)
            self.assertFalse(workspace.start.isEnabled())
            self.assertEqual(len(workspace.batch_queue), 1)
            with patch("esen_jobs.refresh_remote", side_effect=complete):
                workspace._poll_remote()
                workspace.remote_task[1].result(timeout=5)
                workspace._tick()
            workspace.remote_task[1].result(timeout=5)
            workspace._tick()
            snapshot = jobs.upload_snapshot(self.history, workspace.folder_upload[0])
            self.assertEqual([call.args[2][:2] for call in launch.call_args_list],
                             [(snapshot / "01", "in.solver"), (snapshot / "02", "in.run")])
        self.assertEqual(workspace.history.rowCount(), 2)
        self.assertEqual(workspace.history.item(1, 4).text(), "01/in.solver")
        self.assertEqual(workspace.history.item(1, 1).text(), "Completed")
        self.assertFalse(workspace.start.isEnabled())
        workspace.use_inputs.click()
        workspace.folder_scan[0].result(timeout=5)
        workspace._tick()
        self.assertEqual(list(workspace._folder_jobs()), ["02/in.run"])
        self.assertEqual(jobs.upload_snapshot(self.history, workspace.folder_upload[0]), snapshot)


    def test_run_syncs_selected_slurm_scripts_and_waits_for_success(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        for name in ("run_first.slurm", "run_second.slurm", "in.model"):
            (self.source / name).write_text("#!/bin/bash\n", encoding="utf-8")
        truba = FakeTruba()
        truba.auth = self.auth
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(truba, history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()
        self.load_batch(workspace)
        for item in workspace.inputs.items():
            if item.path == "in.model":
                self.assertFalse(item.flags() & Qt.ItemFlag.ItemIsUserCheckable)
            if item.path == "run_second.slurm":
                item.setCheckState(0, Qt.CheckState.Checked)
        self.assertTrue(workspace.upload.isVisible())
        self.assertTrue(workspace.start.isEnabled())
        with (patch("esen_jobs.sync_slurm_folder") as sync,
              patch("esen_jobs.submit_full_node") as launch):
            sync.side_effect = OSError("sync interrupted")
            workspace.start.click()
            with self.assertRaisesRegex(OSError, "sync interrupted"):
                workspace.upload_task[0].result(timeout=5)
            workspace._tick()
            self.assertIn("sync interrupted", workspace.input_note.text())
            launch.assert_not_called()
            self.assertEqual(workspace.history.rowCount(), 0)
            self.assertTrue(workspace.start.isEnabled())
            upload_id = "b" * 32
            sync.side_effect = None
            sync.return_value = upload_id
            snapshot = jobs.upload_snapshot(self.history, upload_id)
            snapshot.mkdir(parents=True)
            (snapshot / "run_second.slurm").write_text("#!/bin/bash\n", encoding="utf-8")
            launch.side_effect = lambda directory, record, *_: jobs.save_job(
                directory, record, state="Submitted", job_id="123"
            )
            workspace.start.click()
            workspace.upload_task[0].result(timeout=5)
            workspace._check_upload()
            workspace.remote_task[1].result(timeout=5)
            workspace._tick()
            sync.assert_called_with(self.history, self.source.resolve(), self.auth, ("run_second.slurm",), progress=ANY)
            launch.assert_called_once()
            self.assertEqual(workspace.history.rowCount(), 1)
            self.assertEqual(workspace.history.item(0, 4).text(), "run_second.slurm")
            self.assertEqual(workspace.history.item(0, 1).text(), "Submitted")

    def test_folder_automatically_groups_unchecked_jobs_when_a_size_is_entered(self):
        app = QApplication.instance() or QApplication([])
        truba = FakeTruba()
        truba.auth = self.auth
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        names = tuple(f"{index:02d}/in.run" for index in range(7))
        for name in names:
            path = self.source / name
            path.parent.mkdir()
            path.touch()
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(truba, history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()
        self.load_batch(workspace)
        self.assertEqual(list(workspace._folder_jobs()), [])
        workspace.jobs_per_script.setFocus()
        field = workspace.jobs_per_script.field
        field.selectAll()
        QTest.keyClicks(field, "3")
        QTest.keyClick(field, Qt.Key.Key_Return)
        self.assertIsNone(workspace.upload_task)
        self.assertEqual(workspace.jobs_per_script.value(), 3)
        self.assertTrue(workspace.start.isEnabled())
        self.assertIn("7 jobs found", workspace.input_note.text())
        workspace.resume_input.setText("folder\\in.run")
        self.assertEqual(workspace._automatic_jobs(), names)
        workspace.resume_input.setText("00\\in.run")
        self.assertEqual(workspace._automatic_jobs(), names[1:])
        workspace.resume_input.clear()
        upload_id = "b" * 32
        snapshot = jobs.upload_snapshot(self.history, upload_id)
        for name in names:
            path = snapshot / name
            path.parent.mkdir(parents=True)
            path.touch()
        with (patch("esen_jobs.sync_slurm_folder", return_value=upload_id) as sync,
              patch("esen_jobs.submit_full_node") as launch):
            workspace.start.click()
            workspace.upload_task[0].result(timeout=5)
            workspace._tick()
            while workspace.remote_task:
                workspace.remote_task[1].result(timeout=5)
                workspace._tick()
            sync.assert_called_once_with(self.history, self.source.resolve(), self.auth, names, progress=ANY)
            actual = [call.args[1]["group_entries"] for call in launch.call_args_list]
            self.assertEqual(actual, [list(names[:3]), list(names[3:5]), list(names[5:])])
            self.assertEqual(workspace.history.rowCount(), 3)
            self.assertIn("Group of 2 jobs", workspace.history.item(0, 4).text())

    def test_grouped_slurm_copies_isolate_cases_and_use_one_submission(self):
        upload = jobs.FolderUpload("b" * 32, self.auth.username)
        snapshot = jobs.upload_snapshot(self.history, upload.upload_id)
        available = {
            "case a/run.slurm": "#SBATCH --ntasks=2\n#SBATCH --job-name=first\n",
            "case 'b'/run.slurm": "#SBATCH --ntasks=2\n#SBATCH --job-name=second\n",
            "other/run.slurm": "#SBATCH --ntasks=4\n",
            "dependent/run.slurm": "#SBATCH --ntasks=2 --dependency=afterok:7\n",
        }
        for name, options in available.items():
            path = snapshot / name
            path.parent.mkdir(parents=True)
            path.write_text(f"#!/bin/bash\n{options}pwd\n", encoding="utf-8")
        grouped, other, dependent = list(jobs.folder_job_groups(snapshot, available, 3, slurm=True))
        self.assertEqual(grouped, tuple(available)[:2])
        self.assertEqual((other, dependent), (("other/run.slurm",), ("dependent/run.slurm",)))
        directory, record = jobs.new_job(self.source, grouped[0], "remote", None, parent=self.history,
                                         folder_upload=upload, group_entries=grouped)
        with patch("esen_jobs.RemoteTransport") as factory:
            transport = factory.return_value
            transport.command.side_effect = ["", "JOB_ID=123\n"]
            jobs.submit_full_node(directory, record, (None, "run.slurm", (), True), None, self.auth)
        script = (directory / "run.slurm").read_text(encoding="utf-8")
        self.assertIn("#SBATCH --ntasks=2\n", script)
        self.assertNotIn("#SBATCH --job-name", script)
        self.assertIn("work/case-0001/case a", script)
        self.assertIn("work/case-0002/case ", script)
        stage, submit = [call.args[0] for call in transport.command.call_args_list]
        self.assertEqual(stage.count("cp -a --reflink=auto"), 2)
        self.assertIn("sbatch --parsable", submit)
        self.assertIn("${file#work/*/}", jobs.remote_output_scan(record))
        self.assertEqual((record["state"], record["job_id"]), ("Submitted", "123"))

    def test_run_submits_an_uploaded_run_list_without_local_inputs(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        truba = FakeTruba()
        truba.auth = self.auth
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(truba, history=self.history)
        self.addCleanup(workspace.dispose)
        folder = PurePosixPath("uploaded runs")
        scripts = [PurePosixPath("case B/launch.slurm"), PurePosixPath("case A/run.slurm")]
        browser = workspace.truba_browser
        browser._sync_connection()
        browser.directory = folder
        browser.loaded = True
        timestamp = 1_700_000_000
        browser._show_entries([SimpleNamespace(filename=jobs.RUN_LIST, st_mode=S_IFREG, st_size=42,
                                               st_mtime=timestamp)])
        self.assertEqual(browser.files.headerItem().text(2), "Date modified")
        self.assertEqual(browser.files.currentItem().text(2),
                         QDateTime.fromSecsSinceEpoch(timestamp).toString("yyyy-MM-dd HH:mm"))
        self.assertEqual(browser.selected_file(self.auth), (folder, jobs.RUN_LIST))
        self.assertTrue(workspace.start.isEnabled())
        self.assertEqual(workspace.history.rowCount(), 0)

        def submit(directory, record, files, settings, auth):
            jobs.save_job(directory, record, state="Submitted", job_id="123", username=auth.username)

        with (patch("esen_jobs.read_truba_run_list", return_value=scripts) as read,
              patch("esen_jobs.submit_full_node", side_effect=submit) as launch,
              patch("esen_jobs.sync_slurm_folder") as sync):
            workspace.start.click()
            workspace.run_list_task[0].result(timeout=5)
            workspace._tick()
            for _ in scripts:
                workspace.remote_task[1].result(timeout=5)
                workspace._tick()
            read.assert_called_once_with(self.auth, folder)
            self.assertEqual([(call.args[1]["truba_input_folder"], call.args[2][1])
                              for call in launch.call_args_list],
                             [("uploaded runs/case B", "launch.slurm"), ("uploaded runs/case A", "run.slurm")])
            sync.assert_not_called()
        self.assertEqual(workspace.history.rowCount(), 2)
        self.assertTrue(all(workspace.history.item(i, 1).text() == "Submitted" for i in range(2)))

    def test_space_checks_only_runnable_files_in_a_multi_selection(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        names = ("run_first.slurm", "run_second.slurm", "data/cell.lmp")
        for name in names[:2]:
            (self.source / name).write_text("#!/bin/bash\n", encoding="utf-8")
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()
        workspace._load_files([str(self.source / name) for name in names])
        QTest.keyClick(workspace.inputs, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual({item.path for item in workspace.inputs.selectedItems()}, {*names, "data"})
        QTest.keyClick(workspace.inputs, Qt.Key.Key_Space)
        self.assertEqual(list(workspace._folder_jobs()), list(names[:2]))
        QTest.keyClick(workspace.inputs, Qt.Key.Key_Space)
        self.assertEqual(list(workspace._folder_jobs()), [])

    def test_remove_selected_support_files_preserves_main_script_and_disk_files(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        preferences.setValue("esen/target", "local")
        names = ("in.model", "data/cell.lmp", "potential.meam")
        (self.source / names[0]).touch()
        (self.source / names[2]).touch()
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()
        workspace._load_files([str(self.source / name) for name in names])
        view = workspace.inputs
        files = view.items()
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, pos=view.visualItemRect(files[0]).center())
        QTest.mouseClick(view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                         view.visualItemRect(files[2]).center())
        self.assertEqual({item.path for item in view.selectedItems() if item.job_order is not None}, set(names))
        self.assertTrue(workspace.remove_inputs.isEnabled())
        self.assertFalse(workspace.use_as_script.isEnabled())
        workspace.remove_inputs.click()
        self.assertEqual(len(workspace.inputs.items()), 1)
        self.assertEqual(workspace.inputs.items()[0].path, names[0])
        self.assertTrue(all((self.source / name).exists() for name in names))

    def test_selected_files_upload_and_slurm_runs_in_the_user_order(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        list_directory = self.enterContext(patch("truba_files.list_directory", return_value=[]))
        names = ("01.slurm", "02.slurm", "data/cell.lmp")
        for name in names[:2]:
            (self.source / name).write_text("#!/bin/bash\n", encoding="utf-8")
        truba = FakeTruba()
        truba.auth = self.auth
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(truba, history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()
        workspace._load_files([str(self.source / name) for name in names])
        workspace.inputs.setCurrentItem(workspace.inputs.items()[1], 0, QItemSelectionModel.SelectionFlag.ClearAndSelect)
        workspace.move_up.click()
        self.assertEqual(workspace.inputs.items()[0].path, names[1])
        for row in range(2):
            workspace.inputs.items()[row].setCheckState(0, Qt.CheckState.Checked)

        def submit(directory, record, _files, _settings, auth):
            jobs.save_job(directory, record, state="Submitted", job_id="123", username=auth.username)

        with (patch("esen_jobs.RemoteTransport") as transport, patch("esen_jobs.upload_tree") as transfer,
              patch("esen_jobs.submit_full_node", side_effect=submit) as launch):
            destination = "Deneme 1"
            browser = workspace.truba_browser

            def finish_browsing():
                browser.task[0].result(timeout=5)
                browser.check_task()

            def folder(name):
                return SimpleNamespace(filename=name, st_mode=S_IFDIR, st_size=0)

            if browser.task:
                finish_browsing()
            list_directory.return_value = [folder(destination)]
            browser.refresh.click()
            finish_browsing()
            list_directory.return_value = []
            browser.files.setCurrentItem(browser.files.topLevelItem(0))
            self.assertTrue(browser.open_folder.isEnabled())
            browser.open_folder.click()
            finish_browsing()

            listing_started = Event()
            finish_stale_listing = Event()
            self.addCleanup(finish_stale_listing.set)

            def list_while_uploading(*_):
                listing_started.set()
                finish_stale_listing.wait(timeout=5)
                return []

            list_directory.side_effect = list_while_uploading
            browser.refresh.click()
            self.assertTrue(listing_started.wait(timeout=5))
            transfer_half_done = Event()
            finish_transfer = Event()
            self.addCleanup(finish_transfer.set)

            def upload_halfway(*_, progress):
                progress(50, 100)
                transfer_half_done.set()
                finish_transfer.wait(timeout=5)

            transfer.side_effect = upload_halfway
            workspace.upload.click()
            self.assertTrue(transfer_half_done.wait(timeout=5))
            workspace._tick()
            self.assertTrue(workspace.transfer_progress.isVisible())
            self.assertEqual(workspace.transfer_progress.value(), 50)
            finish_transfer.set()
            upload_id = workspace.upload_task[0].result(timeout=5)
            workspace._tick()
            self.assertEqual(workspace.transfer_progress.value(), 100)
            transfer.assert_called_once()
            self.assertEqual(transfer.call_args.args[:2], (self.auth, PurePosixPath(destination)))
            transport.assert_not_called()
            remote = f"/arf/scratch/{self.auth.username}/RustMech/{destination}"
            self.assertIn(f"Uploaded to TRUBA: {remote}\n", workspace.input_note.text())
            list_directory.return_value = [SimpleNamespace(filename=names[0], st_mode=S_IFREG, st_size=12)]
            list_directory.side_effect = None
            finish_stale_listing.set()
            finish_browsing()
            finish_browsing()
            list_directory.assert_called_with(self.auth, PurePosixPath(destination))
            self.assertEqual(browser.files.topLevelItem(0).text(0), names[0])
            browser.files.setCurrentItem(browser.files.topLevelItem(0))
            self.assertTrue(browser.download.isEnabled())
            self.assertFalse(browser.open_folder.isEnabled())
            snapshot = jobs.upload_snapshot(self.history, upload_id)
            self.assertTrue(all((snapshot / name).is_file() for name in names))
            self.assertFalse((snapshot / self.entry).exists())
            self.assertEqual(workspace.history.rowCount(), 0)
            launch.assert_not_called()

            list_directory.return_value = [folder(destination)]
            browser.up.click()
            finish_browsing()
            self.assertFalse(workspace.start.isEnabled())
            workspace.inputs.setCurrentItem(workspace.inputs.items()[0])
            workspace.start.click()
            run_upload_id = workspace.upload_task[0].result(timeout=5)
            workspace._check_upload()
            workspace.remote_task[1].result(timeout=5)
            launch.assert_called_once()
            record = launch.call_args.args[1]
            self.assertEqual(record["name"], names[1])
            self.assertEqual(record["folder_upload"], run_upload_id)
            workspace._tick()
            transfer.assert_called_once()
            self.assertTrue(browser.slurm_inputs.isEnabled())
            list_directory.return_value = [SimpleNamespace(filename=names[1], st_mode=S_IFREG, st_size=12)]
            browser.slurm_inputs.click()
            finish_browsing()
            self.assertEqual(browser.address.text(), jobs.upload_location(run_upload_id, self.auth.username) + "/inputs")
            self.assertEqual(browser.files.topLevelItem(0).text(0), names[1])
            self.assertFalse(browser.upload.isEnabled())
            workspace.remote_task[1].result(timeout=5)
            workspace._tick()
            self.assertEqual([call.args[1]["name"] for call in launch.call_args_list], [names[1], names[0]])
            self.assertTrue(all(call.args[1]["state"] == "Submitted" for call in launch.call_args_list))

    def test_reuse_recovers_unsent_slurm_batch_without_resubmitting_accepted_job(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        truba = FakeTruba()
        truba.auth = self.auth
        upload = jobs.FolderUpload("a" * 32, self.auth.username)
        snapshot = jobs.upload_snapshot(self.history, upload.upload_id)
        snapshot.mkdir(parents=True)
        accepted = None
        unsent = []
        for entry in ("01/run.slurm", "02/run.slurm", "03/run.slurm"):
            script = snapshot / entry
            script.parent.mkdir()
            script.write_text("#!/bin/bash\n", encoding="utf-8")
            directory, record = jobs.new_job(
                self.source, entry, "remote", None, parent=self.history,
                batch_folder=self.source.name, folder_upload=upload,
            )
            if accepted is None:
                accepted = directory
                jobs.save_job(directory, record, state="Queued", job_id="7019602")
            else:
                unsent.append(directory)
        with (patch("esen_workspace.QSettings", return_value=preferences),
              patch("truba_files.list_directory", return_value=[])):
            workspace = EsenWorkspace(truba, history=self.history)
        self.addCleanup(workspace.dispose)
        row = next(row for row in range(workspace.history.rowCount())
                   if workspace.history.item(row, 0).data(Qt.ItemDataRole.UserRole) == str(unsent[0]))
        workspace.history.setCurrentCell(row, 0)
        self.assertTrue(workspace.use_inputs.isEnabled())
        workspace.use_inputs.click()
        workspace.folder_scan[0].result(timeout=5)
        workspace._tick()
        self.assertEqual(list(workspace._folder_jobs()), ["02/run.slurm", "03/run.slurm"])

        def submit(directory, record, _files, _settings, _auth):
            jobs.save_job(directory, record, state="Submitted", job_id=directory.name[:6])

        with (patch("esen_jobs.submit_full_node", side_effect=submit) as launch,
              patch("esen_jobs.sync_slurm_folder") as sync):
            workspace.start.click()
            for _ in unsent:
                workspace.remote_task[1].result(timeout=5)
                workspace._tick()
            self.assertEqual([call.args[0] for call in launch.call_args_list], unsent)
            sync.assert_not_called()
        self.assertEqual(workspace.history.rowCount(), 3)
        self.assertEqual(jobs.read_job(accepted)["job_id"], "7019602")

    def test_history_filter_tracks_job_states_and_limits_selection(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        records = {state: self.new_job() for state in ("Running", "Queued", "Completed", "Cancelled", "Failed")}
        for state, job in records.items():
            jobs.save_job(*job, state=state)
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()
        history = workspace.history
        rows = {history.item(row, 1).text(): row for row in range(history.rowCount())}

        self.assertFalse(workspace.active_history_only.isChecked())
        history.setCurrentCell(rows["Completed"], 0)
        workspace.active_history_only.click()
        self.assertIsNone(workspace._selected_directory())
        self.assertIsNone(workspace.record)
        self.assertEqual({state for state, row in rows.items() if not history.isRowHidden(row)}, {"Running", "Queued"})
        self.assertEqual(workspace.queue_model.rowCount(), 2)

        workspace.select_history.click()
        self.assertEqual({workspace._history_directory(item) for item in workspace._history_selection()},
                         {records["Running"][0], records["Queued"][0]})
        self.assertFalse(workspace.select_history.isEnabled())
        workspace.active_history_only.click()
        self.assertTrue(all(not history.isRowHidden(row) for row in rows.values()))
        self.assertEqual(len(workspace._history_selection()), 2)

        workspace.active_history_only.click()
        history.setCurrentCell(rows["Running"], 0)
        jobs.save_job(*records["Running"], state="Cancelled")
        workspace._show_selected()
        self.assertTrue(history.isRowHidden(rows["Running"]))
        self.assertIsNone(workspace.record)
        self.assertEqual(workspace.queue_model.rowCount(), 1)
        self.assertEqual(jobs.read_job(records["Running"][0])["state"], "Cancelled")

    def test_select_all_removes_only_inactive_history_entries(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        first, record = self.new_job("local")
        jobs.save_job(first, record, state="Completed")
        second, record = self.new_job("local")
        jobs.save_job(second, record, state="Failed")
        active, record = self.new_job()
        jobs.save_job(active, record, state="Running", username=self.auth.username)
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()
        QTest.keyClick(workspace.history, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(len(workspace.history.selectionModel().selectedRows()), 3)
        workspace.history.clearSelection()
        workspace.select_history.click()
        self.assertEqual(len(workspace.history.selectionModel().selectedRows()), 3)
        with patch.object(QMessageBox, "question", return_value=QMessageBox.StandardButton.Yes) as confirm:
            workspace.delete_job.click()
        confirm.assert_called_once()
        self.assertIn("2", confirm.call_args.args[2])
        self.assertEqual(workspace.history.rowCount(), 1)
        self.assertTrue(jobs.read_job(first)["hidden_from_history"])
        self.assertTrue(jobs.read_job(second)["hidden_from_history"])
        self.assertFalse(jobs.read_job(active).get("hidden_from_history", False))
        self.assertFalse(workspace.delete_job.isEnabled())

    def test_reuse_multiple_snapshots_creates_jobs_only_when_run(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        originals = {}
        for target in ("local", "remote"):
            directory, record = self.new_job(target)
            snapshot = jobs.snapshot_inputs(directory, *self.files)
            jobs.save_job(directory, record, state="Completed")
            originals[target] = snapshot
        truba = FakeTruba()
        truba.auth = self.auth
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(truba, history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.select_history.click()
        workspace.use_inputs.click()
        self.assertEqual(len(workspace.reuse_batch), 2)
        self.assertEqual(workspace.history.rowCount(), 2)
        self.assertFalse(workspace.batch_queue)
        with (patch("esen_jobs.local_command", return_value=["lmp"]),
              patch.object(workspace, "_run_next_batch_job")):
            workspace.start.click()
        self.assertEqual(workspace.history.rowCount(), 4)
        self.assertEqual(len(workspace.history.selectionModel().selectedRows()), 2)
        self.assertEqual({record["target"]: files[0] for record, files, _ in workspace.batch_queue.values()}, originals)
        self.assertFalse(workspace.reuse_batch)

    def test_refresh_processes_selected_truba_jobs_in_sequence(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        expected = set()
        for number in range(3):
            directory, record = self.new_job()
            owner = self.auth.username if number < 2 else "other-account"
            jobs.save_job(directory, record, state="Queued", username=owner, job_id=str(number + 100))
            if number < 2:
                expected.add(directory)
        truba = FakeTruba()
        truba.auth = self.auth
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(truba, history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.select_history.click()
        with patch("esen_jobs.refresh_remote") as refresh:
            workspace.refresh.click()
            for _ in expected:
                workspace.remote_task[1].result(timeout=5)
                workspace._tick()
            self.assertIsNone(workspace.remote_task)
            self.assertEqual(refresh.call_count, 2)
            self.assertEqual({call.args[0] for call in refresh.call_args_list}, expected)
        self.assertEqual(len(workspace.history.selectionModel().selectedRows()), 3)

    def test_remove_history_entry_preserves_job_files_and_is_persistent(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        directory, record = self.new_job()
        jobs.save_job(directory, record, state="Running", username=self.auth.username)
        output = directory / "stdout.log"
        output.write_text("result", encoding="utf-8")
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
            self.addCleanup(workspace.dispose)
            workspace.show()
            app.processEvents()
            self.assertFalse(workspace.delete_job.isEnabled())
            jobs.save_job(directory, record, state="Completed")
            workspace._show_selected()
            with patch("esen_workspace.QMessageBox.question", return_value=QMessageBox.StandardButton.No):
                workspace.delete_job.click()
            self.assertEqual(workspace.history.rowCount(), 1)
            with patch("esen_workspace.QMessageBox.question", return_value=QMessageBox.StandardButton.Yes):
                workspace.delete_job.click()
            self.assertEqual(workspace.history.rowCount(), 0)
            self.assertEqual(output.read_text(encoding="utf-8"), "result")
            reopened = EsenWorkspace(FakeTruba(), history=self.history)
            self.addCleanup(reopened.dispose)
            self.assertEqual(reopened.history.rowCount(), 0)


if __name__ == "__main__":
    unittest.main()

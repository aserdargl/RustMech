"""Focused regression coverage for Molecular Dynamics and its TRUBA jobs."""

import base64
from io import BytesIO
import os
from pathlib import Path
import tarfile
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QApplication

import esen_jobs as jobs
from esen_workspace import EsenWorkspace
from truba_jobs import RemoteAuth
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

    def test_restartable_refresh_tracks_the_successor(self):
        directory, record = self.new_job()
        jobs.save_job(directory, record, username=self.auth.username, resume_input="restart.in", job_id="123")
        with patch("esen_jobs.RemoteTransport") as factory:
            transport = factory.return_value
            transport.command.return_value = (
                "JOB_ID=456\nAUTO_RESUMED=0\ncompleted=0\ncancelled=0\nHAS_CHECKPOINT=1\n"
                "QUEUE=\nACCT=TIMEOUT|0:0|\nstdout.log=\nstderr.log=\n"
            )
            jobs.refresh_remote(directory, record, self.auth)
            self.assertEqual((record["job_id"], record["state"], record["has_checkpoint"]), ("456", "Failed", True))
            transport.put.assert_not_called()

    def test_automatic_timeout_resume_remains_in_background_tracking(self):
        _app = QApplication.instance() or QApplication([])
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

    @unittest.skipUnless(os.name == "nt", "Windows Job Object")
    def test_md_workspace_restores_maximized_geometry_and_splitters_in_both_tabs(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        store = WindowStateStore(app, preferences)
        self.addCleanup(app.removeEventFilter, store)
        truba = FakeTruba()

        with patch("esen_workspace.QSettings", return_value=preferences):
            first = EsenWorkspace(truba, history=self.history)
            self.addCleanup(first.dispose)
            first.show()
            first.resize(1040, 680)
            first.move(35, 50)
            app.processEvents()
            first.showMaximized()
            app.processEvents()
            splitters = {split.objectName(): split for split in store.splitters(first)}
            splitters["esenMdAlloyAndDetails"].setSizes([460, 610])
            splitters["esenMdAlloyAndSettings"].setSizes([520, 600])
            splitters["esenMdPreviewAndSources"].setSizes([460, 420, 380])
            first.tabs.setCurrentIndex(1)
            app.processEvents()
            splitters["esenMdPlansAndTruba"].setSizes([600, 420])
            expected = {name: split.sizes() for name, split in splitters.items()}
            normal = first.normalGeometry()
            self.assertEqual(store.key(first), "windows/EsenWorkspace/esenWorkspace")
            first.close()

            second = EsenWorkspace(truba, history=self.history)
            self.addCleanup(second.dispose)
            second.show()
            app.processEvents()
            second.tabs.setCurrentIndex(1)
            app.processEvents()

        self.assertTrue(second.isMaximized())
        self.assertEqual(second.normalGeometry(), normal)
        self.assertEqual({split.objectName(): split.sizes() for split in store.splitters(second)}, expected)

    def test_path_validation(self):
        for value in ("../../file", "AUX.log", "C:/data", "a\\b", "foo./bar"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                jobs.relative_file(value)

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
                f"FILES={files}\nLOG=stdout.log\n",
            ]
            jobs.refresh_remote(directory, record, self.auth)
            jobs.remote_files(directory, record, self.auth)
            self.assertNotIn("sbatch", "\n".join(call.args[0] for call in transport.command.call_args_list))
            self.assertEqual(record["state"], "Completed")
            self.assertEqual(record["job_id"], "123")
            self.assertEqual(record["files"], ["stdout.log", "work/sub/result.data"])

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

    def load_batch(self, workspace):
        with patch("esen_workspace.QFileDialog.getExistingDirectory", return_value=str(self.source)):
            workspace.scan_folder_action.trigger()
        workspace.folder_scan[0].result(timeout=5)
        workspace._tick()

if __name__ == "__main__":
    unittest.main()

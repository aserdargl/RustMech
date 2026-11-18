"""Focused regression coverage for ESEN file loading and job lifecycle."""

import base64
import os
from pathlib import Path
import sys
import tarfile
from tempfile import TemporaryDirectory
from threading import Event
import unittest
from unittest.mock import patch

from PySide6.QtCore import QMimeData, QObject, QPoint, QSettings, Qt, QUrl, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import esen_jobs as jobs
from esen_workspace import EsenWorkspace
from truba_jobs import RemoteAuth
from window_state import WindowStateStore


class FakeTruba(QObject):
    connection_changed = Signal(bool)

    def analysis_auth(self):
        return None


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
                "JOB_ID=456\ncompleted=0\ncancelled=0\nHAS_CHECKPOINT=1\n"
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

    def test_local_results_do_not_replace_saved_inputs(self):
        directory, record = self.new_job("local")
        command = [sys.executable, "-c",
                   "from pathlib import Path; Path('data/cell.lmp').write_text('result'); print('finished')"]
        jobs.run_local(directory, record, self.files, command, Event())
        self.assertEqual(jobs.read_job(directory)["state"], "Completed")
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

    def test_full_node_submission_uses_live_partition_and_matching_node_memory(self):
        directory, record = self.new_job()
        settings = dict(partition="smp", account=self.auth.username, walltime="00:02:00",
                        module=jobs.DEFAULT_MODULE, executable="lmp", launcher="srun")
        catalog_reply = (
            "PartitionName=smp State=UP MaxCPUsPerNode=256 MaxMemPerCPU=12000 MaxTime=3-00:00:00\n"
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

    def test_cancel_waits_for_slurm_to_confirm(self):
        directory, record = self.new_job()
        jobs.save_job(directory, record, username=self.auth.username, job_id="456", state="Running")
        with patch("esen_jobs.RemoteTransport") as factory:
            transport = factory.return_value
            transport.command.side_effect = [
                "", "QUEUE=COMPLETING|node\nACCT=RUNNING|0:0\nstdout.log=\nstderr.log=\n",
                "QUEUE=\nACCT=CANCELLED by 42|0:15\nstdout.log=\nstderr.log=\n",
            ]
            jobs.refresh_remote(directory, record, self.auth, cancel=True)
            self.assertEqual(record["state"], "Cancellation requested")
            jobs.refresh_remote(directory, record, self.auth)
            self.assertEqual(record["state"], "Cancelled")
            self.assertEqual(transport.command.call_args_list[0].args[0], "scancel 456")

    def test_load_files_then_choose_script_and_run(self):
        app = QApplication.instance() or QApplication([])
        preferences = QSettings(str(self.root / "settings.ini"), QSettings.Format.IniFormat)
        files = [str(self.source / name) for name in (self.entry, "data/cell.lmp")]
        with patch("esen_workspace.QSettings", return_value=preferences):
            workspace = EsenWorkspace(FakeTruba(), history=self.history)
        self.addCleanup(workspace.dispose)
        workspace.show()
        app.processEvents()

        with patch("esen_workspace.QFileDialog.getOpenFileNames", return_value=(files, "")):
            QTest.mouseClick(workspace.load_files, Qt.MouseButton.LeftButton)
        self.assertEqual(workspace.input_root, self.source)
        self.assertFalse(workspace.start.isEnabled())
        self.assertEqual(workspace.history.count(), 0)
        QTest.mouseClick(workspace.use_as_script, Qt.MouseButton.LeftButton)
        self.assertEqual(workspace.entry, self.entry)
        self.assertEqual(workspace.extra, ["data/cell.lmp"])

        with patch("esen_jobs.run_local") as run, patch("esen_jobs.local_command", return_value=["lmp"]):
            workspace.local_memory.setValue(1536)
            workspace._preview()
            QTest.mouseClick(workspace.start, Qt.MouseButton.LeftButton)
            workspace.local_task[1].result(timeout=5)
            self.assertEqual(run.call_args.args[2], self.files)
            self.assertEqual(run.call_args.kwargs["memory_mib"], 1536)
        workspace.target.setCurrentIndex(1)
        self.assertFalse(workspace.ranks.isVisible())
        self.assertFalse(workspace.local_memory.isVisible())
        self.assertIn("#SBATCH --ntasks=", workspace.preview.toPlainText())
        self.assertNotIn("#SBATCH --mem=1536M", workspace.preview.toPlainText())
        workspace.target.setCurrentIndex(0)
        self.assertTrue(workspace.local_memory.isVisible())
        self.assertEqual(workspace.local_memory.value(), 1536)

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
        self.assertEqual(workspace.history.count(), 0)

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
        first.target.setCurrentIndex(1)
        first.resize(720, 680)
        first.move(25, 25)
        app.processEvents()
        first.account.setFocus()
        QTest.keyClick(first.account, Qt.Key.Key_Return)
        self.assertTrue(first.isVisible())
        self.assertEqual(first.history.count(), 0)
        first.showMaximized()
        app.processEvents()
        splitter = store.splitters(first)[0]
        splitter.setSizes([340, 430])
        expected = splitter.sizes()
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


if __name__ == "__main__":
    unittest.main()

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from learning_data import METHODS, read_dataset, target_columns, training_request
from learning_training import missing_dependencies, run_local_training
from remote_operations import required_capability
from remote_worker import run_operation


class LearningTrainingTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.csv = self.root / "learning.csv"
        self.target = "result.summary.graphs.maximum_force_N"
        columns = ["Sample", "Operation", "Status", "settings.solid_fraction",
                   "settings.mesh_resolution", "Geometry settings (JSON)", self.target, "table.curve"]
        with self.csv.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            for i in range(11):
                density = 20 + 2 * (i // 2)
                settings = dict(structure_family="tpms", structure_name="Gyroid", size=12,
                                cell_size=4, solid_fraction=density, random_seed=i)
                writer.writerow([f"sample-{i}", "mechanical", "Completed" if i < 10 else "Failed",
                                 density, .2 + .05 * (i % 2), json.dumps(settings), density * 10, "x" * 140_000])
        self.dataset = read_dataset(self.csv)

    def test_freezes_geometry_group_across_discretization_and_ignores_results_as_inputs(self):
        self.assertEqual(target_columns(*self.dataset), [self.target])
        self.assertNotIn("table.curve", self.dataset[0])
        request = training_request(self.dataset, self.target, METHODS, 2, 1)
        self.assertEqual(required_capability(request), "learning")
        self.assertEqual([row["group"] for row in request["samples"]], [i // 2 for i in range(10)])
        self.assertEqual(request["features"], ["settings.solid_fraction", "settings.mesh_resolution"])
        self.assertNotIn(str(self.csv), json.dumps(request))

    def test_doe_excel_report_and_csv_produce_the_same_training_request(self):
        from new_doe_report import write_doe_report, write_learning_csv

        columns = ["Sample", "Operation", "settings.solid_fraction"]
        with (self.root / "matrix.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.writer(stream)
            writer.writerow(columns)
            for i in range(1, 7):
                density = 20 + i
                writer.writerow([f"EXP-{i:04d}", "mechanical", density])
                folder = self.root / f"row_{i:05d}"
                folder.mkdir()
                settings = dict(structure_family="tpms", structure_name="Gyroid", size=12,
                                cell_size=4, solid_fraction=density)
                (folder / "experiment.json").write_text(
                    json.dumps(dict(state="Completed" if i < 6 else "Failed", inputs=dict(settings=settings))),
                    encoding="utf-8")
                (folder / "summary.json").write_text(
                    json.dumps(dict(graphs=dict(maximum_force_N=density * 10))),
                    encoding="utf-8")
        excel = read_dataset(write_doe_report(self.root))
        csv_data = read_dataset(write_learning_csv(self.root))
        self.assertEqual(target_columns(*excel), [self.target])
        self.assertEqual(len(excel[1]), 5)
        self.assertEqual(training_request(excel, self.target, ["gp", "cnn"], 2, 1),
                         training_request(csv_data, self.target, ["gp", "cnn"], 2, 1))

    def test_damaged_excel_is_reported_as_a_dataset_error(self):
        workbook = self.root / "invalid.xlsx"
        workbook.write_text("damaged workbook", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid training workbook"):
            read_dataset(workbook)

    def test_training_uses_the_same_split_in_local_and_remote_worker(self):
        methods = [method for method in METHODS if not missing_dependencies([method])]
        if "gp" not in methods:
            self.skipTest("Optional training dependencies are unavailable.")
        request = training_request(self.dataset, self.target, methods, 2, 1)
        local = self.root / "local"
        remote = self.root / "remote"
        run_local_training(request, local)
        with patch("remote_worker._stage"), patch.dict("os.environ", {"SLURM_CPUS_PER_TASK": "2"}):
            manifest = run_operation(request, remote)
        self.assertEqual(manifest["training_report"], "training_report.json")
        for directory in (local, remote):
            report = json.loads((directory / "training_report.json").read_text())
            self.assertEqual(set(report["results"]), set(methods))
            self.assertEqual(report["groups"], {"train": 3, "validation": 1, "test": 1})
            for scores in report["results"].values():
                self.assertTrue((directory / scores["artifact"]).is_file())
                self.assertGreaterEqual(scores["rmse"] + 1e-8, scores["mae"])
            with (directory / "predictions.csv").open(encoding="utf-8-sig", newline="") as stream:
                rows = list(csv.DictReader(stream))
            assignments = {}
            for row in rows:
                self.assertEqual(assignments.setdefault(row["geometry_group"], row["split"]), row["split"])
                self.assertEqual(bool(row["gp"]), row["split"] == "test")
        self.assertEqual((local / "predictions.csv").read_text().splitlines()[0],
                         (remote / "predictions.csv").read_text().splitlines()[0])

"""Shared local and Slurm training entry point."""

from contextlib import nullcontext
import csv
from importlib import import_module
import json
import os
from pathlib import Path

from learning_data import METHODS, SPATIAL


NEURAL = {"mlp", *SPATIAL}
DEPENDENCIES = {
    "numpy": "numpy>=2,<3",
    "sklearn": "scikit-learn>=1.8,<2",
    "catboost": "catboost>=1.2.10,<2",
    "xgboost": "xgboost-cpu>=3.1,<4",
    "torch": "torch>=2.11,<3",
}
METHOD_DEPENDENCY = {"catboost": "catboost", "xgboost": "xgboost",
                     **dict.fromkeys(NEURAL, "torch")}


def missing_dependencies(methods):
    modules = {"numpy", "sklearn", *(METHOD_DEPENDENCY[m] for m in methods if m in METHOD_DEPENDENCY)}
    missing = {}
    for name in sorted(modules):
        try:
            import_module(name)
        except Exception as error:
            missing[name] = str(error)
    return missing


def run_local_training(request, directory):
    from analysis_process import report_analysis_step

    missing = missing_dependencies(request["methods"])
    if missing:
        hints = " ".join(DEPENDENCIES[name] for name in missing)
        raise RuntimeError(f"Training packages are unavailable: {', '.join(missing)}. "
                           f"See README.md for local setup. Required packages: {hints}.")
    train(request, directory, report_analysis_step)
    return str(directory)


def _feature_dicts(rows, columns, categorical):
    for sample in rows:
        yield {column: sample["values"][i] for i, column in enumerate(columns)
               if column in categorical or sample["values"][i] is not None}


def _table_model(method, cores, values, y, training, validation):
    if method == "gp":
        from sklearn.gaussian_process import GaussianProcessRegressor
        from sklearn.gaussian_process.kernels import ConstantKernel, Matern, WhiteKernel

        kernel = ConstantKernel() * Matern(nu=2.5) + WhiteKernel(noise_level=1e-3)
        model = GaussianProcessRegressor(kernel=kernel, normalize_y=True, random_state=42)
        model.fit(values[training], y[training])
    elif method == "catboost":
        from catboost import CatBoostRegressor

        model = CatBoostRegressor(iterations=500, depth=6, learning_rate=.05, random_seed=42,
                                  thread_count=cores, verbose=False, allow_writing_files=False)
        model.fit(values[training], y[training], eval_set=(values[validation], y[validation]),
                  early_stopping_rounds=30)
    else:
        from xgboost import XGBRegressor

        model = XGBRegressor(n_estimators=500, max_depth=6, learning_rate=.05, random_state=42,
                             n_jobs=cores, early_stopping_rounds=30)
        model.fit(values[training], y[training], eval_set=[(values[validation], y[validation])], verbose=False)
    return model


def train(request, directory, progress=print):
    import joblib
    import numpy as np
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.model_selection import GroupShuffleSplit
    from sklearn.preprocessing import StandardScaler
    from threadpoolctl import threadpool_limits

    methods, rows = request["methods"], request["samples"]
    if not methods or len(set(methods)) != len(methods) or any(method not in METHODS for method in methods):
        raise ValueError("Invalid training methods.")
    epochs, patience = request["epochs"], request["patience"]
    if not 1 <= patience <= epochs <= 2000:
        raise ValueError("Epochs and patience must satisfy 1 ≤ patience ≤ epochs ≤ 2000.")
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    progress("Preparing training, validation and test geometry groups")
    groups = np.array([row["group"] for row in rows])
    y = np.array([row["target"] for row in rows], dtype=float)
    if len(set(groups)) < 5 or not np.isfinite(y).all():
        raise ValueError("Training needs finite targets from at least five distinct geometry groups.")
    remaining, test = next(GroupShuffleSplit(n_splits=1, test_size=.2, random_state=42).split(y, groups=groups))
    train_idx, val_idx = next(GroupShuffleSplit(n_splits=1, test_size=.25, random_state=43)
                              .split(y[remaining], groups=groups[remaining]))
    training, validation = remaining[train_idx], remaining[val_idx]
    columns = request["features"]
    vectorizer, scaler = None, None
    if columns:
        dictionaries = list(_feature_dicts(rows, columns, set(request["categorical"])))
        vectorizer = DictVectorizer(sparse=False)
        vectorizer.fit([dictionaries[i] for i in training])
        raw = vectorizer.transform(dictionaries)
        scaler = StandardScaler().fit(raw[training]) if raw.shape[1] else None
        values = scaler.transform(raw) if scaler is not None else raw
    else:
        values = np.empty((len(rows), 0))
    if values.shape[1] == 0 and any(method not in SPATIAL for method in methods):
        raise ValueError("No usable tabular features are present in the training groups.")
    joblib.dump(dict(vectorizer=vectorizer, scaler=scaler, features=columns,
                     categorical=request["categorical"]), directory / "features.joblib")

    grids = None
    if SPATIAL.intersection(methods):
        from learning_networks import VOXELS, occupancy

        grids = np.empty((len(rows), VOXELS, VOXELS, VOXELS), dtype=np.uint8)
        for i, row in enumerate(rows):
            if i % 20 == 0:
                progress(f"Sampling geometry {i + 1}/{len(rows)}")
            grids[i] = occupancy(row["geometry"])
    cores = int(os.environ.get("SLURM_CPUS_PER_TASK", min(8, os.cpu_count() or 1)))
    split = {"train": training, "validation": validation, "test": test}
    packages = {"numpy", "sklearn", "joblib", *(METHOD_DEPENDENCY[m] for m in methods if m in METHOD_DEPENDENCY)}
    versions = {package: str(import_module(package).__version__) for package in sorted(packages)}
    report = dict(schema_version=1, target=request["target"], features=columns,
                  dataset_sha256=request["dataset_sha256"], seed=42,
                  epochs=epochs, patience=patience, packages=versions,
                  samples={key: len(indices) for key, indices in split.items()},
                  groups={key: len(set(groups[indices])) for key, indices in split.items()},
                  mean_baseline_mae=float(np.abs(y[test] - y[training].mean()).mean()), results={})
    if NEURAL.intersection(methods):
        import torch

        torch.set_num_threads(cores)
    predictions = {}
    for method in methods:
        progress(f"Training {METHODS[method]}")
        with threadpool_limits(limits=cores) if method not in NEURAL else nullcontext():
            if method in NEURAL:
                from learning_networks import fit_network

                model, predicted, details = fit_network(
                    method, values, grids if method in SPATIAL else None, y, training, validation,
                    test, epochs=epochs, patience=patience, progress=progress)
                artifact = f"{method}.pt"
                torch.save(model, directory / artifact)
            else:
                model = _table_model(method, cores, values, y, training, validation)
                details = {}
                artifact = f"{method}.joblib"
                if method == "gp":
                    predicted, deviation = model.predict(values[test], return_std=True)
                    predictions["gp_std"] = deviation
                else:
                    predicted = model.predict(values[test])
                joblib.dump(model, directory / artifact)
        error = np.asarray(predicted, dtype=float) - y[test]
        report["results"][method] = dict(artifact=artifact, mae=float(np.abs(error).mean()),
                                         rmse=float(np.sqrt(np.mean(error ** 2))), **details)
        predictions[method] = predicted

    test_position = {index: pos for pos, index in enumerate(test)}
    with (directory / "predictions.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream)
        writer.writerow(["sample", "geometry_group", "split", "actual", *predictions])
        for name, indices in split.items():
            for i in indices:
                writer.writerow([rows[i]["sample"], groups[i], name, y[i],
                                 *(values[test_position[i]] if name == "test" else "" for values in predictions.values())])
    report_path = directory / "training_report.json"
    temporary = report_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=False) + "\n", encoding="utf-8")
    temporary.replace(report_path)
    return report

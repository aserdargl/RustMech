"""Train a small SEA surrogate from the RustMech DoE CSV and resume by batch."""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import time

STARTED_AT = time.monotonic()

import torch
from torch import nn

FEATURES = ("Cube size (mm)", "Cell / Pore (mm)", "Target Density (%)")
TARGET = "SEA (J/g)"
SPLITS = ("train", "validation", "test")
CHECKPOINT_INTERVAL = 1800
RESUME_EXIT = 75
SEED = 42


def load_dataset(raw):
    data = {name: [] for name in SPLITS}
    groups = {}
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    required = {*FEATURES, TARGET, "Status", "Dataset split", "Geometry group"}
    if not required <= set(reader.fieldnames or ()):
        raise ValueError("Export a current RustMech DoE CSV with split metadata.")
    for row in reader:
        if row["Status"].strip().lower() != "completed":
            continue
        split, group = row["Dataset split"], row["Geometry group"]
        values = [float(row[key]) for key in (*FEATURES, TARGET)]
        if split not in data or not group or not all(map(math.isfinite, values)):
            raise ValueError(f"Invalid completed experiment on CSV line {reader.line_num}.")
        if groups.setdefault(group, split) != split:
            raise ValueError(f"Geometry group crosses data splits: {group}")
        data[split].append(values)
    if any(not values for values in data.values()):
        raise ValueError("Each split needs at least one completed experiment.")
    return {key: torch.tensor(value, dtype=torch.float32) for key, value in data.items()}


def atomic_write(path, write):
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        write(stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def train(args):
    raw = args.dataset.read_bytes()
    data = load_dataset(raw)
    signature = (hashlib.sha256(raw).hexdigest(), args.epochs, args.batch_size)
    args.run_dir.mkdir(parents=True, exist_ok=True)
    checkpoint = args.run_dir / "checkpoint.pt"
    report = args.run_dir / "metrics.json"
    torch.manual_seed(SEED)
    model = nn.Sequential(nn.Linear(3, 32), nn.ReLU(), nn.Linear(32, 16),
                          nn.ReLU(), nn.Linear(16, 1))
    optimizer = torch.optim.Adam(model.parameters())
    scheduler = torch.optim.lr_scheduler.ExponentialLR(optimizer, gamma=0.99)
    center = data["train"].mean(dim=0)
    scale = data["train"].std(dim=0, unbiased=False).clamp_min(1e-8)
    normalized = {key: (value - center) / scale for key, value in data.items()}
    state = dict(epoch=0, batch=0, best_loss=float("inf"), best=None)
    if checkpoint.exists():
        state = torch.load(checkpoint, map_location="cpu", weights_only=True)
        if state["signature"] != signature:
            raise ValueError("Dataset or training settings changed. Use a new run directory.")
        model.load_state_dict(state["model"])
        optimizer.load_state_dict(state["optimizer"])
        scheduler.load_state_dict(state["scheduler"])
        torch.set_rng_state(state["rng"])
        if report.exists() and state["epoch"] == args.epochs:
            return 0
        print(f'Resuming epoch {state["epoch"]}, batch {state["batch"]}', flush=True)

    def save():
        state.update(signature=signature, model=model.state_dict(), optimizer=optimizer.state_dict(),
                     scheduler=scheduler.state_dict(), rng=torch.get_rng_state())
        atomic_write(checkpoint, lambda stream: torch.save(state, stream))

    last_save = time.monotonic()
    deadline = STARTED_AT + args.segment_seconds
    train_data = normalized["train"]
    while state["epoch"] < args.epochs:
        generator = torch.Generator().manual_seed(SEED + state["epoch"])
        batches = torch.randperm(len(train_data), generator=generator).split(args.batch_size)
        for index in range(state["batch"], len(batches)):
            batch = train_data[batches[index]]
            optimizer.zero_grad()
            loss = nn.functional.mse_loss(model(batch[:, :-1]).flatten(), batch[:, -1])
            loss.backward()
            optimizer.step()
            state["batch"] = index + 1
            now = time.monotonic()
            if now >= deadline or now - last_save >= CHECKPOINT_INTERVAL:
                save()
                last_save = now
                if now >= deadline:
                    print(f'Checkpoint at epoch {state["epoch"]}, batch {state["batch"]}', flush=True)
                    return RESUME_EXIT
        with torch.no_grad():
            validation = normalized["validation"]
            loss = nn.functional.l1_loss(model(validation[:, :-1]).flatten(), validation[:, -1]).item()
            if loss < state["best_loss"]:
                state["best_loss"] = loss
                state["best"] = {key: tensor.clone() for key, tensor in model.state_dict().items()}
        scheduler.step()
        state["epoch"] += 1
        state["batch"] = 0

    save()
    model.load_state_dict(state["best"])
    with torch.no_grad():
        prediction = model(normalized["test"][:, :-1]).flatten() * scale[-1] + center[-1]
        mae = (prediction - data["test"][:, -1]).abs().mean().item()
    artifact = dict(model=model.state_dict(), center=center, scale=scale, features=FEATURES, target=TARGET)
    atomic_write(args.run_dir / "model.pt", lambda stream: torch.save(artifact, stream))
    metrics = {"test_mae_j_g": mae, "rows": {key: len(value) for key, value in data.items()}}
    predictions = io.StringIO()
    writer = csv.writer(predictions)
    writer.writerow([*FEATURES, "Simulated SEA (J/g)", "Predicted SEA (J/g)"])
    writer.writerows([*row, estimate] for row, estimate in zip(data["test"].tolist(), prediction.tolist()))
    atomic_write(args.run_dir / "test_predictions.csv",
                 lambda stream: stream.write(predictions.getvalue().encode()))
    atomic_write(report, lambda stream: stream.write(json.dumps(metrics, indent=2).encode()))
    print(json.dumps(metrics), flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--segment-seconds", type=float, default=70 * 3600)
    options = parser.parse_args()
    if min(options.epochs, options.batch_size) < 1 or not 0 <= options.segment_seconds <= 70 * 3600:
        parser.error("Use positive epochs/batch size and a segment between 0 and 70 hours.")
    torch.set_num_threads(1)
    raise SystemExit(train(options))

"""Disposable training process. It never connects to the database or publishes."""

from __future__ import annotations

import fcntl
import importlib.util
import json
from pathlib import Path
import sys


def main():
    stage = Path(sys.argv[1])
    # A crashed parent's child may briefly outlive it. The reconciler must not
    # remove its directory while it still holds this process-scoped lock.
    with (stage / ".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        spec = importlib.util.spec_from_file_location("frozen_training_engine", stage / "forecast.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        inputs = json.loads((stage / "training-input.json").read_text())
        evaluation = module.train_artifacts(inputs["records"], stage, inputs["lineage"])
        (stage / "evaluation.json").write_text(json.dumps(evaluation, allow_nan=False))


if __name__ == "__main__":
    main()

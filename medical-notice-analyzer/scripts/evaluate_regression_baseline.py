from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Sequence


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.offline_quality_evaluator import (  # noqa: E402
    _validate_evaluation_for_freeze,
    compare_evaluation_to_baseline,
    evaluate_artifact_from_snapshots,
    load_json,
)


def _write_json_atomically(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _verify_gate(evaluation: dict[str, object]) -> None:
    _validate_evaluation_for_freeze(evaluation)
    metrics = evaluation["metrics"]
    completed = int(metrics["completed_sample_count"])
    required_zero = (
        "failed_count",
        "forbidden_phrase_hit_count",
        "state_contradiction_count",
        "missing_workflow_run_id_count",
    )
    failed = [key for key in required_zero if int(metrics[key]) != 0]
    if int(metrics["identity_hash_complete_count"]) != completed:
        failed.append("identity_hash_complete_count")
    if int(metrics["metrics_complete_count"]) != completed:
        failed.append("metrics_complete_count")
    if failed:
        raise ValueError(f"regression gate failed: {', '.join(failed)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate an 8099 regression artifact offline")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--verify-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    artifact = load_json(args.artifact)
    evaluation = evaluate_artifact_from_snapshots(
        args.artifact_root or args.artifact.parent,
        artifact,
    )
    replay = evaluation["snapshot_verification"]
    if args.baseline:
        evaluation["baseline_comparison"] = compare_evaluation_to_baseline(
            evaluation, load_json(args.baseline)
        )
    if args.verify_only:
        _verify_gate(evaluation)
        expected = int(evaluation["metrics"]["completed_sample_count"])
        if replay["verified"] < expected:
            raise ValueError("not every completed sample has a replayable snapshot")
        if evaluation.get("baseline_comparison", {}).get("passed") is False:
            raise ValueError("regression baseline comparison failed")
    if args.output:
        _write_json_atomically(args.output, evaluation)
    print(json.dumps(evaluation, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

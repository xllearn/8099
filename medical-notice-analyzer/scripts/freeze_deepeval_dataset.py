from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.deepeval_advisory.datasets import (
    Fixed10FreezeError,
    freeze_calibration100_dataset,
    freeze_fixed10_dataset,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Freeze an immutable offline DeepEval advisory dataset.",
    )
    parser.add_argument(
        "dataset",
        choices=("fixed10", "calibration100"),
    )
    parser.add_argument(
        "--source-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--output-dir",
        required=True,
        type=Path,
    )
    parser.add_argument(
        "--lock-timeout-seconds",
        default=10.0,
        type=float,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    freeze = {
        "fixed10": freeze_fixed10_dataset,
        "calibration100": freeze_calibration100_dataset,
    }[arguments.dataset]
    try:
        status = freeze(
            arguments.source_dir,
            arguments.output_dir,
            lock_timeout_seconds=arguments.lock_timeout_seconds,
        )
    except Fixed10FreezeError as exc:
        print(
            json.dumps(
                {
                    "category": exc.category,
                    "error": f"{arguments.dataset} dataset freeze failed",
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            file=sys.stderr,
        )
        return 2
    print(
        json.dumps(
            {
                "dataset_version": f"{arguments.dataset}/v1",
                "status": status,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

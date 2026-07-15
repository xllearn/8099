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
    BASELINE_COVERAGE_MODES,
    freeze_baseline,
    load_json,
)
from app.regression_manifest import (  # noqa: E402
    build_manifest_contract,
    load_manifest,
    manifest_sha256,
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freeze the 8099 regression baseline")
    parser.add_argument("--baseline-id", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evaluation", type=Path, action="append", required=True)
    parser.add_argument(
        "--coverage-mode",
        choices=BASELINE_COVERAGE_MODES,
        default="full",
    )
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    manifest = load_manifest(args.manifest)
    manifest_contract = build_manifest_contract(manifest)
    baseline = freeze_baseline(
        baseline_id=args.baseline_id,
        manifest_sha256=manifest_sha256(manifest_contract),
        evaluations=[load_json(path) for path in args.evaluation],
        coverage_mode=args.coverage_mode,
    )
    _write_json_atomically(args.output, baseline)
    print(json.dumps(baseline, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

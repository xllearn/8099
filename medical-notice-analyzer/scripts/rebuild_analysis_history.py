from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.analysis_history import AnalysisHistoryStore  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Rebuild the analysis history index")
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path(os.getenv("ANALYSIS_RUN_DIR", "/app/data/analysis_runs")),
    )
    parser.add_argument(
        "--history-dir",
        type=Path,
        default=Path(os.getenv("ANALYSIS_HISTORY_DIR", "/app/data/analysis_history")),
    )
    parser.add_argument(
        "--evidence-pack-dir",
        type=Path,
        default=Path(os.getenv("EVIDENCE_PACK_DIR", "/app/data/evidence_packs")),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    store = AnalysisHistoryStore(args.history_dir, enforce_single_worker=False)
    result = store.rebuild_from_run_dir(
        args.run_dir,
        evidence_pack_dir=args.evidence_pack_dir,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from app.deepeval_advisory.reporting import (  # noqa: E402
    build_safe_snapshot,
    start_reporting_server,
)
from app.deepeval_advisory.settings import (  # noqa: E402
    AdvisorySettings,
    SettingsError,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the non-blocking DeepEval advisory sidecar."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true")
    mode.add_argument("--pause", action="store_true")
    mode.add_argument("--resume", action="store_true")
    mode.add_argument("--status", action="store_true")
    return parser


def _state_dir() -> Path:
    raw = os.environ.get("DEEPEVAL_ADVISORY_DIR", "").strip()
    path = Path(raw)
    if not raw or not path.is_absolute():
        raise SettingsError(
            "DEEPEVAL_ADVISORY_DIR must be an absolute path"
        )
    return path


def _set_runtime_paused(state_dir: Path, paused: bool) -> None:
    control = state_dir / "control"
    control.mkdir(parents=True, exist_ok=True)
    marker = control / "runtime-paused"
    if paused:
        marker.touch(exist_ok=True)
    else:
        marker.unlink(missing_ok=True)


def _runtime_is_paused(state_dir: Path) -> bool:
    return (state_dir / "control" / "runtime-paused").is_file()


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.pause or args.resume or args.status:
            state_dir = _state_dir()
            if args.pause:
                _set_runtime_paused(state_dir, True)
            elif args.resume:
                _set_runtime_paused(state_dir, False)
            snapshot = build_safe_snapshot(state_dir)
            snapshot["runtime_paused"] = _runtime_is_paused(state_dir)
            print(
                json.dumps(
                    snapshot,
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 0

        settings = AdvisorySettings.from_mapping(os.environ)
        from app.deepeval_advisory.worker import AdvisoryWorker

        worker = AdvisoryWorker(settings)
        if args.once:
            summary = asyncio.run(worker.run_once())
            print(
                json.dumps(
                    build_safe_snapshot(settings.advisory_dir),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            return 1 if summary.discovery_errors else 0

        server, thread = start_reporting_server(
            settings.advisory_dir,
            host=settings.http_host,
            port=settings.http_port,
        )
        try:
            asyncio.run(worker.run_forever())
        except KeyboardInterrupt:
            return 0
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)
    except (SettingsError, RuntimeError, ValueError) as exc:
        print(f"deepeval advisory unavailable: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

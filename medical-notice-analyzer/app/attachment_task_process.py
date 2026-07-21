from __future__ import annotations

import importlib
import multiprocessing
import os
import shutil
import signal
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Mapping


_PROCESS_SLOTS = threading.BoundedSemaphore(3)


class AttachmentTaskProcessError(RuntimeError):
    pass


class AttachmentTaskProcessTimeout(AttachmentTaskProcessError):
    pass


def _resolve_worker(worker_path: str):
    module_name, separator, function_name = str(worker_path or "").partition(":")
    if not separator or not module_name or not function_name:
        raise AttachmentTaskProcessError("invalid attachment worker path")
    worker = getattr(importlib.import_module(module_name), function_name, None)
    if not callable(worker):
        raise AttachmentTaskProcessError("attachment worker is not callable")
    return worker


def _child_entry(
    connection: Any,
    worker_path: str,
    row: dict[str, Any],
    options: dict[str, Any],
    temporary_root: str,
) -> None:
    try:
        if os.name == "posix":
            os.setsid()
        os.environ["TMPDIR"] = temporary_root
        os.environ["TMP"] = temporary_root
        os.environ["TEMP"] = temporary_root
        tempfile.tempdir = temporary_root
        result = _resolve_worker(worker_path)(row, options)
        connection.send(("ok", result))
    except BaseException as exc:  # noqa: BLE001
        try:
            connection.send(("error", exc.__class__.__name__))
        except Exception:  # noqa: BLE001
            pass
    finally:
        connection.close()


def _terminate_process_tree(process: multiprocessing.Process) -> None:
    if not process.is_alive():
        process.join(timeout=0.2)
        return
    if os.name == "posix" and process.pid:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            process.terminate()
    else:
        process.terminate()
    process.join(timeout=0.5)
    if not process.is_alive():
        return
    if os.name == "posix" and process.pid:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            process.kill()
    else:
        process.kill()
    process.join(timeout=1.0)


def run_attachment_task_isolated(
    row: Mapping[str, Any],
    options: Mapping[str, Any],
    *,
    worker_path: str,
    timeout_seconds: float,
    cancel_event: threading.Event | None = None,
) -> Any:
    timeout = max(0.01, float(timeout_seconds))
    deadline = time.monotonic() + timeout
    if not _PROCESS_SLOTS.acquire(timeout=timeout):
        raise AttachmentTaskProcessTimeout("attachment process slot timeout")
    process: multiprocessing.Process | None = None
    receive_connection: Any = None
    send_connection: Any = None
    temporary_root: Path | None = None
    try:
        remaining = max(0.0, deadline - time.monotonic())
        if remaining <= 0:
            raise AttachmentTaskProcessTimeout("attachment task deadline exceeded")
        configured_root = str(os.getenv("ATTACHMENT_TEMP_DIR") or "").strip()
        parent = Path(configured_root) if configured_root else None
        if parent is not None:
            parent.mkdir(parents=True, exist_ok=True)
        temporary_root = Path(
            tempfile.mkdtemp(prefix=".attachment-task-", dir=str(parent) if parent else None)
        )
        context = multiprocessing.get_context("spawn")
        receive_connection, send_connection = context.Pipe(duplex=False)
        process = context.Process(
            target=_child_entry,
            args=(
                send_connection,
                worker_path,
                dict(row),
                dict(options),
                str(temporary_root),
            ),
            name="attachment-task-process",
            daemon=True,
        )
        process.start()
        send_connection.close()
        send_connection = None
        while True:
            if cancel_event is not None and cancel_event.is_set():
                raise AttachmentTaskProcessTimeout("attachment task cancelled")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise AttachmentTaskProcessTimeout("attachment task deadline exceeded")
            if receive_connection.poll(min(0.05, remaining)):
                try:
                    status, payload = receive_connection.recv()
                except EOFError as exc:
                    raise AttachmentTaskProcessError("attachment worker returned no result") from exc
                process.join(timeout=0.5)
                if process.is_alive():
                    _terminate_process_tree(process)
                if status == "ok":
                    return payload
                raise AttachmentTaskProcessError(f"attachment worker failed:{payload}")
            if not process.is_alive():
                process.join(timeout=0.2)
                raise AttachmentTaskProcessError(
                    f"attachment worker exited without result:{process.exitcode}"
                )
    finally:
        if process is not None:
            _terminate_process_tree(process)
            process.close()
        if receive_connection is not None:
            receive_connection.close()
        if send_connection is not None:
            send_connection.close()
        if temporary_root is not None:
            shutil.rmtree(temporary_root, ignore_errors=True)
        _PROCESS_SLOTS.release()

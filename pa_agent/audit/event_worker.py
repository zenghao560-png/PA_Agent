"""Bounded daemon worker: saved files are the only input, never live records."""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import queue
import threading
from pathlib import Path
from typing import Any

from .event_evidence import AUDIT_VERSION, calculate_event_evidence
from .event_repository import save_event_audit

_log = logging.getLogger(__name__)


class EventAuditWorker:
    def __init__(self, *, directory: Path | None = None, max_pending: int = 32):
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        self.directory = directory
        self.queue = queue.Queue(maxsize=max_pending)
        self.lock = threading.Lock()
        self.pending = set()
        self.stopped = False
        self.thread = None

    def submit(self, source_path: Path) -> bool:
        path = Path(source_path).resolve()
        with self.lock:
            if self.stopped or path in self.pending:
                return False
            if self.thread is None:
                try:
                    thread = threading.Thread(target=self._loop, daemon=True, name="pa-event-audit")
                    thread.start()
                except (RuntimeError, OSError):
                    _log.exception("event audit worker could not start; submission skipped")
                    return False
                self.thread = thread
            try:
                self.queue.put_nowait(path)
            except queue.Full:
                _log.warning("event audit queue full; skipped %s", path.name)
                return False
            self.pending.add(path)
            return True

    def _process(self, path: Path) -> None:
        # save_full returns a path even on failure: require a complete on-disk record.
        raw = path.read_bytes()
        data = json.loads(raw)
        required = ("meta", "kline_data", "stage1_diagnosis", "stage2_decision")
        if (
            any(key not in data for key in required)
            or data.get("_partial_reason")
            or data.get("exception")
            or not isinstance(data.get("stage1_diagnosis"), dict)
            or not isinstance(data.get("stage2_decision"), dict)
            or not isinstance(data.get("kline_data"), list)
        ):
            raise ValueError("incomplete analysis record")
        meta = data["meta"]
        if not all(meta.get(key) for key in ("symbol", "timeframe", "timestamp_local_ms")):
            raise ValueError("missing record identity")
        source_hash = hashlib.sha256(raw).hexdigest()
        decision_id = hashlib.sha256(str(path).encode()).hexdigest()[:24]
        payload = {
            "audit_id": f"{decision_id}:{AUDIT_VERSION}",
            "decision_id": decision_id,
            "source_record": str(path),
            "source_sha256": source_hash,
            "symbol": meta["symbol"],
            "timeframe": meta["timeframe"],
            "analysis_timestamp_ms": meta["timestamp_local_ms"],
            **calculate_event_evidence(data),
        }
        save_event_audit(payload, directory=self.directory)

    def _loop(self):
        while True:
            try:
                path = self.queue.get(timeout=0.1)
            except queue.Empty:
                with self.lock:
                    if self.stopped:
                        return
                continue
            try:
                self._process(path)
            except Exception:  # noqa: BLE001 -- audit failures must stay isolated
                _log.exception("event audit failed for %s", path.name)
            finally:
                with self.lock:
                    self.pending.discard(path)
                self.queue.task_done()

    def shutdown(self, wait: bool = False) -> None:
        with self.lock:
            self.stopped = True
            thread = self.thread
        if wait and thread is not None:
            thread.join()


_worker = EventAuditWorker()


def submit_event_audit(record: Any = None, *, source_path: Path | None = None) -> bool:
    """Only enqueue the saved path. Never serialize or inspect the live record."""
    try:
        return isinstance(source_path, (str, Path)) and _worker.submit(source_path)
    except Exception:  # noqa: BLE001 -- audit submission must not fail analysis
        _log.exception("event audit submission failed")
        return False


def shutdown_event_audit(wait: bool = False) -> None:
    _worker.shutdown(wait=wait)


atexit.register(shutdown_event_audit)

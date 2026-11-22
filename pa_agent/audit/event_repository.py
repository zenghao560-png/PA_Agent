"""Atomic, versioned audit storage, separate from source analysis records."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Any

from pa_agent.config.paths import PROJECT_ROOT

EVENT_AUDIT_DIR = PROJECT_ROOT / "records" / "event_audit"
_write_lock = threading.Lock()


def save_event_audit(payload: dict[str, Any], *, directory: Path | None = None) -> Path:
    # Windows can reject concurrent replacements of the same destination.
    with _write_lock:
        return _save_event_audit(payload, directory=directory)


def _save_event_audit(payload: dict[str, Any], *, directory: Path | None = None) -> Path:
    directory = directory or EVENT_AUDIT_DIR
    directory.mkdir(parents=True, exist_ok=True)

    def safe(value):
        return "".join(c if c.isalnum() or c in "-_" else "_" for c in str(value))

    path = directory / f"{safe(payload['decision_id'])}.{safe(payload['audit_version'])}.json"
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory, suffix=".tmp", delete=False
        ) as stream:
            temp_path = Path(stream.name)
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return path

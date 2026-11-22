import json
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest

from pa_agent.audit.event_repository import save_event_audit


def test_atomic_idempotent_write(tmp_path):
    payload = {"decision_id": "test", "audit_version": "event-v2", "value": 1}
    path = save_event_audit(payload, directory=tmp_path)
    assert save_event_audit(payload, directory=tmp_path) == path
    assert json.loads(path.read_text())["value"] == 1
    assert len(list(tmp_path.iterdir())) == 1


def test_concurrent_write_and_unique_tempfiles(tmp_path):
    payload = {"decision_id": "test", "audit_version": "event-v2"}
    with ThreadPoolExecutor(4) as pool:
        list(pool.map(lambda _: save_event_audit(payload, directory=tmp_path), range(20)))
    assert len(list(tmp_path.iterdir())) == 1
    assert json.loads(next(tmp_path.iterdir()).read_text()) == payload


def test_failed_replace_preserves_old_file_and_cleans_temp(tmp_path):
    payload = {"decision_id": "test", "audit_version": "event-v2"}
    path = save_event_audit(payload, directory=tmp_path)
    before = path.read_bytes()
    with (
        patch("pa_agent.audit.event_repository.os.replace", side_effect=OSError("full")),
        pytest.raises(OSError),
    ):
        save_event_audit({**payload, "new": True}, directory=tmp_path)
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_nan_rejected_and_temp_cleaned(tmp_path):
    with pytest.raises(ValueError):
        save_event_audit(
            {"decision_id": "test", "audit_version": "event-v2", "x": float("nan")},
            directory=tmp_path,
        )
    assert not list(tmp_path.iterdir())

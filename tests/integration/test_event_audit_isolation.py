"""Isolation tests: disk-only input, bounded work and failure containment."""

import hashlib
import json
import threading
from unittest.mock import patch

from pa_agent.audit.event_worker import EventAuditWorker, submit_event_audit
from tests.unit.test_event_evidence import bar, record


def write_record(tmp_path, name="decision.json", data=None):
    path = tmp_path / name
    path.write_text(json.dumps(data or record([bar(i) for i in range(6)])), encoding="utf-8")
    return path


def test_wait_audited_without_mutating_source(tmp_path):
    source = write_record(tmp_path)
    before = source.read_bytes()
    worker = EventAuditWorker(directory=tmp_path / "audit")
    assert worker.submit(source)
    worker.shutdown(wait=True)
    assert source.read_bytes() == before
    payload = json.loads(next((tmp_path / "audit").glob("*.json")).read_text())
    assert payload["source_sha256"] == hashlib.sha256(before).hexdigest()
    assert payload["source_record"] == str(source.resolve())
    assert json.loads(before)["stage2_decision"] == {"decision": "WAIT"}


def test_submit_nonblocking_dedup_and_bounded(tmp_path):
    worker = EventAuditWorker(directory=tmp_path / "audit", max_pending=1)
    entered, release = threading.Event(), threading.Event()

    def process(path):
        entered.set()
        assert release.wait(5)

    worker._process = process
    try:
        assert worker.submit(tmp_path / "one.json")
        assert entered.wait(2)  # worker is blocked, submission already returned
        assert not worker.submit(tmp_path / "one.json")
        assert worker.submit(tmp_path / "two.json")
        assert not worker.submit(tmp_path / "three.json")
    finally:
        release.set()
        worker.shutdown(wait=True)
    assert not worker.thread.is_alive()
    assert not worker.submit(tmp_path / "four.json")


def test_missing_corrupt_partial_files_do_not_kill_worker(tmp_path):
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{", encoding="utf-8")
    partial = record()
    partial["_partial_reason"] = "cancelled"
    bad = write_record(tmp_path, "partial.json", partial)
    incomplete = record()
    incomplete["stage2_decision"] = None
    missing_stage2 = write_record(tmp_path, "missing_stage2.json", incomplete)
    good = write_record(tmp_path)
    worker = EventAuditWorker(directory=tmp_path / "audit")
    for path in [tmp_path / "missing.json", corrupt, bad, missing_stage2, good]:
        worker.submit(path)
    worker.shutdown(wait=True)
    assert len(list((tmp_path / "audit").glob("*.json"))) == 1


def test_calculation_failure_contained(tmp_path):
    source = write_record(tmp_path)
    worker = EventAuditWorker(directory=tmp_path / "audit")
    with patch(
        "pa_agent.audit.event_worker.calculate_event_evidence", side_effect=RuntimeError("test")
    ):
        worker.submit(source)
        worker.queue.join()
    worker.submit(source)
    worker.shutdown(wait=True)
    assert len(list((tmp_path / "audit").glob("*.json"))) == 1


def test_repeated_submission_has_one_output(tmp_path):
    source = write_record(tmp_path)
    worker = EventAuditWorker(directory=tmp_path / "audit")
    worker.submit(source)
    worker.queue.join()
    worker.submit(source)
    worker.shutdown(wait=True)
    assert len(list((tmp_path / "audit").glob("*.json"))) == 1


def test_write_failure_contained(tmp_path):
    source = write_record(tmp_path)
    worker = EventAuditWorker(directory=tmp_path / "audit")
    with patch("pa_agent.audit.event_worker.save_event_audit", side_effect=OSError("full")):
        worker.submit(source)
        worker.shutdown(wait=True)
    assert source.exists()


def test_never_serializes_live_record():
    class Poison:
        def model_dump(self):
            raise AssertionError("live record accessed")

    assert submit_event_audit(Poison(), source_path=None) is False


def test_orchestrator_outputs_unchanged_when_audit_submission_fails(
    frame,
    pending_writer,
    assembler,
    exp_reader,
    monkeypatch,
):
    from unittest.mock import MagicMock

    from pa_agent.ai.router import route_strategy_files
    from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
    from pa_agent.util.threading import CancelToken, OrchestratorEvent
    from tests.fixtures.validators import schema_test_validator

    from .conftest import VALID_STAGE1, VALID_STAGE2, make_reply

    outputs = []
    sequences = []
    for failing in (False, True):
        client = MagicMock()
        client.stream_chat.side_effect = [make_reply(VALID_STAGE1), make_reply(VALID_STAGE2)]
        if failing:

            def submit(*args, **kwargs):
                raise RuntimeError("audit unavailable")
        else:

            def submit(*args, **kwargs):
                return False

        monkeypatch.setattr("pa_agent.audit.event_worker.submit_event_audit", submit)
        orchestrator = TwoStageOrchestrator(
            client=client,
            assembler=assembler,
            router=route_strategy_files,
            validator=schema_test_validator(),
            pending_writer=pending_writer,
            exp_reader=exp_reader,
        )
        emitted = []
        result = orchestrator.submit(
            frame=frame, cancel_token=CancelToken(), on_event=emitted.append
        )
        assert result.exception is None
        assert OrchestratorEvent.RecordSaved in emitted
        outputs.append((result.stage1_diagnosis, result.stage2_decision))
        sequences.append(emitted)
    assert outputs[0] == outputs[1]
    assert sequences[0] == sequences[1]


def test_gate_wait_record_is_submitted_after_save(
    frame, assembler, exp_reader, monkeypatch, tmp_path
):
    import copy
    from unittest.mock import MagicMock

    from pa_agent.ai.router import route_strategy_files
    from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
    from pa_agent.records.pending_writer import PendingWriter
    from pa_agent.util.threading import CancelToken

    from .conftest import VALID_STAGE1, make_reply

    diagnosis = copy.deepcopy(VALID_STAGE1)
    diagnosis["gate_result"] = "wait"
    client = MagicMock()
    client.stream_chat.return_value = make_reply(diagnosis)
    validator = MagicMock()
    from pa_agent.ai.json_validator import Ok

    validator.validate.return_value = Ok(diagnosis)
    submitted = []

    def submit(record, *, source_path):
        assert source_path.exists()
        saved = json.loads(source_path.read_text(encoding="utf-8"))
        assert saved["stage2_decision"] == record.stage2_decision
        submitted.append(source_path)

    monkeypatch.setattr("pa_agent.audit.event_worker.submit_event_audit", submit)
    orchestrator = TwoStageOrchestrator(
        client=client,
        assembler=assembler,
        router=route_strategy_files,
        validator=validator,
        pending_writer=PendingWriter(pending_dir=tmp_path),
        exp_reader=exp_reader,
    )
    result = orchestrator.submit(frame=frame, cancel_token=CancelToken(), on_event=lambda e: None)
    assert result.exception is None
    assert result.stage1_diagnosis["gate_result"] == "wait"
    assert result.stage2_decision is not None
    assert len(submitted) == 1
    assert client.stream_chat.call_count == 1


def test_thread_start_failure_returns_false_and_retry_runs(tmp_path):
    source = write_record(tmp_path)
    worker = EventAuditWorker(directory=tmp_path / "audit")
    try:
        with patch(
            "pa_agent.audit.event_worker.threading.Thread.start",
            side_effect=RuntimeError("cannot start"),
        ):
            assert worker.submit(source) is False
        assert worker.thread is None
        assert worker.queue.empty()
        assert not worker.pending
        assert worker.submit(source) is True
        worker.shutdown(wait=True)
        assert len(list((tmp_path / "audit").glob("*.json"))) == 1
    finally:
        worker.shutdown(wait=True)


def test_shutdown_safe_after_thread_constructor_failure(tmp_path):
    worker = EventAuditWorker(directory=tmp_path / "audit")
    with patch(
        "pa_agent.audit.event_worker.threading.Thread", side_effect=OSError("resource limit")
    ):
        assert worker.submit(tmp_path / "source.json") is False
    assert worker.thread is None
    worker.shutdown(wait=True)
    assert worker.submit(tmp_path / "source.json") is False


def test_real_background_audit_preserves_analysis_outputs_and_events(
    frame,
    assembler,
    exp_reader,
    monkeypatch,
    tmp_path,
):
    from unittest.mock import MagicMock

    from pa_agent.ai.router import route_strategy_files
    from pa_agent.orchestrator.two_stage import TwoStageOrchestrator
    from pa_agent.records.pending_writer import PendingWriter
    from pa_agent.util.threading import CancelToken
    from tests.fixtures.validators import schema_test_validator

    from .conftest import VALID_STAGE1, VALID_STAGE2, make_reply

    worker = EventAuditWorker(directory=tmp_path / "audit")
    outputs = []
    sequences = []
    source_bytes = []
    try:
        for enabled in (False, True):
            client = MagicMock()
            client.stream_chat.side_effect = [make_reply(VALID_STAGE1), make_reply(VALID_STAGE2)]

            def submit(record, *, source_path, enabled=enabled):
                if enabled:
                    source_bytes.append((source_path, source_path.read_bytes()))
                    return worker.submit(source_path)
                return False

            monkeypatch.setattr("pa_agent.audit.event_worker.submit_event_audit", submit)
            orchestrator = TwoStageOrchestrator(
                client=client,
                assembler=assembler,
                router=route_strategy_files,
                validator=schema_test_validator(),
                pending_writer=PendingWriter(pending_dir=tmp_path / ("on" if enabled else "off")),
                exp_reader=exp_reader,
            )
            emitted = []
            result = orchestrator.submit(
                frame=frame, cancel_token=CancelToken(), on_event=emitted.append
            )
            assert result.exception is None
            assert client.stream_chat.call_count == 2
            outputs.append(
                (
                    result.stage1_diagnosis,
                    result.stage2_decision,
                    result.stage1_messages,
                    result.stage2_messages,
                )
            )
            sequences.append(emitted)
        worker.shutdown(wait=True)
        assert outputs[0] == outputs[1]
        assert sequences[0] == sequences[1]
        assert len(list((tmp_path / "audit").glob("*.json"))) == 1
        assert all(path.read_bytes() == before for path, before in source_bytes)
    finally:
        worker.shutdown(wait=True)

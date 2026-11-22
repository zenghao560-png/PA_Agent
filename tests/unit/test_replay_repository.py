import json

from pa_agent.replay.repository import load_results


def test_load_migrates_legacy_wait_result(tmp_path):
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({
        "decision_id": "x",
        "status": "not_applicable",
        "reason": "stage2_wait_no_order",
    }), encoding="utf-8")
    rows = load_results(directory=tmp_path)
    assert rows[0].result_kind == "wait_observation"


def test_load_excludes_unsupported_order_from_order_totals(tmp_path):
    path = tmp_path / "unsupported.json"
    path.write_text(json.dumps({
        "decision_id": "x",
        "status": "not_applicable",
        "reason": "missing_valid_order_fields",
        "order_type": "limit",
    }), encoding="utf-8")
    rows = load_results(directory=tmp_path)
    assert rows[0].result_kind == "unsupported_order"

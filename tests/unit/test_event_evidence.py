"""Causal event tests with fixed, explicit price structures."""

import copy
import math

import pytest

from pa_agent.audit.event_evidence import AUDIT_VERSION, calculate_event_evidence

BASE = 1_700_000_000_000
STEP = 60_000


def bar(i, o=100, h=105, low=95, c=100, **extra):
    return {
        "seq": 100 - i,
        "ts_open": BASE + i * STEP,
        "open": o,
        "high": h,
        "low": low,
        "close": c,
        "closed": True,
        **extra,
    }


def record(bars=None, **meta):
    return {
        "meta": {
            "timestamp_local_ms": BASE + 100 * STEP,
            "symbol": "BTCUSDT",
            "timeframe": "1m",
            **meta,
        },
        "kline_data": bars if bars is not None else [bar(i) for i in range(5)],
        "stage1_diagnosis": {},
        "stage2_decision": {"decision": "WAIT"},
        "exception": None,
    }


def events(data):
    return calculate_event_evidence(data)["snapshot_evidence"]["events"]


def test_breakout_and_reclaim_use_original_level():
    data = record([bar(i) for i in range(5)] + [bar(5, h=112, c=110), bar(6, o=110, h=111, c=100)])
    result = events(data)
    failure = next(e for e in result if e["event_type"] == "breakout_failure")
    assert failure["level_price"] == 105
    assert failure["origin_time_ms"] == BASE + 5 * STEP
    assert failure["structure_id"] == result[0]["structure_id"]


def test_downside_is_mirror():
    data = record([bar(i) for i in range(5)] + [bar(5, low=88, c=90), bar(6, o=90, low=89, c=100)])
    assert any(
        e["event_type"] == "breakout_failure" and e["side"] == "lower" and e["level_price"] == 95
        for e in events(data)
    )


@pytest.mark.parametrize("side", ["upper", "lower"])
def test_actual_breakout_test_requires_later_touch(side):
    extra = (
        [bar(5, h=112, c=110), bar(6, o=110, h=112, low=105, c=108)]
        if side == "upper"
        else [bar(5, low=88, c=90), bar(6, o=90, h=95, low=88, c=92)]
    )
    result = events(record([bar(i) for i in range(5)] + extra))
    tests = [e for e in result if e["event_type"] == "breakout_test"]
    assert len(tests) == 1
    assert tests[0]["trigger_time_ms"] > tests[0]["origin_time_ms"]
    assert tests[0]["confirmation_status"] == "held_at_close"


def test_surviving_breakout_not_test():
    result = events(
        record(
            [bar(i) for i in range(5)]
            + [bar(5, h=112, c=110), bar(6, o=110, h=112, low=108, c=111)]
        )
    )
    assert not any(e["event_type"] in ("breakout_failure", "breakout_test") for e in result)


@pytest.mark.parametrize("side", ["upper", "lower"])
def test_wick_sweep_and_reverse_confirmation(side):
    extra = (
        [bar(5, h=110), bar(6, low=90, c=94)]
        if side == "upper"
        else [bar(5, low=90), bar(6, h=110, c=106)]
    )
    result = events(record([bar(i) for i in range(5)] + extra))
    sweep = next(e for e in result if e["event_type"] == "sweep_reclaim")
    confirmation = next(e for e in result if e["event_type"] == "sweep_confirmation")
    assert sweep["side"] == side
    assert sweep["structure_id"] == confirmation["structure_id"]
    assert sweep["confirmation_status"] == "reclaimed_unconfirmed"


def test_equal_boundary_does_not_invent_sweep():
    assert events(record([bar(i) for i in range(7)])) == []


def test_order_and_seq_do_not_change_identity():
    data = record([bar(i) for i in range(5)] + [bar(5, h=110)])
    expected = events(data)
    changed = copy.deepcopy(data)
    changed["kline_data"].reverse()
    for b in changed["kline_data"]:
        b["seq"] += 10
    assert events(changed) == expected


def test_input_immutable_and_deterministic():
    data = record([bar(i) for i in range(5)] + [bar(5, h=110)])
    before = copy.deepcopy(data)
    assert calculate_event_evidence(data) == calculate_event_evidence(data)
    assert data == before


def test_id_separates_symbols():
    data = record([bar(i) for i in range(5)] + [bar(5, h=110)])
    other = copy.deepcopy(data)
    other["meta"]["symbol"] = "SOLUSDT"
    assert events(data)[0]["event_id"] != events(other)[0]["event_id"]


def test_future_bar_cannot_affect_snapshot():
    data = record(
        [bar(i) for i in range(5)] + [bar(5, h=112, c=110)], timestamp_local_ms=BASE + 6 * STEP
    )
    before = events(data)
    data["kline_data"].append(bar(6, o=110, h=111, c=100))
    assert events(data) == before
    assert calculate_event_evidence(data)["future_observation"] == {
        "status": "not_collected",
        "events": [],
    }
    data["meta"]["timestamp_local_ms"] += STEP
    assert any(e["event_type"] == "breakout_failure" for e in events(data))


@pytest.mark.parametrize(
    "bad",
    [
        bar(5, closed=False),
        bar(5, h=math.nan),
        bar(5, low=-1),
        bar(5, c=120),
        {"seq": 1},
        bar(5, ts_open=-1),
    ],
)
def test_invalid_bars_excluded(bad):
    result = calculate_event_evidence(record([bar(i) for i in range(5)] + [bad]))
    assert result["snapshot_evidence"]["excluded_bar_count"] == 1
    assert result["status"] == "insufficient_data"


def test_empty_data_not_completed():
    result = calculate_event_evidence(record([]))
    assert result["status"] == "insufficient_data"
    assert result["audit_version"] == AUDIT_VERSION


def test_missing_cutoff_rejected():
    with pytest.raises((KeyError, ValueError)):
        calculate_event_evidence({"meta": {}, "kline_data": []})


def test_expired_breakout_not_reported_as_failure():
    data = record(
        [bar(i) for i in range(5)]
        + [bar(5, h=112, c=110)]
        + [bar(i, o=110, h=112, low=108, c=110) for i in range(6, 12)]
        + [bar(12)]
    )
    assert not any(e["event_type"] == "breakout_failure" for e in events(data))


def test_conflicting_duplicate_timestamps_are_excluded_in_any_order():
    data = record([bar(i) for i in range(5)] + [bar(5, h=110), bar(5, h=115)])
    original = calculate_event_evidence(data)
    data["kline_data"].reverse()
    assert calculate_event_evidence(data) == original
    assert original["snapshot_evidence"]["excluded_reasons"]["duplicate_timestamp"] == 2


def test_unknown_timeframe_cannot_claim_time_boundary():
    with pytest.raises(ValueError, match="timeframe"):
        calculate_event_evidence(record(timeframe="unknown"))


def test_rejection_reason_for_future_bar_is_explicit():
    result = calculate_event_evidence(
        record([bar(i) for i in range(7)], timestamp_local_ms=BASE + 6 * STEP)
    )
    assert result["snapshot_evidence"]["excluded_reasons"]["unavailable_at_analysis_time"] == 1
    assert all(
        e["available_at_ms"] <= BASE + 6 * STEP for e in result["snapshot_evidence"]["events"]
    )


@pytest.mark.parametrize("side", ["upper", "lower"])
def test_missing_bars_break_event_lifecycle(side):
    extra = (
        [bar(5, h=112, c=110), bar(50, o=110, h=111, c=100)]
        if side == "upper"
        else [bar(5, low=88, c=90), bar(50, o=90, low=89, c=100)]
    )
    result = calculate_event_evidence(record([bar(i) for i in range(5)] + extra))
    assert result["status"] == "partial"
    assert not any(
        e["event_type"] in ("breakout_failure", "breakout_test")
        for e in result["snapshot_evidence"]["events"]
    )
    gap = result["snapshot_evidence"]["continuity_gaps"][0]
    assert gap["elapsed_ms"] == 45 * STEP


def test_reference_window_restarts_after_gap():
    result = calculate_event_evidence(
        record(
            [bar(i) for i in range(5)] + [bar(i) for i in range(10, 15)] + [bar(15, h=112, c=110)]
        )
    )
    found = result["snapshot_evidence"]["events"]
    assert len(found) == 1
    assert found[0]["trigger_time_ms"] == BASE + 15 * STEP
    assert result["status"] == "partial"


def test_fragmented_data_not_reported_complete_or_sufficient():
    result = calculate_event_evidence(record([bar(i) for i in range(0, 20, 2)]))
    assert result["status"] == "insufficient_data"
    assert result["snapshot_evidence"]["events"] == []


def test_excluded_invalid_bar_also_interrupts_observation():
    result = calculate_event_evidence(
        record(
            [bar(i) for i in range(5)]
            + [bar(5, h=112, c=110), bar(6, h=math.nan), bar(7, o=110, h=111, c=100)]
        )
    )
    assert result["status"] == "partial"
    assert not any(
        e["event_type"] == "breakout_failure" for e in result["snapshot_evidence"]["events"]
    )


@pytest.mark.parametrize("side", ["upper", "lower"])
def test_invalidated_sweep_cannot_confirm_later(side):
    extra = (
        [bar(5, h=110), bar(6, h=115, c=112), bar(7, low=90, c=94)]
        if side == "upper"
        else [bar(5, low=90), bar(6, low=85, c=88), bar(7, h=110, c=106)]
    )
    result = events(record([bar(i) for i in range(5)] + extra))
    sweep = next(e for e in result if e["event_type"] == "sweep_reclaim" and e["side"] == side)
    lifecycle = [e for e in result if e["structure_id"] == sweep["structure_id"]]
    assert [e["event_type"] for e in lifecycle] == ["sweep_reclaim", "sweep_invalidation"]
    assert lifecycle[-1]["trigger_time_ms"] == BASE + 6 * STEP


def test_equal_sweep_extreme_does_not_invalidate():
    result = events(
        record(
            [bar(i) for i in range(5)] + [bar(5, h=110), bar(6, h=112, c=110), bar(7, low=90, c=94)]
        )
    )
    sweep = next(e for e in result if e["event_type"] == "sweep_reclaim")
    lifecycle = [e["event_type"] for e in result if e["structure_id"] == sweep["structure_id"]]
    assert lifecycle == ["sweep_reclaim", "sweep_confirmation"]


def test_first_successful_test_is_declared_end_of_tracking():
    result = calculate_event_evidence(
        record(
            [bar(i) for i in range(5)]
            + [bar(5, h=112, c=110), bar(6, o=110, h=112, low=105, c=108), bar(7)]
        )
    )
    snapshot = result["snapshot_evidence"]
    first = next(e for e in snapshot["events"] if e["event_type"] == "breakout")
    assert [
        e["event_type"] for e in snapshot["events"] if e["structure_id"] == first["structure_id"]
    ] == ["breakout", "breakout_test"]
    assert snapshot["breakout_tracking_scope"] == "until_first_test_failure_or_expiry"


@pytest.mark.parametrize("timeframe", ["1M", "0m", "-1m", "+1m", "1.5m"])
def test_noncanonical_timeframes_not_silently_reinterpreted(timeframe):
    with pytest.raises(ValueError, match="timeframe"):
        calculate_event_evidence(record(timeframe=timeframe))

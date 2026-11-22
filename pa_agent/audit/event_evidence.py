"""Read-only, causal observations; five-bar references are not trading signals."""

from __future__ import annotations

import hashlib
import json
import math
from collections import Counter
from typing import Any

AUDIT_VERSION = "event-v3"
LOOKBACK = 5
OBSERVATION_BARS = 5


def _duration_ms(timeframe: str) -> int:
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    if not timeframe[:-1].isdecimal():
        return 0
    try:
        return int(timeframe[:-1]) * units[timeframe[-1]]
    except (ValueError, KeyError, IndexError):
        return 0


def calculate_event_evidence(record: dict[str, Any]) -> dict[str, Any]:
    """Use only valid closed bars available at analysis time. Never mutate input."""
    meta = record.get("meta") or {}
    cutoff = float(meta["timestamp_local_ms"])
    if not math.isfinite(cutoff) or cutoff <= 0:
        raise ValueError("invalid analysis timestamp")
    duration = _duration_ms(str(meta.get("timeframe", "")))
    if duration <= 0:
        raise ValueError("unsupported timeframe: cannot establish bar availability")
    bars = []
    exclusions = Counter()
    for raw in record.get("kline_data") or []:
        try:
            b = {key: float(raw[key]) for key in ("ts_open", "open", "high", "low", "close")}
            ts = b["ts_open"]
            valid = (
                all(math.isfinite(v) for v in b.values())
                and ts > 0
                and b["low"] > 0
                and b["low"] <= min(b["open"], b["close"])
                and b["high"] >= max(b["open"], b["close"])
            )
            if not valid:
                exclusions["invalid_ohlc_or_timestamp"] += 1
                continue
            if raw.get("closed") is not True:
                exclusions["not_closed"] += 1
                continue
            if ts + duration > cutoff:
                exclusions["unavailable_at_analysis_time"] += 1
                continue
            bars.append(b)
        except (KeyError, TypeError, ValueError):
            exclusions["invalid_ohlc_or_timestamp"] += 1
    counts = Counter(b["ts_open"] for b in bars)
    # Ambiguous duplicate timestamps must not depend on input order.
    exclusions["duplicate_timestamp"] = sum(n for n in counts.values() if n > 1)
    bars = [b for b in bars if counts[b["ts_open"]] == 1]
    excluded = sum(exclusions.values())
    bars.sort(key=lambda b: b["ts_open"])
    events = []
    active = []
    sweeps = []
    continuity_gaps = []
    segment_start = 0
    has_reference_window = False

    def emit(kind, side, level, origin, bar, status):
        identity = [
            meta.get("symbol"),
            meta.get("timeframe"),
            kind,
            side,
            level,
            origin["ts_open"],
            bar["ts_open"],
        ]
        event_id = hashlib.sha256(json.dumps(identity).encode()).hexdigest()[:24]
        structure = [*identity[:2], side, level, origin["ts_open"]]
        events.append(
            {
                "event_id": event_id,
                "structure_id": hashlib.sha256(json.dumps(structure).encode()).hexdigest()[:24],
                "event_type": kind,
                "side": side,
                "level_price": level,
                "trigger_time_ms": bar["ts_open"],
                "origin_time_ms": origin["ts_open"],
                "available_at_ms": bar["ts_open"] + duration if duration else None,
                "evidence_bar_times_ms": [origin["ts_open"], bar["ts_open"]],
                "confirmation_status": status,
                "extreme_price": bar["high"] if side == "upper" else bar["low"],
            }
        )

    for i in range(len(bars)):
        b = bars[i]
        if i and b["ts_open"] - bars[i - 1]["ts_open"] != duration:
            continuity_gaps.append(
                {
                    "previous_open_ms": bars[i - 1]["ts_open"],
                    "next_open_ms": b["ts_open"],
                    "elapsed_ms": b["ts_open"] - bars[i - 1]["ts_open"],
                    "expected_interval_ms": duration,
                }
            )
            # No lifecycle observation or reference window may bridge missing data.
            active.clear()
            sweeps.clear()
            segment_start = i
        if i - segment_start < LOOKBACK:
            continue
        has_reference_window = True
        for sweep in list(sweeps):
            side, level, start, origin = sweep
            if i - start > OBSERVATION_BARS:
                sweeps.remove(sweep)
                continue
            invalidated = (
                b["close"] > origin["high"] if side == "upper" else b["close"] < origin["low"]
            )
            if invalidated:
                emit("sweep_invalidation", side, level, origin, b, "close_beyond_sweep_extreme")
                sweeps.remove(sweep)
                continue
            confirmed = (
                b["close"] < origin["low"] if side == "upper" else b["close"] > origin["high"]
            )
            if confirmed:
                emit("sweep_confirmation", side, level, origin, b, "opposite_close_confirmed")
                sweeps.remove(sweep)
        for attempt in list(active):
            side, level, start, origin = attempt
            if i - start > OBSERVATION_BARS:
                active.remove(attempt)
                continue
            failed = b["close"] < level if side == "upper" else b["close"] > level
            touched = b["low"] <= level if side == "upper" else b["high"] >= level
            held = b["close"] > level if side == "upper" else b["close"] < level
            if failed:
                emit("breakout_failure", side, level, origin, b, "reclaimed")
                active.remove(attempt)
            elif touched and held:
                emit("breakout_test", side, level, origin, b, "held_at_close")
                active.remove(attempt)
        prior = bars[i - LOOKBACK : i]
        for side, level in (
            ("upper", max(x["high"] for x in prior)),
            ("lower", min(x["low"] for x in prior)),
        ):
            breakout = b["close"] > level if side == "upper" else b["close"] < level
            swept = (
                b["high"] > level and b["close"] < level
                if side == "upper"
                else b["low"] < level and b["close"] > level
            )
            if breakout:
                # Keep a fixed reference until failure, test, or expiry.
                if not any(a[0] == side for a in active):
                    emit("breakout", side, level, b, b, "initial_breakout")
                    active.append((side, level, i, b))
            elif swept:
                emit("sweep_reclaim", side, level, b, b, "reclaimed_unconfirmed")
                sweeps.append((side, level, i, b))
    return {
        "audit_version": AUDIT_VERSION,
        "status": "insufficient_data"
        if not has_reference_window
        else ("partial" if excluded or continuity_gaps else "completed"),
        "snapshot_evidence": {
            "source": "saved_analysis_kline_data",
            "reference_method": "prior_5_closed_bar_extreme",
            "observation_window_bars": OBSERVATION_BARS,
            "analysis_timestamp_ms": cutoff,
            "analysis_cutoff_bar": bars[-1]["ts_open"] if bars else None,
            "first_bar_time_ms": bars[0]["ts_open"] if bars else None,
            "bar_count": len(bars),
            "excluded_bar_count": excluded,
            "excluded_reasons": dict(exclusions),
            "continuity_gaps": continuity_gaps,
            "breakout_tracking_scope": "until_first_test_failure_or_expiry",
            "events": events,
        },
        "future_observation": {"status": "not_collected", "events": []},
    }

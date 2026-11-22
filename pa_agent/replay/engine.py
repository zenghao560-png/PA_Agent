"""Deterministic, read-only OHLC replay for saved Stage2 decisions.

Bars are accepted newest-first, matching AnalysisRecord.kline_data. Only bars
strictly newer than the decision bar are considered. Intrabar ordering is never
invented: conflicting touches become an ambiguous result.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from pa_agent.replay.models import ReplayResult, ReplayStatus


@dataclass(frozen=True)
class ReplayPolicy:
    limit_entry_max_bars: int = 3
    stop_entry_max_bars: int = 2
    holding_max_bars: int = 20
    # Keep the persisted filename stable; replay results are recalculated in place.
    version: str = "replay-v1"


def _num(value: Any) -> float | None:
    try:
        result = float(value)
        return result if result == result else None
    except (TypeError, ValueError):
        return None


def _direction(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if any(x in text for x in ("long", "buy", "多")):
        return "long"
    if any(x in text for x in ("short", "sell", "空")):
        return "short"
    return None


def _order_type(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if any(x in text for x in ("limit", "限价")):
        return "limit"
    if any(x in text for x in ("stop", "突破", "止损")):
        return "stop"
    return None


def _bar(row: dict[str, Any], key: str) -> float | None:
    return _num(row.get(key))


def _bar_diagnostics(row: dict[str, Any], *, mode: str) -> dict[str, Any]:
    """Keep the OHLC evidence behind an intrabar replay conclusion."""
    return {
        "mode": mode,
        "ts_open": row.get("ts_open"),
        "open": row.get("open"),
        "high": row.get("high"),
        "low": row.get("low"),
        "close": row.get("close"),
    }


def replay_decision(
    decision: dict[str, Any],
    future_bars: Iterable[dict[str, Any]],
    *,
    decision_id: str = "unknown",
    policy: ReplayPolicy | None = None,
    source_record: str | None = None,
) -> ReplayResult:
    """Replay one normalized decision against bars after its source snapshot.

    ``future_bars`` must be chronological (oldest first) and must not include
    the source decision bar. A caller that cannot establish this boundary must
    pass no bars and receive ``data_insufficient`` rather than risk lookahead.
    """
    policy = policy or ReplayPolicy()
    d = decision.get("decision") if isinstance(decision.get("decision"), dict) else decision
    order_type = _order_type(d.get("order_type"))
    side = _direction(d.get("order_direction") or d.get("direction"))
    entry = _num(d.get("entry_price") or d.get("entry"))
    stop = _num(d.get("stop_loss") or d.get("stop_loss_price") or d.get("stop_price") or d.get("stop"))
    target = _num(d.get("take_profit_1") or d.get("take_profit_price") or d.get("tp1") or d.get("take_profit"))
    base = dict(decision_id=decision_id, policy_version=policy.version,
                order_type=order_type, order_direction=side, entry_price=entry,
                stop_price=stop, target_price=target, source_record=source_record)
    if not order_type or not side or entry is None or stop is None or target is None:
        return ReplayResult(status=ReplayStatus.NOT_APPLICABLE, reason="missing_valid_order_fields", **base)
    bars = list(future_bars)
    if not bars:
        return ReplayResult(status=ReplayStatus.DATA_INSUFFICIENT, reason="no_future_closed_bars", **base)
    max_entry = policy.limit_entry_max_bars if order_type == "limit" else policy.stop_entry_max_bars
    waiting = bars[:max_entry]
    for index, bar in enumerate(waiting):
        open_price = _bar(bar, "open")
        high, low, ts = _bar(bar, "high"), _bar(bar, "low"), _num(bar.get("ts_open"))
        if high is None or low is None:
            return ReplayResult(status=ReplayStatus.DATA_INSUFFICIENT, reason="invalid_future_bar", **base)
        entry_hit = (low <= entry) if side == "long" and order_type == "limit" else (high >= entry) if side == "short" and order_type == "limit" else (high >= entry) if side == "long" else (low <= entry)
        invalidated = (low <= stop) if side == "long" else (high >= stop)
        if entry_hit and invalidated:
            # OHLC cannot reveal intrabar order when the bar opens between the
            # entry and stop.  If it opens beyond one boundary, the path to the
            # other boundary determines the order under continuous-price replay.
            if open_price is not None:
                if side == "short" and open_price <= entry:
                    result = _replay_holding(base, bars[index:], index + 1, ts, side, entry, stop, target, policy)
                    result.diagnostics = _bar_diagnostics(bar, mode="entry_then_invalidation")
                    return result
                if side == "long" and open_price >= entry:
                    result = _replay_holding(base, bars[index:], index + 1, ts, side, entry, stop, target, policy)
                    result.diagnostics = _bar_diagnostics(bar, mode="entry_then_invalidation")
                    return result
                if side == "short" and open_price >= stop:
                    return ReplayResult(status=ReplayStatus.ENTRY_INVALIDATED, entry_wait_bars=index + 1, reason="stop_or_structure_invalidated_before_entry", diagnostics=_bar_diagnostics(bar, mode="invalidation_at_open"), **base)
                if side == "long" and open_price <= stop:
                    return ReplayResult(status=ReplayStatus.ENTRY_INVALIDATED, entry_wait_bars=index + 1, reason="stop_or_structure_invalidated_before_entry", diagnostics=_bar_diagnostics(bar, mode="invalidation_at_open"), **base)
            return ReplayResult(status=ReplayStatus.ENTRY_AMBIGUOUS, entry_wait_bars=index + 1, reason="entry_and_invalidated_on_same_bar", diagnostics=_bar_diagnostics(bar, mode="intrabar_order_unknown"), **base)
        if invalidated:
            return ReplayResult(status=ReplayStatus.ENTRY_INVALIDATED, entry_wait_bars=index + 1, reason="stop_or_structure_invalidated_before_entry", **base)
        if entry_hit:
            return _replay_holding(base, bars[index:], index + 1, ts, side, entry, stop, target, policy)
    if len(bars) < max_entry:
        return ReplayResult(status=ReplayStatus.DATA_INSUFFICIENT, entry_wait_bars=len(bars), reason="entry_window_incomplete", **base)
    return ReplayResult(status=ReplayStatus.ENTRY_EXPIRED, entry_wait_bars=max_entry, reason="entry_window_expired", **base)


def _replay_holding(base: dict[str, Any], bars: list[dict[str, Any]], wait: int, entry_ts: float | None, side: str, entry: float, stop: float, target: float, policy: ReplayPolicy) -> ReplayResult:
    for held, bar in enumerate(bars[:policy.holding_max_bars], 1):
        high, low = _bar(bar, "high"), _bar(bar, "low")
        ts = _num(bar.get("ts_open"))
        if high is None or low is None:
            return ReplayResult(status=ReplayStatus.DATA_INSUFFICIENT, entry_wait_bars=wait, holding_bars=held, entry_time=entry_ts, **base)
        stop_hit = high >= stop if side == "short" else low <= stop
        target_hit = low <= target if side == "short" else high >= target
        if stop_hit and target_hit:
            open_price = _bar(bar, "open")
            if open_price is not None:
                target_first = (side == "short" and open_price <= target) or (side == "long" and open_price >= target)
                stop_first = (side == "short" and open_price >= stop) or (side == "long" and open_price <= stop)
                if target_first != stop_first:
                    is_target = target_first
                    risk = abs(entry - stop)
                    reward = abs(target - entry)
                    return ReplayResult(status=ReplayStatus.TARGET_1 if is_target else ReplayStatus.STOP, entry_wait_bars=wait, holding_bars=held, entry_time=entry_ts, exit_time=ts, result_r=(reward / risk if is_target else -1.0) if risk else None, reason="target_reached" if is_target else "stop_reached", diagnostics=_bar_diagnostics(bar, mode="target_then_stop" if is_target else "stop_then_target"), **base)
            return ReplayResult(status=ReplayStatus.EXIT_AMBIGUOUS, entry_wait_bars=wait, holding_bars=held, entry_time=entry_ts, exit_time=ts, reason="stop_and_target_on_same_bar", diagnostics=_bar_diagnostics(bar, mode="intrabar_order_unknown"), **base)
        if stop_hit or target_hit:
            is_target = target_hit
            risk = abs(entry - stop)
            reward = abs(target - entry)
            return ReplayResult(status=ReplayStatus.TARGET_1 if is_target else ReplayStatus.STOP, entry_wait_bars=wait, holding_bars=held, entry_time=entry_ts, exit_time=ts, result_r=(reward / risk if is_target else -1.0) if risk else None, reason="target_reached" if is_target else "stop_reached", **base)
    if len(bars) < policy.holding_max_bars:
        return ReplayResult(status=ReplayStatus.DATA_INSUFFICIENT, entry_wait_bars=wait, holding_bars=len(bars), entry_time=entry_ts, reason="holding_window_incomplete", **base)
    return ReplayResult(status=ReplayStatus.HOLDING_EXPIRED, entry_wait_bars=wait, holding_bars=policy.holding_max_bars, entry_time=entry_ts, reason="holding_window_expired", **base)

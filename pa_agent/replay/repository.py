"""Separate persistence for replay results; never rewrites source records."""
from __future__ import annotations

import json
from pathlib import Path

from pa_agent.config.paths import PROJECT_ROOT
from pa_agent.replay.models import ReplayResult
from pa_agent.replay.engine import replay_decision, ReplayPolicy

REPLAY_DIR = PROJECT_ROOT / "records" / "replay"


def save_result(result: ReplayResult, *, directory: Path | None = None) -> Path:
    root = directory or REPLAY_DIR
    root.mkdir(parents=True, exist_ok=True)
    path = root / f"{result.decision_id}.{result.policy_version}.json"
    path.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return path


def load_results(*, directory: Path | None = None) -> list[ReplayResult]:
    root = directory or REPLAY_DIR
    if not root.is_dir():
        return []
    results: list[ReplayResult] = []
    for path in sorted(root.glob("*.json")):
        try:
            result = ReplayResult.model_validate(json.loads(path.read_text(encoding="utf-8")))
            # v1 records written before WAIT had its own result_kind used the
            # default order_replay value. Normalize them at read time without
            # rewriting source files or changing any order result.
            if result.status.value == "not_applicable" and result.reason == "stage2_wait_no_order":
                result = result.model_copy(update={"result_kind": "wait_observation"})
            elif result.status.value == "not_applicable" and result.reason == "missing_valid_order_fields":
                # This is an unsupported/invalid order snapshot, not a losing
                # order. Keep it visible for audit but exclude it from order
                # performance totals.
                result = result.model_copy(update={"result_kind": "unsupported_order"})
            results.append(result)
        except Exception:
            continue
    return results


def replay_record(record: object, bars: list[dict], *, policy: ReplayPolicy | None = None) -> ReplayResult | None:
    """Replay a saved AnalysisRecord against newer closed bars only.

    This helper is deliberately opt-in and read-only. It returns ``None`` when
    the record cannot establish a safe source-bar boundary.
    """
    data = record.model_dump() if hasattr(record, "model_dump") else record
    if not isinstance(data, dict):
        return None
    kline_data = data.get("kline_data") or []
    if not kline_data:
        return None
    try:
        anchor = float(kline_data[0].get("ts_open"))
        future = []
        for raw in bars:
            b = raw if isinstance(raw, dict) else {
                "ts_open": getattr(raw, "ts_open"), "open": getattr(raw, "open"),
                "high": getattr(raw, "high"), "low": getattr(raw, "low"),
                "close": getattr(raw, "close"), "closed": getattr(raw, "closed", True),
            }
            if float(b.get("ts_open")) > anchor and b.get("closed", True):
                future.append(b)
    except (TypeError, ValueError, AttributeError):
        return None
    meta = data.get("meta") or {}
    decision_id = f"{meta.get('symbol', 'unknown')}-{meta.get('timestamp_local_ms', 'unknown')}"
    result = replay_decision(data.get("stage2_decision") or {}, sorted(future, key=lambda x: float(x["ts_open"])), decision_id=decision_id, policy=policy)
    save_result(result)
    return result


def replay_saved_records(
    bars: list[object], *, symbol: str, timeframe: str, directory: Path | None = None,
    policy: ReplayPolicy | None = None,
) -> int:
    """Update all replayable saved records for one symbol/timeframe.

    It is intended for a background worker. Invalid or legacy records are
    skipped, and one bad record cannot stop the remaining history.
    """
    root = directory or (PROJECT_ROOT / "records" / "pending")
    count = 0
    for path in sorted(root.glob("*.json")) if root.is_dir() else []:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            meta = raw.get("meta") or {}
            if meta.get("symbol") != symbol or meta.get("timeframe") != timeframe:
                continue
            if not raw.get("stage2_decision") or not raw.get("kline_data"):
                continue
            stage2 = raw.get("stage2_decision") or {}
            decision = stage2.get("decision") if isinstance(stage2, dict) else {}
            if isinstance(decision, dict) and str(decision.get("order_type") or "").strip() in {"不下单", "wait", "WAIT"}:
                wait_result = ReplayResult(
                    decision_id=f"{meta.get('symbol', 'unknown')}-{meta.get('timestamp_local_ms', 'unknown')}",
                    status="not_applicable", result_kind="wait_observation",
                    reason="stage2_wait_no_order", source_record=str(path),
                )
                save_result(wait_result)
                count += 1
                continue
            if replay_record(raw, bars, policy=policy) is not None:
                count += 1
        except Exception:
            continue
    return count

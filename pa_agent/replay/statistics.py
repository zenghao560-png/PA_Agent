"""Small, deterministic summaries for persisted replay results."""
from __future__ import annotations

from collections import Counter
from typing import Iterable

from pa_agent.replay.models import ReplayResult


def summarize(results: Iterable[ReplayResult]) -> dict[str, object]:
    rows = list(results)
    counts = Counter(row.status.value for row in rows)
    orders = [row for row in rows if row.result_kind == "order_replay"]
    completed = [row.result_r for row in orders if row.result_r is not None]
    return {
        "sample_count": len(orders),
        "wait_observation_count": sum(row.result_kind == "wait_observation" for row in rows),
        "unsupported_order_count": sum(row.result_kind == "unsupported_order" for row in rows),
        "status_counts": dict(counts),
        "average_r": sum(completed) / len(completed) if completed else None,
        "median_r": sorted(completed)[len(completed) // 2] if completed else None,
        "theoretical_target_count": sum(row.status.value == "target_1" for row in rows),
        "theoretical_stop_count": sum(row.status.value == "stop" for row in rows),
        "entry_expired_count": sum(row.status.value == "entry_expired" for row in rows),
        "entry_invalidated_count": sum(row.status.value == "entry_invalidated" for row in rows),
        "ambiguous_count": sum("ambiguous" in row.status.value for row in rows),
    }

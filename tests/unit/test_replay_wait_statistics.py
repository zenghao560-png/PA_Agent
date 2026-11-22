from pa_agent.replay.models import ReplayResult, ReplayStatus
from pa_agent.replay.statistics import summarize


def test_wait_observation_is_not_mixed_with_order_statistics():
    summary = summarize([
        ReplayResult(decision_id="w", status=ReplayStatus.NOT_APPLICABLE, result_kind="wait_observation"),
        ReplayResult(decision_id="t", status=ReplayStatus.TARGET_1, result_r=2),
    ])
    assert summary["sample_count"] == 1
    assert summary["wait_observation_count"] == 1
    assert summary["average_r"] == 2

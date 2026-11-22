from pa_agent.replay.models import ReplayResult, ReplayStatus
from pa_agent.replay.statistics import summarize


def test_summary_keeps_theoretical_results_separate_by_status():
    rows = [
        ReplayResult(decision_id="a", status=ReplayStatus.TARGET_1, result_r=2),
        ReplayResult(decision_id="b", status=ReplayStatus.STOP, result_r=-1),
        ReplayResult(decision_id="c", status=ReplayStatus.ENTRY_EXPIRED),
    ]
    summary = summarize(rows)
    assert summary["sample_count"] == 3
    assert summary["theoretical_target_count"] == 1
    assert summary["theoretical_stop_count"] == 1
    assert summary["average_r"] == 0.5

"""Offline, read-only replay of saved model prices; never calls an AI API."""
import copy
import json
from pathlib import Path

from pa_agent.util.trade_metrics import compute_risk_reward, validate_order_trade_metrics


def main():
    root = Path(__file__).resolve().parents[1]
    paths = sorted((root / "records/pending").glob("*.json"))
    cases = []
    for path in paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        if record["meta"]["timestamp_local_iso"] not in (
            "2026-11-18T10:00:01.660", "2026-11-18T10:03:21.707",
            "2026-11-18T10:30:01.488",
        ):
            continue
        raw = json.loads(record["stage2_response"]["content"])
        original = copy.deepcopy(raw["decision"])
        adjusted = copy.deepcopy(original)
        errors = validate_order_trade_metrics(adjusted)
        preserved = copy.deepcopy(original)
        preserved_errors = validate_order_trade_metrics(preserved, apply_rr_cap_adjustment=False)
        cases.append({
            "file": path.name,
            "meta": record["meta"],
            "original_model_decision": original,
            "current_metrics_decision": adjusted,
            "current_metrics_errors": errors,
            "preserved_stop_decision": preserved,
            "preserved_stop_errors_with_existing_cap": preserved_errors,
            "original_rr": compute_risk_reward(original.get("entry_price"), original.get("take_profit_price"), original.get("stop_loss_price"), original.get("order_direction")),
            "saved_final_stage1": record["stage1_diagnosis"],
            "saved_final_stage2": record["stage2_decision"],
            "limitations": "Metrics-only replay without frame-specific tick/freshness checks; not a full Stage2 approval or AI rerun.",
        })
    output = root / "docs/reviews/2026-11-18-rr-replay.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(cases, ensure_ascii=False, indent=2), encoding="utf-8")
    for case in cases:
        print(case["meta"]["timestamp_local_iso"], "RR", case["original_rr"], "current_errors", case["current_metrics_errors"], "preserved_errors", case["preserved_stop_errors_with_existing_cap"])


if __name__ == "__main__":
    main()

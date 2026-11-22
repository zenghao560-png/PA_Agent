from pa_agent.replay.engine import ReplayPolicy, replay_decision
from pa_agent.replay.models import ReplayStatus


def decision(order_type="限价单"):
    return {"decision": {"order_type": order_type, "order_direction": "做多", "entry_price": 100, "stop_loss": 95, "take_profit_1": 110}}


def bar(ts, high, low, open_price=None):
    return {"ts_open": ts, "open": low if open_price is None else open_price, "high": high, "low": low, "close": high}


def test_limit_triggers_on_second_bar_then_target():
    result = replay_decision(decision(), [bar(1, 105, 101), bar(2, 101, 99), bar(3, 111, 100)])
    assert result.status == ReplayStatus.TARGET_1
    assert result.entry_wait_bars == 2
    assert result.holding_bars == 2


def test_limit_expires_after_window():
    result = replay_decision(decision(), [bar(1, 105, 101), bar(2, 105, 101), bar(3, 105, 101)])
    assert result.status == ReplayStatus.ENTRY_EXPIRED


def test_entry_before_stop_is_ambiguous():
    result = replay_decision(decision(), [bar(1, 101, 94, open_price=98)])
    assert result.status == ReplayStatus.ENTRY_AMBIGUOUS


def test_open_above_long_entry_confirms_entry_before_stop():
    result = replay_decision(decision(), [bar(1, 101, 94, open_price=101)])
    assert result.status == ReplayStatus.STOP


def test_open_below_short_entry_confirms_entry_before_stop():
    payload = {"decision": {"order_type": "限价单", "order_direction": "做空", "entry_price": 100, "stop_loss_price": 105, "take_profit_1": 90}}
    result = replay_decision(payload, [bar(1, 106, 98, open_price=98)])
    assert result.status == ReplayStatus.STOP


def test_same_exit_bar_is_ambiguous():
    result = replay_decision(decision(), [bar(1, 101, 99), bar(2, 111, 94, open_price=100)])
    assert result.status == ReplayStatus.EXIT_AMBIGUOUS


def test_open_beyond_long_target_resolves_target_before_stop():
    result = replay_decision(decision(), [bar(1, 101, 99), bar(2, 111, 94, open_price=111)])
    assert result.status == ReplayStatus.TARGET_1
    assert result.diagnostics["mode"] == "target_then_stop"


def test_open_beyond_short_stop_resolves_stop_before_target():
    payload = {"decision": {"order_type": "限价单", "order_direction": "做空", "entry_price": 100, "stop_loss_price": 105, "take_profit_1": 90}}
    result = replay_decision(payload, [bar(1, 100, 99, open_price=100), bar(2, 106, 89, open_price=106)])
    assert result.status == ReplayStatus.STOP
    assert result.diagnostics["mode"] == "stop_then_target"


def test_wait_is_not_an_order():
    result = replay_decision({"decision": {"order_type": "不下单"}}, [bar(1, 200, 1)])
    assert result.status == ReplayStatus.NOT_APPLICABLE


def test_stage2_price_field_names_are_supported():
    payload = {"decision": {"order_type": "限价单", "order_direction": "做多", "entry_price": 100, "stop_loss_price": 95, "take_profit_price": 110}}
    result = replay_decision(payload, [bar(1, 101, 99), bar(2, 111, 100)])
    assert result.status == ReplayStatus.TARGET_1

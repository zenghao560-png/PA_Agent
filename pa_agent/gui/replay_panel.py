"""UI for viewing persisted theoretical replay results."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import QComboBox, QLabel, QListWidget, QVBoxLayout, QWidget

from pa_agent.replay.repository import load_results
from pa_agent.replay.statistics import summarize

_STATUS_LABELS = {
    "target_1": "理论止盈（TP1）",
    "stop": "理论止损",
    "entry_expired": "限价单过期，未触发",
    "entry_invalidated": "入场前结构失效",
    "entry_ambiguous": "入场顺序无法确认",
    "exit_ambiguous": "止盈/止损顺序无法确认",
    "holding_expired": "持有期限结束，未到目标或止损",
    "data_insufficient": "后续 K 线不足",
    "not_applicable": "等待（未下单）",
}
_TYPE_LABELS = {"limit": "限价单", "stop": "突破/止损单"}
_SIDE_LABELS = {"long": "做多", "short": "做空"}
_MODE_LABELS = {
    "entry_then_invalidation": "开盘路径确认先入场",
    "invalidation_at_open": "开盘已越过失效位",
    "target_then_stop": "开盘路径确认先止盈",
    "stop_then_target": "开盘路径确认先止损",
    "intrabar_order_unknown": "同K线顺序无法确认",
}
_BEIJING_TZ = timezone(timedelta(hours=8))


def _format_beijing_time(epoch_value: float) -> str:
    """Format epoch seconds/milliseconds as Beijing time (UTC+8)."""
    seconds = epoch_value / 1000.0 if epoch_value > 1e12 else epoch_value
    return datetime.fromtimestamp(seconds, tz=timezone.utc).astimezone(_BEIJING_TZ).strftime("%Y-%m-%d %H:%M")


class ReplayPanel(QWidget):
    """Read-only replay result list; it never starts or changes analysis."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._title = QLabel("策略回放")
        self._note = QLabel(
            "这里评估的是历史决策的后续价格路径：订单样本只包含有入场、止损和目标的方案；"
            "WAIT 单独统计，不代表真实成交或真实盈亏。"
        )
        self._note.setWordWrap(True)
        self._filter = QComboBox()
        self._filter.addItem("全部状态", "")
        self._list = QListWidget()
        layout = QVBoxLayout(self)
        layout.addWidget(self._title)
        layout.addWidget(self._note)
        layout.addWidget(self._filter)
        layout.addWidget(self._list)
        self._filter.currentIndexChanged.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        self._list.clear()
        results = load_results()
        # Newest decision first; result-file modification time is not reliable
        # because background replay updates older decisions later.
        def decision_ts(result: object) -> int:
            try:
                return int(str(getattr(result, "decision_id", "")).rsplit("-", 1)[-1])
            except (TypeError, ValueError):
                return -1

        results.sort(key=decision_ts, reverse=True)
        if not results:
            self._list.addItem("暂无回放结果。")
            return
        known = {result.status.value for result in results}
        existing = {self._filter.itemData(i) for i in range(self._filter.count())}
        for status in sorted(known - existing):
            self._filter.addItem(status, status)
        selected = self._filter.currentData()
        visible = [result for result in results if not selected or result.status.value == selected]
        summary = summarize(visible)
        self._list.addItem(
            f"订单样本 {summary['sample_count']}  |  WAIT {summary['wait_observation_count']}  |  无法回放 {summary['unsupported_order_count']}  |  理论止盈 {summary['theoretical_target_count']}  |  "
            f"理论止损 {summary['theoretical_stop_count']}  |  过期 {summary['entry_expired_count']}  |  "
            f"失效 {summary['entry_invalidated_count']}  |  歧义 {summary['ambiguous_count']}  |  "
            f"平均R {summary['average_r'] if summary['average_r'] is not None else '—'}  |  "
            f"中位数R {summary['median_r'] if summary['median_r'] is not None else '—'}"
        )
        for result in visible:
            parts = result.decision_id.rsplit("-", 1)
            try:
                when = _format_beijing_time(float(parts[-1]))
            except (ValueError, TypeError):
                when = result.decision_id
            if result.result_kind == "wait_observation":
                label = f"{when}  |  等待（本轮未下单）"
            else:
                side = _SIDE_LABELS.get(result.order_direction or "", "方向未知")
                order_type = _TYPE_LABELS.get(result.order_type or "", "订单类型未知")
                label = f"{when}  |  {side} {order_type}  |  {_STATUS_LABELS.get(result.status.value, result.status.value)}"
            if result.result_r is not None:
                label += f"  |  {result.result_r:+.2f}R"
            if result.entry_wait_bars is not None:
                label += f"  |  等待 {result.entry_wait_bars} 根K线"
            if result.holding_bars is not None:
                label += f"  |  持有 {result.holding_bars} 根K线"
            mode = (result.diagnostics or {}).get("mode")
            if mode in _MODE_LABELS:
                label += f"  |  {_MODE_LABELS[mode]}"
            self._list.addItem(label)
        self._list.setLayoutDirection(Qt.LayoutDirection.LeftToRight)

    def showEvent(self, event: QShowEvent) -> None:
        self.refresh()
        super().showEvent(event)

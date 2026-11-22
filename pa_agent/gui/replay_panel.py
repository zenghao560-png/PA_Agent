"""Read-only replay browser with a separately loaded evidence section."""

# ruff: noqa: RUF001 -- Localized Chinese UI punctuation.
from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from pa_agent.gui.event_audit_panel import EventAuditPanel
from pa_agent.replay.repository import load_results
from pa_agent.replay.statistics import summarize

_STATUS_LABELS = {
    "waiting_entry": "等待入场",
    "entry_triggered": "已触发入场",
    "target_1": "理论止盈（TP1）",
    "stop": "理论止损",
    "entry_expired": "限价单过期，未触发",
    "entry_invalidated": "入场前结构失效",
    "entry_ambiguous": "入场顺序无法确认",
    "exit_ambiguous": "止盈/止损顺序无法确认",
    "holding_expired": "持有期限结束",
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
    seconds = epoch_value / 1000.0 if epoch_value > 1e12 else epoch_value
    return (
        datetime.fromtimestamp(seconds, tz=UTC).astimezone(_BEIJING_TZ).strftime("%Y-%m-%d %H:%M")
    )


def _decision_ts(result):
    try:
        return int(result.decision_id.rsplit("-", 1)[-1])
    except (TypeError, ValueError):
        return -1


def _r(value):
    return f"{value:.2f}" if value is not None else "—"


class ReplayPanel(QWidget):
    """Presentation filters never alter persisted results or analysis rules."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._results = []
        self._section_state = None
        self._section_ratios = {}
        self._splitter = QSplitter(Qt.Orientation.Vertical)
        self._splitter.setChildrenCollapsible(False)
        self._splitter.setHandleWidth(7)
        self._splitter.splitterMoved.connect(self._remember_sections)
        self._splitter.setToolTip("拖动分隔条，调整回放记录和事件证据的显示高度")
        records = QWidget()
        records.setMinimumHeight(210)
        top = QVBoxLayout(records)
        top.setContentsMargins(0, 0, 0, 0)
        self._title = QLabel("策略回放")
        top.addWidget(self._title)
        self._note = QLabel(
            "历史决策的理论价格路径，不代表真实成交或盈亏。选择记录查看下方事件证据。"
        )
        self._note.setWordWrap(True)
        top.addWidget(self._note)
        controls = QHBoxLayout()
        self._kind = QComboBox()
        for label, value in [("全部决策", ""), ("只看交易方案", "order"), ("只看 WAIT", "wait")]:
            self._kind.addItem(label, value)
        self._filter = QComboBox()
        self._filter.addItem("全部状态", "")
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索日期、品种或结果")
        self._search.setClearButtonEnabled(True)
        self._refresh = QPushButton("刷新记录")
        self._refresh.setToolTip("只读取已保存的回放记录，不重新分析")
        for control in (self._kind, self._filter):
            controls.addWidget(control)
        controls.addWidget(self._search, 1)
        controls.addWidget(self._refresh)
        top.addLayout(controls)
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        self._summary.setTextFormat(Qt.TextFormat.PlainText)
        self._summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        top.addWidget(self._summary)
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["分析时间（北京）", "品种", "决策", "回放结果", "理论 R", "等待 / 持有（根）"]
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.verticalHeader().hide()
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        for i, width in enumerate((155, 110, 125, 165, 80, 130)):
            self._table.setColumnWidth(i, width)
        self._table.setMinimumHeight(110)
        top.addWidget(self._table, 1)
        self._empty = QLabel("暂无回放记录。")
        top.addWidget(self._empty)
        self._audit_panel = EventAuditPanel()
        self._splitter.addWidget(records)
        self._splitter.addWidget(self._audit_panel)
        self._splitter.setStretchFactor(0, 3)
        self._splitter.setStretchFactor(1, 2)
        self._audit_panel._toggle.toggled.connect(self._resize_sections)
        self._audit_panel.availability_changed.connect(self._resize_sections)
        layout = QVBoxLayout(self)
        layout.addWidget(self._splitter)
        self._table.itemSelectionChanged.connect(self._on_replay_selected)
        self._filter.currentIndexChanged.connect(self._render)
        self._kind.currentIndexChanged.connect(self._render)
        self._search.textChanged.connect(self._render)
        self._refresh.clicked.connect(self.refresh)
        self.refresh()

    def _resize_sections(self, *_):
        height = max(self._splitter.height(), 400)
        expanded = self._audit_panel._toggle.isChecked()
        has_data = self._audit_panel._records.currentData() is not None
        state = (expanded, has_data)
        if state == self._section_state:
            return
        self._section_state = state
        if state in self._section_ratios:
            top = int(height * self._section_ratios[state])
            self._splitter.setSizes([top, height - top])
            return
        bottom = (
            min(460, int(height * 0.55)) if expanded and has_data else (150 if expanded else 34)
        )
        self._splitter.setSizes([max(210, height - bottom), bottom])

    def _remember_sections(self, *_):
        sizes = self._splitter.sizes()
        if self._section_state is not None and sum(sizes):
            self._section_ratios[self._section_state] = sizes[0] / sum(sizes)

    def refresh(self):
        self._results = sorted(load_results(), key=_decision_ts, reverse=True)
        self._filter.blockSignals(True)
        existing = {self._filter.itemData(i) for i in range(self._filter.count())}
        for status in sorted({r.status.value for r in self._results} - existing):
            self._filter.addItem(_STATUS_LABELS.get(status, status), status)
        self._filter.blockSignals(False)
        self._render()

    def _render(self, *_):
        old_scroll = (
            self._table.verticalScrollBar().value(),
            self._table.horizontalScrollBar().value(),
        )
        old = self._table.item(self._table.currentRow(), 0)
        old_id = old.data(Qt.ItemDataRole.UserRole)["decision_id"] if old else None
        selected, kind, search = (
            self._filter.currentData(),
            self._kind.currentData(),
            self._search.text().strip().lower(),
        )
        rows = []
        for result in self._results:
            is_wait = result.result_kind == "wait_observation"
            if (
                (selected and result.status.value != selected)
                or (kind == "wait" and not is_wait)
                or (kind == "order" and result.result_kind != "order_replay")
            ):
                continue
            try:
                if _decision_ts(result) < 0:
                    raise ValueError("unknown timestamp")
                when = _format_beijing_time(float(_decision_ts(result)))
            except (ValueError, OSError, OverflowError):
                when = result.decision_id
            symbol = result.decision_id.rsplit("-", 1)[0]
            decision = (
                "WAIT（未下单）"
                if is_wait
                else f"{_SIDE_LABELS.get(result.order_direction, '方向未知')} {_TYPE_LABELS.get(result.order_type, '订单未知')}"
            )
            status = _STATUS_LABELS.get(result.status.value, result.status.value)
            duration = f"{result.entry_wait_bars if result.entry_wait_bars is not None else '—'} / {result.holding_bars if result.holding_bars is not None else '—'} 根"
            cells = [
                when,
                symbol,
                decision,
                status,
                f"{result.result_r:+.2f}" if result.result_r is not None else "—",
                duration,
            ]
            if search and search not in " ".join(cells).lower():
                continue
            rows.append((result, cells))
        summary = summarize([result for result, _ in rows])
        self._summary.setText(
            f"显示 {len(rows)} / {len(self._results)} 条   ·   交易方案 {summary['sample_count']}   ·   WAIT {summary['wait_observation_count']}   ·   无法回放 {summary['unsupported_order_count']}\n"
            f"理论止盈 {summary['theoretical_target_count']}   ·   理论止损 {summary['theoretical_stop_count']}   ·   过期 {summary['entry_expired_count']}   ·   失效 {summary['entry_invalidated_count']}   ·   歧义 {summary['ambiguous_count']}   ·   平均 R {_r(summary['average_r'])}   ·   中位 R {_r(summary['median_r'])}"
        )
        self._table.blockSignals(True)
        self._table.setRowCount(0)
        self._table.setRowCount(len(rows))
        chosen = None
        for i, (result, cells) in enumerate(rows):
            identity = {
                "decision_id": result.decision_id,
                "source_record": result.source_record,
                "symbol": cells[1],
                "timestamp": _decision_ts(result),
                "label": " | ".join(cells[:4]),
            }
            extra = _MODE_LABELS.get((result.diagnostics or {}).get("mode"), "")
            for j, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, identity)
                item.setToolTip(" | ".join(cells) + ("\n" + extra if extra else ""))
                self._table.setItem(i, j, item)
            if result.decision_id == old_id:
                chosen = i
        self._table.blockSignals(False)
        self._empty.setVisible(not rows)
        self._empty.setText(
            "暂无回放记录。"
            if not self._results
            else "没有符合筛选条件的记录，清空搜索或切换筛选即可。"
        )
        if rows:
            self._table.selectRow(chosen if chosen is not None else 0)
            if chosen is not None:
                self._table.verticalScrollBar().setValue(old_scroll[0])
                self._table.horizontalScrollBar().setValue(old_scroll[1])
        else:
            self._audit_panel.clear_replay_selection()

    def _on_replay_selected(self):
        item = self._table.item(self._table.currentRow(), 0)
        if item is not None:
            self._audit_panel.select_replay(item.data(Qt.ItemDataRole.UserRole))

    def showEvent(self, event: QShowEvent):
        self.refresh()
        self._resize_sections()
        super().showEvent(event)

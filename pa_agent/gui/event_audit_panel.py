"""Read-only, lazily loaded event audit browser below theoretical replay."""
# ruff: noqa: RUF001 -- Chinese UI punctuation is intentional.

from __future__ import annotations

import json
import logging
import math
import threading
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from pa_agent.audit.event_evidence import AUDIT_VERSION
from pa_agent.audit.event_repository import EVENT_AUDIT_DIR

_EVENT_NAMES = {
    "breakout": "收盘突破",
    "breakout_test": "突破回测",
    "breakout_failure": "失败突破",
    "sweep_reclaim": "扫高／扫低收回",
    "sweep_confirmation": "扫后反向确认",
    "sweep_invalidation": "扫后失效",
}
_STATUS_NAMES = {
    "initial_breakout": "初始突破",
    "held_at_close": "回测收盘守住",
    "reclaimed": "收盘收回",
    "reclaimed_unconfirmed": "收回，未确认",
    "opposite_close_confirmed": "反向收盘确认",
    "close_beyond_sweep_extreme": "越过原极值，已失效",
}
_TZ = timezone(timedelta(hours=8))
_CONTEXT_BARS = 3


def _timeframe_ms(value: str) -> int:
    units = {"m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    try:
        number, unit = value[:-1], value[-1]
        if not number.isdecimal() or unit not in units:
            return 0
        return int(number) * units[unit]
    except (TypeError, ValueError, IndexError):
        return 0


def _time(value):
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=_TZ).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OverflowError, OSError):
        return "时间未知"


def _price(value):
    try:
        n = float(value)
        return f"{n:.8f}".rstrip("0").rstrip(".") if math.isfinite(n) else "—"
    except (TypeError, ValueError):
        return "—"


def load_audit_entries(directory: Path):
    """Read current-version evidence only. Return entries and rejected-file count."""
    entries, rejected = [], 0
    if not directory.exists():
        return entries, rejected
    for path in directory.glob(f"*.{AUDIT_VERSION}.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            snapshot = data["snapshot_evidence"]
            events = snapshot["events"]
            if (
                data["audit_version"] != AUDIT_VERSION
                or not data["decision_id"]
                or not isinstance(data["decision_id"], str)
                or not data["symbol"]
                or not data["timeframe"]
                or not math.isfinite(float(data["analysis_timestamp_ms"]))
                or not isinstance(events, list)
                or not all(isinstance(e, dict) for e in events)
            ):
                raise ValueError("invalid audit")
            if (
                not isinstance(data["symbol"], str)
                or not isinstance(data["timeframe"], str)
                or data.get("status") not in {"completed", "partial", "insufficient_data"}
                or not isinstance(snapshot.get("excluded_reasons", {}), dict)
                or not isinstance(snapshot.get("continuity_gaps", []), list)
                or not isinstance(data.get("source_record", ""), str)
            ):
                raise ValueError("invalid audit metadata")
            for event in events:
                if any(
                    not isinstance(event.get(key), str)
                    for key in (
                        "event_id",
                        "structure_id",
                        "event_type",
                        "side",
                        "confirmation_status",
                    )
                ) or any(
                    not math.isfinite(float(event[key]))
                    for key in ("trigger_time_ms", "origin_time_ms", "level_price", "extreme_price")
                ):
                    raise ValueError("invalid event evidence")
            entries.append({"path": str(path), "data": data})
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            rejected += 1
    entries.sort(key=lambda item: float(item["data"]["analysis_timestamp_ms"]), reverse=True)
    return entries, rejected


class EventAuditPanel(QWidget):
    """No calculation, source-record mutation, network, or periodic polling."""

    _loaded = pyqtSignal(object)
    availability_changed = pyqtSignal()

    def __init__(self, parent=None, *, directory=None):
        super().__init__(parent)
        self._directory = Path(directory) if directory is not None else EVENT_AUDIT_DIR
        self._entries = []
        self._busy = False
        self._wanted_replay = None
        self._event_record_id = None
        self._toggle = QToolButton()
        self._toggle.setText("事件证据审计")
        self._toggle.setCheckable(True)
        self._toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._toggle.setToolTip("展开查看已保存的价格事件证据，不会重新分析")
        self._content = QWidget()
        self._content.hide()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 4, 0, 0)
        outer.addWidget(self._toggle)
        self._area = QScrollArea()
        self._area.setWidgetResizable(True)
        self._area.setFrameShape(QFrame.Shape.NoFrame)
        self._area.setWidget(self._content)
        self._area.hide()
        outer.addWidget(self._area)
        layout = QVBoxLayout(self._content)
        layout.setContentsMargins(0, 0, 0, 0)
        self._toggle.setToolTip(
            "分析时的价格事件证据，不代表下单信号或盈亏。时间均为北京时间；展开不会重新分析。"
        )
        controls = QHBoxLayout()
        self._records = QComboBox()
        self._records.setMinimumWidth(0)
        self._records.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._records.setMinimumContentsLength(12)
        self._records.setToolTip("选择分析时间、品种和周期；含 WAIT 决策的审计")
        self._refresh = QPushButton("刷新")
        self._refresh.setToolTip("仅重新读取审计文件，不请求行情、不调用 AI")
        controls.addWidget(self._records, 1)
        self._follow = QCheckBox("跟随回放")
        self._follow.setChecked(True)
        self._follow.setToolTip("手动选择审计后自动关闭；重新勾选可回到上方选中的回放")
        controls.addWidget(self._follow)
        controls.addWidget(self._refresh)
        layout.addLayout(controls)
        self._load_notice = QLabel()
        self._load_notice.setWordWrap(True)
        self._load_notice.hide()
        layout.addWidget(self._load_notice)
        self._identity = QLabel("展开后读取已保存的证据。")
        self._identity.setWordWrap(True)
        self._identity.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._identity)
        self._warning = QLabel()
        self._warning.setWordWrap(True)
        self._warning.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self._warning)
        self._event_body = QWidget()
        self._event_body.hide()
        layout.addWidget(self._event_body)
        layout.addStretch()
        layout = QVBoxLayout(self._event_body)
        layout.setContentsMargins(0, 0, 0, 0)
        filters = QHBoxLayout()
        self._scope = QComboBox()
        self._scope.addItem("附近 3 个周期（记录时间）", "context")
        self._scope.addItem("全部快照事件", "all")
        self._scope.setToolTip(
            "按记录时间筛选最近三个周期的事件，缺口内可能不足三根K线；时间接近不代表AI采用了这些事件。"
        )
        self._kind = QComboBox()
        self._kind.addItem("全部事件", "")
        for key, label in _EVENT_NAMES.items():
            self._kind.addItem(label, key)
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索价位、时间或状态")
        self._search.setClearButtonEnabled(True)
        filters.addWidget(self._scope)
        filters.addWidget(self._kind)
        filters.addWidget(self._search, 1)
        layout.addLayout(filters)
        self._summary = QLabel()
        self._summary.setWordWrap(True)
        layout.addWidget(self._summary)
        self._table = QTableWidget(0, 6)
        self._table.setHorizontalHeaderLabels(
            ["状态发生时间", "事件", "位置", "参考价", "状态", "结构形成时间"]
        )
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self._table.verticalHeader().hide()
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setStretchLastSection(True)
        self._table.setMinimumHeight(120)
        self._table.setMaximumHeight(260)
        layout.addWidget(self._table)
        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlaceholderText("点击事件查看形成、确认或失效时间及对应价位。")
        self._detail.setMaximumHeight(130)
        self._detail.hide()
        layout.addWidget(self._detail)
        self._copy = QPushButton("复制详情")
        self._copy.setEnabled(False)
        detail_controls = QHBoxLayout()
        self._details_toggle = QToolButton()
        self._details_toggle.setText("展开详情")
        self._details_toggle.setCheckable(True)
        self._details_toggle.toggled.connect(self._detail.setVisible)
        detail_controls.addWidget(self._details_toggle)
        detail_controls.addStretch()
        detail_controls.addWidget(self._copy)
        layout.removeWidget(self._summary)
        detail_controls.insertWidget(0, self._summary, 1)
        layout.insertLayout(layout.indexOf(self._table), detail_controls)
        self._toggle.toggled.connect(self._expanded)
        self._refresh.clicked.connect(self.reload)
        self._records.currentIndexChanged.connect(self._render_record)
        self._records.activated.connect(self._manual_record_selected)
        self._follow.toggled.connect(self._follow_changed)
        self._kind.currentIndexChanged.connect(self._render_events)
        self._scope.currentIndexChanged.connect(self._render_events)
        self._search.textChanged.connect(self._render_events)
        self._table.itemSelectionChanged.connect(self._show_detail)
        self._copy.clicked.connect(
            lambda: QApplication.clipboard().setText(self._detail.toPlainText())
        )
        self._loaded.connect(self._apply_loaded)

    def _expanded(self, expanded):
        self._toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
        self._content.setVisible(expanded)
        self._area.setVisible(expanded)
        if expanded:
            self.reload()

    def reload(self):
        if self._busy or not self._toggle.isChecked():
            return
        self._busy = True
        self._refresh.setEnabled(False)
        self._refresh.setText("读取中…")
        directory = self._directory

        def run():
            try:
                result = {"entries": load_audit_entries(directory), "error": False}
            except Exception:
                logging.getLogger(__name__).debug("Audit view load failed", exc_info=True)
                result = {"entries": ([], 0), "error": True}
            with suppress(RuntimeError):  # panel may be destroyed during the read
                self._loaded.emit(result)

        try:
            threading.Thread(target=run, name="pa-audit-view", daemon=True).start()
        except (RuntimeError, OSError):
            self._apply_loaded({"entries": ([], 0), "error": True})

    def _apply_loaded(self, result):
        previous = self._records.currentData()
        previous_id = previous.get("decision_id") if isinstance(previous, dict) else None
        self._busy = False
        self._refresh.setEnabled(True)
        self._refresh.setText("刷新")
        entries, rejected = result["entries"]
        missing_after_rejection = (
            rejected
            and previous_id
            and not any(e["data"]["decision_id"] == previous_id for e in entries)
        )
        if result["error"] or missing_after_rejection:
            self._load_notice.setText(
                "刷新失败，当前显示上次成功读取的数据，请稍后重试。"
                if self._entries
                else "暂时无法读取审计文件，可稍后刷新。"
            )
            self._load_notice.show()
            return
        self._load_notice.clear()
        self._load_notice.hide()
        self._entries, rejected = result["entries"]
        self._records.blockSignals(True)
        self._records.clear()
        self._records.addItem("请选择分析记录", None)
        for entry in self._entries:
            data = entry["data"]
            label = (
                f"{_time(data['analysis_timestamp_ms'])}  {data['symbol']} · {data['timeframe']}"
            )
            self._records.addItem(label, data)
            self._records.setItemData(self._records.count() - 1, label, Qt.ItemDataRole.ToolTipRole)
        self._records.blockSignals(False)
        self._rejected = rejected
        if self._wanted_replay and self._follow.isChecked():
            self._select_requested()
        else:
            index = next(
                (
                    i
                    for i in range(1, self._records.count())
                    if self._records.itemData(i)["decision_id"] == previous_id
                ),
                0 if previous_id else (1 if self._entries else 0),
            )
            self._records.setCurrentIndex(index)
            self._render_record()
            if previous_id and index == 0:
                self._warning.setText("此前查看的审计已不在当前列表，请重新选择记录。")

    def select_replay(self, identity):
        self._wanted_replay = identity
        if self._follow.isChecked() and self._toggle.isChecked() and not self._busy:
            self._select_requested()

    def _manual_record_selected(self, *_):
        self._follow.setChecked(False)

    def _follow_changed(self, enabled):
        if enabled and self._wanted_replay and self._toggle.isChecked() and not self._busy:
            self._select_requested()

    def clear_replay_selection(self):
        self._wanted_replay = None
        if not self._follow.isChecked():
            return
        self._records.setCurrentIndex(0)
        self._render_record()
        self._identity.setText("先在上方选择一条回放记录，也可手动选择已有审计。")

    def _select_requested(self):
        wanted = self._wanted_replay
        matches = []
        for i in range(1, self._records.count()):
            data = self._records.itemData(i)
            source = wanted.get("source_record")
            if source and data.get("source_record"):
                match = Path(source).resolve() == Path(data["source_record"]).resolve()
            else:
                match = data.get("symbol") == wanted.get("symbol") and data.get(
                    "analysis_timestamp_ms"
                ) == wanted.get("timestamp")
            if match:
                matches.append(i)
        self._records.setCurrentIndex(matches[0] if len(matches) == 1 else 0)
        self._render_record()
        if len(matches) != 1:
            self._identity.setText("所选回放：" + str(wanted.get("label", "")))
            self._warning.setText(
                "找到多份对应审计，请在上方手动选择品种与周期。"
                if matches
                else "未找到对应审计；可能尚未生成或该历史记录没有审计。可刷新重试，或手动选择其他记录。"
            )

    def _render_record(self, *_):
        data = self._records.currentData()
        if data:
            self._identity.setText(
                f"审计记录：{data['symbol']} · {data['timeframe']} · {_time(data['analysis_timestamp_ms'])}"
            )
            snap = data["snapshot_evidence"]
            warnings = []
            status = data.get("status")
            if status == "partial":
                warnings.append("证据不完整，请结合以下数据提示查看。")
            elif status == "insufficient_data":
                warnings.append("连续有效 K 线不足，无法形成完整事件证据。")
            reasons = snap.get("excluded_reasons") or {}
            for key, label in [
                ("unavailable_at_analysis_time", "时间晚于分析截止，已排除"),
                ("not_closed", "未收盘，已排除"),
                ("invalid_ohlc_or_timestamp", "价格或时间无效，已排除"),
                ("duplicate_timestamp", "时间重复，已排除"),
            ]:
                if reasons.get(key):
                    warnings.append(f"{label}：{reasons[key]} 根")
            if reasons.get("unavailable_at_analysis_time"):
                warnings.append(
                    "时间轴存在异常：下方按记录中的时间筛选，尚不能确认与真实行情时序一致。"
                )
            if snap.get("continuity_gaps"):
                warnings.append(
                    f"时间不连续：{len(snap['continuity_gaps'])} 处，未跨缺口跟踪事件。"
                )
            if getattr(self, "_rejected", 0):
                warnings.append(f"已跳过 {self._rejected} 份无法读取的审计文件。")
            self._warning.setText(
                "\n".join(warnings) if warnings else "未发现已记录的数据完整性异常。"
            )
            self._records.setToolTip(self._records.currentText())
        else:
            self._identity.setText("暂无可查看的记录。")
            self._warning.setText("暂无审计记录。分析完成后可点刷新；历史记录可能没有审计结果。")
            if getattr(self, "_rejected", 0):
                self._warning.setText(
                    f"已跳过 {self._rejected} 份无法读取的审计文件，暂无可查看的记录。"
                )
        self._render_events()
        self._event_body.setVisible(bool(data))
        self.availability_changed.emit()

    def _render_events(self, *_):
        data = self._records.currentData()
        record_id = data.get("decision_id") if data else None
        same_record = record_id is not None and record_id == self._event_record_id
        old = self._table.item(self._table.currentRow(), 0)
        previous_event = (
            old.data(Qt.ItemDataRole.UserRole).get("event_id") if old and same_record else None
        )
        table_scroll = (
            self._table.verticalScrollBar().value(),
            self._table.horizontalScrollBar().value(),
        )
        detail_scroll = (
            self._detail.verticalScrollBar().value(),
            self._detail.horizontalScrollBar().value(),
        )
        area_scroll = self._area.verticalScrollBar().value()
        events = (data.get("snapshot_evidence") or {}).get("events", []) if data else []
        kind = self._kind.currentData()
        scope = self._scope.currentData()
        search = self._search.text().strip().lower()
        cutoff = (data.get("snapshot_evidence") or {}).get("analysis_cutoff_bar") if data else None
        duration = _timeframe_ms(data.get("timeframe", "")) if data else 0
        context_start = (
            float(cutoff) - duration * (_CONTEXT_BARS - 1) if cutoff and duration else None
        )
        scoped_events = [
            event
            for event in events
            if scope == "all"
            or (
                context_start is not None
                and context_start <= float(event.get("trigger_time_ms", 0)) <= float(cutoff)
            )
        ]
        rows = []
        for event in scoped_events:
            cells = [
                _time(event.get("trigger_time_ms")),
                _EVENT_NAMES.get(event.get("event_type"), str(event.get("event_type", "未知事件"))),
                {"upper": "上沿", "lower": "下沿"}.get(event.get("side"), "—"),
                _price(event.get("level_price")),
                _STATUS_NAMES.get(
                    event.get("confirmation_status"),
                    str(event.get("confirmation_status", "未知状态")),
                ),
                _time(event.get("origin_time_ms")),
            ]
            if (not kind or event.get("event_type") == kind) and (
                not search or search in " ".join(cells).lower()
            ):
                rows.append((event, cells))
        rows.sort(key=lambda row: float(row[0].get("trigger_time_ms") or 0), reverse=True)
        self._table.setRowCount(0)
        self._detail.clear()
        self._copy.setEnabled(False)
        self._table.setRowCount(len(rows))
        self._table.setVisible(bool(rows))
        self._details_toggle.setVisible(bool(rows))
        self._copy.setVisible(bool(rows))
        self._detail.setVisible(bool(rows) and self._details_toggle.isChecked())
        previous_row = None
        for i, (event, cells) in enumerate(rows):
            if event.get("event_id") == previous_event:
                previous_row = i
            for j, text in enumerate(cells):
                cell = QTableWidgetItem(text)
                cell.setData(Qt.ItemDataRole.UserRole, event)
                cell.setToolTip(
                    f"形成：{_time(event.get('origin_time_ms'))}\n状态发生：{_time(event.get('trigger_time_ms'))}\n{text}"
                )
                self._table.setItem(i, j, cell)
        snap = data["snapshot_evidence"] if data else {}
        structures = len({e.get("structure_id") or e.get("event_id") for e in scoped_events})
        scope_label = "决策上下文" if scope == "context" else "全部快照"
        self._summary.setText(
            f"范围：{scope_label} · 本范围 {structures} 个结构 / {len(scoped_events)} 条事件 · 筛选后 {len(rows)} 条"
            + (
                f"\n记录时间区间：{_time(context_start)} — {_time(cutoff)}（K线开盘时间）"
                if scope == "context" and context_start is not None
                else ""
            )
            + f"\n全快照：{snap.get('bar_count', 0)} 根有效 K 线 / {len(events)} 条事件"
            + (
                "\n无法确定最近 K 线范围，请检查记录或切换全部快照。"
                if data and scope == "context" and context_start is None
                else ""
            )
            + ("\n未发现符合筛选条件的事件。" if data and not rows else "")
        )
        self._summary.setToolTip(
            "同一结构可能有多条状态记录；突破跟踪止于首次回测、失败或观察期结束。时间附近的事件不代表AI实际引用的依据。"
        )
        self._event_record_id = record_id
        if rows:
            self._table.selectRow(previous_row if previous_row is not None else 0)
            if previous_row is not None:
                self._table.verticalScrollBar().setValue(table_scroll[0])
                self._table.horizontalScrollBar().setValue(table_scroll[1])
                self._detail.verticalScrollBar().setValue(detail_scroll[0])
                self._detail.horizontalScrollBar().setValue(detail_scroll[1])
        if same_record:
            self._area.verticalScrollBar().setValue(area_scroll)

    def _show_detail(self):
        items = self._table.selectedItems()
        if not items:
            return
        event = items[0].data(Qt.ItemDataRole.UserRole)
        data = self._records.currentData()
        if not event or not data:
            return
        lines = [
            self._identity.text(),
            f"事件：{_EVENT_NAMES.get(event.get('event_type'), event.get('event_type'))}",
            f"状态：{_STATUS_NAMES.get(event.get('confirmation_status'), event.get('confirmation_status'))}",
            f"参考价：{_price(event.get('level_price'))}；当前事件极值：{_price(event.get('extreme_price'))}",
            f"形成时间：{_time(event.get('origin_time_ms'))}",
            f"本次状态时间：{_time(event.get('trigger_time_ms'))}",
            f"证据可用时间：{_time(event.get('available_at_ms'))}",
            f"最新纳入 K 线：{_time(data['snapshot_evidence'].get('analysis_cutoff_bar'))}",
            f"结构编号：{event.get('structure_id', '—')}",
            f"原始记录：{data.get('source_record', '—')}",
        ]
        self._detail.setPlainText("\n".join(lines))
        self._copy.setEnabled(True)

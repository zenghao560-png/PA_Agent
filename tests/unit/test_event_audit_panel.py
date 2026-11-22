"""UI isolation and navigation tests; all data is local and disposable."""

import json
from unittest.mock import patch

from pa_agent.audit.event_evidence import calculate_event_evidence
from pa_agent.gui.event_audit_panel import EventAuditPanel, load_audit_entries
from tests.unit.test_event_evidence import BASE, bar, record


def audit(tmp_path, name="one", symbol="BTCUSDT", timeframe="1m", timestamp=None):
    data = record(
        [bar(i) for i in range(5)] + [bar(5, h=110), bar(6, low=90, c=94)],
        symbol=symbol,
        timeframe=timeframe,
    )
    if timestamp is not None:
        data["meta"]["timestamp_local_ms"] = timestamp
    evidence = calculate_event_evidence(data)
    payload = {
        **evidence,
        "decision_id": name,
        "symbol": symbol,
        "timeframe": timeframe,
        "analysis_timestamp_ms": data["meta"]["timestamp_local_ms"],
        "source_record": str(tmp_path / (name + ".json")),
    }
    path = tmp_path / f"{name}.{evidence['audit_version']}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path, payload


def expand(qtbot, panel):
    panel._toggle.setChecked(True)
    qtbot.waitUntil(lambda: not panel._busy, timeout=3000)


def test_collapsed_panel_never_reads_disk(qtbot, tmp_path):
    with patch(
        "pa_agent.gui.event_audit_panel.load_audit_entries",
        side_effect=AssertionError("eager read"),
    ):
        panel = EventAuditPanel(directory=tmp_path)
        qtbot.addWidget(panel)
        panel.select_replay({"symbol": "BTCUSDT", "timestamp": 1})
        panel.reload()
        assert not panel._busy
        assert panel._entries == []


def test_expand_loads_and_filters_copy_details_readonly(qtbot, tmp_path):
    source, _payload = audit(tmp_path)
    before = source.read_bytes()
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    expand(qtbot, panel)
    assert panel._records.count() == 2
    assert "BTCUSDT" in panel._identity.text()
    assert panel._table.rowCount() > 0
    panel._kind.setCurrentIndex(panel._kind.findData("sweep_reclaim"))
    assert panel._table.rowCount() == 1
    assert "形成时间" in panel._detail.toPlainText()
    panel._copy.click()
    from PyQt6.QtWidgets import QApplication

    assert QApplication.clipboard().text() == panel._detail.toPlainText()
    panel._search.setText("no-match")
    assert panel._table.rowCount() == 0
    assert not panel._copy.isEnabled()
    assert source.read_bytes() == before


def test_empty_directory_is_not_reported_as_processing(qtbot, tmp_path):
    panel = EventAuditPanel(directory=tmp_path / "absent")
    qtbot.addWidget(panel)
    expand(qtbot, panel)
    assert "暂无审计记录" in panel._warning.text()
    assert not panel._copy.isEnabled()


def test_corrupt_files_skipped_and_legacy_ignored(qtbot, tmp_path):
    path, _payload = audit(tmp_path)
    (tmp_path / path.name.replace("one.", "bad.")).write_text("{", encoding="utf-8")
    (tmp_path / "old.event-v1.json").write_text("{", encoding="utf-8")
    entries, rejected = load_audit_entries(tmp_path)
    assert len(entries) == 1 and rejected == 1
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    expand(qtbot, panel)
    assert "跳过 1 份" in panel._warning.text()


def test_replay_selection_matches_exact_source_and_never_guesses_cycle(qtbot, tmp_path):
    _first, one = audit(tmp_path)
    _, _two = audit(tmp_path, name="two")
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    panel.select_replay(
        {"symbol": "BTCUSDT", "timestamp": one["analysis_timestamp_ms"], "label": "test"}
    )
    expand(qtbot, panel)
    assert panel._records.currentIndex() == 0
    assert "多份对应审计" in panel._warning.text()
    panel.select_replay({"source_record": one["source_record"], "label": "exact"})
    assert panel._records.currentData()["decision_id"] == "one"
    panel.select_replay({"symbol": "SOLUSDT", "timestamp": 1, "label": "SOL test"})
    assert panel._records.currentIndex() == 0
    assert panel._table.rowCount() == 0
    assert "未找到对应审计" in panel._warning.text()
    assert "SOL test" in panel._identity.text()


def test_refresh_keeps_manual_choice(qtbot, tmp_path):
    _, one = audit(tmp_path)
    audit(tmp_path, name="two")
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    panel.select_replay({"source_record": one["source_record"]})
    expand(qtbot, panel)
    idx = next(
        i
        for i in range(1, panel._records.count())
        if panel._records.itemData(i)["decision_id"] == "two"
    )
    panel._records.setCurrentIndex(idx)
    panel._records.activated.emit(idx)
    panel.reload()
    qtbot.waitUntil(lambda: not panel._busy)
    assert panel._records.currentData()["decision_id"] == "two"


def test_partial_evidence_warning_is_visible(qtbot, tmp_path):
    path, payload = audit(tmp_path)
    payload["status"] = "partial"
    payload["snapshot_evidence"]["excluded_reasons"] = {"unavailable_at_analysis_time": 16}
    payload["snapshot_evidence"]["continuity_gaps"] = [{}]
    path.write_text(json.dumps(payload), encoding="utf-8")
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    expand(qtbot, panel)
    assert "证据不完整" in panel._warning.text()
    assert "16 根" in panel._warning.text()
    assert "时间不连续" in panel._warning.text()


def test_thread_start_failure_can_retry_in_ui(qtbot, tmp_path):
    audit(tmp_path)
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    with patch(
        "pa_agent.gui.event_audit_panel.threading.Thread.start", side_effect=RuntimeError("test")
    ):
        panel._toggle.setChecked(True)
    assert not panel._busy
    assert "无法读取" in panel._load_notice.text()
    panel.reload()
    qtbot.waitUntil(lambda: not panel._busy)
    assert panel._table.rowCount() > 0


def test_replay_rows_and_statistics_remain_unchanged_and_selection_links(qtbot, tmp_path):
    from pa_agent.gui.replay_panel import ReplayPanel
    from pa_agent.replay.models import ReplayResult
    from tests.unit.test_event_evidence import BASE

    results = [
        ReplayResult(
            decision_id=f"BTCUSDT-{BASE}", status="not_applicable", result_kind="wait_observation"
        )
    ]
    with patch("pa_agent.gui.replay_panel.load_results", return_value=results):
        panel = ReplayPanel()
        qtbot.addWidget(panel)
        assert not panel._audit_panel._toggle.isChecked()
        assert panel._table.rowCount() == 1
        assert "交易方案 0" in panel._summary.text()
        assert "WAIT 1" in panel._summary.text()
        labels = [panel._table.item(0, i).text() for i in range(panel._table.columnCount())]
        panel._table.selectRow(0)
        assert panel._audit_panel._wanted_replay["timestamp"] == BASE
        panel.refresh()
        assert [panel._table.item(0, i).text() for i in range(panel._table.columnCount())] == labels
        assert panel._table.currentRow() == 0
        assert panel._audit_panel._entries == []


def test_reader_does_not_trigger_analysis_or_replay_computation(qtbot, tmp_path):
    audit(tmp_path)
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    with (
        patch(
            "pa_agent.audit.event_evidence.calculate_event_evidence",
            side_effect=AssertionError("calculation"),
        ),
        patch(
            "pa_agent.audit.event_worker.submit_event_audit",
            side_effect=AssertionError("submission"),
        ),
        patch(
            "pa_agent.replay.repository.replay_saved_records", side_effect=AssertionError("replay")
        ),
    ):
        expand(qtbot, panel)
        assert panel._table.rowCount() > 0


def test_malformed_event_file_cannot_crash_rendering(tmp_path):
    path, payload = audit(tmp_path)
    payload["snapshot_evidence"]["events"][0]["trigger_time_ms"] = "bad-time"
    path.write_text(json.dumps(payload), encoding="utf-8")
    assert load_audit_entries(tmp_path) == ([], 1)


def test_loading_does_not_block_user_and_duplicate_refresh_is_ignored(qtbot, tmp_path):
    import threading

    entered, release = threading.Event(), threading.Event()

    def slow_read(directory):
        entered.set()
        assert release.wait(3)
        return [], 0

    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    with patch(
        "pa_agent.gui.event_audit_panel.load_audit_entries", side_effect=slow_read
    ) as reader:
        try:
            panel._toggle.setChecked(True)
            assert entered.wait(1)
            assert panel._busy
            panel.reload()
            assert reader.call_count == 1
        finally:
            release.set()
            qtbot.waitUntil(lambda: not panel._busy)
    assert panel._refresh.isEnabled()


def test_missing_link_does_not_show_an_unrelated_decision(qtbot, tmp_path):
    audit(tmp_path)
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    expand(qtbot, panel)
    assert panel._table.rowCount() > 0
    panel.select_replay({"symbol": "SOLUSDT", "timestamp": 1, "label": "SOL"})
    assert panel._table.rowCount() == 0
    assert not panel._detail.toPlainText()
    assert not panel._copy.isEnabled()


def test_no_matching_audit_hides_empty_controls(qtbot, tmp_path):
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    expand(qtbot, panel)
    assert panel._event_body.isHidden()
    audit(tmp_path)
    panel.reload()
    qtbot.waitUntil(lambda: not panel._busy)
    assert not panel._event_body.isHidden()
    panel.select_replay({"symbol": "SOLUSDT", "timestamp": 1, "label": "SOL"})
    assert panel._event_body.isHidden()


def test_replay_search_grouping_and_rounding_do_not_reload_or_modify_results(qtbot):
    from pa_agent.gui.replay_panel import ReplayPanel
    from pa_agent.replay.models import ReplayResult

    results = [
        ReplayResult(
            decision_id="BTCUSDT-1700000000000",
            status="target_1",
            result_r=1.23456789,
            order_direction="short",
            order_type="limit",
        ),
        ReplayResult(
            decision_id="SOLUSDT-1700000060000",
            status="not_applicable",
            result_kind="wait_observation",
        ),
    ]
    before = [r.model_dump() for r in results]
    with patch("pa_agent.gui.replay_panel.load_results", return_value=results) as reader:
        panel = ReplayPanel()
        qtbot.addWidget(panel)
        assert panel._table.rowCount() == 2
        assert "1.23" in panel._summary.text()
        assert "1.23456789" not in panel._summary.text()
        panel._kind.setCurrentIndex(panel._kind.findData("wait"))
        assert panel._table.rowCount() == 1
        assert panel._table.item(0, 1).text() == "SOLUSDT"
        panel._kind.setCurrentIndex(0)
        panel._search.setText("BTC")
        assert panel._table.rowCount() == 1
        panel._search.setText("unmatched")
        assert panel._table.rowCount() == 0
        assert not panel._audit_panel._wanted_replay
        assert reader.call_count == 1
        assert [r.model_dump() for r in results] == before
        panel._search.clear()
        idx = panel._filter.findData("target_1")
        assert panel._filter.itemText(idx).startswith("理论止盈")
        panel._filter.setCurrentIndex(idx)
        assert panel._table.rowCount() == 1
        assert panel._table.item(0, 4).text() == "+1.23"


def test_small_replay_window_keeps_usable_record_area_and_draggable_divider(qtbot, tmp_path):
    from pa_agent.gui.replay_panel import ReplayPanel
    from pa_agent.replay.models import ReplayResult

    results = [
        ReplayResult(
            decision_id="BTCUSDT-1700000000000",
            status="not_applicable",
            result_kind="wait_observation",
        )
    ]
    with patch("pa_agent.gui.replay_panel.load_results", return_value=results):
        panel = ReplayPanel()
        qtbot.addWidget(panel)
        panel._audit_panel._directory = tmp_path
        panel.resize(900, 600)
        panel.show()
        expand(qtbot, panel._audit_panel)
        qtbot.wait(20)
        assert panel._table.height() >= 110
        assert panel._splitter.sizes()[0] > panel._splitter.sizes()[1]
        assert panel._audit_panel._event_body.isHidden()
        panel._splitter.setSizes([300, 250])
        assert all(s > 0 for s in panel._splitter.sizes())


def test_decision_context_scope_is_default_and_full_snapshot_is_explicit(qtbot, tmp_path):
    # Put one event outside the default three-bar context and keep a recent one.
    data = record([bar(i) for i in range(5)] + [bar(5, h=112, c=110), bar(6, o=110, h=111, c=100)])
    evidence = calculate_event_evidence(data)
    old = dict(evidence["snapshot_evidence"]["events"][0])
    old["trigger_time_ms"] = BASE
    evidence["snapshot_evidence"]["events"].append(old)
    payload = {
        **evidence,
        "decision_id": "one",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "analysis_timestamp_ms": data["meta"]["timestamp_local_ms"],
        "source_record": str(tmp_path / "one.json"),
    }
    (tmp_path / f"one.{evidence['audit_version']}.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    expand(qtbot, panel)
    assert panel._scope.currentData() == "context"
    context_count = panel._table.rowCount()
    assert context_count < len(evidence["snapshot_evidence"]["events"])
    assert "决策上下文" in panel._summary.text()
    panel._scope.setCurrentIndex(panel._scope.findData("all"))
    assert panel._table.rowCount() == len(evidence["snapshot_evidence"]["events"])
    assert "全部快照" in panel._summary.text()


def test_three_bar_window_is_inclusive_at_start_and_excludes_fourth_bar(qtbot, tmp_path):
    path, payload = audit(tmp_path)
    snap = payload["snapshot_evidence"]
    cutoff = snap["analysis_cutoff_bar"]
    step = 60_000
    prototype = snap["events"][0]
    snap["events"] = []
    for offset in (0, 1, 2, 3, -1):
        event = dict(prototype)
        event.update(
            event_id=f"e{offset}", structure_id=f"s{offset}", trigger_time_ms=cutoff - offset * step
        )
        snap["events"].append(event)
    path.write_text(json.dumps(payload), encoding="utf-8")
    before = path.read_bytes()
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    expand(qtbot, panel)
    assert panel._table.rowCount() == 3
    times = [panel._table.item(i, 0).data(256)["trigger_time_ms"] for i in range(3)]
    assert times == [cutoff, cutoff - step, cutoff - 2 * step]
    assert "本范围 3 个结构 / 3 条事件" in panel._summary.text()
    assert "全快照" in panel._summary.text() and "5 条事件" in panel._summary.text()
    assert "K线开盘时间" in panel._summary.text()
    panel._kind.setCurrentIndex(panel._kind.findData("breakout_failure"))
    assert panel._table.rowCount() == 0
    assert "本范围 3 个结构" in panel._summary.text()
    assert "筛选后 0 条" in panel._summary.text()
    assert path.read_bytes() == before


def test_origin_time_visible_for_later_confirmation(qtbot, tmp_path):
    audit(tmp_path)
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    expand(qtbot, panel)
    panel._kind.setCurrentIndex(panel._kind.findData("sweep_confirmation"))
    assert panel._table.rowCount() == 1
    assert panel._table.item(0, 0).text() != panel._table.item(0, 5).text()
    assert "形成" in panel._table.item(0, 0).toolTip()


def test_unknown_context_boundary_never_falls_back_to_all_events(qtbot, tmp_path):
    path, payload = audit(tmp_path)
    payload["snapshot_evidence"]["analysis_cutoff_bar"] = None
    path.write_text(json.dumps(payload), encoding="utf-8")
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    expand(qtbot, panel)
    assert panel._table.rowCount() == 0
    assert "无法确定" in panel._summary.text()
    panel._scope.setCurrentIndex(panel._scope.findData("all"))
    assert panel._table.rowCount() > 0


def test_manual_audit_survives_parent_refresh_until_follow_reenabled(qtbot, tmp_path):
    from pa_agent.gui.replay_panel import ReplayPanel
    from pa_agent.replay.models import ReplayResult

    _, one = audit(tmp_path, name="one")
    audit(tmp_path, name="two")
    result = ReplayResult(
        decision_id=f"BTCUSDT-{one['analysis_timestamp_ms']}",
        status="not_applicable",
        result_kind="wait_observation",
        source_record=one["source_record"],
    )
    with patch("pa_agent.gui.replay_panel.load_results", return_value=[result]):
        panel = ReplayPanel()
        qtbot.addWidget(panel)
        browser = panel._audit_panel
        browser._directory = tmp_path
        expand(qtbot, browser)
        assert browser._records.currentData()["decision_id"] == "one"
        idx = next(
            i
            for i in range(1, browser._records.count())
            if browser._records.itemData(i)["decision_id"] == "two"
        )
        browser._records.setCurrentIndex(idx)
        browser._records.activated.emit(idx)
        assert not browser._follow.isChecked()
        panel.refresh()
        assert browser._records.currentData()["decision_id"] == "two"
        browser.reload()
        qtbot.waitUntil(lambda: not browser._busy)
        assert browser._records.currentData()["decision_id"] == "two"
        browser._follow.setChecked(True)
        assert browser._records.currentData()["decision_id"] == "one"


def test_event_and_scroll_positions_survive_rerender_and_disk_reload(qtbot, tmp_path):
    from PyQt6.QtCore import Qt

    path, payload = audit(tmp_path)
    template = payload["snapshot_evidence"]["events"][0]
    cutoff = payload["snapshot_evidence"]["analysis_cutoff_bar"]
    payload["snapshot_evidence"]["events"] = [
        {
            **template,
            "event_id": f"event-{i}",
            "structure_id": f"structure-{i}",
            "trigger_time_ms": cutoff - i * 60000,
            "origin_time_ms": cutoff - i * 60000,
            "available_at_ms": cutoff - (i - 1) * 60000,
        }
        for i in range(40)
    ]
    path.write_text(json.dumps(payload), encoding="utf-8")
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    panel.resize(540, 800)
    panel.show()
    expand(qtbot, panel)
    panel._scope.setCurrentIndex(panel._scope.findData("all"))
    panel._details_toggle.setChecked(True)
    panel._table.selectRow(20)
    panel._table.scrollToItem(panel._table.item(20, 0))
    qtbot.wait(20)
    panel._table.horizontalScrollBar().setValue(panel._table.horizontalScrollBar().maximum())
    panel._detail.verticalScrollBar().setValue(panel._detail.verticalScrollBar().maximum())
    before = (
        panel._table.verticalScrollBar().value(),
        panel._table.horizontalScrollBar().value(),
        panel._detail.verticalScrollBar().value(),
    )
    assert before[0] > 0
    selected_id = panel._table.item(20, 0).data(Qt.ItemDataRole.UserRole)["event_id"]
    panel._render_record()
    assert (
        panel._table.item(panel._table.currentRow(), 0).data(Qt.ItemDataRole.UserRole)["event_id"]
        == selected_id
    )
    assert (
        panel._table.verticalScrollBar().value(),
        panel._table.horizontalScrollBar().value(),
        panel._detail.verticalScrollBar().value(),
    ) == before
    panel.reload()
    qtbot.waitUntil(lambda: not panel._busy)
    assert (
        panel._table.item(panel._table.currentRow(), 0).data(Qt.ItemDataRole.UserRole)["event_id"]
        == selected_id
    )
    assert panel._table.verticalScrollBar().value() == before[0]


def test_failed_refresh_retains_old_content_and_clears_notice_after_recovery(qtbot, tmp_path):
    audit(tmp_path)
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    expand(qtbot, panel)
    previous = panel._records.currentData()["decision_id"]
    rows = panel._table.rowCount()
    details = panel._detail.toPlainText()
    with patch(
        "pa_agent.gui.event_audit_panel.load_audit_entries", side_effect=OSError("access denied")
    ):
        panel.reload()
        qtbot.waitUntil(lambda: not panel._busy)
    assert panel._records.currentData()["decision_id"] == previous
    assert panel._table.rowCount() == rows
    assert panel._detail.toPlainText() == details
    assert "上次成功" in panel._load_notice.text()
    assert panel._refresh.isEnabled()
    panel.reload()
    qtbot.waitUntil(lambda: not panel._busy)
    assert panel._load_notice.isHidden()


def test_corrupt_selected_file_retains_cached_record_with_stale_notice(qtbot, tmp_path):
    path, _ = audit(tmp_path)
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    expand(qtbot, panel)
    rows = panel._table.rowCount()
    path.write_text("{", encoding="utf-8")
    panel.reload()
    qtbot.waitUntil(lambda: not panel._busy)
    assert panel._table.rowCount() == rows
    assert "上次成功" in panel._load_notice.text()


def test_splitter_and_replay_scroll_survive_refresh_and_tab_show(qtbot, tmp_path):
    from pa_agent.gui.replay_panel import ReplayPanel
    from pa_agent.replay.models import ReplayResult

    _, one = audit(tmp_path)
    result = ReplayResult(
        decision_id=f"BTCUSDT-{one['analysis_timestamp_ms']}",
        status="not_applicable",
        result_kind="wait_observation",
        source_record=one["source_record"],
    )
    with patch("pa_agent.gui.replay_panel.load_results", return_value=[result]):
        panel = ReplayPanel()
        qtbot.addWidget(panel)
        panel._audit_panel._directory = tmp_path
        panel.resize(900, 700)
        panel.show()
        expand(qtbot, panel._audit_panel)
        panel._splitter.setSizes([450, 200])
        panel._splitter.splitterMoved.emit(panel._splitter.sizes()[0], 1)
        sizes = panel._splitter.sizes()
        panel.refresh()
        assert panel._splitter.sizes() == sizes
        panel.hide()
        panel.show()
        assert panel._splitter.sizes() == sizes
        panel._audit_panel._toggle.setChecked(False)
        panel._audit_panel._toggle.setChecked(True)
        qtbot.waitUntil(lambda: not panel._audit_panel._busy)
        assert abs(panel._splitter.sizes()[0] - sizes[0]) <= 2


def test_removed_manual_record_does_not_silently_switch_to_another(qtbot, tmp_path):
    path, _ = audit(tmp_path, name="one")
    audit(tmp_path, name="two")
    panel = EventAuditPanel(directory=tmp_path)
    qtbot.addWidget(panel)
    expand(qtbot, panel)
    idx = next(
        i
        for i in range(1, panel._records.count())
        if panel._records.itemData(i)["decision_id"] == "one"
    )
    panel._records.setCurrentIndex(idx)
    panel._records.activated.emit(idx)
    path.unlink()
    panel.reload()
    qtbot.waitUntil(lambda: not panel._busy)
    assert panel._records.currentIndex() == 0
    assert panel._table.rowCount() == 0
    assert "重新选择" in panel._warning.text()


def test_replay_record_and_scroll_position_survive_background_style_refresh(qtbot):
    from pa_agent.gui.replay_panel import ReplayPanel
    from pa_agent.replay.models import ReplayResult

    results = [
        ReplayResult(
            decision_id=f"BTCUSDT-{1700000000000 + i * 60000}",
            status="not_applicable",
            result_kind="wait_observation",
        )
        for i in range(40)
    ]
    with patch("pa_agent.gui.replay_panel.load_results", return_value=results):
        panel = ReplayPanel()
        qtbot.addWidget(panel)
        panel.resize(550, 650)
        panel.show()
        panel._table.selectRow(20)
        panel._table.scrollToItem(panel._table.item(20, 0))
        qtbot.wait(20)
        panel._table.horizontalScrollBar().setValue(panel._table.horizontalScrollBar().maximum())
        before = (
            panel._table.verticalScrollBar().value(),
            panel._table.horizontalScrollBar().value(),
        )
        assert before[0] > 0
        record_time = panel._table.item(20, 0).text()
        panel.refresh()
        assert panel._table.item(panel._table.currentRow(), 0).text() == record_time
        assert (
            panel._table.verticalScrollBar().value(),
            panel._table.horizontalScrollBar().value(),
        ) == before

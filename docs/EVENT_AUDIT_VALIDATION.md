# Event evidence audit: current v3 review (2026-11-22)

## Current scope and verification

This follow-up changes only the independent audit evidence/worker and their tests.
No changes were made in this iteration to the orchestrator hooks, existing trading
modules, GUI, data provider, record schema, prompt assets or configuration.

Before editing, the review reproduced missing-data bridging and failed thread
startup recovery (confirmed defects). Sweep invalidation was a missing audit
state: it is now explicitly defined using a close beyond the original sweep
extreme. This definition affects observer labels only, never trading rules.

- Output version is `event-v3`; v1/v2 files remain separately versioned and intact.
- Any interval discontinuity resets active breakout/sweep observations and the
  five-bar reference window. It is reported in `continuity_gaps`. A gap is an
  observed time discontinuity, not proof the data source is faulty (e.g. session
  closures); no trading-calendar logic or source timestamps are changed.
- After a gap, five contiguous reference bars must exist before a new event can
  be observed. Fragmented inputs with no usable reference return insufficient
  data, rather than claiming complete evidence. Excluded invalid bars also cause
  a reset where their removal leaves a discontinuity.
- A close above an upper sweep extreme / below a lower sweep extreme records
  `sweep_invalidation` and ends that sweep. Later reversal cannot retroactively
  confirm it. Equal closes do not invent invalidation. Lifecycle rows retain the
  original structure ID.
- Failed worker construction/start returns False without enqueueing. The thread
  field remains unset, enabling a later retry and safe shutdown.
- Canonical lowercase interval units prevent `1M` from being silently interpreted
  as one minute. Unsupported intervals only reject the audit, never the analysis.
- Breakout tracking still ends at the first test/failure or expiry. This bounded
  first-outcome scope is now included in each snapshot; it does not claim to
  follow every later outcome or collect new future bars.

Current results using Python 3.11.9:

- New audit tests: **54 passed**.
- Audit plus market-features/strategy-routing tests: **80 passed**.
- Core regression: **1141 passed, 13 failed, 7 live tests deselected**.
- XML failure identifiers match the preceding regression exactly. Those same 13
  failures were already reproduced using the original HEAD orchestrator.
- A real background-worker versus disabled-worker integration test produces
  identical Stage1, Stage2, prompts, emitted event order and two model calls.
  Source record bytes are unchanged. Submission-failure and WAIT-gate isolation
  tests remain passing.
- The same two real historical records retain their original SHA256, Stage1,
  Stage2 and final decisions after offline v3 auditing. V2/v3 audit results are
  retained side by side in `logs/event-audit-historical/`.
- Actual compile, Ruff, and whitespace checks pass. This establishes tested
  isolation, not zero resource use or a proof that the entire project is bug-free.

Current runtime reports:

- `logs/event-audit-v3-targeted.xml`
- `logs/event-audit-v3-core.txt` and `.xml`
- `logs/event-audit-v3-historical-check.json`

The upstream timestamp discrepancy and pre-existing test failures documented
below remain open. This iteration does not rewrite market timestamps or original
trading behavior to make those tests pass. The four previously failing GUI E2E
tests below were not rerun in this iteration because GUI code is unchanged.

---

## UI addendum: below Strategy Replay

The existing Strategy Replay tab now contains a separate, initially collapsed
Event Evidence Audit section below its original list. Existing replay computation,
status filters, summary text, sorting and trading decisions remain unchanged.
Only `gui/replay_panel.py` connects the new, independent browser.

User flow:

1. Open Strategy Replay and expand Event Evidence Audit.
2. Choose a replay row to locate its evidence, or choose an analysis timestamp,
   symbol and timeframe in the audit dropdown (newest first).
3. Filter event types or search prices/times/status. Select an event to see its
   origin, observation/availability time, level, extreme and source record.
4. Copy selected-event details when needed. Refresh reads files only.

No match clears the table rather than showing a different decision. If symbol and
timestamp cannot distinguish multiple audits, manual selection is required.
Manual selection is retained across refresh. Missing, partial, discontinuous and
corrupt evidence have explicit messages. Unsupported old-version audit files are
not presented as current verified evidence. This does not generate missing audits.

The browser performs no file scan until expanded. A single in-flight reader per
panel loads existing files off the UI thread; duplicate refreshes are ignored,
startup failure can be retried, and late results after widget destruction are
ignored. There is no polling, market-data request, AI call or replay calculation.
No new menus, sidebar tabs, configuration or data-provider hooks were added.

UI coverage includes deferred reads, replay association and ambiguity, empty and
corrupt files, partial-data warnings, filters/search/copy, preserving selection,
thread start retry, nonblocking loads, unchanged replay rows/statistics, and
assertions that browsing cannot call analysis/audit/replay computation.
UI tests: **13 passed**. Combined audit/structure/routing tests: **93 passed**.
Final core regression: **1154 passed, 13 failed, 7 live tests deselected**.
Failure identifiers match the pre-UI v3 regression exactly (no new failures).
The test report is `logs/event-audit-ui-tests.xml`; core regression is
`logs/event-audit-ui-core.xml`. A standalone 560px-wide preview was rendered and
visually inspected (`logs/event-audit-ui.png`, mock replay rows with saved audit
data); it is not a screenshot of the running user's window.

## Decision-context boundary correction

The GUI now uses `[latest accepted bar open - 2 * interval, latest accepted bar
open]`, inclusive at both ends, for the three-bar view. The former lower bound
subtracted three intervals and accidentally admitted four candle slots. Events
above the upper bound are also excluded. Unknown cutoff/interval never silently
falls back to all events; the user must choose the full-snapshot scope explicitly.

For the actual 15:00:06 BTCUSDT.P 30m record, the corrected window is 13:30 through
14:30 in recorded Beijing display time: four event rows and four structures.
Scope structure/event counts are separate from the full snapshot's 184 accepted
bars and 108 events; category/text filters have their own remaining-row count.
A new column shows formation time separately from status time, with both times
in tooltips and copied details. Earlier origin does not mean wrong record
association: a later confirmation can refer to an older sweep.

The actual source/audit files were hashed before and after rendering and remain
byte-identical (`logs/event-context-real-check.json`). UI and audit-related
tests: 100 passed (`logs/event-context-correction-tests.xml`). Final core regression:
1166 passed, 13 pre-existing failures (same failure identifiers), 7 live tests deselected
(`logs/event-context-core.xml`).

### Upstream time-axis finding (not modified)

The installed tvDatafeed `main.py` uses naive local `datetime.fromtimestamp`.
`TradingViewSource._row_ts_ms` passes that to `datetime_to_ts_ms`, whose documented
naive policy is UTC (shared with other sources). A local no-network round-trip
through those functions reproduces **+8 hours** on this machine. This explains a
concrete incompatible conversion path; it does not justify subtracting a fixed
eight hours from every historical record or altering a shared helper.

Changing source epochs also affects forming-bar detection, display formatting,
replay and downstream snapshot boundaries. To preserve the user's restriction
on existing functionality/flow, this GUI correction does not modify providers,
bar classification, shared datetime helpers, historical records or trade rules.
Instead, excluded time data now produces an explicit warning that the displayed
record-time axis is anomalous and not yet verified against actual market time.
These audit rows must not be represented as verified actual-market chronology.
A source-specific fix requires its own cross-module impact review and tests.

## Archived v2 report

# Event evidence audit: implementation and validation (2026-11-22)

## Scope and result

The event audit is a read-only observer. The only production integration changes are
at the two successful full-record save call sites in `two_stage.py`. It never
changes Stage1/Stage2, prompts, strategy loading, price plans, RR or the final
BUY/SELL/WAIT. It makes no market-data requests or model calls.

The original prototype's audit defects were confirmed implementation errors
(category A), not defects in the original author's trading rules. They have been
fixed locally within `pa_agent/audit/` and covered by automated tests.

## Corrected behavior

- Version `event-v2` separates corrected output from unreliable prototype v1 files.
  Old v1 files are retained, not silently migrated or used as validated results.
- A breakout fixes the prior five closed bars' extreme at the instant it occurs.
  A subsequent close inside that fixed level, within five observed bars, is a
  failure. Holding on a later touch is a `breakout_test`; the breakout itself is
  `breakout`. Equality alone does not invent failure/confirmation.
- A wick reclaim is unconfirmed evidence. A later close beyond the sweep bar's
  opposite extreme is recorded separately as `sweep_confirmation`.
- `structure_id` links lifecycle observations. `event_id` uses symbol, timeframe,
  type, side, fixed level and absolute origin/trigger timestamps, never `seq`.
  Count structures, not lifecycle rows, when evaluating opportunities.
- Five-bar references are explicitly labelled observational references. They do
  not claim to reproduce AI-selected structures or existing strategy signals.
- Only finite, valid OHLC, explicitly closed, uniquely timestamped bars whose
  closing time is no later than analysis time are accepted. Ambiguous duplicate
  timestamps are excluded independently of input order. Unknown timeframe or
  missing analysis time is rejected rather than inventing availability.
- Results report the cutoff, exact evidence times, availability time, excluded
  bar counts/reasons and partial/insufficient status. No future observations are
  invented. New future tracking remains outside this implementation.
- The worker reads the saved file, not the live model. Missing, malformed,
  partial, failed and missing-decision files are rejected inside the worker.
  Source SHA256 and path provide traceability, including WAIT records.
- There is one lazy daemon worker, a queue capped at 32 pending tasks, in-flight
  duplicate suppression and nonblocking overflow rejection with a warning.
  Shutdown rejects new work; explicit `wait=True` drains it for tests. At process
  exit shutdown is requested without blocking the GUI. Queued observations are
  best effort and are not promised to survive forced process termination.
- Unique temporary files, atomic replacement, cleanup and a write lock protect
  output. Repeated writes of one decision/version produce one destination.
  Audit errors never propagate back into the decision chain.

## Tests and evidence

Interpreter: `py -3.11` -> `E:/work/code/trading/runtimes/Python311/python.exe`,
Python 3.11.9. The WindowsApps `python.exe` alias was not a valid verification
method; earlier claims based on its empty output are superseded by these results.

1. New audit tests: **37 passed** (evidence, repository and isolation).
2. Audit plus existing market-features and strategy-routing tests: **63 passed**.
3. Core regression (unit, integration, property; live excluded):
   **1124 passed, 13 failed, 7 deselected**.
4. All 13 failures were rerun with the literal HEAD version of the orchestrator
   loaded in memory (no working-tree overwrite). **All 13 fail on that baseline**.
5. Four existing GUI E2E tests fail at `ctx.settings.general` because their mock
   AppContext has `settings=None`, before audit integration is exercised. All
   four failures reproduce on the original HEAD orchestrator. A headless harness
   suppresses interactive startup credential dialogs and connectivity probes;
   no trading methods are patched. The unmodified headless run can otherwise
   block on those dialogs.
6. Ruff on all new audit/test files and actual Python compile checks pass.
7. Latest two complete real records (13:00 and 13:30 BTCUSDT.P 30m) were processed
   offline. Their entire source bytes, Stage1, Stage2 and final decisions remain
   identical. New audit outputs and hashes are retained under logs. This was a
   deterministic audit rerun, not a new AI analysis or proof of strategy profit.
8. Isolation tests compare Stage1/Stage2 and orchestrator events with an inert
   audit submitter versus one raising an exception. They are identical. WAIT
   gate-short-circuit tests verify submission occurs after the file exists and
   does not introduce a second model call. A deliberately blocked worker proves
   submission returns before calculation finishes; this is not a promise of
   mathematically zero CPU/latency overhead.

Reproduce targeted tests:

```powershell
py -3.11 -m pytest tests/unit/test_event_evidence.py tests/unit/test_event_repository.py tests/integration/test_event_audit_isolation.py tests/unit/test_market_features.py tests/unit/test_pattern_routing.py
```

Run core regression:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
py -3.11 -m pytest tests/unit tests/integration tests/property -m 'not live' --tb=short
```

Runtime reports (local, ignored by git):

- `logs/event-audit-targeted.xml`
- `logs/event-audit-core-regression.txt` and `.xml`
- `logs/event-audit-baseline-failures.txt` and `.xml`
- `logs/event-audit-e2e.txt` and `.xml`
- `logs/event-audit-e2e-baseline.txt` and `.xml`
- `logs/event-audit-historical-check.json`
- `logs/event-audit-historical/*.event-v2.json`

## Findings outside the audit changes

The 13 core failures concern missing `tools/stage2_raw_sample.txt`, prediction
expectations, existing normalization/category invariants, log test capture, and
forming-bar expectations. They are documented, not hidden by changing original
trading rules or weakening existing assertions. The full project is not green.

Both checked historical records contain 16 bars whose recorded closing times
are later than the recorded analysis timestamp. For the 13:30 local decision,
analysis is 05:30 UTC while the latest recorded bar opens at 13:00 UTC. This is
consistent with a possible source time-zone conversion discrepancy, but does not
prove real future price access. The audit excludes those bars, reports
`unavailable_at_analysis_time=16` and marks the result `partial`. It does not
silently subtract eight hours, repair historical inputs, or change the data
provider. These historical audit results must not be presented as a complete
strategy-quality assessment until the source timestamp semantics are verified.

## UI state preservation verification (2026-11-22)

This update changes only replay/audit presentation state. It does not change
Stage1, Stage2, prompts, trading validation, market-data conversion, stored
source records or replay outcome calculations.

- Follow Replay is explicit and enabled by default. Manual audit selection
  disables following; re-enabling it restores the current replay association.
- Refresh preserves event selection by event ID within the same decision,
  table/detail scroll positions and the user-adjusted splitter proportions.
- Read failures retain cached content with a visible stale-data notice. A
  corrupt selected audit is retained from cache; a confirmed deleted manual
  selection requests reselection instead of silently switching decisions.
- The context filter is labelled nearby three periods in recorded time,
  avoiding a promise that three actual candles are available.

Validation:

- Targeted UI, evidence, repository, isolation, market-feature and routing
  tests: **107 passed** (`logs/audit-ui-state-tests.xml`). Includes the final
  replay table scroll-preservation test.
- Core unit/integration/property regression, excluding live tests:
  **1167 passed, 13 failed, 7 deselected**
  (`logs/audit-ui-state-core.xml`). This run preceded the final added
  replay-scroll test; production code was unchanged afterwards.
- All 13 failure identifiers exactly match both
  `logs/event-context-core.xml` and `logs/replay-ui-v2-core.xml`: no new
  failing tests. Earlier baseline reproduction remains applicable; the full
  project is not green.
- Python compilation of both UI modules and the UI tests passed;
  `git diff --check` passed (line-ending warning only).

The previously documented upstream timestamp discrepancy remains unresolved
and is intentionally not repaired by this UI update.

# PA_Agent 决策链审查（2026-11-18）

## 范围和方法

遵循项目 AGENTS.md，仅审查 RR 调整链、背景计算窗口和审计口径。未修改决策业务逻辑、阈值或提示词。用保存的 stage2_response.content 还原模型输出，复制后调用现有指标校验函数；没有重新请求 AI，没有修改历史原件。

复现：在项目根目录设置 PYTHONPATH=.，运行 tools/audit_decision_rr.py。
证据：同目录 2026-11-18-rr-replay.json，含原始模型方案、当前校验结果、保留止损的对照和保存的 Stage1/Stage2。

此前“最近两条”的选取使用了修改时间，恢复文件会改变修改时间，导致漏掉10:03记录。应按 meta.timestamp_local_ms 排序。此次同时检查10:00、10:03、10:30三条，避免遗漏。

## 1. RR 改写：明确的原设计行为，与当前维护原则冲突

位置：util/trade_metrics.py 的 MIN_RISK_REWARD_RATIO、MAX_TP1_RISK_REWARD_RATIO、adjust_decision_stop_for_tp1_rr_cap、validate_order_trade_metrics；ai/stage2_normalizer.py 的 normalize_stage2 和 _coerce_decision_when_trade_metrics_fail；ai/json_validator.py 的 _check_trade_metrics。

调用链：模型 JSON → Stage2 normalizer → 直接扩大止损 → 指标校验（也可扩大止损）→ 失败清空三价并改10.3及terminal → 节点引擎；JSON validator也调用指标校验。只删一个调用点不足以消除改写。

原设计：代码注释和 decision_stance 明确要求保持目标、扩大止损，将TP1 RR限制到1。目的可能是避免过紧止损，但这只是推测，未确认原作者动机。不是算术错误，也不是模型止损误传。

原始10:30：entry=64261.4，stop=64103.99，TP1=64627.2，主观胜率=50；risk=157.41，reward=365.8，RR=2.3238676。当前规则将stop改成63895.6，risk=365.8，方程期望=0，拒绝。仅关闭止损调整时仍因RR>1上限拒绝，不会自动成为有效建议。

分类：A类（与用户明确的结构止损维护要求冲突）；胜率50来自模型原始输出，先前“程序默认补50%”的说法在本案例不成立。

最小建议：经确认后同时关闭两个生产调用路径中的止损改写，并同步提示词；保留现有RR上限时应明确以超上限拒绝。取消上限是独立策略变更，不能捆绑实施。保留原始三价及拒绝原因，不能仅保存清空后的值。

影响：保留上限时10:30仍拒绝，但拒绝原因变化，结构止损不再被改写；取消上限可能使指标检查通过，但不代表结构、信号、连续性等完整门控通过。

验证限制：本轮是纯指标离线对照，未提供frame，因此不含tick推断、K1 freshness等检查。不是重新运行模型，也不是完整Stage2批准。10:00与10:03原始即不下单，无RR方案，指标检查不介入。

## 2. 背景窗口：描述范围大于实际投票范围

位置：ai/trend_context.py compute_background_direction、_direction_vote_on_slice、render_three_window_summary；ai/prompt_assembler.py §2.2摘要。

实际：bars[40:]为候选背景，但投票window=min(30,len(bg_bars))；200根时核心统计使用K41–K70。EMA本身仍含更早数据影响，不能说完全不使用更早数据。文本却标为K200–K41，模型可能理解成整个160根的综合趋势。

原意：将背景与最近40根隔离，用较局部的投票形成参考、不否决近期；为何只用30根未找到明确解释，保留原算法。

分类：A类口径不准确；不能据此判定bullish计算错误。建议只补充“背景候选范围、实际投票窗口、EMA斜率子窗口”的描述，不扩大计算窗口、不改方向阈值。

影响：计算不变；提示词文字变清楚后，未来AI解释可能变化，不能承诺Stage1输出逐字不变。应先用同一输入比较组装文本，记录方向数值不变；AI复跑另行单独标注随机性。

## 3. 审计字段与最终输出不同步

位置：ai/decision_nodes.py apply_stage1在合并gate_trace前构造node_coverage；ai/node_coverage.py明确仅诊断、不参与交易；stage2_normalizer._coerce_decision_when_trade_metrics_fail用terminal.setdefault('label',...)。

coverage反映合并前节点，并不保证对应最终gate_trace，历史数据存在2.5字段不一致。需要追踪merge和后续normalizer才能决定修复点，暂归B类成因待验证、A类记录表象不一致，不新增交易拦截。

10:30的terminal.outcome已改reject，但原“限价做多”label因setdefault而保留，key_factors仍描述RR约2.3。原文保留有审计价值，但未标记成原方案会误导展示。属于A类拒绝后的展示/审计同步缺陷。

最小建议：拒绝时明确覆盖最终terminal.label；模型原label、三价、理由存入调整记录。覆盖率在最终trace完成后重建，保持program基线；先核对后续调用再动代码。

## 结论

优先修复方向是保留结构止损与拒绝理由可追踪、背景范围表达、拒绝后标签同步。周期阈值、顺势限制、均衡档计划型限价和主观胜率方程均暂保留。下一步应明确是否保留1:1上限，再同步修改代码、提示词和相关测试，不能仅删除扩大止损函数。

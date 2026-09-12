# ROADMAP — mempipeline 演进路线图

> 状态标注：`[x]` 已落地，`[ ]` 拟议/待裁决。已落地阶段写实；拟议阶段标候选方向，需人工裁决后再推进。
> 上游权威：PDCA实操方法论与mempipeline融入边界（Phase A/B/C 映射）、mempipeline 可演进方案总纲（加固治理护城河，P1→P2→P3）。

## 定位锚点（为何存在）

治理优先的个人记忆资产管线，1 写者 + N 投稿 + 无限读。差异化：完整软状态转化的治理状态机 + 本地隐私优先。对社区项目（Mem0 / Basic Memory / Zep-Graphiti）取舍的核心结论：不打语义推理/生态牌，打可审计、可回滚、本地闭环。

## 阶段权威映射

| Phase | 语义 | 落点 | 状态 |
|-------|------|------|------|
| A | 写一致性 | engine.write_atomic 幂等原子写 + 崩溃 sidecar | `[x]` |
| B | 语义/检索 | semantic(BGE-M3+RRF)、crossref、recall_golden | `[x]` |
| C | 自我进化 | recall_golden.check 信号层（默认关） | `[x]` 信号层 / `[ ]` 闭环机制 |

## 主线程：加固治理护城河 P1→P2→P3

### P1 自动化审计 `[x]`
- 状态转移轨迹 append-only 记录与还原（trace / lifecycle）
- reason 由系统生成，免人工填写
- 旧/冷/冗余笔记只出候选清单（scan_stale_notes），不自动改状态
- 验收：test_governance.py P1 段（生命周期按序还原 + 轨迹追加而非覆盖）

### P2 时间维度图谱 `[x]`
- 主题聚簇（subject_key，标题字符级规范化，摘要不参与键）
- 时间序回放（TopicTimeline.recall / spans_days）
- 演化信号（first / continued / revived / same_day + duplicate / drift / stable）
- 时间基准回退（updated/created 优先，缺失回落 mtime，显式 time_src 审计标签）
- 项目隔离（projects=[...]）
- 验收：test_timegrap.py 6 段（聚簇 / 脉络 / 信号 / 回落 / 隔离 / 阈值）

### P3 自我进化闭环 `[3/3]`（③①② 均已立项落地）
候选方向（已全部开工并落地）：
- **golden set 扩充 + 召回回归门槛 `[x]`**：GOLDEN 从 4 条扩为覆盖「长期+中期」两层的全层级回归基线（`mempipeline/recall_golden.py`，10 条），周检门禁 `weekly_health.golden_regress()` 自动取用；全量锚点取库内真实笔记，本批命中率 1.000。
  - 验收：真实镜像 check() 全层级命中率 1.000 > 红线 2/3；test_mempipeline.py RECALL GOLDEN 段（注入沙盒集）仍绿；旧版 `_test_recall_golden.py` SMOKE 8/8。
- **Act 仍人类触发 `[x]`**：新增 `mempipeline/act.py`——召回退化诊断与 Act 建议面。对 golden 未命中，把查询拆到主词空间 term，逐 term 对照期望笔记，定位缺词并分档三杠杆：synonym（缺专用词）/ stopword（全库通用词缺区分度）/ threshold（已获分未进 top-k）。**只显影、只建议，不自动改参、不写库**；人类采纳后的写回仍走调用方 write_atomic + 审计。
  - 验收：test_act.py 11/11（synonym/stopword/threshold/never_scored/trace_recall）。
- **时间图谱信号使入治理 `[x]`**：把 timegrap 的断更(revived) / 结论漂移(drift) 信号作为候选源喂给 P1 候选清单（`scan_stale_notes(..., timeline=...)`），仍不自动改状态。
  - 验收：test_governance.py 第 8 段（drift→re-review、revived→review、扫描后 4 篇仍 active）。
- 验证信号（草案，落地时定稿）：召回回归命中率、候选清单误报率、跨轮次脉络连续性。`[ ]` 待跨轮次运行后积累样本再定稿
- 边界（铁律）：Act 默认关；重引擎不入；不新增第二套度量。

## 非目标 / 硬边界

- 重引擎不入：不引入 Lean / NautilusTrader / 消息总线 / Iceberg 等重型依赖，保持 A 股本地可跑。
- Act 默认关：任何自动改参一律禁止，人工是最终 Check→Act。
- 不合并治理流程本体：不吞并 sediment / kb-ingest-gate，三套 scope 不同（系统记忆 / 业务内容 / 治理），只编排不合并。

## 质量门禁（已启用）

- pre-commit 三段：语法检查 + 隐私驻点（硬编码绝对路径/内联密钥，占位符白名单）+ 冒烟测试。
- 6 套测试（timegrap / governance / mempipeline / bridge / semantic / panel）+ py_compile。
- 开源 SOP 基线：config.example.py 占位符，config.py 经`.gitignore`排除，零硬编码路径。

## 当前状态

- 版本 0.7.0；GitHub master 已同步 + Release 已建。
- 已闭环：P1（自动化审计）、P2（时间维度图谱）、P3（自我进化闭环 3/3：③ 时间图谱信号使入治理 fab1feb / ① golden 全层级回归基线 086086a / ② Act 人类触发建议面 18121cd）。
- 待办：验证信号（候选清单误报率 / 跨轮次脉络连续性）待跨轮次运行积累样本后定稿；无其他 TODO 挂点。

### P3.5 纯增量加固（2026-09-12，A+B 段全做）

| 编号 | 内容 | commit |
|------|------|--------|
| c0 | audit 桥/面板各自 `_NullAudit` 合并为 `audit.NullAudit` 单一实现 | `47b961c` |
| A1 | RRF 语义缓存：`qemb` 查询嵌入 + `qres` top-k，按索引 gen 失效 | `e25fa99` |
| A2 | Act 候选去留效用诊断 `advise_retention`（重要×时新×脉络活跃度，Advisory 只读） | `04e3eba` |
| A3 | TFIDF doc 表内容键 `ckey`(SHA-256) UNIQUE 去重（ASI06 防重复/污染） | `ffbce4b` |
| B4 | timegrap 时序边 `valid_from/valid_to` 编码内容生效/被取代窗口 | `b00a0a4` |
| B5 | panel 纯逻辑层拆 `panel_ops`，主模块 602 行瘦身 | `6718a70` |

七套测试全绿（act/bridge/governance/mempipeline/panel/semantic/timegrap）后 push master 至 `6718a70`。

### P3.6 检索时效与过时感知（C2/C3/C4，2026-09-12 全做）

把上一轮 ∮ 的"检索时效系"候选全部落地（本轮实现，提交待闭环）：
| 编号 | 内容 | 落点 |
|------|------|------|
| C2 | 时新度信号进 RRF 排序：`time_factor`（mtime 指数衰减 0..1）+ `hybrid_recall(time_weight=..., decay_half_life_days=...)`，默认 `time_weight=0` 保持现状 | `mempipeline/semantic.py` |
| C3 | FAMA 式过时复用检测：`build_stale_map`（timegrap valid_to 推导过时旧稿集）+ `check(stale_paths=)` 标 `stale_reuse` 抑制 `freshness`，缺省不计 stale 不改 greenline | `mempipeline/recall_golden.py` |
| C4 | Act 跨 query 缺词聚合建议面：`summarize_cards` 汇总三档杠杆命中数与高频缺词，只建议不落库 | `mempipeline/act.py` |

验收：`test_evolution.py` 覆盖三类（时新度单调性/排序、过时复用检测、缺词聚合），全套件回归绿。边界遵守：C2 默认关（time_weight=0）可逆；C3/C4 纯只读、Act 默认关铁律不变。

### P3.7 检索时效/遗忘质量（D1+D2，2026-09-12 落地推送，D2 已跨仓接线）

| 编号 | 内容 | 落点 |
|------|------|------|
| D1 | `time_factor` 多策略（exponential 现状 / linear 两倍半衰期归零），`hybrid_recall(decay_strategy=...)`，`time_weight=0` 默认现状。**不做 weibull**（SSGM 原文待核，避免宣称为其等价物） | `mempipeline/semantic.py` |
| D2 | `recall_golden.forget_quality()` 遗忘质量快照（totals/retrieval/composite 稳定 schema，纯只读零状态），落地 Forgetting-as-eval 时间序列，喂周检 | `mempipeline/recall_golden.py` |
| D2-线 | 脚本仓 `weekly_health.py` 新增 `forget_quality_signal()` 采样，写入 SIGNAL_LOG 落盘位 + 报告⑨/--signal-stats 呈现（真实镜像冒烟 composite=1.0 / stale_reuse=0 / 234 篇·67 过时） | `_agent运行台\脚本\weekly_health.py`（9025d74） |

验收：`test_evolution.py` D1/D2 段（linear 越半衰更强淘汰、半衰期等值、非法策略回落；forget_quality schema 与 stale_reuse_rate 升降）全过；全套件回归绿。mempipeline 仓 push master 至 `8f6635c`。

### P3.8 软失效状态显式化（D3，2026-09-12 落地推送 + 周检接线）

| 编号 | 内容 | 落点 |
|------|------|------|
| D3 | `_timeline_signal_candidates` 新增 `superseded` 候选：`valid_windows()` 中 valid_to 非空（已被同主题后续稿取代）的过时旧稿 → `suggestion=supersede`，只出候选、**不改写文件**（对齐 Mem0 ADD-only / Claude 审计可回滚零删除）；与 drift/revived 按路径去重 | `mempipeline/governance.py` |
| D3-线 | 周检 `stale_scan(timeline=)` 传入单次建图结果，D3 superseded 与 drift/revived 并入同一陈旧扫描候选，报告⑨可见 | `_agent运行台\脚本\weekly_health.py`（c5936c6） |

验收：`test_governance.py` D3 段（旧稿进 superseded、新稿不标、带 supersede 建议、不改状态）全过；全套件回归绿。mempipeline 仓提交 `535c65f`。

> **D3 复核修正（2026-09-12）**：复核首周基线发现 67 条 superseded 全部为**跨层伪报**——subject 簇按 project_id 聚簇，「项目约束」（长期）+「项目会话」（中期）跨层异构对被判为"旧稿被新稿取代"。根因：`valid_to` 在跨层判定(line 425)前无条件赋值。修复 `timegrap._derive_signals`：`valid_to` 仅在同层相邻对成立（与 content-relation 同层守卫同一语义），跨层对置 None。真实镜像 superseded 67→0。

### ∮ 下一阶段候选演进（对照 2025–26 前沿·最新复核，拟议待裁决）

来源（一手为主；2026-06 联网复核）：
- 存量已验证真实：A-MEM 双向回溯（NeurIPS'25 arXiv:2502.12110）；Episodic Memory 五性质（arXiv:2502.06975）；Mem0 ADD/UPDATE/DELETE/NOOP（ECAI'25 arXiv:2504.19413）；RMM Prospective/Retrospective（ACL'25 2025.acl-long.413）；MaRS 六遗忘策略+(ε,δ)-DP（arXiv:2512.12856）；SYNAPSE 三重混合检索+时间衰减（ACL'26 arXiv:2601.02744）。
- 2026 新增强势相关：Mem0 2026-04 改单次 ADD-only+分层检索，05 新增 **Temporal Reasoning + Memory Decay**（LoCoMo 92.5/LongMemEval 94.4，一手 mem0.ai）；Anthropic 2025-09 /tools/memory、2026-03 Chat Memory 全量开放、2026-04 **Managed Agents 版本化文件式记忆+审计日志+回滚+内容脱敏**（一手，与 mempipeline 审计架构同构）；评测系 **Memora-FAMA**（arXiv:2604.20006）、**ForgetEval**（arXiv:2606.15903）、**MemTier**（arXiv:2605.03675）；模型/自我进化系 ActiveMem（2606.10532）、AgeMem-RL（2601.01885）、MindMemOS（2608.12428）。
- 需标注的存疑/二手：Stability/Safety Governed Memory（arXiv:2603.11768）摘要未见 "Weibull" 字样，**Weibull 衰减形式原文待核**，本轮不据此设计；Claude Aug'25 记忆统一为二手。

**推荐 D 段（写作/观测侧，低风险纯增量，golden 兜底）**
- ~~**D1 时效衰减策略可选化**~~ `[x]`：`time_weight=0` 默认现状，golden 兜底；已落地 P3.7。
- ~~**D2 遗忘即评测（Forgetting-as-eval）**~~ `[x]`：forget-quality 时间序列已接入周检落盘位，悬空「验证信号」落地为量化指标；已落地 P3.7。
- **D3 软失效状态显式化** `[x]`：valid_to 取代的过时旧稿 → `superseded` 候选（只建议不改写），与 scan_stale_notes / P3-③ 同构；已落地 P3.8。

**回收长线（需裁决打破当前约束，不推荐本轮）**
- C1 Agentic 双向链接（写侧入库检索历史并回写）：写路径引入 agentic 循环，**违背「LLM 任务 stateless、无执行循环」硬约束**。
- MaRS 差分隐私遗忘（(ε,δ)-DP）：单人本地单用户场景隐私收益有限。
- MindMemOS 自我进化 schema / EDUD 事件级记忆：需 LLM 自我 schema 演化或大变构，冲击现有 project token 主题键，高风险。

**下一轮裁决范围**：D 段 D1/D2/D3 已全部落地（P3.7/P3.8）。候选余量回到架构侧长线（C1 写侧 agentic 双向链接 / MaRS 差分隐私 / MindMemOS 自进化 schema）——均需打破当前硬约束，**不建议本轮推进**；D 段价值已在观测序列落地，建议先随周检跑数周积累 forget-quality 样本再决断。

---
生成：ROADMAP v0.2（2026-09-12 联网复核并规划 D 段）。拟议阶段非既定计划。

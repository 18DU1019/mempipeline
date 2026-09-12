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

### ∮ 下一阶段候选演进（对照 2025–26 前沿，拟议待裁决）

来源（一手为主，标注）：A-MEM/Zettelkasten 双向回溯链接（NeurIPS'25 arXiv:2502.12110）；Episodic Memory（arXiv:2502.06975）；Mem0（arXiv:2504.19413）；RMM 反思式记忆管理 Prospective/Retrospective（ACL'25 2025.acl-long.413）；Memora 过时复用惩罚指标 FAMA + Forgetting 评测（arXiv:2604.20006）；MaRS 六遗忘策略与 (ε,δ)-DP（arXiv:2512.12856）；Stability/Safety Governed Memory 时序衰减 Weibull 与治理（arXiv:2603.11768）；SYNAPSE 三重混合检索+时间衰减（arXiv:2601.02744）。以上为 2025-12 至 2026-05 一手论文；检索效率/健康类（Mem0）为业界工程二手，标注推测属性。

**写作/检索侧候选（低风险纯增量，复用现有架构，golden 护栏兜底）**
- **C2 时间/脉络信号进 RRF 排序**：融合加时新度加权（复用 valid_from/to 时效窗）+ 项目脉络共识度，对齐 SYNAPSE 时间衰减。风险：可能拉低 recall，需 golden 回归红线。
- **C3 过时记忆复用检测（FAMA 式）**：把"复用 valid_to 已覆盖的旧稿"计为惩罚项接入 golden 回归，产出时序新鲜度指标；复用现有 timegrap valid_from/to + 观测样本盘。纯观测零状态。
- **C4 引用信号增强 Act 建议面**：把周检 golden 未命中 + 信号样本盘累积证据喂给 act 诊断（Retrospective 方向），仍只建议、**不自动改参**（遵守 Act 默认关铁律）。

**架构侧候选（需裁决打破当前约束，暂不推荐本轮）**
- C1 Agentic 双向链接（写侧入库检索历史并回写）：收益联想召回，代价是写路径引入 agentic 循环，**违背「LLM 任务 stateless business call、无执行循环」硬约束**。
- C6 事件级 EDU 记忆表示（EMem）：大变构，冲击现有 project token 主题键，高风险。
- C5 隐私/敏感分级 + 动态访问控制（MaRS DP / SSGM）：单用户本地优先场景价值有限。
- C7 遗忘策略形式化（MaRS 六策略）：可视化为策略选择，可与 C2/C3 合并。

**推荐组合（下一轮裁决范围）**：C2 + C3（检索时效 + 过时复用检测，纯增量）；C4 作为 Act 建议面增强；C1/C5/C6 归档长线。

---
生成：ROADMAP v0.1；2026-09-01。拟议阶段非既定计划。

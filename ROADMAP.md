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

### P3 自我进化闭环 `[ ]`（拟议，待裁决）
候选方向（需人工裁决后再推进）：
- golden set 扩充 + 召回回归门槛：GOLDEN 从 3 条扩为覆盖全层级的回归基线，纳入提交门禁。
- Act 仍人类触发：改 synonyms / 停用字 / 阈值，写回仍走 write_atomic + 审计；系统只显影「退化在哪、该看哪里」，不自动改参。
- 时间图谱信号使入治理：把 timegrap 的断更 / 结论漂移信号作为候选源，喂给 P1 候选清单，仍不自动改状态。
- 验证信号（草案，落地时定稿）：召回回归命中率、候选清单误报率、跨轮次脉络连续性。
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
- 已闭环：P1、P2；pre-commit 隐私驻点。
- 待办：P3 方向裁决 → 正式立项 → 每阶段闭环时更新本表。

---
生成：ROADMAP v0.1；2026-09-01。拟议阶段非既定计划。

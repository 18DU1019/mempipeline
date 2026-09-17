# mempipeline 架构正本

> 版本 v1.2（2026-09-17）· 补齐系统架构无单一权威文档的缺口。
> 定位：**治理优先的个人记忆资产管线**——1 写者 + N 投稿 + 无限读。
> 差异化：完整软状态转化的治理状态机 + 本地隐私优先。区别于社区项目（Mem0 / Basic Memory / Zep-Graphiti）：不打语义推理/生态牌，打**可审计、可回滚、本地闭环**。

## 1. 分层与数据流

```
写者(Writer) ──投稿──▶ WRITER_CONTRACTS.check_contract 校验
                            │ (allowed_tiers / project_scope / governable)
                            ▼
                  engine.write_atomic  幂等原子写 + 崩溃 .bak sidecar
                            │            └ audit(append-only trace + manifest 指纹)
                            ▼
                  治理状态机 governance  candidate→promoted 软流转（零删除，只建议）
                            │
                            ▼
                  timegrap 时间维度图谱  subject聚簇 / drift / revived / superseded
                            │            valid_from/valid_to 边
                            ▼
                  读侧召回：recall全扫基准 / TFIDFIndex倒排快路径 / semantic hybrid(RRF+time_weight)
                            │
                            ▼
                  recall_golden 兜底回归 + forget_quality 评测 → act(人类触发建议，只读)
```

关键不变量：
- **写端幂等**：同一稳定正文二次写入返回 `skipped`，不重复落盘。
- **治理只建议**：状态流转是软操作，候选/陈旧只出清单，永远不自动改状态、不删除。
- **Act 默认关**：系统从不自动改参；人类是最终 Check→Act。

## 2. 职责边界

| 模块 | 职责 | 不含 |
|---|---|---|
| protocol | Note 数据契约、TIER_DIR、Tier 口径 | 无业务判断 |
| engine | 幂等原子写 + sidecar 崩溃恢复 | 不改内容、不裁决 |
| audit | append-only 轨迹 + manifest 指纹 | 不判断对错，只登记 |
| writer_contracts | 写者约束注册表（纯数据）+ `check_contract` 只读校验 | 不存内容、不做运行时治理 |
| governance | 状态机、陈旧候选、superseded 合并 | 不写文件、不执行 Act |
| timegrap | 主题聚簇、演化信号、时间序回放 | 内容相似度（归 crossref） |
| recall / semantic / crossref | 检索与关联 | 不做治理决策 |
| recall_golden | golden 回归 + forget_quality + stale 标记 | 不自动修参 |
| act | 召回退化诊断建议面（synonym/stopword/threshold 三杠杆） | 不写库、不改参 |
| panel / panel_ops / bridge | 面板 HTTP 服务 + 异写者桥接 | 不触碰镜像只读侧（除 admin 提交） |

## 3. 硬约束（Non-Goals，铁律级）

1. **重引擎不入**：不引入 Lean / NautilusTrader / 消息总线 / Iceberg 等重型依赖，保持本地可跑。
2. **Act 默认关**：自动改参一律禁止，人工是最终 Check→Act。
3. **不合并治理流程本体**：不吞并 sediment / kb-ingest-gate，三套 scope 不同（系统记忆 / 业务内容 / 治理），只编排不合并。
4. **确定性裁决，LLM 不判新旧**：跨写者冲突用 `max(serial) / valid_from` 确定性比较，绝不让 LLM 判新旧（MemoryAgentBench 实证）。
5. **内容不可变、追溯可回滚**：对齐 Mem0 ADD-only / Claude 审计架构，零删除，只出 supersede 候选。

## 4. 写者接入协议（E/F 段，写者可转移）

写者的接入 = **改数据，不动 engine/governance**：
1. 在 `WRITER_CONTRACTS` 加一行：`writer_id → {allowed_tiers, project_scope, governable}`。
2. 写入口 frontmatter 模板产出对应 `writer_id`；字段口径经 `check_contract` 校验。
3. 约束归属自动生效：timegrap 主题键读 `project_id`（权威），治理候选按 `governable` 过滤，周检探针按 `writer_id` 报覆盖。
4. 转移 = 改登记表 + 停旧写者入口。

信任模型：`信任 = min(写者基线, 观测置信)`；写者基线来自注册表静态条目，观测置信来自 `confidence_*` 字段。**防内容侧投毒游戏化**（Nous 实证：自信措辞投毒可得 0.96 信任）。
> 诚实边界：`writer_id`、`confidence_*` 是自声明溯源，非防伪证明（Major Labs 六系统实测 provenance 普遍不可签名）。防伪依赖 git + audit_core；单人本地场景不上密码学签名（过度设计）。

## 5. 已知架构债（分级，供决策）

| 级 | 债 | 说明 |
|---|---|---|
| P1 | ~~无系统架构正本~~ | 本文件已立，历史缺口关闭 |
| P2 | ~~三路召回并存~~ **重命名（2026-09-14）** | 原表述"收敛为单一读侧门面"经联网论证判定为**逆共识**：2026 生产级 RAG 共识是多路并行 + RRF 融合（InfoQ/Azure 等四源一致），砍单路还会拆掉 lexical 无 Ollama 降级护栏。债重命名为「**读侧门面（RecallService）缺失**」——三入口（MemoryRecall 全扫 / TFIDFIndex 倒排 / semantic hybrid）是**编排面分散**，非策略冗余。**触发线**：出现第 4 条读侧路径或 B 轨空间核接入时开工门面编排（一个入口、内部多路，对齐 Qdrant hybrid 形态）；当前无新增读侧需求，缓行非搁置。裁决留档：`_agent运行台\输出\mempipeline-三候选线论证与联网查漏补缺-2026-09-14.md` |
| P2 | 上帝模块 **部分清偿（2026-09-14）** | ~~governance 混职责~~ → 已按职责拆包（`_state`/`_score`/`_scan`/`_health`，`__init__` re-export 外部 API 零变化，12 套测试全绿+覆盖率 85% 门禁兜底）。**拆分判据=职责耦合非行数**。timegrap(397) 键/图/信号共享数据结构、内聚度实测高于原评估，panel 已有 panel_ops 先例——剩余两模块不立项，功能扩张致耦合上升时按同款"零行为变化"口径再拆 |
| P2 | 多键协调 | subject_key（文件名正则）/ project_id（权威）/ writer_id（溯源）三键，机器人稿件须带稳定 project_id 否则主题簇碎裂 |
| P3 | ~~无覆盖率门禁~~ **已清偿（2026-09-14）** | 立 `coverage_gate.py`（逐自运行测试累积 coverage；pytest --cov 单独跑会低估至 40%，故不用之），TOTAL floor=80%、基线实测 85%，已接入 `.githooks/pre-commit` 替代原单文件冒烟。只准升不准降，改阈值须连带改 docstring 裁决日期 |

## 6. 具身机器人接入接缝（分域）

- **A 轨·文本记忆域（已落地 F1）**：`robot-vision` 写者条目入注册表（medium / family / governable=True），actor 作为一等写者投稿文本摘要，复用 T 轨全文核。
- **B 轨·空间几何域（独立立项）**：空间锚定记忆为独立子域，**存储/索引/治理状态机/重引擎全部隔离**，只共享投稿契约概念与 golden 回归方法论。双索引 = Open3D 空间索引 + HNSW 语义索引。
  - 防腐边界（v3 实验实证）：L2 符号层（楼层/房间标签）软权重防腐有效；L3 纯坐标层防腐靠**锚点校准**唯一成立，软权重/硬过滤均被漂移穿透。
  - **接缝待补**：文本核与空间核的"保存位置编排接口"在主仓尚无占位，B 轨落地时需先定义此协同接口再谈互通。

## 7. 接入现状（部署拓扑与真实调用方，2026-09-17 关账）

> 本节关闭 2026-08-28 记录的「文档 vs 盘上事实」口径落差：此前文档只描述能力清单，未登记任何真实调用方。以下为接入事实正本，后续新增调用方在此续行。

| 部署位 | 角色 | 形态 | 验收口径 |
|---|---|---|---|
| 公司机 A（正本所在） | 写端 + 日常召回主链路 | 脚本版（`recall.py` 三信号全量评分 + `audit_core`，运行台脚本私有），**未切换**到包内读侧 | 全量评分含规则层 boost / 弃权线 / JUDGE overlay，包内无等价物 |
| 公司机 | 手机端只读召回服务 | `recall_http.py`（`mempipeline.recall.TFIDFIndex` 快路径 + `protocol.TIER_DIR`），Tailscale 网段限定 | 2026-09-17 上线，282 篇，单查 ~130ms |
| 笔记本 B | **第一个真实调用方**（读端一期） | 本地免安装部署（py312 embeddable），MEM_ROOT 经 SMB 只读挂载正本，索引本地 SQLite | 2026-09-17 验收 PASS：282 篇入库，单查 0.14-0.17s；三查询 top3 排序与写端全扫**一致未决**——写端字面通道对该查询组精确平手、无排序判别力（2026-09-17 公司机复核），无法独立复现；判别力查询集已基线化，跨机 A/B 待跑 |
| 手机 | 终端读户 | Tailscale → 公司机 A recall_http，无本地部署 | 2026-09-17 入网，浏览器访问 |

边界登记：数据实时（SMB/本地均读正本），**代码为时点快照**（公司机仓库升级后需重打 zip 同步笔记本）。读端能力缺口：`collect_rules`（规则层扫描）与 boot/inject（冷启动注入）等价物未随包发布（脚本版私有），列为二期 A 项（§8 已立项）；混排评分正本 = 公司机脚本版 `recall.py` 字面通道常数，读端移植只取字面通道，语义层/版本链 boost/JUDGE 划为范围外。跨机排序 A/B 基线（2026-09-17 公司机侧）：`511880 数据错位` / `518880 数据错位` / `代理期 价格量级`——top1 一致为硬断言，top3 成员集为报告级（公司侧 #2/#3 存在同源孪生稿精确平手，tie-break 无判别意义）。

## 8. 二期立项（A/B/C/D 四项，2026-09-17 立项）

> 缘起：一期读端验收（§7 笔记本 B 行）后，二期五方向（E/A/B/C/D）经读端会话投稿清单与写端会话两轮裁决定案。E（口径收编）已由 §7 关账；早前「C/D/B 缓挂+触发条件」裁决于同日推翻，**A/B/C/D 四项全部正式立项**。

| 项 | 内容 | 执行侧 | 口径 / 红线 | 验收 |
|---|---|---|---|---|
| A 读端规则层 + 冷启动注入 | 读端新增 `collect_rules` 入口（扫规则层目录，经只读挂载可达）+ inject 等价物（长期锚点 + 近期预热，对齐三步法） | 笔记本 B | 混排评分正本 = 公司机 A 脚本版 `recall.py` 字面通道常数（`w_r=0.4 / λ=0.05 / w_i=0.4 / w_s=0.2`、`LAYER_IMP`、`RULE_REL_BOOST=2.5`、ABSTAIN 线）；语义层 / 版本链 boost / JUDGE overlay 划为**范围外**（读端无语义引擎，硬抄=假一致） | 同查询 top3 与公司机 A 一致性断言（判别力查询集按 §7 基线：top1 硬断言，top3 成员集报告级） |
| B 长期层 last_active 补齐 | 改 `distill_memory._distill_project_long` + `staging_ingest._as_note`，回填 52 篇存量 | 公司机 A | **回填类硬约束**：绝不能复用 `_write`（幂等剔除表判「内容未变」跳过，新字段永远补不上），须一次性直接编辑器回填；改模板与存量回填必须成对做 | rec 反映内容活跃度而非蒸馏时间；公司机两套读侧（distill_mem 已修 / 脚本版待同步）口径统一 |
| C 语义层 hybrid 召回 | 笔记本 B 装 Ollama + bge-m3，启用包内现成 `SemanticIndex` + RRF 融合（语义不可用自动回落 TF-IDF） | 笔记本 B | TF-IDF 已达标，属**质量增强非性能必需**；实施前置检查 = 低配内存余量确认（bge-m3 推理常驻） | 跨表述召回命中 + 无 Ollama 时回落护栏不破坏一期验收口径 |
| D 本地镜像同步 | 全量镜像单向同步到笔记本 B 本地（一期口径 282 篇入库），sweep/查询全走本地盘 | 笔记本 B | **红线：同步前先查 redaction 差异**（P3.12 时效保护——脱敏后未同步期间旧文仍可检索）；同步的是记忆数据非代码，「代码为时点快照」边界（§7）不因 D 改变 | sweep 4.5-8.7s 降至 <1s；公司机 A 关机时离线可读 |

排期备注：A 优先（读端自足性收益最大、不动公司机）；C/D 各带前置检查再实施；B 公司机侧自行排期。
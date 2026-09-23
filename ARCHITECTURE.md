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
> 投产现状（2026-09-22 决断点5 落账）：`confidence_*` 生产侧零写者产出（全库唯一产出者 robot_mock.py 为演示件），观测置信动态聚合**未接线**——当前链路实际执行 `信任 = min(基线, 自报 trust)`（cap_trust 封顶链已落地）。触发条件 = 第二个观测型写者真实接入生产且人工升档（改登记表 baseline_trust）不可行；届时落点已探明：trust_rank 读 confidence_* → timegrap/crossref 独立证据计数 → cap_trust 链（详见 ROADMAP P3.14）。
> 诚实边界：`writer_id`、`confidence_*` 是自声明溯源，非防伪证明（Major Labs 六系统实测 provenance 普遍不可签名）。防伪依赖 git + audit_core；单人本地场景不上密码学签名（过度设计）。

## 5. 已知架构债（分级，供决策）

| 级 | 债 | 说明 |
|---|---|---|
| P1 | ~~信任自声明越权~~ **已清偿（2026-09-17）** | 全链路质检发现：投稿自带 `trust:"trusted"` 经 normalize_trust 三档字面量直通，绕过 WRITER_CONTRACTS 登记表，与 trust_rank「登记表唯一权威源」口径矛盾。修复=`cap_trust`（min 语义）原语入 protocol，ingest 写侧 + trust_of_path 读侧双端封顶（自声明归一后 min 于写者基线；存量越权 frontmatter 读取侧即压回，无需重灌）；对齐 writer_contracts「信任 = min(基线, 观测置信)」既有模型，test_trust.py §6 六断言覆盖 |
| P1 | ~~无系统架构正本~~ | 本文件已立，历史缺口关闭 |
| P2 | ~~三路召回并存~~ **重命名（2026-09-14）** | 原表述"收敛为单一读侧门面"经联网论证判定为**逆共识**：2026 生产级 RAG 共识是多路并行 + RRF 融合（InfoQ/Azure 等四源一致），砍单路还会拆掉 lexical 无 Ollama 降级护栏。债重命名为「**读侧门面（RecallService）缺失**」——三入口（MemoryRecall 全扫 / TFIDFIndex 倒排 / semantic hybrid）是**编排面分散**，非策略冗余。**触发线**：出现第 4 条读侧路径或 B 轨空间核接入时开工门面编排（一个入口、内部多路，对齐 Qdrant hybrid 形态）；当前无新增读侧需求，缓行非搁置。裁决留档：`_agent运行台\输出\mempipeline-三候选线论证与联网查漏补缺-2026-09-14.md` |
| P2 | 上帝模块 **部分清偿（2026-09-14）** | ~~governance 混职责~~ → 已按职责拆包（`_state`/`_score`/`_scan`/`_health`，`__init__` re-export 外部 API 零变化，12 套测试全绿+覆盖率 85% 门禁兜底）。**拆分判据=职责耦合非行数**。timegrap(397) 键/图/信号共享数据结构、内聚度实测高于原评估，panel 已有 panel_ops 先例——剩余两模块不立项，功能扩张致耦合上升时按同款"零行为变化"口径再拆 |
| P2 | 多键协调 | subject_key（文件名正则）/ project_id（权威）/ writer_id（溯源）三键，机器人稿件须带稳定 project_id 否则主题簇碎裂 |
| P2 | ~~TFIDFIndex 无 vacuum~~ **已清偿（2026-09-17，二期 F 项）** | 质检发现：`build()` 仅剔除 redacted 路径，物理删除/外迁的镜像文件留幽灵索引路径，快路径召回返回已不存在文件。修复=build() 收集「父目录落在本轮扫描范围」的失存已知路径，与红act剔除同批 DELETE+一次 commit（原子），清单显式落 `self.last_vacuum` 供调用方打印（范围外路径保守不动防 mem_root 变更误清）；返回值仍为 int（新增索引数），6 处调用方零破坏。验收测试 test_mempipeline.test_tfidf_vacuum（删除场景 0 幽灵命中 + 清单正确 + 越界保守） |
| P2 | ~~静默异常吞噬面~~ **已清偿（2026-09-17，二期 G 项，销号表=§8.2）** | 质检登记时估 17 处，立项前 ruff 精确实盘 **36 处**（BLE001/S110/S112 去重）。收敛 4 处数据读路径（recall.py :171/:270 → read_errors/last_read_errors 计数、semantic.py :163 → last_read_errors、_health.py :37 → 报告 dict 增 read_errors key），计数不改行为（缺席语义不变）；保留 32 处（展示/解析/请求层兜底，逐条理由见 §8.2）。验收测试 test_mempipeline.test_g_error_visibility（坏编码条目缺席但计数显形，正常候选不受影响） |
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
| A 读端规则层 + 冷启动注入 **（代码已实施 2026-09-17，commit 见 git log；跨机 top1 一致性验收待实测）** | 读端新增 `collect_rules` 入口（扫规则层目录，经只读挂载可达）+ inject 等价物（长期锚点 + 近期预热，对齐三步法）。落地=mempipeline/inject.py：正本字面常数（W_R/LAMBDA/W_I/W_S、LAYER_IMP 三层、RULE_REL_BOOST=2.5、ABSTAIN 线、recency 封顶 0.2、activity_date 链 last_active>created>updated）+ `_fm_all` 全键解析（panel_ops 白名单解析缺活跃度链键，注入面自持）+ `score_mixed(layer=...)` 显式判层（rule_root 目录名数据无关化）+ `inject()` 主入口（top/anchor/warmup/abstain/read_errors 六面）。验收测试 test_inject_rules（正本常数手工复算 15 断言） | 笔记本 B | 混排评分正本 = 公司机 A 脚本版 `recall.py` 字面通道常数（`w_r=0.4 / λ=0.05 / w_i=0.4 / w_s=0.4`、`LAYER_IMP`、`RULE_REL_BOOST=2.5`、ABSTAIN 线）；语义层 / 版本链 boost / JUDGE overlay 划为**范围外**（读端无语义引擎，硬抄=假一致） | 同查询 top3 与公司机 A 一致性断言（判别力查询集按 §7 基线：top1 硬断言，top3 成员集报告级）——实测状态：§7 L96 记 2026-09-17 三查询 top3 与写端全扫「一致未决」（该查询组字面通道精确平手、无判别力），跨机 A/B 待家里机读端上线后跑；口径已定为 top1 硬断言（2026-09-24 裁决） |
| B 长期层 last_active 补齐 **（已实施 2026-09-17，脚本仓 git log 为准）** | 改 `distill_memory._distill_project_long` + `staging_ingest._as_note`，回填长期层存量缺字片段。实施终态：实测 **132/132** 全缺（立项 124/126 为时点数）；模板侧成对改动实为四处——`_distill_project_long`（签名收 rows，值=源项目最近活跃日期/无 topics 回落 pm mtime）+ `_distill_user_profile`（B 行字面外延，成对铁律推论：回填含画像 1 篇，模板不补则重写即抹）+ `_as_note`（long 层透传/created 兜底）+ `_stable` 剔除表增 `last_active`（防活跃度推进触发空转重写与 staging key 漂移；`_carry_fields` 刻意不续接——旧值优先会阻止活跃度前移）。回填=`backfill_long_last_active.py`（一次性直接编辑器，分写者族定值，unknown 不猜；留档可重跑） | 公司机 A | **回填类硬约束**：绝不能复用 `_write`（幂等剔除表判「内容未变」跳过，新字段永远补不上），须一次性直接编辑器回填；改模板与存量回填必须成对做。读侧兼容性已核：脚本版 recall 对缺 `last_active` 有 activity_date 链补偿，补齐后自动走新字段，不冲突 | rec 反映内容活跃度而非蒸馏时间。验收实测：回填 132（约束 68/staging 63/画像 1）、幂等重跑 0 回填/132 跳过、content key 不漂移（真实文件断言）、distill 端到端 写2/跳173（跳过面=回填未引发空转重写；写入面=源真变的画像+0998ae，重写后 last_active 模板产出未抹） |
| C 语义层 hybrid 召回 **（前置确认完成 2026-09-17，硬件实测待笔记本上线）** | 笔记本 B 装 Ollama + bge-m3，启用包内现成 `SemanticIndex` + RRF 融合（语义不可用自动回落 TF-IDF）。前置确认结论：包内 `semantic.py` 三件现成零新增 Python 依赖（embed=urllib 直调 Ollama / rrf_fuse 免调参不依赖公司机语义标定带 / hybrid_recall L295 失败自动回落）；bge-m3 一手数据（ollama.com）=下载 1.2GB、常驻增量约 2-2.5GB 内存、磁盘约 2GB、一次性 build 282 篇 CPU 嵌入估算 5-15 分钟后增量；一期快照含旧版 semantic.py（缺 G 项修复）且无 inject.py，实施前重打 zip（§7 既有边界自然携带）；**判定规则=实测空闲内存 ≥3GB 开实施，不足降级（小模型/维持单路，本项非性能必需无损失）** | 笔记本 B | TF-IDF 已达标，属**质量增强非性能必需**；实施前置检查 = 低配内存余量确认（bge-m3 推理常驻） | 跨表述召回命中 + 无 Ollama 时回落护栏不破坏一期验收口径 |
| D 本地镜像同步 | 全量镜像单向同步到笔记本 B 本地（一期口径 282 篇入库），sweep/查询全走本地盘 | 笔记本 B | **红线：同步前先查 redaction 差异**（P3.12 时效保护——脱敏后未同步期间旧文仍可检索）；同步的是记忆数据非代码，「代码为时点快照」边界（§7）不因 D 改变 | sweep 4.5-8.7s 降至 <1s；公司机 A 关机时离线可读 |

排期备注：A 优先（读端自足性收益最大、不动公司机）；C/D 各带前置检查再实施；B 公司机侧自行排期。

### 8.1 增补立项（2026-09-17 晚，全链路质检后拍板）

> 缘起：全链路代码质检（12/12 测试套 + 85% 覆盖 + mypy/ruff/密钥复扫 + 链路读码）产出 P2 两项，经用户拍板纳入二期。编号 F/G 续接（E 已由 §7 口径收编消耗，不复用）；原 A/B/C/D 四项不受影响。

| 项 | 内容 | 执行侧 | 口径 / 红线 | 验收 |
|---|---|---|---|---|
| F 索引 vacuum | `TFIDFIndex.build()` 对 `known` 集合中已不存在于镜像的路径执行清除（对齐既有 redacted 剔除分支的形态），产出清除清单供调用方打印 | 公司机 A | 索引是可重算派生数据，但清除必须显式可见（清单返回/打印，不静默）；**建议排在 D 项实施前**——D 同步会放大幽灵路径影响面；同义归一快路径（`_norm` 非空时 build 跳过既有文档）语义不受影响 | 构造「索引后删文件再 build」场景：快路径 0 幽灵命中 + 清除清单正确；12 套测试全绿，coverage ≥ 80 |
| G 异常吞噬面收敛 | 逐处盘点包体 17 处 `except Exception`（recall/semantic/governance 扫描读文件、engine skip 判定、panel 请求层等），数据读路径失败改为计数/日志上报（如 ingest stats 增 `errors` 面、recall 失败计数），纯防御性兜底保留 | 公司机 A | **不改 RecallBackend 契约**（3a 约束继续有效）；只加可见性不加行为分支，禁止借机顺手重构；每处处置（收敛/保留）留销号说明 | 17 处逐条销号表入档；改动后 12 套测试全绿 + coverage 不降；无新增 ruff 告警 |

### 8.2 G 项销号表（2026-09-17 实盘 36 处；立项"17 处"为质检报告低估值，以本表为准）

| 文件 | 处数 | 处置 |
|---|---|---|
| recall.py | 3 | **:171 收敛**（MemoryRecall.read_errors 累计计数）、**:270 收敛**（TFIDFIndex.last_read_errors 每轮 build 重置）；:155 保留（索引异常回落全扫=设计降级路径，已有注释） |
| semantic.py | 5 | **:163 收敛**（SemanticIndex.last_read_errors）；:73/:256/:291/:313 保留（recency 中性值/缓存写失败不阻断/语义失败回落 lexical/trust 回 unknown——均为降级语义非丢失） |
| governance/_health.py | 1 | **:37 收敛**（健康报告 dict 增 `read_errors` key——报告面自身即可见性载体，新 key 向后兼容） |
| governance/_scan.py | 2 | :22/:76 保留（扫描采样容错；问题清单本身即报告面，动返回结构违反 re-export 零变化约束） |
| governance/_state.py | 3 | :128/:192/:217 保留（解析兜底/状态流转容错；异常即保守侧语义） |
| panel.py | 7 | :51/:60/:403/:412/:439 保留（config 回落/语义层可选依赖/检索降级/sidecar 失败不阻断响应/trust 回落）；:448/:465 保留且本身即错误响应（HTTP 400 返回） |
| panel_ops.py | 4 | :25/:75/:92 保留（展示兜底：FM 空/审计尾空/mtime 0）；:160 保留且本身即错误上报面（返回 error 字符串） |
| crossref.py | 3 | :35/:59 保留（派生计算兜底：norm 空/title 回落 stem）；:153 保留（反链回写跳过——低频且结果人工可见度高） |
| timegrap.py | 3 | :83/:252/:259 保留（展示层容错：title 回落文件名/mtime 回落 datetime.min/snippet 回空） |
| trust_rank.py | 1 | :33 保留（读失败回 (None,None,None)，下游语义=unknown 档，安全侧） |
| engine.py | 1 | :47 保留（skip 判定读失败=无法比对=重写，安全侧） |
| audit.py | 1 | :84 保留且自带可见性动作（manifest 损坏即留 `.corrupt-<ts>` 快照文件留痕） |
| access_log.py | 1 | :61 保留（曝光记录失败不阻断召回响应） |
| bridge.py | 1 | :53 保留（桥导出读失败兜底） |

合计 36 = 收敛 4 + 保留 32。

### 8.3 H 项立项（2026-09-18，AGI 记忆借鉴三候选之①落地）

> 缘起：AGI 记忆系统借鉴分析产出三候选，经用户排期拍板：①面板监控三块先做（编号 H 续接 §8.1）；②TF-IDF 同义词表待用户确认词表内容后另立项；③JUDGE 权重离线坐标搜索缓挂（判定数据积累满 3 次阈值前无 delta，recall_judge 观察期内不动权重）。

| 项 | 内容 | 执行侧 | 口径 / 红线 | 验收 |
|---|---|---|---|---|
| H 面板监控三块（召回质量/索引新鲜度/活跃度） | panel 新增只读端点：`/api/activity`（updated 月度分桶近 12 月 + 窗口外数 + latest，mtime 兜底）、`/api/exposure?days=`（AccessLog.distribution + 未曝光面对账）；索引新鲜度复用既有 `/api/index_status`（indexed vs mirror gap），不重复造。纯逻辑落 panel_ops（B5 分层），panel.py 只接线；hub :8791 加「记忆监控」展示卡消费三端点（展示层归 hub，对齐「运维监控已迁 :8791」既有决策） | 公司机 A | 只读观测，不写任何文件、不进评分链路（AMV-P1 口径延续：sidecar 先记录不消费）；exposure 未配置 sidecar 时降级 `enabled=false` 不报错；时间基准 naive 本地（与 access_log.record/now_iso 同口径，不引入 tz 分叉） | test_panel.test_activity_exposure（分桶窗口/latest 提取/未曝光对账/降级 8 断言）；test_panel 套全绿 |

### 8.4 AGI-② 同义词表落地（2026-09-18，AGI 记忆借鉴三候选之②销号）

> 词表内容经用户"八组照单全收"拍板，接线范围经用户"全部接线+门复跑"拍板。落地中发现并发会话正在同仓 rebase（600b232 已先行提交词表进包 + TFIDFIndex 双侧归一），本会话接线由 stash 恢复后完成。

| 维度 | 定案 |
|---|---|
| 词表 | `recall.py :: DEFAULT_SYNONYMS` 8 组：commit→提交、frontmatter→metadata、audit→审计、sandbox→沙箱、闭环→收尾/收口、记忆入库→沉淀、回撤→回吐、估值→低估值/低估。head=语料高频形式（df 实测），alts=纯查询词（df≈0） |
| 接线点 | panel.py hybrid 搜索 + `_tfidf()`；semantic.py `hybrid_recall` lexical 腿（调用方注入实例时以注入方配置为准）；weekly_health.py `golden_regress` 门禁与生产同表（防"生产归一、门禁不归一"口径漂移）；运行台 `脚本/recall.py` 查询侧接线为前置已入库 |
| 词表修正 | 删「元数据」alt（frontmatter 组）：GOLDEN 回归实证中文通用词强映射到特指 YAML 头引发词法漂移——"9月5日 知识库 大扫除 元数据 治理"锚点被 frontmatter 主题笔记挤出 Top-5（hit_rate 1.0→0.9167），用户拍板删 alt，门禁复跑回 1.0 |
| 延迟实测 | 294 篇真实镜像：同义归一开销≈0（794→799ms/查询）。快路径已恢复（2026-09-18 同日二次迭代）：recall.py 快路径开关改「同表放行」——索引 _norm 与实例 _norm 相等即走索引（TFIDFIndex 双侧归一与全扫同构、主词空间对等，异表回落全扫）；weekly_health.rebuild_tfidf 以 DEFAULT_SYNONYMS 建索引，生产 tfidf.db 已全量重建（294 篇 1.9s，备份 .bak-presyn-20260918），实测 133-152ms/查询（较全扫 ~800ms 约 5.5 倍提速） |
| 语义边界 | 整词归一：中文连写（如"提交的精确判据"）不触发；空表时行为与旧版完全一致（可逆）。排除组（语义冲突不合并）：快照↔备份 / IRR↔年化 / ETF↔指数基金 |
| 验收 | test_mempipeline/test_semantic/test_panel 三套件全绿；GOLDEN 门禁两口径（无同义/默认表）hit_rate 与 freshness 均 1.0、passed=true |
| ③ JUDGE | 状态不变：权重搜索缓挂（观察期内判定数据未满 3 次阈值，不动权重） |
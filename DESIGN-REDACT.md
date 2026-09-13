# 记忆脱敏 / 红act 设计备忘

> 版本 v1.3（2026-09-13，全量落码回归绿） · 上游：对照研究 P3.12 候选
> 状态：`implemented` —— 已落：软终态 + recall 两路检索切断 + timegrap 信号排除 + 软/硬脱敏 + 审计轨迹（commit 3ff5b76）＋ 展示/导出收敛（panel /api/browse 剔除 redacted）＋ 语义层剔除（SemanticIndex.build 从 emb 删 redacted 向量 + gen 换代令 qres 缓存失效）。
> 对齐语义：Claude Managed Agents 的 redact + 红act（官方一手）；与 mempipeline「零删除 + 本地闭环」硬约束兼容。

---
## 0. 目标（问题声明）

在保留「零删除 + append-only 审计」本体的前提下，提供一条**紧急撤离通道**：把库内某条含密钥/PII 的稿从「活跃可检索/可导出」态移除，使其不再被 recall 与导出救回。它对齐 Claude 的 redact 语义（有 git/审计保留谁何时脱敏了什么），但**不覆盖审计轨迹**——脱敏动作本身的历史仍全留存。

反面：**加这条通道的方向风险**是"红act 变相成了删除"。因此红act 是**软终态标记**，只有人工触发，系统从不自动判定谁该被脱敏。

---
## 1. 触发的核心矛盾（必须先摆平）

现状检索路径（真实代码，非推测）：
- `recall.py` 的 `MemoryRecall`：全量扫描 `*.md`，逐个读原文做 tfidf/semantic 打分，Top-K 返回。
- `recall.py` 的 `TFIDFIndex`：`build()` 时对每个文档原文建倒排索引，`recall()` 用它做快路径。

**推论**：只要文件还在库里、正文未变，脱敏标记若不进入检索过滤，任何一次 recall/tfidf 都能通过原文找回秘文。因此红act **必须同时切断两条泄漏孔**，缺一即失败：
1. 检索路径（recall 两路打分）
2. 展示/导出路径（panel/bridge 列表）

（审计侧不构成泄漏孔——audit 只存路径 + hash 指纹，不存正文，见 `mempipeline/audit.py` FileAudit。git 历史里若已 tracking 该正文则属 git 层泄漏，超出本规格范围，另记于「已知边界」。）

---
## 2. 设计决策

### 2.1 不新增状态体系：`redacted` = `rejected` 同级终态

- 落在 `governance.py:STATES` + `VALID_TRANSITIONS`。
- 语义：这是软状态机的**终态**，只从 `active / project / promoted / candidate` 单向迁入，迁出集合为空。
- 理由：不扩大状态机维度，复用既有「软标记、零删除」设计语言，治理报告 `by_status` 分布天然可见。

### 2.2 三段式动作顺序（防泄漏窗口）

顺序不可颠倒。**任何一步提前执行都会开窗**：

1. **检索切断（先于标记）**：recall 读盘打分前过滤掉 `status: redacted` 文档。全扫路 `continue`；倒排路在 `build()` 时跳过。
2. **迁移标记（随后）**：`transition(path, "redacted", audit=…)` 把 frontmatter `status` 改为 `redacted`——走既有 `engine.write_atomic` 幂等原子写 + `audit.trace` 转移轨迹（可回查"谁在何时脱敏了哪条"）。
3. **内容处置（可选、默认关）**：是否将正文替换为占位符。默认关——见 2.4。

> 若先改 frontmatter 再改检索过滤，存在一个窗口期：标记已变但索引未重建 → TFIDF 路径仍能命中即便 `*terminated*`？不——TFIDF 的 index 是 build 时快照，标记改动不影响已 build 的倒排；泄漏窗口特指**全扫路径**与**展示路径**。但为最小心智负担，统一按「先切断 → 再标记」排，杜绝任何竞态想象。

### 2.3 检索切断落点（最小侵入，两处）

- 全扫路径 `MemoryRecall.recall`：读 `txt` 后先判 frontmatter `status: redacted` → `continue`，不进候选、不打分、不进 Top-K。
- 倒排路径 `TFIDFIndex`：`build()` 时跳过 `status: redacted` 文档。**脱敏后需触发一次 `build()` 重建索引**，否则旧倒排仍残留该秘文——此步骤在规格里标为**不可省略**。

### 2.4 内容处置的取舍（与零删除/审计的张力）

- 审计是 append-only 且**只存路径 + hash、不存正文**（`FileAudit`）。
- 若把文件正文抹掉，原秘文将无法从库内取证——丢失本地审计价值。
- **因此默认：保留正文、只对检索与导出脱敏**（软脱敏，可逆、忠于审计）。
- 确需抹正文的"硬脱敏"：由人工显式自选，接受「失去本地取证」代价；此时仍走 write_atomic + audit 记新 hash，使审计可证明正文已变更。

### 2.5 展示/导出收敛

panel 与 bridge 对 `status: redacted` 一律不出现于任何列表/检索结果，对齐既有 `non_governable_writers` 的剔除模式（不新增机制，复用已有"按状态/属性过滤列表"路径）。

- **已落**：panel `_browse`（/api/browse）对 `status: redacted` 稿 `continue` 剔出列表；bridge `export_promoted` 仅导 `status=promoted`，redacted 天然不导出；panel `_tfidf` 检索走 `MemoryRecall`（recall 层已过滤）。
- **观测位刻意保留**：`collect_stats` 的 `by_status` 仍计入 redacted（治理报告需可见脱敏量，同 §4）。

---
## 3. 状态机边界

| from | to | 允许 |
|---|---|---|
| active / project / promoted / candidate | redacted | ✔（人工触发） |
| redacted | 任一套件 | ✘（终态） |

`VALID_TRANSITIONS` 扩展后仍满足：零删除、软标记、不扩维度、只建议 + 人类 Check→Act。

---
## 4. 观测位（不引入新度量）

周检 `governance_health` 的 `by_status` 分布天然含 `redacted` 计数；可加一条「最近一次红act 时间」作为治理撤离活动观测。不新增第二套指标。

---
## 5. 验收点（落地后自动化）

1. 红act 前：`recall` 全扫与 `tfidf` 两路均能命中该稿；红act（标记 + 重建索引）后两路均不再返回。
2. `transition(path, "redacted")` 成功且写入审计，可还原 `…→redacted` 轨迹，历史条目不丢。
3. 从 `redacted` 再迁出被 `VALID_TRANSITIONS` 合法拒绝。
4. `test_governance.py` / `test_recall` / 全套件回归绿。

---
## 6. 与硬约束的关系（为什么合规）

- **零删除**：红act 是软终态标记，不删除文件、正文默认保留。
- **本地闭环**：无新依赖、无远程调用，全部本地实现。
- **Act 默认关**：脱敏仅人类触发，系统无自动判定。
- **审计**：动作本身记轨迹，历史不丢。

---
## 7. 已裁决争议点（2026-09-13 落地规则）

### 7.1 redacted 是否退出 timegrap 聚簇与 superseded 候选源？

**已裁决：退出活跃信号源，保留谱系历史。**
- 已 redacted 的稿**不作**新的 `drift` / `revived` / subject 聚簇活跃成员，不再喂 superseded / drift 候选（对齐「脱敏 = 撤离活跃面」语义）。
- 在**历史脉络回放**中仍占位（`TopicTimeline.recall` 显示"这条曾是脉络节点、后被打码"），保留 `valid_from/valid_to` 谱系完整、可追溯。
- 落地：仅在 `timegrap._derive_signals` 的信号派生处**跳过 redacted 元素**，不动聚簇 / 边结构（改量最小，P3.8 已对跨层 valid_to 做过同级守卫，复用该语义）。

### 7.2 硬脱敏（抹正文）是否要？

**已裁决：要，但做成独立、显式、罕见的硬通道；默认值仍是软脱敏。**
- **软脱敏**（标记 + 检索/导出切断，正文保留）= 默认，满足绝大多数场景，忠于审计取证。
- **硬脱敏**（抹正文 + 重写）= 仅当软通道不够用（确需从本地抹掉正文）时触发；由人工**显式、逐条、二次确认**执行。
  - 触发即**先写前备份**进 git 历史外的归档区（防丢失底线），再抹正文。
  - 审计记**新 hash**（write_atomic + audit），使审计可证明正文已变更。
  - 与 §2.4 的"内容处置默认关"衔接：硬脱敏是该段唯一打开的显式路径。

---

## 7·B 遗留边界（非本规格范围，明示）

- **git 层泄漏**：若某条含秘文正文此前已被 git trailing，红act 只能脱离"检索/导出"态，无法清除 git 历史中的旧 version——真正的硬脱敏需在 git 层处理（如 `git gc`/filter 重写），超出本次规格。
- **语义缓存（qemb/qres，已落码）**：裁决后明示——`qemb` 存的是查询嵌入（键为查询 hash），不含文档路径，**本身不构成泄漏源**；真正的语义泄漏在 `emb`（redacted 文档向量残留）与 `qres`（结果快照含 redacted 路径）。落地：`SemanticIndex.build` 对 redacted 文档删除 `emb` 向量 + `_bump_gen()` 换代令 `qres` 缓存失效（对齐 recall.TFIDFIndex `drop_paths` 语义，重建索引不可省略）。

---
*代码为准（P3.12 全量落码）。落地以人工裁决并在 ROADMAP P3.12 更新为准。*
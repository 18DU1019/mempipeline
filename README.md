# mempipeline

![ci](https://github.com/18DU1019/mempipeline/actions/workflows/ci.yml/badge.svg)


**Shared-memory *write* pipeline for the 1-writer / N-contributor /
unlimited-reader model.** Atomic idempotent writes, a pluggable audit
backend, and a staging ingest gate — with zero private paths baked in.

`mempipeline` sits on the **writer side** of a layered Markdown memory
mirror. Readers (recall / injection) live in the sibling library
`distill-mem`.

## Model

| Role        | Capability                               | Channel              |
|-------------|------------------------------------------|----------------------|
| **Writer**  | the single one who distills into mirror  | engine                |
| **Contributor** | drops authored note to staging gate | ingest               |
| **Reader**  | reads mirror, never writes back          | any read-only API     |

All paths are injected by the caller; nothing is hard-coded.

## Install / import

```bash
pip install mempipeline
# or: clone and add the folder to sys.path, then `import mempipeline`
```

## Quick start

```python
from pathlib import Path
from mempipeline.audit import FileAudit
from mempipeline.engine import write_atomic
from mempipeline.protocol import Note

mem_root = Path("./example_data/mem")
audit = FileAudit(
    log_path=mem_root / ".." / "audit" / "log.md",
    manifest_path=mem_root / ".." / "audit" / "manifest.json",
    root_dir=mem_root,
)

note = Note(title="Rule", summary="demo", tier="medium", importance=0.6, body="body")
out = mem_root / "02-中期记忆" / "项目会话-规则-ab1234cd.md"
status, wrote = write_atomic(out, note.to_frontmatter() + "\n\n" + note.body + "\n", audit)
assert status == "wrote"      # first write
status, _ = write_atomic(out, note.to_frontmatter() + "\n\n" + note.body + "\n", audit)
assert status == "skipped"    # idempotent: same stable body, no rewrite
```

> Run the zero-dependency end-to-end suite locally: `python test_mempipeline.py`

### N contributors via staging

Drop authored Markdown (with `title` / `summary` / `memory_tier` /
`source_agent` frontmatter) into a staging dir, then:

```python
from mempipeline.ingest import ingest
ingest(
    staging_dir=Path("./example_data/staging"),
    mem_root=mem_root,
    tier_dirs={"long": "01-长期记忆", "medium": "02-中期记忆"},
    audit=audit,
    on_ingest=lambda out: print("git add --", out),
)
```

## Design pillars

- **Atomic + idempotent** — writes go through a temp file + `os.replace`;
  identical stable body (timestamps stripped) is skipped, never rewritten.
- **Audit is a pluggable backend** — default `FileAudit` (append-only log +
  external fingerprint manifest); swap in a DB/API backend behind the same
  `AuditBackend` interface.
- **Data-decoupled** — `protocol` owns the frontmatter contract; every path
  is injected. Nothing here knows your vault.

## Time-dimension graph (P2)

`mempipeline.timegrap` is a **read-only** scanner that clusters notes into
timelines and traces how a topic evolves over time.

- **Subject clustering** - `subject_key(title)` normalizes a title into a
  stable cluster key (strips whitespace / punctuation / stop chars), so the
  same topic keeps one theme even when titles differ in wording or
  punctuation. Summaries never participate in the key, so *drifted*
  notes (whose conclusion changed) still land in the same timeline and
  can be seen as drift.
- **Timeline playback** - each cluster yields a chronologically ascending
  `TopicTimeline`; `recall()` returns the paths in time order,
  `spans_days()` gives the span.
- **Evolution signals** - per node, derived read-only: `first` /
  `continued` / `revived` (gap >= `gap_days`, default 90) / `same_day`, plus
  a `relation` of `duplicate` / `drift` / `stable` from bigram-Jaccard
  summary similarity against the previous note (thresholds
  `dup_threshold` / `drift_threshold`, both configurable and recorded on
  each signal).
- **Time benchmark fallback** - `updated`/`created` frontmatter win; missing
  fields fall back to file mtime with an explicit `time_src` tag for
  auditability.
- **Project isolation** - pass `projects=[...]` to constrain the scan; no
  cross-project leakage.

Example:

```python
from mempipeline.timegrap import build_timeline
timelines = build_timeline(mem_root)          # {subject_key: TopicTimeline}
for sk, tl in timelines.items():
    print(sk, tl.spans_days(), [sig["flag"] for sig in tl.signals])
```

---

## 中文使用说明

### 定位与三种身份

`mempipeline` 是「1 写者 + N 投稿 + 无限读」模型的**写侧**管线。镜像是一个分层的 Markdown 目录，读者的召回/注入在兄弟库 `distill-mem`。

| 身份 | 能力 | 通道 | 落点 |
|---|---|---|---|
| **写者** | 唯一有资格直接蒸馏进镜像 | `engine.write_atomic` | 幂等原子写 |
| **投稿者** | 把带 frontmatter 的笔记丢到闸门 | `ingest.ingest` | staging 熔合 |
| **读者** | 只读镜像、永不回写 | `distill-mem` 的 inject/recall | 任意只读接口 |

所有路径由调用方注入，**零硬编码私有路径**。

### 快速接入（四步）

1. **配置路径**：复制 `config.example.py` 为 `config.py`，改 `MEM_ROOT` / `STAGING_DIR` / `STAGING_ROOT` / `AUDIT_LOG` / `MANIFEST` / `TIER_DIRS`。其中 `STAGING_ROOT` 为面板投稿位根，缺省回落 `MEMPIPELINE_STAGING` 环境变量或中性默认值；`config.py` 已被 `.gitignore` 排除，仅 `config.example.py` 样例入库。
2. **写者**（蒸馏进镜像）：
   ```python
   from pathlib import Path
   from mempipeline.audit import FileAudit
   from mempipeline.engine import write_atomic
   from mempipeline.protocol import Note

   mem = Path("./example_data/mem")
   audit = FileAudit(mem / ".." / "audit" / "log.md",
                     mem / ".." / "audit" / "manifest.json", mem)
   note = Note(title="规则", summary="单笔风险≤1ATR", tier="long",
               importance=0.9, body="正文。")
   out = mem / "01-长期记忆" / "项目会话-规则-ab12cd34.md"
   s, _ = write_atomic(out, note.to_frontmatter() + "\n\n" + note.body + "\n", audit)
   # s == "wrote"（首次）→ 再写同稳定正文 → s == "skipped"（幂等跳过）
   ```
3. **投稿者**（staging 熔合）：把带 `title`/`summary`/`memory_tier`/`source_agent` frontmatter 的裸 md 放入 staging 目录，再
   ```python
   from mempipeline.ingest import ingest
   ingest(staging, mem, {"long": "01-长期记忆", "medium": "02-中期记忆"},
          audit, on_ingest=lambda o: print("git add --", o))
   ```
4. **读者**：复用兄弟库 `distill-mem` 的 `recall`/`inject` 接口即可，见该库 README。

### DQ 转义契约（2026-08-27 起生效）

写侧序列化时，**frontmatter 的字符串字段一律输出 YAML 双引号标量（无条件引号）**：

```text
---
title: "2026"            # 纯数字字符串，仍读回 str，不会被解析成 int
summary: "a # b：含冒号"   # 含 # 的串，不会被当注释截断
source_agent: "writer"
---
importance: 0.9          # 数值字段保持裸值
```

两点原因：

1. **指示符歧义**：plain 标量内 `#`（行首或前有空白）会被 YAML 当注释起始，`title: a # b` 解析成 `a`，值被静默截断。
2. **schema 类型失真**：YAML 1.2 core schema 把未加引号的纯数字/布尔字符串解析成 int/bool——`title: 2026` 读回是 int 而非 str。

配套：`ingest._parse_fm` 解析投稿 frontmatter 时会**剥匹配的外层引号**，故投稿端写引号或不写引号都兼容。转义顺序为 `\\` → `\"` → `\n\r\t`，全部合法 Double-Quoted 序列。

### 安全与幂等契约（2026-09-01 起生效）

**`project_id` 安全边界**：落盘路径由 `safe_project_id` 校验，仅放行 `[\w.-]+`；含 `/`、`\`、`..` 或其它非法字符的 `project_id` 一律回落 `legacy`/`global`，防止路径穿越与绝对路径逃逸。投稿端若依赖带子目录的 `project_id`，须改用合法字符。

**`content_key` 幂等判据**：稳定正文 SHA-256 截断由 8 位提高至 12 位 hex，降低碰撞概率。迁移影响：升级前已用 8 位 key 落库的笔记，重写同一正文时因 key 变化会被判为新笔记而重复写入；如有需要，可对既有笔记按稳定正文重建 key 一次，或接受单次重复后由人工清理。

## License

Apache-2.0. See [LICENSE](LICENSE).

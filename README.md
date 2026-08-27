# mempipeline

**Shared-memory *write* pipeline for the 1-writer / N-contributor /
unlimited-reader model.** Atomic idempotent writes, a pluggable audit
backend, and a staging ingest gate — with zero private paths baked in.

`mempipeline` sits on the **writer side** of a layered Markdown memory
mirror. Readers (recall / injection) live in the sibling library
[`distill-mem`](https://github.com/18DU1019/distill-mem).

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

---

## 中文使用说明

### 定位与三种身份

`mempipeline` 是「1 写者 + N 投稿 + 无限读」模型的**写侧**管线。镜像是一个分层的 Markdown 目录，读者的召回/注入在兄弟库 [`distill-mem`](https://github.com/18DU1019/distill-mem)。

| 身份 | 能力 | 通道 | 落点 |
|---|---|---|---|
| **写者** | 唯一有资格直接蒸馏进镜像 | `engine.write_atomic` | 幂等原子写 |
| **投稿者** | 把带 frontmatter 的笔记丢到闸门 | `ingest.ingest` | staging 熔合 |
| **读者** | 只读镜像、永不回写 | `distill-mem` 的 inject/recall | 任意只读接口 |

所有路径由调用方注入，**零硬编码私有路径**。

### 快速接入（四步）

1. **配置路径**：复制 `config.example.py` 为 `config.py`，改 `MEM_ROOT` / `STAGING_DIR` / `AUDIT_LOG` / `MANIFEST` / `TIER_DIRS`。
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

## License

Apache-2.0. See [LICENSE](LICENSE).

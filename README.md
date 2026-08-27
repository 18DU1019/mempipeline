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

## License

Apache-2.0. See [LICENSE](LICENSE).

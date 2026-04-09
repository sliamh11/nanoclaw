---
governs:
  - evolution/
  - eval/
---
# Pattern: eval-change

## ADR gate (mandatory)

Before any change to `evolution/`, read `docs/decisions/INDEX.md`. Three decisions have non-obvious permanent constraints:

| ADR | Ruling |
|-----|--------|
| `eval-ipc-file-output.md` | Results via shared-volume files, **not stdout** — Docker pipe buffering is permanent. Do not revert. |
| `eval-no-disk-cache.md` | In-memory cache only — disk cache silently masks regressions across builds. |
| `eval-selective-warmup.md` | Warm only active test datasets — saves ~3× time, avoids API rate saturation. |

## Storage migrations

Use the existing `try/except ALTER TABLE` pattern in `evolution/storage/providers/sqlite.py` lines 215–244. Never add columns in the `CREATE TABLE` block — it only runs once.

```python
for col, coltype in [("new_col", "TEXT")]:
    try:
        db.execute(f"ALTER TABLE interactions ADD COLUMN {col} {coltype}")
    except sqlite3.OperationalError:
        pass  # Column already exists
```

## Provider pattern

Adding a new backend = one file + one registration line. Abstract contract in `evolution/storage/provider.py`. Concrete impl in `evolution/storage/providers/`.

## Tests

Python tests in `scripts/tests/`. Run `python3 -m pytest scripts/tests/` before committing.

"""
Tests for scripts/memory_indexer.py — parsing, chunking, DB helpers.

memory_indexer.py imports google.genai at module level.
We stub those imports before loading the module.
"""
import importlib
import os
import sys
import types
from pathlib import Path

import pytest

# Ensure project root is importable
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)
_SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent)
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _install_google_genai_stub():
    """Install a minimal stub for google.genai so memory_indexer can import."""
    if "google" not in sys.modules:
        google_mod = types.ModuleType("google")
        sys.modules["google"] = google_mod
    else:
        google_mod = sys.modules["google"]

    if not hasattr(google_mod, "genai"):
        genai_mod = types.ModuleType("google.genai")

        class _FakeClient:
            def __init__(self, **kwargs):
                pass

        genai_mod.Client = _FakeClient
        setattr(google_mod, "genai", genai_mod)
        sys.modules["google.genai"] = genai_mod

    if "google.genai.types" not in sys.modules:
        types_mod = types.ModuleType("google.genai.types")
        types_mod.EmbedContentConfig = object
        sys.modules["google.genai.types"] = types_mod

    genai_types_attr = sys.modules.get("google.genai")
    if genai_types_attr and not hasattr(genai_types_attr, "types"):
        setattr(genai_types_attr, "types", sys.modules["google.genai.types"])


_install_google_genai_stub()


@pytest.fixture(autouse=True)
def fresh_vault(tmp_path, monkeypatch):
    """Point DEUS_VAULT_PATH to a temp vault and ensure memory_indexer is clean."""
    vault = tmp_path / "vault"
    (vault / "Session-Logs").mkdir(parents=True)
    (vault / "Atoms").mkdir()
    monkeypatch.setenv("DEUS_VAULT_PATH", str(vault))
    # Always reload to pick up env change
    if "memory_indexer" in sys.modules:
        del sys.modules["memory_indexer"]
    yield vault


@pytest.fixture
def mi(tmp_path, fresh_vault, monkeypatch):
    """Load memory_indexer with a temp DB."""
    mod = importlib.import_module("memory_indexer")
    test_db = tmp_path / "memory.db"
    monkeypatch.setattr(mod, "DB_PATH", test_db)
    return mod


# ── extract_frontmatter ───────────────────────────────────────────────────


def test_extract_frontmatter_empty_for_no_frontmatter(mi):
    result = mi.extract_frontmatter("# Just a header\nsome content")
    assert result == {}


def test_extract_frontmatter_extracts_date(mi):
    content = "---\ndate: 2024-06-15\ntldr: short summary\n---\nbody"
    result = mi.extract_frontmatter(content)
    assert result.get("date") == "2024-06-15"


def test_extract_frontmatter_extracts_tldr(mi):
    content = "---\ndate: 2024-06-15\ntldr: short summary\n---\nbody"
    result = mi.extract_frontmatter(content)
    assert result.get("tldr") == "short summary"


def test_extract_frontmatter_extracts_topics(mi):
    content = "---\ndate: 2024-06-15\ntopics: [math, physics]\n---\nbody"
    result = mi.extract_frontmatter(content)
    assert result.get("topics") == "math, physics"


def test_extract_frontmatter_extracts_decisions_list(mi):
    content = "---\ndate: 2024\ndecisions:\n  - Use vitest\n  - Mock all I/O\n---\nbody"
    result = mi.extract_frontmatter(content)
    assert "Use vitest" in result.get("decisions", "")
    assert "Mock all I/O" in result.get("decisions", "")


def test_extract_frontmatter_raw_contains_block(mi):
    content = "---\ndate: 2024-06-15\n---\nbody"
    result = mi.extract_frontmatter(content)
    assert "raw" in result
    assert "date: 2024-06-15" in result["raw"]


# ── extract_decisions_section ─────────────────────────────────────────────


def test_extract_decisions_section_empty_when_missing(mi):
    result = mi.extract_decisions_section("# Summary\nsome text")
    assert result == ""


def test_extract_decisions_section_extracts_body(mi):
    content = (
        "# Title\n## Decisions Made\nWe chose approach A.\n"
        "Because it was simpler.\n## Next Steps\nMore stuff"
    )
    result = mi.extract_decisions_section(content)
    assert "approach A" in result
    assert "More stuff" not in result


# ── chunks_for_log ────────────────────────────────────────────────────────


def test_chunks_for_log_empty_for_no_frontmatter(mi, tmp_path):
    p = tmp_path / "session.md"
    p.write_text("No frontmatter here")
    chunks = mi.chunks_for_log(p, p.read_text())
    assert chunks == []


def test_chunks_for_log_returns_frontmatter_chunk(mi, tmp_path):
    p = tmp_path / "session.md"
    p.write_text(
        "---\ndate: 2024-06-15\ntldr: a good session summary\ntopics: [engineering]\n---\n## Summary\nContent here"
    )
    chunks = mi.chunks_for_log(p, p.read_text())
    assert any(c["type"] == "frontmatter" for c in chunks)


def test_chunks_for_log_returns_decisions_chunk_when_present(mi, tmp_path):
    p = tmp_path / "session.md"
    p.write_text(
        "---\ndate: 2024-06-15\ntldr: summary\n---\n"
        "## Decisions Made\nWe chose to use sqlite-vec for vector storage.\n"
        "## Summary\nSome more"
    )
    chunks = mi.chunks_for_log(p, p.read_text())
    types = [c["type"] for c in chunks]
    assert "decisions" in types


# ── open_db + DB helpers ──────────────────────────────────────────────────


def test_open_db_creates_entries_table(mi):
    db = mi.open_db()
    tables = {
        r[0]
        for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    db.close()
    assert "entries" in tables


def test_entry_exists_false_for_new_db(mi):
    db = mi.open_db()
    result = mi.entry_exists(db, "/nonexistent/path.md")
    db.close()
    assert result is False


def test_delete_entries_removes_rows(mi):
    db = mi.open_db()
    db.execute(
        "INSERT INTO entries (path, date, chunk, type) VALUES (?, ?, ?, ?)",
        ["/test/path.md", "2024-01-01", "some chunk text", "frontmatter"],
    )
    db.commit()
    assert mi.entry_exists(db, "/test/path.md")
    mi.delete_entries(db, "/test/path.md")
    assert not mi.entry_exists(db, "/test/path.md")
    db.close()


# ── cmd_recent ───────────────────────────────────────────────────────────


def _create_session(vault, date_folder: str, name: str, tldr: str, mtime_offset: float = 0):
    """Helper to create a session log file with controlled mtime."""
    day_dir = vault / "Session-Logs" / date_folder
    day_dir.mkdir(parents=True, exist_ok=True)
    p = day_dir / f"{name}.md"
    p.write_text(
        f"---\ntype: session\ndate: {date_folder}\ntldr: {tldr}\n---\n## Summary\n"
    )
    # Set mtime: base + offset (higher offset = newer)
    import time
    base_mtime = time.time() - 10000 + mtime_offset
    os.utime(p, (base_mtime, base_mtime))
    return p


def test_cmd_recent_mtime_tiebreaker(mi, fresh_vault, capsys):
    """Sessions on the same day should be ordered by mtime, newest first."""
    _create_session(fresh_vault, "2024-06-15", "session-a", "first session", mtime_offset=100)
    _create_session(fresh_vault, "2024-06-15", "session-b", "second session", mtime_offset=200)
    _create_session(fresh_vault, "2024-06-15", "session-c", "third session", mtime_offset=300)

    mi.cmd_recent(2)
    output = capsys.readouterr().out
    lines = [l for l in output.strip().split("\n") if l.startswith("- [")]

    assert len(lines) == 2
    # session-c (newest mtime) should be first, then session-b
    assert "session c" in lines[0]
    assert "session b" in lines[1]


def test_cmd_recent_days_returns_all_from_day(mi, fresh_vault, capsys):
    """--recent-days N should return ALL sessions from the last N calendar days."""
    # 3 sessions on the same day
    _create_session(fresh_vault, "2024-06-15", "session-a", "a", mtime_offset=100)
    _create_session(fresh_vault, "2024-06-15", "session-b", "b", mtime_offset=200)
    _create_session(fresh_vault, "2024-06-15", "session-c", "c", mtime_offset=300)
    # 1 session on a different day
    _create_session(fresh_vault, "2024-06-14", "session-old", "old", mtime_offset=50)

    mi.cmd_recent(1, days=True)
    output = capsys.readouterr().out
    lines = [l for l in output.strip().split("\n") if l.startswith("- [")]

    # Should return all 3 from 2024-06-15, NOT the one from 2024-06-14
    assert len(lines) == 3
    assert "session old" not in output


def test_cmd_recent_days_spans_multiple_days(mi, fresh_vault, capsys):
    """--recent-days 2 should return sessions from 2 distinct calendar days."""
    _create_session(fresh_vault, "2024-06-15", "today-a", "a", mtime_offset=300)
    _create_session(fresh_vault, "2024-06-15", "today-b", "b", mtime_offset=200)
    _create_session(fresh_vault, "2024-06-14", "yesterday", "y", mtime_offset=100)
    _create_session(fresh_vault, "2024-06-13", "ancient-session", "o", mtime_offset=50)

    mi.cmd_recent(2, days=True)
    output = capsys.readouterr().out
    lines = [l for l in output.strip().split("\n") if l.startswith("- [")]

    # 2 from Jun 15 + 1 from Jun 14 = 3
    assert len(lines) == 3
    assert "ancient-session" not in output


def test_cmd_recent_days_mtime_order_within_day(mi, fresh_vault, capsys):
    """Within a day, sessions should be ordered newest-first by mtime."""
    _create_session(fresh_vault, "2024-06-15", "early", "e", mtime_offset=100)
    _create_session(fresh_vault, "2024-06-15", "middle", "m", mtime_offset=200)
    _create_session(fresh_vault, "2024-06-15", "late", "l", mtime_offset=300)

    mi.cmd_recent(1, days=True)
    output = capsys.readouterr().out
    lines = [l for l in output.strip().split("\n") if l.startswith("- [")]

    assert len(lines) == 3
    assert "late" in lines[0]
    assert "middle" in lines[1]
    assert "early" in lines[2]

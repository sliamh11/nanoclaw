"""Unit tests for scripts/memory_tree_verifier.py (Phase 8).

All tests use an injected transport stub — no Ollama needed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


_ROOT = Path(__file__).resolve().parent.parent.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


v = _load("memory_tree_verifier", _ROOT / "scripts" / "memory_tree_verifier.py")


def _stub(response_text: str):
    """Transport that returns a fixed response regardless of payload."""
    def _t(url, payload, timeout):
        return response_text
    return _t


class TestVerifyCandidates:
    def test_empty_candidates_returns_empty(self):
        out = v.verify_candidates("q", [], transport=_stub(""))
        assert out == []

    def test_parses_three_labels(self):
        cands = [
            {"path": "a.md", "text": "About A"},
            {"path": "b.md", "text": "About B"},
            {"path": "c.md", "text": "About C"},
        ]
        response = (
            "a.md|yes|directly answers\n"
            "b.md|partial|related topic\n"
            "c.md|no|off-topic\n"
        )
        out = v.verify_candidates("q", cands, transport=_stub(response))
        labels = {e["path"]: e["label"] for e in out}
        assert labels == {"a.md": "yes", "b.md": "partial", "c.md": "no"}

    def test_unknown_label_becomes_unknown(self):
        cands = [{"path": "a.md", "text": "A"}]
        out = v.verify_candidates("q", cands, transport=_stub("a.md|maybe|unsure"))
        assert out[0]["label"] == "unknown"

    def test_missing_candidate_becomes_unknown(self):
        cands = [{"path": "a.md", "text": "A"}, {"path": "b.md", "text": "B"}]
        # Only a.md labeled; b.md omitted.
        out = v.verify_candidates("q", cands, transport=_stub("a.md|yes|x"))
        by_path = {e["path"]: e["label"] for e in out}
        assert by_path["a.md"] == "yes"
        assert by_path["b.md"] == "unknown"

    def test_hallucinated_path_ignored(self):
        cands = [{"path": "a.md", "text": "A"}]
        response = (
            "a.md|yes|real\n"
            "ghost.md|yes|verifier invented this\n"
        )
        out = v.verify_candidates("q", cands, transport=_stub(response))
        assert len(out) == 1
        assert out[0]["path"] == "a.md"

    def test_label_case_insensitive(self):
        cands = [{"path": "a.md", "text": "A"}]
        out = v.verify_candidates("q", cands, transport=_stub("a.md|YES|x"))
        assert out[0]["label"] == "yes"

    def test_preserves_input_keys(self):
        cands = [{"path": "a.md", "text": "A", "score": 0.7, "id": "xyz"}]
        out = v.verify_candidates("q", cands, transport=_stub("a.md|yes|x"))
        assert out[0]["score"] == 0.7
        assert out[0]["id"] == "xyz"
        assert out[0]["label"] == "yes"

    def test_transport_raises_unreachable_propagates(self):
        def raising(url, payload, timeout):
            raise v.VerifierUnreachable("ollama offline")
        with pytest.raises(v.VerifierUnreachable):
            v.verify_candidates("q", [{"path": "a.md", "text": "A"}], transport=raising)

    def test_transport_arbitrary_exception_wrapped(self):
        def boom(url, payload, timeout):
            raise RuntimeError("oops")
        with pytest.raises(v.VerifierUnreachable):
            v.verify_candidates("q", [{"path": "a.md", "text": "A"}], transport=boom)


class TestFormatCandidates:
    def test_truncates_long_text(self):
        long = "x" * (v.MAX_TEXT_CHARS_PER_CANDIDATE + 100)
        out = v._format_candidates([{"path": "a.md", "text": long}])
        assert "…" in out
        assert len(out) < len(long) + 100  # truncation happened

    def test_flattens_newlines(self):
        out = v._format_candidates([{"path": "a.md", "text": "line1\nline2"}])
        assert "line1 line2" in out

    def test_missing_text_renders_empty(self):
        out = v._format_candidates([{"path": "a.md"}])
        assert "a.md" in out


class TestParseResponse:
    def test_basic_three_lines(self):
        text = (
            "a.md|yes|reason1\n"
            "b.md|no|reason2\n"
        )
        out = v._parse_response(text, ["a.md", "b.md"])
        assert out["a.md"]["label"] == "yes"
        assert out["b.md"]["label"] == "no"

    def test_ignores_preamble(self):
        text = (
            "Sure, here are the labels:\n"
            "\n"
            "a.md|yes|relevant\n"
            "Let me know if you need more.\n"
        )
        out = v._parse_response(text, ["a.md"])
        assert out["a.md"]["label"] == "yes"

    def test_ignores_malformed_lines(self):
        text = "a.md|yes|ok\nrandom line without pipes\nb.md||missing label\n"
        out = v._parse_response(text, ["a.md", "b.md"])
        assert "a.md" in out
        # b.md line has empty label → regex still matches but label isn't in valid set
        # actually `b.md||missing label` has no word for label → regex won't match
        assert "b.md" not in out

    def test_reason_truncated_to_200(self):
        long = "r" * 500
        text = f"a.md|yes|{long}\n"
        out = v._parse_response(text, ["a.md"])
        assert len(out["a.md"]["reason"]) == 200


class TestRerankByVerifier:
    def test_drops_no_labels(self):
        ranked = [
            ("id1", "a.md", "A", 0.8, "flat"),
            ("id2", "b.md", "B", 0.6, "flat"),
            ("id3", "c.md", "C", 0.5, "flat"),
        ]
        labeled = [
            {"path": "a.md", "label": "yes", "reason": ""},
            {"path": "b.md", "label": "no", "reason": ""},
            {"path": "c.md", "label": "partial", "reason": ""},
        ]
        kept, dropped = v.rerank_by_verifier(ranked, labeled)
        kept_paths = [r[1] for r in kept]
        assert kept_paths == ["a.md", "c.md"]
        assert dropped == ["b.md"]

    def test_unknown_labels_kept(self):
        ranked = [("id1", "a.md", "A", 0.8, "flat")]
        labeled = [{"path": "a.md", "label": "unknown", "reason": ""}]
        kept, dropped = v.rerank_by_verifier(ranked, labeled)
        assert len(kept) == 1
        assert dropped == []

    def test_missing_label_kept_as_unknown(self):
        ranked = [("id1", "a.md", "A", 0.8, "flat")]
        kept, dropped = v.rerank_by_verifier(ranked, [])
        assert len(kept) == 1
        assert dropped == []

    def test_all_no_returns_empty(self):
        ranked = [
            ("id1", "a.md", "A", 0.8, "flat"),
            ("id2", "b.md", "B", 0.6, "flat"),
        ]
        labeled = [
            {"path": "a.md", "label": "no", "reason": ""},
            {"path": "b.md", "label": "no", "reason": ""},
        ]
        kept, dropped = v.rerank_by_verifier(ranked, labeled)
        assert kept == []
        assert len(dropped) == 2


# ── TestRetrieveWithVerifier — end-to-end wiring via stub transport ───────────

mt = _load("memory_tree", _ROOT / "scripts" / "memory_tree.py")


@pytest.fixture
def tmp_db(tmp_path):
    db = mt.open_db(tmp_path / "tree.db")
    yield db
    db.close()


@pytest.fixture
def populated_db(tmp_db, tmp_path, monkeypatch):
    """Vault with 2 distinguishable nodes so the verifier can label them."""
    def stub_embed(text):
        vec = [0.0] * mt.EMBED_DIM
        for i, c in enumerate(text[:mt.EMBED_DIM]):
            vec[i] = (ord(c) % 17) / 17.0
        return vec
    monkeypatch.setattr(mt, "embed_text", stub_embed)
    vault = tmp_path / "vault"
    (vault / "Persona" / "life").mkdir(parents=True)
    (vault / "MEMORY_TREE.md").write_text(
        "---\nid: root0000000000000000000000000001\ntitle: Root\n"
        "description: Root.\nlevel: 0\nchildren:\n  - Persona/life/bg.md\n"
        "  - Persona/life/household.md\n---\n"
    )
    (vault / "Persona" / "life" / "bg.md").write_text(
        "---\nid: bg000000000000000000000000000002\ntitle: Background\n"
        "description: AWS work history dates and roles.\nlevel: 2\n---\n"
    )
    (vault / "Persona" / "life" / "household.md").write_text(
        "---\nid: hh000000000000000000000000000003\ntitle: Household\n"
        "description: Lives with Shani; Eden moves in Aug 2026.\nlevel: 2\n---\n"
    )
    mt.build_tree(vault, tmp_db)
    return tmp_db


class TestRetrieveWithVerifier:
    def test_off_by_default_keeps_ranking(self, populated_db):
        # Use low thresholds so we always get results for comparison.
        baseline = mt.retrieve(
            populated_db, "AWS work", k=5,
            low_threshold=0.0, abstain_threshold=0.0,
        )
        assert baseline["results"], "baseline should return something"
        assert not any("verifier" in t for t in baseline["trace"])

    def test_verifier_drops_no_labelled(self, populated_db):
        def transport(url, payload, timeout):
            # Label bg.md as 'no' so it should be dropped.
            lines = []
            for p in ["Persona/life/bg.md", "Persona/life/household.md", "MEMORY_TREE.md"]:
                label = "no" if p == "Persona/life/bg.md" else "yes"
                lines.append(f"{p}|{label}|stub")
            return "\n".join(lines)

        result = mt.retrieve(
            populated_db, "AWS work", k=5,
            low_threshold=0.0, abstain_threshold=0.0,
            use_verifier=True,
            verifier_transport=transport,
        )
        paths = [r["path"] for r in result["results"]]
        assert "Persona/life/bg.md" not in paths
        assert any("verifier_dropped" in t for t in result["trace"])

    def test_verifier_unreachable_fails_open(self, populated_db):
        def transport(url, payload, timeout):
            raise v.VerifierUnreachable("offline")
        result = mt.retrieve(
            populated_db, "AWS work", k=5,
            low_threshold=0.0, abstain_threshold=0.0,
            use_verifier=True,
            verifier_transport=transport,
        )
        # Fail-open: result still returned, trace records unreachable.
        assert result["results"]
        assert any("verifier_unreachable" in t for t in result["trace"])

    def test_verifier_all_no_triggers_abstain(self, populated_db):
        def transport(url, payload, timeout):
            # Label every candidate 'no' → top empty → abstain fires.
            lines = []
            for p in ["MEMORY_TREE.md", "Persona/life/bg.md", "Persona/life/household.md"]:
                lines.append(f"{p}|no|stub")
            return "\n".join(lines)
        result = mt.retrieve(
            populated_db, "anything", k=5,
            low_threshold=0.0, abstain_threshold=0.1,
            use_verifier=True,
            verifier_transport=transport,
        )
        assert result["fell_back"] is True
        assert result["results"] == []

    def test_verifier_partial_kept(self, populated_db):
        def transport(url, payload, timeout):
            lines = []
            for p in ["MEMORY_TREE.md", "Persona/life/bg.md", "Persona/life/household.md"]:
                lines.append(f"{p}|partial|stub")
            return "\n".join(lines)
        result = mt.retrieve(
            populated_db, "household", k=5,
            low_threshold=0.0, abstain_threshold=0.0,
            use_verifier=True,
            verifier_transport=transport,
        )
        assert len(result["results"]) >= 1

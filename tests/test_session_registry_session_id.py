"""Tests for session_id filter in query_registry()."""
import json
import textwrap
from pathlib import Path

import pytest

from scripts.hooks.__lib.session_registry import query_registry


@pytest.fixture
def registry_file(tmp_path: Path):
    """Create a temp registry with cross-terminal entries."""
    p = tmp_path / "session_registry.jsonl"
    entries = [
        {"session_id": "sess-A", "terminal_id": "t1", "ts": "2026-01-01", "transcript_path": "/a1"},
        {"session_id": "sess-A", "terminal_id": "t2", "ts": "2026-01-02", "transcript_path": "/a2"},
        {"session_id": "sess-B", "terminal_id": "t1", "ts": "2026-01-03", "transcript_path": "/b1"},
        {"session_id": "sess-A", "terminal_id": "t3", "ts": "2026-01-04", "transcript_path": "/a3"},
        {"session_id": "sess-C", "terminal_id": "t2", "ts": "2026-01-05", "transcript_path": "/c1"},
    ]
    p.write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return p


def test_session_id_filter_returns_cross_terminal(registry_file):
    """query_registry(session_id=X) returns entries across multiple terminals."""
    result = query_registry(session_id="sess-A", limit=20, registry_path=registry_file)
    ids = [e["terminal_id"] for e in result]
    assert ids == ["t1", "t2", "t3"]
    assert all(e["session_id"] == "sess-A" for e in result)


def test_session_id_filter_single_terminal(registry_file):
    """query_registry(session_id=X) works when all entries on one terminal."""
    result = query_registry(session_id="sess-C", limit=20, registry_path=registry_file)
    assert len(result) == 1
    assert result[0]["terminal_id"] == "t2"


def test_session_id_filter_no_match(registry_file):
    """query_registry(session_id=X) returns empty for unknown session."""
    result = query_registry(session_id="nonexistent", limit=20, registry_path=registry_file)
    assert result == []


def test_session_id_none_returns_all(registry_file):
    """query_registry(session_id=None) preserves backward compat — returns all."""
    result = query_registry(limit=20, registry_path=registry_file)
    assert len(result) == 5


def test_session_id_with_terminal_id_combined(registry_file):
    """Both filters together narrow results."""
    result = query_registry(session_id="sess-A", terminal_id="t2", limit=20, registry_path=registry_file)
    assert len(result) == 1
    assert result[0]["terminal_id"] == "t2"
    assert result[0]["session_id"] == "sess-A"


def test_session_id_respects_limit(registry_file):
    """Limit applies after filtering by session_id."""
    result = query_registry(session_id="sess-A", limit=2, registry_path=registry_file)
    assert len(result) == 2
    # Should be last 2 in append order
    assert result[0]["terminal_id"] == "t2"
    assert result[1]["terminal_id"] == "t3"

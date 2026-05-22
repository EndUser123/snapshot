"""Tests for the registry-backed terminal_id fallback in terminal_detection.

Verifies G4 fix from the gap-to-opportunity audit:
- When env vars and console handle are unavailable, the fallback looks up
  terminal_id in session_registry.jsonl by session_id and then by cwd.
- Matches /id's authoritative source so artifacts stay co-located across
  sessions in the same terminal/workspace.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add the snapshot scripts/hooks/__lib to sys.path so we can import the module.
_LIB = Path(__file__).resolve().parents[1] / "scripts" / "hooks" / "__lib"
sys.path.insert(0, str(_LIB))


@pytest.fixture
def isolated_registry(tmp_path, monkeypatch):
    """Redirect query_registry to a tmp file.

    Implementation detail: session_registry.query_registry's `registry_path`
    default arg is bound at function-definition time, so monkeypatching
    DEFAULT_REGISTRY_PATH alone has no effect. We wrap the function directly.

    Also strips terminal env vars so the fallback chain reaches the registry
    lookup step instead of short-circuiting on env.
    """
    for var in ("CLAUDE_TERMINAL_ID", "TERMINAL_ID", "TERM_ID", "SESSION_TERMINAL", "WT_SESSION"):
        monkeypatch.delenv(var, raising=False)

    registry_path = tmp_path / "session_registry.jsonl"
    import session_registry  # type: ignore[import-not-found]
    original = session_registry.query_registry

    def _patched(*, terminal_id=None, cwd=None, limit=20, registry_path=registry_path):
        return original(
            terminal_id=terminal_id, cwd=cwd, limit=limit, registry_path=registry_path
        )

    monkeypatch.setattr(session_registry, "query_registry", _patched)
    monkeypatch.setattr(session_registry, "DEFAULT_REGISTRY_PATH", registry_path)
    return registry_path


def _write_entry(registry_path: Path, **fields) -> None:
    """Append one entry to the registry. Caller supplies session_id, terminal_id, cwd, ts."""
    with registry_path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(fields) + "\n")


class TestRegistryLookupBySessionId:
    def test_returns_matching_terminal_id(self, isolated_registry):
        _write_entry(
            isolated_registry,
            ts="2026-05-13T10:00:00+00:00",
            terminal_id="console_aaaa-1111",
            session_id="sess-known",
            cwd="C:/work/foo",
        )
        from terminal_detection import _lookup_terminal_from_registry
        assert _lookup_terminal_from_registry("sess-known", None) == "console_aaaa-1111"

    def test_returns_most_recent_when_session_id_repeats(self, isolated_registry):
        _write_entry(
            isolated_registry,
            ts="2026-05-13T10:00:00+00:00",
            terminal_id="console_old",
            session_id="sess-repeat",
            cwd="C:/work",
        )
        _write_entry(
            isolated_registry,
            ts="2026-05-13T11:00:00+00:00",
            terminal_id="console_new",
            session_id="sess-repeat",
            cwd="C:/work",
        )
        from terminal_detection import _lookup_terminal_from_registry
        # Reversed iteration → most recent matches first.
        assert _lookup_terminal_from_registry("sess-repeat", None) == "console_new"

    def test_unknown_session_returns_empty(self, isolated_registry):
        _write_entry(
            isolated_registry,
            ts="2026-05-13T10:00:00+00:00",
            terminal_id="console_x",
            session_id="some-other-sess",
            cwd="C:/x",
        )
        from terminal_detection import _lookup_terminal_from_registry
        assert _lookup_terminal_from_registry("nonexistent", None) == ""


class TestRegistryLookupByCwd:
    def test_returns_matching_cwd_terminal(self, isolated_registry, tmp_path):
        workspace = str(tmp_path / "workspace")
        Path(workspace).mkdir()
        _write_entry(
            isolated_registry,
            ts="2026-05-13T10:00:00+00:00",
            terminal_id="console_cwd-match",
            session_id="some-sess",
            cwd=workspace,
        )
        from terminal_detection import _lookup_terminal_from_registry
        assert _lookup_terminal_from_registry(None, workspace) == "console_cwd-match"

    def test_session_id_takes_precedence_over_cwd(self, isolated_registry, tmp_path):
        workspace = str(tmp_path / "ws")
        Path(workspace).mkdir()
        # cwd-matching entry first, then a session-id-matching entry pointing elsewhere.
        _write_entry(
            isolated_registry,
            ts="2026-05-13T10:00:00+00:00",
            terminal_id="console_cwd-only",
            session_id="other-sess",
            cwd=workspace,
        )
        _write_entry(
            isolated_registry,
            ts="2026-05-13T10:05:00+00:00",
            terminal_id="console_session-match",
            session_id="target-sess",
            cwd="/some/other/path",
        )
        from terminal_detection import _lookup_terminal_from_registry
        # session_id is the more authoritative match → wins.
        assert _lookup_terminal_from_registry("target-sess", workspace) == "console_session-match"


class TestFallbackChainOrder:
    def test_synthetic_only_when_all_sources_fail(self, isolated_registry):
        # Empty registry → no lookup match → falls through to synthetic.
        # Synthetic format: f"session_{session_id[:12]}"
        from terminal_detection import _fallback_detect_terminal_id
        tid = _fallback_detect_terminal_id("orphan-sess-abc123")
        assert tid == "session_" + "orphan-sess-abc123"[:12]

    def test_registry_match_preempts_synthetic(self, isolated_registry):
        _write_entry(
            isolated_registry,
            ts="2026-05-13T10:00:00+00:00",
            terminal_id="console_real",
            session_id="orphan-sess-abc123",
            cwd="C:/anywhere",
        )
        from terminal_detection import _fallback_detect_terminal_id
        # Same session_id as above — should now hit the registry path.
        tid = _fallback_detect_terminal_id("orphan-sess-abc123")
        assert tid == "console_real", (
            f"Expected registry hit to preempt synthetic, got {tid}"
        )

    def test_empty_session_id_returns_empty_string(self, isolated_registry):
        from terminal_detection import _fallback_detect_terminal_id
        # No env, no console handle, no session_id → empty.
        # (resolve_terminal_key will then raise ValueError, which is correct
        # for the no-information-at-all case.)
        tid = _fallback_detect_terminal_id(None)
        assert tid == ""


class TestLookupWithPath:
    """The internal _lookup_with_path() returns (tid, sub_path) for audit logging."""

    def test_session_id_match_returns_registry_sid(self, isolated_registry):
        _write_entry(
            isolated_registry,
            ts="2026-05-13T10:00:00",
            terminal_id="console_alpha",
            session_id="sid-1",
            cwd="C:/x",
        )
        from terminal_detection import _lookup_with_path
        tid, path = _lookup_with_path("sid-1", None)
        assert tid == "console_alpha"
        assert path == "registry_sid"

    def test_cwd_match_returns_registry_cwd(self, isolated_registry, tmp_path):
        workspace = str(tmp_path / "ws")
        Path(workspace).mkdir()
        _write_entry(
            isolated_registry,
            ts="2026-05-13T10:00:00",
            terminal_id="console_beta",
            session_id="other-sid",
            cwd=workspace,
        )
        from terminal_detection import _lookup_with_path
        tid, path = _lookup_with_path(None, workspace)
        assert tid == "console_beta"
        assert path == "registry_cwd"

    def test_no_match_returns_empty_path(self, isolated_registry):
        from terminal_detection import _lookup_with_path
        tid, path = _lookup_with_path("unknown", None)
        assert tid == ""
        assert path == ""


class TestResolutionLogging:
    """Resolution-path logging writes one jsonl entry per fallback call."""

    @pytest.fixture
    def log_path(self, tmp_path, monkeypatch):
        """Redirect the audit log to a tmp file and return its path."""
        target = tmp_path / "terminal_resolution.jsonl"
        import terminal_detection as td
        monkeypatch.setattr(td, "_RESOLUTION_LOG_PATH", target)
        return target

    def _read_log(self, path: Path) -> list[dict]:
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def test_env_var_path_logged(self, log_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_TERMINAL_ID", "abc123")
        from terminal_detection import _fallback_detect_terminal_id
        _fallback_detect_terminal_id("some-sess")
        entries = self._read_log(log_path)
        assert len(entries) == 1
        assert entries[0]["path"] == "env"
        assert entries[0]["returned"] == "env_abc123"
        assert entries[0]["session_id"] == "some-sess"

    def test_synthetic_path_logged(self, log_path, isolated_registry):
        # No env vars, empty registry → falls through to synthetic.
        from terminal_detection import _fallback_detect_terminal_id
        _fallback_detect_terminal_id("orphan-sess-xyz")
        entries = self._read_log(log_path)
        assert len(entries) == 1
        assert entries[0]["path"] == "synthetic"
        assert entries[0]["returned"].startswith("session_")

    def test_registry_sid_path_logged(self, log_path, isolated_registry):
        _write_entry(
            isolated_registry,
            ts="2026-05-13T10:00:00",
            terminal_id="console_real",
            session_id="resumed-sess",
            cwd="C:/x",
        )
        from terminal_detection import _fallback_detect_terminal_id
        tid = _fallback_detect_terminal_id("resumed-sess")
        assert tid == "console_real"
        entries = self._read_log(log_path)
        assert len(entries) == 1
        assert entries[0]["path"] == "registry_sid"
        assert entries[0]["returned"] == "console_real"

    def test_empty_path_logged_when_no_signals(self, log_path, isolated_registry):
        from terminal_detection import _fallback_detect_terminal_id
        tid = _fallback_detect_terminal_id(None)  # no session_id either
        assert tid == ""
        entries = self._read_log(log_path)
        assert len(entries) == 1
        assert entries[0]["path"] == "empty"
        assert entries[0]["returned"] == ""

    def test_log_disk_error_does_not_break_detection(self, isolated_registry, monkeypatch, tmp_path):
        """If logging itself fails, fallback still returns a valid result."""
        # Point the log at an unwritable target (a directory pretending to be a file).
        bad = tmp_path / "not-writable"
        bad.mkdir()  # so opening it as a file fails
        import terminal_detection as td
        monkeypatch.setattr(td, "_RESOLUTION_LOG_PATH", bad)
        monkeypatch.setenv("CLAUDE_TERMINAL_ID", "fallback-still-works")
        from terminal_detection import _fallback_detect_terminal_id
        # Must not raise.
        tid = _fallback_detect_terminal_id("any-sess")
        assert tid == "env_fallback-still-works"


class TestRegistryFailureModes:
    def test_missing_registry_file_returns_empty(self, tmp_path, monkeypatch):
        """When the registry file doesn't exist, lookup returns empty silently."""
        registry_path = tmp_path / "does-not-exist.jsonl"
        import session_registry
        monkeypatch.setattr(session_registry, "DEFAULT_REGISTRY_PATH", registry_path)
        from terminal_detection import _lookup_terminal_from_registry
        assert _lookup_terminal_from_registry("anything", None) == ""

    def test_corrupt_lines_are_skipped(self, isolated_registry):
        """Malformed JSON lines in the registry don't break the lookup."""
        with isolated_registry.open("a", encoding="utf-8") as f:
            f.write("{not valid json\n")
            f.write(json.dumps({
                "ts": "2026-05-13T10:00:00",
                "terminal_id": "console_valid",
                "session_id": "good-sess",
                "cwd": "C:/x",
            }) + "\n")
            f.write("{also broken\n")
        from terminal_detection import _lookup_terminal_from_registry
        assert _lookup_terminal_from_registry("good-sess", None) == "console_valid"

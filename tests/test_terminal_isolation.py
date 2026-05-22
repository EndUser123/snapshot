#!/usr/bin/env python3
"""Terminal isolation tests for V2 handoff storage."""

from __future__ import annotations


from core.hooks.__lib.handoff_files import SnapshotFileStorage as HandoffFileStorage
from core.hooks.__lib.handoff_v2 import build_envelope, build_resume_snapshot


def _payload(terminal_id: str, *, goal: str, transcript_path: str) -> dict:
    snapshot = build_resume_snapshot(
        terminal_id=terminal_id,
        source_session_id="source",
        goal=goal,
        current_task=goal,
        progress_percent=40,
        progress_state="in_progress",
        blockers=[],
        active_files=[f"{goal}.py"],
        pending_operations=[],
        next_step="Continue",
        decision_refs=[],
        evidence_refs=["ev_1"],
        transcript_path=transcript_path,
        message_intent="instruction",
    )
    return build_envelope(
        resume_snapshot=snapshot,
        decision_register=[],
        evidence_index=[
            {
                "id": "ev_1",
                "type": "transcript",
                "label": "transcript",
                "path": transcript_path,
            }
        ],
    )


def test_storage_keeps_terminals_separate(tmp_path):
    # Create real transcript files for validation
    transcript_a = tmp_path / "transcript_a.jsonl"
    transcript_b = tmp_path / "transcript_b.jsonl"
    transcript_a.write_text('{"role": "user", "content": "task_a"}')
    transcript_b.write_text('{"role": "user", "content": "task_b"}')

    storage_a = HandoffFileStorage(tmp_path, "console_a")
    storage_b = HandoffFileStorage(tmp_path, "console_b")

    assert storage_a.save_handoff(
        _payload("console_a", goal="task_a", transcript_path=str(transcript_a))
    )
    assert storage_b.save_handoff(
        _payload("console_b", goal="task_b", transcript_path=str(transcript_b))
    )

    loaded_a = storage_a.load_handoff()
    loaded_b = storage_b.load_handoff()

    assert loaded_a is not None
    assert loaded_b is not None
    assert loaded_a["resume_snapshot"]["goal"] == "task_a"
    assert loaded_b["resume_snapshot"]["goal"] == "task_b"


def test_storage_rejects_wrong_terminal_file_contents(tmp_path):
    # Create real transcript file for validation
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text('{"role": "user", "content": "test"}')

    storage = HandoffFileStorage(tmp_path, "console_target")
    wrong_storage = HandoffFileStorage(tmp_path, "console_source")

    assert wrong_storage.save_handoff(
        _payload("console_source", goal="wrong", transcript_path=str(transcript))
    )

    raw = wrong_storage.load_raw_handoff()
    assert raw is not None
    storage.handoff_dir.mkdir(parents=True, exist_ok=True)
    with open(storage.handoff_file, "w", encoding="utf-8") as handle:
        import json

        json.dump(raw, handle, indent=2)

    assert storage.load_handoff() is None


class TestFallbackTerminalDetection:
    """Tests for _fallback_detect_terminal_id session_id derivation."""

    def test_fallback_returns_env_when_available(self, monkeypatch):
        """When CLAUDE_TERMINAL_ID is set, fallback returns it prefixed."""
        monkeypatch.setenv("CLAUDE_TERMINAL_ID", "my-term-123")
        from core.hooks.__lib.terminal_detection import _fallback_detect_terminal_id

        result = _fallback_detect_terminal_id()
        assert result == "env_my-term-123"

    def test_fallback_returns_session_id_derived_when_all_sources_fail(self, monkeypatch):
        """When all detection sources return empty, fallback derives from session_id."""
        # Clear all terminal env vars
        for var in ["CLAUDE_TERMINAL_ID", "TERMINAL_ID", "TERM_ID", "SESSION_TERMINAL"]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv("WT_SESSION", raising=False)

        from core.hooks.__lib.terminal_detection import _fallback_detect_terminal_id

        result = _fallback_detect_terminal_id(session_id="97bdd749-b5f0-4adc-a82a-a849b2488302")
        assert result == "session_97bdd749-b5f"

    def test_fallback_returns_empty_when_no_session_id_and_all_sources_fail(
        self, monkeypatch
    ):
        """When no session_id and no env vars, fallback returns empty string."""
        for var in ["CLAUDE_TERMINAL_ID", "TERMINAL_ID", "TERM_ID", "SESSION_TERMINAL"]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv("WT_SESSION", raising=False)

        from core.hooks.__lib.terminal_detection import _fallback_detect_terminal_id

        result = _fallback_detect_terminal_id()
        assert result == ""

    def test_resolve_terminal_key_uses_session_id_fallback(self, monkeypatch):
        """resolve_terminal_key passes session_id to fallback when terminal_id is empty.

        When identity.json is absent/mismatched AND no env vars available,
        the session_id is passed through to _fallback_detect_terminal_id.
        """
        for var in ["CLAUDE_TERMINAL_ID", "TERMINAL_ID", "TERM_ID", "SESSION_TERMINAL"]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.delenv("WT_SESSION", raising=False)

        from core.hooks.__lib.terminal_detection import resolve_terminal_key

        # When all detection sources fail AND no cached identity exists,
        # session_id is the last resort — this MUST NOT raise ValueError.
        result = resolve_terminal_key(
            terminal_id="", session_id="97bdd749-b5f0-4adc-a82a-a849b2488302"
        )
        assert result == "session_97bdd749-b5f"

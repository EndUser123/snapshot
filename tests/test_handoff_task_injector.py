"""Tests for userpromptsubmit_task_injector.py — compaction recovery hook.

Verifies:
  - No marker -> empty result (normal prompts unaffected)
  - Expired marker -> empty result, marker cleared
  - Missing handoff file -> empty result, marker cleared
  - Valid marker + valid envelope -> context injected, marker cleared (one-shot)
  - Kill-switch env var -> empty result
  - Recovery message contains required fields from envelope
  - Terminal scoping: different terminals use different marker files
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from unittest.mock import patch

# The hook source lives in packages/handoff/scripts/hooks/ but imports
# UserPromptSubmit_modules.base from .claude/hooks/.  Add both to sys.path.
_package_root = Path(__file__).resolve().parents[1]  # packages/handoff/
# Walk up to find the project root (directory that contains .claude/)
_project_root = _package_root
for _candidate in [_package_root, *_package_root.parents]:
    if (_candidate / ".claude" / "hooks").is_dir():
        _project_root = _candidate
        break
_hooks_dir = _project_root / ".claude" / "hooks"
for _p in (_package_root, _hooks_dir):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

# Import via the real module path (not the symlink name).
import scripts.hooks.snapshot_UserPromptSubmit as _mod  # noqa: E402


def _make_envelope(
    goal: str = "Test goal",
    current_task: str = "Test task",
    active_files: list[str] | None = None,
    pending_ops: list[dict] | None = None,
    next_step: str = "Do the next thing",
    n_1_transcript_path: str = "/tmp/session.jsonl",
    n_2_transcript_path: str | None = None,
    progress_state: str = "in_progress",
    progress_percent: int = 50,
) -> dict:
    return {
        "resume_snapshot": {
            "goal": goal,
            "current_task": current_task,
            "active_files": active_files or [],
            "pending_operations": pending_ops or [],
            "next_step": next_step,
            "n_1_transcript_path": n_1_transcript_path,
            "n_2_transcript_path": n_2_transcript_path,
            "progress_state": progress_state,
            "progress_percent": progress_percent,
            "blockers": [],
            "status": "pending",
            "message_intent": "instruction",
        }
    }


def _make_marker(
    handoff_path: str, terminal_id: str = "default", age: float = 0.0
) -> dict:
    return {
        "timestamp": time.time() - age,
        "handoff_path": handoff_path,
        "terminal_id": terminal_id,
    }


_MOD_PATH = "scripts.hooks.snapshot_UserPromptSubmit"


class TestNoMarker:
    def test_no_marker_returns_empty(self) -> None:
        """Normal prompts (no marker) must not inject anything."""
        from UserPromptSubmit_modules.base import HookContext

        ctx = HookContext(prompt="do something", data={"terminal_id": "t1"})
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch(f"{_MOD_PATH}._locate_hooks_state_dir", return_value=Path(tmpdir)):
                result = _mod.handoff_task_injector_hook(ctx)
        assert result.context is None


class TestExpiredMarker:
    def test_expired_marker_returns_empty(self) -> None:
        """Marker older than TTL must not inject and must be deleted."""
        from UserPromptSubmit_modules.base import HookContext

        ctx = HookContext(prompt="do something", data={"terminal_id": "t_expired"})
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch(f"{_MOD_PATH}._locate_hooks_state_dir", return_value=tmp):
                marker_file = _mod._marker_path("t_expired")
                marker_file.parent.mkdir(parents=True, exist_ok=True)
                marker = _make_marker(
                    "/does/not/exist.json",
                    "t_expired",
                    age=_mod._MARKER_TTL_SECONDS + 1,
                )
                marker_file.write_text(json.dumps(marker), encoding="utf-8")

                result = _mod.handoff_task_injector_hook(ctx)

                assert result.context is None
                assert (
                    not marker_file.exists()
                ), "Expired marker should have been deleted"


class TestMissingHandoffFile:
    def test_missing_handoff_returns_empty_clears_marker(self) -> None:
        """Valid marker but missing handoff file -> empty result, marker cleared."""
        from UserPromptSubmit_modules.base import HookContext

        ctx = HookContext(prompt="do something", data={"terminal_id": "t_missing"})
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch(f"{_MOD_PATH}._locate_hooks_state_dir", return_value=tmp):
                marker_file = _mod._marker_path("t_missing")
                marker_file.parent.mkdir(parents=True, exist_ok=True)
                marker = _make_marker("/nonexistent/handoff.json", "t_missing")
                marker_file.write_text(json.dumps(marker), encoding="utf-8")

                result = _mod.handoff_task_injector_hook(ctx)

                assert result.context is None
                assert (
                    not marker_file.exists()
                ), "Marker must be cleared even when handoff is missing"


class TestSuccessfulRecovery:
    def _setup_valid_state(
        self, tmp: Path, terminal_id: str, envelope: dict
    ) -> tuple[Path, Path]:
        """Write handoff envelope and marker, return (handoff_file, marker_file)."""
        handoff_file = tmp / f"{terminal_id}_handoff.json"
        handoff_file.write_text(json.dumps(envelope), encoding="utf-8")
        marker_file = _mod._marker_path(terminal_id)
        marker_file.parent.mkdir(parents=True, exist_ok=True)
        marker = _make_marker(str(handoff_file), terminal_id)
        marker_file.write_text(json.dumps(marker), encoding="utf-8")
        return handoff_file, marker_file

    def test_valid_marker_injects_context(self) -> None:
        """Valid marker + valid handoff envelope -> restoration context injected."""
        from UserPromptSubmit_modules.base import HookContext

        envelope = _make_envelope(goal="Build the compaction recovery hook")
        ctx = HookContext(prompt="continue", data={"terminal_id": "t_good"})
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch(f"{_MOD_PATH}._locate_hooks_state_dir", return_value=tmp):
                self._setup_valid_state(tmp, "t_good", envelope)
                result = _mod.handoff_task_injector_hook(ctx)

        assert result.context is not None
        assert len(result.context) > 0

    def test_context_contains_goal(self) -> None:
        """Injected context must contain the goal from the envelope."""
        from UserPromptSubmit_modules.base import HookContext

        envelope = _make_envelope(goal="Implement compaction recovery")
        ctx = HookContext(prompt="continue", data={"terminal_id": "t_goal"})
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch(f"{_MOD_PATH}._locate_hooks_state_dir", return_value=tmp):
                self._setup_valid_state(tmp, "t_goal", envelope)
                result = _mod.handoff_task_injector_hook(ctx)

        assert result.context is not None
        assert "Implement compaction recovery" in result.context
        # Compact format uses "User requested:" prefix around goal
        assert "User requested:" in result.context

    def test_marker_cleared_after_injection(self) -> None:
        """Marker must be deleted after injection — one-shot behaviour."""
        from UserPromptSubmit_modules.base import HookContext

        envelope = _make_envelope()
        ctx = HookContext(prompt="continue", data={"terminal_id": "t_oneshot"})
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch(f"{_MOD_PATH}._locate_hooks_state_dir", return_value=tmp):
                _, marker_file = self._setup_valid_state(tmp, "t_oneshot", envelope)
                result1 = _mod.handoff_task_injector_hook(ctx)
                result2 = _mod.handoff_task_injector_hook(
                    ctx
                )  # second call: marker gone

        assert result1.context is not None
        assert result2.context is None

    def test_context_uses_compact_format_no_raw_transcript_path(self) -> None:
        """Injected context must use <compact-restore> format with no raw transcript path."""
        from UserPromptSubmit_modules.base import HookContext

        envelope = _make_envelope(n_1_transcript_path="/sessions/abc123.jsonl")
        ctx = HookContext(prompt="continue", data={"terminal_id": "t_tp"})
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch(f"{_MOD_PATH}._locate_hooks_state_dir", return_value=tmp):
                self._setup_valid_state(tmp, "t_tp", envelope)
                result = _mod.handoff_task_injector_hook(ctx)

        assert result.context is not None
        # Compact format: no transcript path leaked, no "Transcript:" placeholder
        assert "<compact-restore>" in result.context
        assert "status: restored" in result.context
        assert "transcript_chain:" in result.context
        assert "n_1_transcript_path:" in result.context
        assert "n_2_transcript_path:" in result.context
        # Raw path must not appear in output (privacy by omission)
        assert "/sessions/abc123.jsonl" not in result.context

    def test_context_contains_current_task(self) -> None:
        """Injected context must include the active goal/next-step contract."""
        from UserPromptSubmit_modules.base import HookContext

        envelope = _make_envelope(current_task="Write the injector hook")
        ctx = HookContext(prompt="continue", data={"terminal_id": "t_ct"})
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            with patch(f"{_MOD_PATH}._locate_hooks_state_dir", return_value=tmp):
                self._setup_valid_state(tmp, "t_ct", envelope)
                result = _mod.handoff_task_injector_hook(ctx)

        assert result.context is not None
        assert "Test goal" in result.context
        assert "Do the next thing" in result.context


class TestKillSwitch:
    def test_disabled_by_env_var(self) -> None:
        """COMPACTION_RECOVERY_ENABLED=false must suppress injection."""
        from UserPromptSubmit_modules.base import HookContext

        envelope = _make_envelope()
        ctx = HookContext(prompt="continue", data={"terminal_id": "t_disabled"})
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            handoff_file = tmp / "t_disabled_handoff.json"
            handoff_file.write_text(json.dumps(envelope), encoding="utf-8")

            with patch(f"{_MOD_PATH}._locate_hooks_state_dir", return_value=tmp):
                marker_file = _mod._marker_path("t_disabled")
                marker_file.parent.mkdir(parents=True, exist_ok=True)
                marker = _make_marker(str(handoff_file), "t_disabled")
                marker_file.write_text(json.dumps(marker), encoding="utf-8")

                with patch.dict(os.environ, {"COMPACTION_RECOVERY_ENABLED": "false"}):
                    result = _mod.handoff_task_injector_hook(ctx)

        assert result.context is None


class TestTerminalScoping:
    def test_different_terminals_use_different_markers(self) -> None:
        """Marker files must be scoped to terminal_id."""
        path_a = _mod._marker_path("console_abc")
        path_b = _mod._marker_path("console_xyz")
        assert path_a != path_b
        assert "console_abc" in path_a.name
        assert "console_xyz" in path_b.name

    def test_marker_name_sanitizes_special_chars(self) -> None:
        """Terminal IDs with special characters must produce valid filenames."""
        path = _mod._marker_path("term/with:special<chars>")
        assert "/" not in path.name
        assert ":" not in path.name
        assert "<" not in path.name

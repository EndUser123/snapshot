#!/usr/bin/env python3
"""
PreCompact_workflow_checkpoint.py - Save workflow checkpoint before compaction.

Runs BEFORE compaction erases context:
1. Reads current skill workflow state via read_pending_state()
2. Writes a compact checkpoint to the state directory
3. Checkpoint is read by Stop hook on post-compaction resume

Checkpoint is written to:
  P:\\\\\\.claude/state/skill_execution_{terminal_id}/compaction_checkpoint.json

This ensures the workflow phase machine state survives compaction.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

# Add skill_guard to path for skill_execution_state import
_HOOKS_DIR = Path(__file__).resolve().parent
_SKILL_GUARD_SRC = Path("P:/packages/.claude-marketplace/plugins/skill-guard/src")
if str(_SKILL_GUARD_SRC) in sys.path or str(_HOOKS_DIR) in sys.path:
    pass
else:
    if _SKILL_GUARD_SRC.exists():
        sys.path.insert(0, str(_SKILL_GUARD_SRC))


def _extract_terminal_id(data: dict) -> str:
    """Extract terminal_id from hook data."""
    terminal = data.get("terminal_id", "")
    if terminal:
        return str(terminal)

    session = data.get("session", {})
    if isinstance(session, dict):
        terminal = session.get("terminal_id", "")
        if terminal:
            return str(terminal)

    terminal = os.environ.get("CLAUDE_TERMINAL_ID", "")
    if terminal:
        return terminal

    return ""


def _sanitize_terminal_id(terminal_id: str) -> str:
    """Sanitize terminal ID for use in file paths."""
    import re

    return re.sub(r"[^a-zA-Z0-9_:\-]", "_", terminal_id)


def _get_state_dir(terminal_id: str) -> Path:
    """Get the state directory for this terminal."""
    sanitized = _sanitize_terminal_id(terminal_id)
    state_dir = Path("P:\\\\\\.claude/state") / f"skill_execution_{sanitized}"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir


def _read_current_state(terminal_id: str) -> dict | None:
    """Read the current workflow state from ledger via read_pending_state().

    This reads from the hook ledger which has the full state including
    workflow_stage fields populated by skill_execution_state.

    Falls back to direct file read for backward compatibility with
    pre-existing state files.
    """
    try:
        # Try to use read_pending_state from skill_execution_state
        from skill_execution_state import read_pending_state

        state = read_pending_state()
        if state:
            return state
    except Exception:
        pass

    # Fallback to direct file read
    state_dir = _get_state_dir(terminal_id)
    state_file = state_dir / "skill_execution_pending.json"
    if not state_file.exists():
        return None

    try:
        return json.loads(state_file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def main() -> None:
    """Main entry point for PreCompact router."""
    raw_input = sys.stdin.read().strip()
    if not raw_input:
        sys.exit(0)

    try:
        raw_input = raw_input.lstrip("\ufeff")
        data = json.loads(raw_input)
    except json.JSONDecodeError:
        sys.exit(0)

    try:
        terminal_id = _extract_terminal_id(data)
        if not terminal_id:
            sys.exit(0)

        # Read current workflow state
        state = _read_current_state(terminal_id)
        if not state:
            sys.exit(0)

        # Write compaction checkpoint
        state_dir = _get_state_dir(terminal_id)
        checkpoint_file = state_dir / "compaction_checkpoint.json"

        checkpoint = {
            "skill": state.get("skill", ""),
            "phase": state.get("phase", "pending"),
            "loaded_at": state.get("loaded_at", 0),
            "completion_criteria": state.get("completion_criteria", []),
            "enforcement_tier": state.get("enforcement_tier", "advisory"),
            "tools_used": state.get("tools_used", []),
            "first_tool_validated": state.get("first_tool_validated", False),
            "checkpoint_at": time.time(),
            "terminal_id": terminal_id,
            # Workflow stage for topic drift prevention (v1.0)
            "workflow_stage": {
                "active_step": state.get("active_step", ""),
                "step_definition": state.get("step_definition", ""),
                "done_criteria": state.get("done_criteria", []),
                "do_not_distract": state.get("do_not_distract", []),
                "step_index": state.get("step_index", 0),
                "total_steps": state.get("total_steps", 0),
            },
        }

        # Atomic write
        temp = checkpoint_file.with_suffix(".tmp")
        temp.write_text(json.dumps(checkpoint, indent=2))
        os.replace(str(temp), str(checkpoint_file))

    except Exception:
        # Fail silently - PreCompact errors should not block compaction
        pass


if __name__ == "__main__":
    main()

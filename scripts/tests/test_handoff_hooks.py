#!/usr/bin/env python3
"""Focused hook tests for Handoff V2."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from core.hooks.PreCompact_handoff_capture import (
    detect_planning_session,
    detect_session_type,
)
from core.hooks.__lib.handoff_files import SnapshotFileStorage as HandoffFileStorage

HOOKS_DIR = Path(__file__).resolve().parents[1] / "hooks"


def _write_transcript(path: Path, entries: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        for entry in entries:
            handle.write(json.dumps(entry) + "\n")


def test_detect_session_type_prefers_planning_keywords():
    session_type, emoji = detect_session_type(
        "/arch design the compact handoff replacement",
        ["P:\\\\\\packages/.claude-marketplace/plugins/snapshot/scripts/hooks/PreCompact_snapshot_capture.py"],
    )

    assert session_type == "planning"
    assert emoji == "📋"


def test_detect_planning_session_creates_approval_blocker():
    blocker = detect_planning_session("/plan-workflow build the new handoff format", [])

    assert blocker is not None
    assert blocker["type"] == "awaiting_approval"


def test_precompact_hook_writes_v2_envelope(tmp_path, monkeypatch):
    monkeypatch.setenv("SNAPSHOT_PROJECT_ROOT", str(tmp_path))
    transcript_path = tmp_path / "transcripts" / "capture.jsonl"
    _write_transcript(
        transcript_path,
        [
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Implement the handoff v2 restore path and never restore stale snapshots.",
                        }
                    ]
                },
            },
            {
                "type": "tool_use",
                "name": "Edit",
                "input": {
                    "file_path": "P:\\\\\\packages/.claude-marketplace/plugins/snapshot/scripts/hooks/__lib/snapshot_v2.py",
                    "old_string": "old code",
                    "new_string": "new code",
                },
            },
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "text",
                            "text": "Decision: never auto-restore stale snapshots. editing file P:\\\\\\packages/.claude-marketplace/plugins/snapshot/scripts/hooks/__lib/snapshot_v2.py next.",
                        }
                    ]
                },
            },
        ],
    )

    payload = {
        "session_id": "session-capture",
        "terminal_id": "console_test_capture",
        "transcript_path": str(transcript_path),
        "cwd": str(tmp_path),
        "hook_event_name": "PreCompact",
        "trigger": "manual",
    }

    result = subprocess.run(
        [sys.executable, str(HOOKS_DIR / "PreCompact_snapshot_capture.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        check=True,
    )

    output = json.loads(result.stdout)
    assert output["decision"] == "approve"

    storage = HandoffFileStorage(tmp_path, "console_test_capture")
    saved = storage.load_raw_handoff()
    assert saved is not None
    snapshot = saved["resume_snapshot"]
    assert snapshot["status"] == "pending"
    assert snapshot["goal"].startswith("Implement the handoff v2 restore path")
    assert (
        "P:\\\\\\packages/.claude-marketplace/plugins/snapshot/scripts/hooks/__lib/snapshot_v2.py"
        in snapshot["active_files"]
    )
    assert snapshot["decision_refs"]

#!/usr/bin/env python3
"""SessionStart child hook: capture authoritative identity to cache file.

Reads session_id, transcript_path, cwd from hook data.
Reads terminal_id from WT_SESSION env var.
Writes combined identity to .claude/.artifacts/{terminal_id}/identity.json.

This is the single source of truth for the /id skill.
Called by snapshot_SessionStart.py as a child hook in the SEQUENCE.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

_HOOKS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_HOOKS_DIR / "__lib"))

from terminal_detection import detect_terminal_id

_REGISTRY_MAX_LINES = 10_000
_REGISTRY_KEEP_LINES = 5_000



def _append_to_session_registry(
    terminal_id: str,
    session_id: str,
    transcript_path: str,
    cwd: str,
    artifacts_root: Path,
) -> None:
    """Append current session to session_registry.jsonl.

    Makes the session immediately available for cross-terminal queries
    (e.g., /chs export) instead of waiting for next compaction.
    """
    registry_path = artifacts_root / "session_registry.jsonl"
    registry_path.parent.mkdir(parents=True, exist_ok=True)

    entry = {
        "ts": datetime.now(UTC).isoformat(),
        "terminal_id": terminal_id,
        "session_id": session_id,
        "transcript_path": transcript_path,
        "goal": "",
        "progress_percent": 0,
        "handoff_path": "",
        "cwd": cwd,
    }

    try:
        with registry_path.open("a", encoding="utf-8") as rf:
            rf.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass  # Non-fatal: registry write failure shouldn't block SessionStart


def _prune_session_registry(registry_path: Path) -> None:
    if not registry_path.exists():
        return
    try:
        lines = registry_path.read_text(encoding="utf-8").splitlines()
        if len(lines) < _REGISTRY_MAX_LINES:
            return
        kept = lines[-_REGISTRY_KEEP_LINES:]
        tmp = registry_path.with_suffix(".tmp")
        tmp.write_text("\n".join(kept) + "\n", encoding="utf-8")
        registry_path.unlink()
        tmp.replace(registry_path)
    except Exception:
        pass


def run(data: dict) -> dict | None:
    if not isinstance(data, dict):
        return None

    # ponytail: throwaway probe (remove after #899). Captures which env signals
    # survive the hook boundary + what payload fields are available, to choose
    # between payload-based vs file-handoff terminal_id fix routes.
    try:
        probe_path = Path(os.environ.get("CLAUDE_PROJECT_DIR", "P:/")) / ".claude" / ".artifacts" / "_probe" / "identity_probe.jsonl"
        probe_path.parent.mkdir(parents=True, exist_ok=True)
        probe_entry = {
            "ts": datetime.now(UTC).isoformat(),
            "env_CLAUDE_TERMINAL_ID": os.environ.get("CLAUDE_TERMINAL_ID"),
            "env_WT_SESSION": os.environ.get("WT_SESSION"),
            "env_ITERM_SESSION_ID": os.environ.get("ITERM_SESSION_ID"),
            "env_WEZTERM_SESSION_ID": os.environ.get("WEZTERM_SESSION_ID"),
            "env_TMUX": os.environ.get("TMUX"),
            "env_ConEmuServerPID": os.environ.get("ConEmuServerPID"),
            "payload_keys": sorted(data.keys()),
            "ppid": os.getppid(),
        }
        with probe_path.open("a", encoding="utf-8") as pf:
            pf.write(json.dumps(probe_entry, ensure_ascii=False) + "\n")
    except Exception:
        pass

    terminal_id = detect_terminal_id()
    if not terminal_id:
        return None

    cwd = data.get("cwd", "")
    # Anchor artifacts to the project root, never to raw cwd: a script whose
    # cwd is a nested package dir would otherwise leak .claude/.artifacts into
    # the package tree. CLAUDE_PROJECT_DIR is the cwd-independent project root;
    # fall back to the canonical workspace root (matches terminal_detection.py).
    project_root = os.environ.get("CLAUDE_PROJECT_DIR") or "P:/"
    artifacts_root = Path(project_root) / ".claude" / ".artifacts"

    identity = {
        "terminal": {
            "id": terminal_id,
            "source": "WT_SESSION",
        },
        "claude": {
            "session_id": data.get("session_id", ""),
            "transcript_path": data.get("transcript_path", ""),
            "cwd": cwd,
        },
        "captured_at": datetime.now(UTC).isoformat(),
    }

    safe_tid = terminal_id.replace("/", "-").replace("\\", "-").replace(":", "-")
    artifact_dir = artifacts_root / safe_tid
    artifact_dir.mkdir(parents=True, exist_ok=True)

    dest = artifact_dir / "identity.json"
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(identity, indent=2) + "\n", encoding="utf-8")
    if dest.exists():
        dest.unlink()
    tmp.replace(dest)

    _append_to_session_registry(
        terminal_id=terminal_id,
        session_id=identity["claude"]["session_id"],
        transcript_path=identity["claude"]["transcript_path"],
        cwd=identity["claude"]["cwd"],
        artifacts_root=artifacts_root,
    )

    _prune_session_registry(artifacts_root / "session_registry.jsonl")

    return None

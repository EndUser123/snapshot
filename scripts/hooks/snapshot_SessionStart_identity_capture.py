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

    terminal_id = detect_terminal_id()
    if not terminal_id:
        return None

    cwd = data.get("cwd", "")
    if cwd:
        artifacts_root = Path(cwd) / ".claude" / ".artifacts"
    else:
        artifacts_root = Path("P:/.claude/.artifacts")

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

    _prune_session_registry(artifacts_root / "session_registry.jsonl")

    return None

#!/usr/bin/env python3
"""Session registry reader for handoff system.

Provides a query interface to the append-only JSONL session registry
written by PreCompact_handoff_capture.py.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_REGISTRY_PATH = Path("P:\\\\\\.claude/.artifacts/session_registry.jsonl")


def query_registry(
    *,
    terminal_id: str | None = None,
    session_id: str | None = None,
    cwd: str | None = None,
    limit: int = 20,
    registry_path: Path = DEFAULT_REGISTRY_PATH,
) -> list[dict]:
    """Query the session registry JSONL file.

    Args:
        terminal_id: Filter to entries for this terminal.
        session_id: Filter to entries for this session (cross-terminal chain).
        cwd: Filter to entries matching this working directory.
        limit: Maximum entries to return (default 20).
        registry_path: Path to the JSONL registry file.

    Returns:
        List of entry dicts, most-recent-last (append order).
    """
    if not registry_path.exists():
        return []

    entries: list[dict] = []
    try:
        raw = registry_path.read_text(encoding="utf-8")
    except OSError:
        return []

    for line_no, line in enumerate(raw.splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(entry, dict):
            continue
        if terminal_id is not None and entry.get("terminal_id") != terminal_id:
            continue
        if session_id is not None and entry.get("session_id") != session_id:
            continue
        if cwd is not None and entry.get("cwd") != cwd:
            continue
        entries.append(entry)

    return entries[-limit:]

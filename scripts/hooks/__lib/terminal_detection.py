#!/usr/bin/env python3
"""
Terminal Detection Module - Compatibility Wrapper

Lazy-imports terminal detection from skill-guard when available.
Falls back to a local implementation using the same priority order:
1. CLAUDE_TERMINAL_ID and other env vars
2. Windows WT_SESSION / GetConsoleWindow() handle
3. Empty string (callers must handle)
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from .canonical_terminal_id import canonical_terminal_id, canonical_terminal_id_from_env

_TERMINAL_ENV_VARS = [
    "CLAUDE_TERMINAL_ID",
    "TERMINAL_ID",
    "TERM_ID",
    "SESSION_TERMINAL",
]

_sg_detect_terminal_id = None
_sg_resolved = False


def get_verified_identity(session_id: str | None = None) -> dict | None:
    """Read and verify the global identity cache for the current terminal.

    This implements a 'Handshake' pattern: we only trust the cached identity
    if it matches our live session_id. This prevents using stale data from
    a previous session in the same terminal.
    """
    # 1. Start with the fastest heuristic-based ID (WT_SESSION)
    terminal_id = detect_terminal_id()
    if not terminal_id:
        return None

    # 2. Locate the identity.json file in the canonical artifacts root
    # Matching $CLAUDE_ROOT/hooks\SessionStart_identity_capture.py
    artifacts_root = Path(os.environ.get("CLAUDE_ARTIFACTS_ROOT", "P:/.claude/.artifacts"))
    safe_tid = terminal_id.replace("/", "-").replace("\\", "-").replace(":", "-")
    identity_file = artifacts_root / safe_tid / "identity.json"

    if not identity_file.exists():
        return None

    # 3. THE HANDSHAKE: Verify against live session_id
    try:
        identity = json.loads(identity_file.read_text(encoding="utf-8"))
        if session_id:
            cached_sid = identity.get("claude", {}).get("session_id")
            if cached_sid and cached_sid != session_id:
                # Stale data: identity file belongs to a DIFFERENT session
                return None
        return identity
    except (json.JSONDecodeError, OSError):
        return None


def _try_import_skill_guard() -> None:
    """Attempt to import detect_terminal_id from skill-guard (once)."""
    global _sg_detect_terminal_id, _sg_resolved
    if _sg_resolved:
        return
    _sg_resolved = True

    current_file = Path(__file__)
    project_root = current_file.parent.parent.parent.parent.parent
    for candidate in (
        project_root / "skill-guard" / "src",
        project_root / ".claude" / "hooks" / "skill-guard" / "src",
        current_file.parent.parent.parent.parent / "skill-guard",
    ):
        marker = candidate / "skill_guard" / "utils" / "terminal_detection.py"
        if marker.exists():
            if str(candidate) not in sys.path:
                sys.path.insert(0, str(candidate))
            try:
                from skill_guard.utils.terminal_detection import (
                    detect_terminal_id as _impl,
                )
                _sg_detect_terminal_id = _impl
            except Exception:
                pass
            return


def _lookup_with_path(
    session_id: str | None, cwd: str | None
) -> tuple[str, str]:
    """Look up terminal_id from session_registry.jsonl, returning the matching sub-path.

    Internal helper for resolution-path logging. Public wrapper
    `_lookup_terminal_from_registry()` discards the path component for
    backward compatibility with existing callers.

    Returns:
        (terminal_id, sub_path) where sub_path is one of:
        "registry_sid" — matched by exact session_id
        "registry_cwd" — matched by cwd (no session_id match)
        ""             — no match
    """
    if not session_id and not cwd:
        return "", ""
    try:
        from session_registry import query_registry  # type: ignore[import-not-found]
    except ImportError:
        try:
            sibling = Path(__file__).parent
            if str(sibling) not in sys.path:
                sys.path.insert(0, str(sibling))
            from session_registry import query_registry  # type: ignore[import-not-found]
        except ImportError:
            return "", ""

    try:
        entries = query_registry(limit=200)
    except Exception:
        return "", ""

    # 1. Exact session_id match — most authoritative for resumed sessions.
    if session_id:
        for entry in reversed(entries):  # most recent first
            if entry.get("session_id") == session_id:
                tid = str(entry.get("terminal_id", "")).strip()
                if tid:
                    return tid, "registry_sid"

    # 2. cwd match — most-recent entry whose cwd matches.
    if cwd:
        normalized_cwd = str(Path(cwd).resolve()) if Path(cwd).exists() else cwd
        for entry in reversed(entries):
            entry_cwd = str(entry.get("cwd", ""))
            if not entry_cwd:
                continue
            try:
                if str(Path(entry_cwd).resolve()) == normalized_cwd or entry_cwd == cwd:
                    tid = str(entry.get("terminal_id", "")).strip()
                    if tid:
                        return tid, "registry_cwd"
            except OSError:
                continue

    return "", ""


def _lookup_terminal_from_registry(
    session_id: str | None, cwd: str | None
) -> str:
    """Look up a real terminal_id from session_registry.jsonl.

    Backward-compat wrapper around `_lookup_with_path()`. Returns the
    terminal_id only, discarding which sub-path matched. New code that
    needs to distinguish session_id-match vs cwd-match should call
    `_lookup_with_path()` directly.

    Returns "" if no useful entry is found. Best-effort; never raises.
    """
    tid, _ = _lookup_with_path(session_id, cwd)
    return tid


# Resolution-path audit log.
# Records every invocation of _fallback_detect_terminal_id so an offline
# audit can answer "which path resolved my terminal_id, and did it agree
# with what /id reports?". Best-effort — disk errors never break detection.
_RESOLUTION_LOG_PATH = (
    Path.home() / ".claude" / ".artifacts" / "snapshot" / "logs"
    / "terminal_resolution.jsonl"
)


def _log_resolution(
    path: str, returned: str, session_id: str | None, cwd: str | None
) -> None:
    """Append one resolution-path entry to the audit log. Never raises."""
    try:
        from datetime import datetime, timezone
        _RESOLUTION_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id or "",
            "cwd": cwd or "",
            "path": path,
            "returned": returned,
        }
        with _RESOLUTION_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # Logging must never block detection.
        return


def _fallback_detect_terminal_id(session_id: str | None = None) -> str:
    """Fallback using WT_SESSION, normalized env vars, Windows console handle, and the session registry.

    Resolution order:
      1. canonical_terminal_id_from_env() — shared algorithm: CLAUDE_TERMINAL_ID,
         WT_SESSION, ITERM_SESSION_ID, WEZTERM_SESSION_ID, TMUX, ConEmuServerPID.
         Produces the same `console_<id>` every reader derives (no format drift).
      2. session_registry.jsonl lookup by session_id/cwd — resume continuity.
         Recovers a prior terminal_id when no live terminal env signal is present
         (e.g. resumed in a bare shell). Preserved feature; not new-derivation.
      3. canonical_terminal_id() derived fallback — sha1(ppid), unique per
         terminal and stable for its lifetime. Replaces the legacy synthetic
         `session_{session_id[:12]}` which no reader could derive.

    Every return site logs (path, returned) to terminal_resolution.jsonl so an
    offline audit can compare hook resolution against /id's strict result.
    """
    cwd = os.getcwd()

    # 1. Canonical env-signal detection (shared across all plugins).
    tid = canonical_terminal_id_from_env()
    if tid:
        _log_resolution("canonical_env", tid, session_id, cwd)
        return tid

    # 2. Registry-backed lookup — resume continuity (preserved feature).
    registry_tid, registry_path = _lookup_with_path(session_id, cwd)
    if registry_tid:
        _log_resolution(registry_path, registry_tid, session_id, cwd)
        return registry_tid

    # 3. Derived fallback — shared algorithm; never synthetic, never static.
    tid = canonical_terminal_id()
    _log_resolution("canonical_derived", tid, session_id, cwd)
    return tid


def detect_terminal_id(session_id: str | None = None) -> str:
    """Detect terminal ID. Uses verified handshake if session_id provided, fallback otherwise."""
    if session_id:
        identity = get_verified_identity(session_id)
        if identity:
            tid = identity.get("terminal", {}).get("id")
            if tid:
                return tid

    _try_import_skill_guard()
    if _sg_detect_terminal_id is not None:
        tid = _sg_detect_terminal_id()
        if tid:
            return tid
    return _fallback_detect_terminal_id(session_id)


def resolve_terminal_key(
    terminal_id: str | None = None, session_id: str | None = None
) -> str:
    """Resolve the terminal key for handoff file storage.

    Args:
        terminal_id: Optional terminal ID
        session_id: Optional session ID (enables verified handshake)

    Returns:
        Resolved terminal key string
    """
    # Fall back to detection whenever no usable terminal_id was provided.
    # Previously this checked `is None` only, which let an empty string ""
    # (as Claude Code 2.1.140 passes in PreCompact input) skip the fallback
    # and fail validation below — blocking /compact entirely.
    if not terminal_id or not str(terminal_id).strip():
        terminal_id = detect_terminal_id(session_id)

    # Validate terminal_id format
    if not terminal_id or not terminal_id.strip():
        raise ValueError("terminal_id cannot be empty or whitespace-only")

    if "\x00" in terminal_id:
        raise ValueError(
            f"terminal_id cannot contain null bytes (got: {repr(terminal_id)})"
        )

    if ".." in terminal_id or terminal_id.startswith("./"):
        raise ValueError(
            f"terminal_id cannot contain path traversal sequences (got: {terminal_id})"
        )

    if terminal_id.startswith("/") or terminal_id.startswith("\\"):
        raise ValueError(f"terminal_id cannot be an absolute path (got: {terminal_id})")

    # Sanitize terminal ID for filename (replace unsafe characters)
    # skill-guard uses format: {source}_{id} where source is "env" or "console"
    # These are already filename-safe, but we sanitize for safety
    safe_id = terminal_id.replace("/", "-").replace("\\", "-").replace(":", "-")
    return safe_id

"""Compaction Recovery — UserPromptSubmit hook.

Detects mid-session compaction events via a short-lived marker file written by
``PreCompact_snapshot_capture.py`` immediately after saving the Handoff V2
envelope.  On the first user prompt after a compaction, this hook reads the
envelope and injects restoration context automatically — no explicit "read the
transcript" directive needed.

Activation wiring (router-runtime-contract):
    This file is NOT a router entrypoint. It is loaded as a UserPromptSubmit
    module via the ``@register_hook("handoff_task_injector", priority=1.0)``
    decorator below, which is then dispatched by the global UPS router in
    ``P:/.claude/hooks/UserPromptSubmit_router.py`` via
    ``UserPromptSubmit_modules/registry.py``. There is no entry in
    ``P:/.claude/settings.json`` for UserPromptSubmit that points at this
    script directly; the global router is the single activation path.
    See ``docs/router-runtime-contract.md`` for the full contract.

FLOW:
    PreCompact (PreCompact_snapshot_capture.py)
        ↓ saves handoff envelope to state/handoff/{terminal_id}_handoff.json
        ↓ writes state/compaction_marker_{terminal_id}.json  <- NEW
    UserPromptSubmit (this hook)
        ↓ checks for compaction marker
        ↓ loads handoff envelope
        ↓ injects restoration context (one-shot)
        ↓ deletes marker

Gap closed: SessionStart fires at session *start* (including post-compact session
restart), but intra-session compactions have no automatic recovery injection.
This hook fills that gap by listening for the marker signal on every UPS event
and injecting exactly once.
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from UserPromptSubmit_modules.base import HookContext, HookResult
from UserPromptSubmit_modules.registry import register_hook

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


def _locate_hooks_state_dir(terminal_id: str) -> Path:
    """Return the canonical state directory for hooks in the artifacts root."""
    return Path("P:/.claude/.artifacts") / terminal_id / "snapshot"


_MARKER_PREFIX = "compaction_marker_"
_SMOKE_PREFIX = "restore_smoke_"
# TTL is a safety valve only — the one-shot deletion is the primary guard.
_MARKER_TTL_SECONDS = 3600  # 1 hour
_SMOKE_TTL_SECONDS = 120  # 2 minutes — window for next hook to clear it

_ENABLED_ENV = "COMPACTION_RECOVERY_ENABLED"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_terminal_id(context: HookContext) -> str:
    """Extract terminal ID from hook context."""
    # Priority: context data -> env var -> "default"
    tid = (
        context.data.get("terminal_id")
        or context.data.get("terminalId")
        or context.data.get("CLAUDE_TERMINAL_ID")
        or os.environ.get("CLAUDE_TERMINAL_ID")
        or "default"
    )
    return str(tid).strip()


def _marker_path(terminal_id: str) -> Path:
    """Return path to the compaction marker file for this terminal."""
    safe_id = re.sub(r"[^a-zA-Z0-9_.\-]+", "_", str(terminal_id))
    return _locate_hooks_state_dir(terminal_id) / f"{_MARKER_PREFIX}{safe_id}.json"


def _load_marker(terminal_id: str) -> dict | None:
    """Load the compaction marker; return None if absent, unreadable, or expired."""
    path = _marker_path(terminal_id)
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            marker = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None

    ts = float(marker.get("timestamp", 0.0))
    if (time.time() - ts) > _MARKER_TTL_SECONDS:
        _clear_marker(terminal_id)
        return None

    return marker


def _clear_marker(terminal_id: str) -> None:
    """Delete the compaction marker (one-shot injection guard)."""
    try:
        _marker_path(terminal_id).unlink(missing_ok=True)
    except OSError:
        pass


def _smoke_path(terminal_id: str) -> Path:
    """Return path to the restore-smoke marker file for this terminal."""
    safe_id = re.sub(r"[^a-zA-Z0-9_.\-]+", "_", str(terminal_id))
    return _locate_hooks_state_dir(terminal_id) / f"{_SMOKE_PREFIX}{safe_id}.json"


def write_restore_smoke_marker(terminal_id: str, session_id: str) -> None:
    """Write a post-restore smoke marker consumed by the next hook.

    If the marker is not cleared within _SMOKE_TTL seconds, the next hook
    logs a non-blocking warning that the restore output may not have been
    consumed by Claude Code.
    """
    try:
        _smoke_path(terminal_id).parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "type": "restore_smoke",
            "terminal_id": terminal_id,
            "session_id": session_id,
            "timestamp": time.time(),
        }
        path = _smoke_path(terminal_id)
        path.write_text(json.dumps(payload), encoding="utf-8")
    except Exception:
        pass  # Non-fatal — smoke marker is advisory only


def check_restore_smoke_marker(terminal_id: str, current_session_id: str) -> bool:
    """Check for an uncleared restore smoke marker.

    Returns True if the marker exists and matches the current session_id,
    indicating the restore output was potentially not consumed.
    Returns False if the marker is absent (normal — was cleared) or
    belongs to a different session.

    When True is returned, a warning is logged but the hook continues
    normally (non-blocking).
    """
    import logging as _logging
    _logger = _logging.getLogger(__name__)
    try:
        path = _smoke_path(terminal_id)
        if not path.exists():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            return False

        ts = payload.get("timestamp", 0.0)
        if (time.time() - ts) > _SMOKE_TTL_SECONDS:
            path.unlink(missing_ok=True)
            return False

        marker_session = payload.get("session_id", "")
        if marker_session != current_session_id:
            # Different session — stale marker, clean it up
            path.unlink(missing_ok=True)
            return False

        _logger.warning(
            "[UserPromptSubmit] Restore smoke marker not cleared — "
            "restore output may not have been consumed by Claude Code. "
            "terminal=%s session=%s (age=%.1fs)",
            terminal_id,
            current_session_id,
            time.time() - ts,
        )
        path.unlink(missing_ok=True)
        return True
    except Exception:
        return False


def _load_envelope(handoff_path: str) -> dict | None:
    """Load the Handoff V2 envelope JSON; return None on any error."""
    path = Path(handoff_path)
    if not path.exists():
        return None
    try:
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def _build_recovery_message(envelope: dict) -> str:
    """Format a concise restoration context block from a Handoff V2 envelope."""
    # Delegate to shared compact formatter for contract consistency.
    # Both SessionStart and UPS paths now emit the same <compact-restore> block.
    try:
        import importlib
        snapshot_v2 = importlib.import_module("scripts.hooks.__lib.snapshot_v2")
        message = snapshot_v2.build_restore_message_compact(envelope)
        locator = (envelope.get("resume_snapshot") or {}).get("user_message_locator")
        if locator:
            message += (
                "\n\nThe complete prior user message is in the canonical transcript at "
                f"`{locator.get('transcript_path')}`, JSONL line "
                f"{locator.get('line_start')}. Read that entry fully if its "
                "details are needed; the snapshot preview is intentionally bounded."
            )
        return message
    except ImportError as exc:
        import logging
        logging.getLogger(__name__).warning(
            "[task_injector] Failed to import snapshot_v2: %s", exc
        )
        return "[task_injector] Warning: snapshot_v2 unavailable, recovery context not generated"


# ---------------------------------------------------------------------------
# Hook entry point
# ---------------------------------------------------------------------------


@register_hook("handoff_task_injector", priority=1.0)
def handoff_task_injector_hook(context: HookContext) -> HookResult:
    """Inject Handoff V2 restoration context on the first prompt after compaction.

    ``PreCompact_snapshot_capture.py`` writes a compaction marker immediately
    after saving the handoff envelope.  This hook detects that marker, loads
    the envelope, builds a restoration message, injects it once, then deletes
    the marker so subsequent prompts are unaffected.
    """
    enabled = os.environ.get(_ENABLED_ENV, "true").lower()
    if enabled not in ("1", "true", "yes"):
        return HookResult.empty()

    terminal_id = _get_terminal_id(context)

    # Smoke test: verify the previous SessionStart restore output was consumed.
    # If the smoke marker persists (wasn't cleared), log a warning — non-blocking.
    session_id = context.data.get("session_id", "")
    check_restore_smoke_marker(terminal_id, session_id)

    marker = _load_marker(terminal_id)
    if marker is None:
        return HookResult.empty()

    handoff_path = marker.get("handoff_path", "")
    # Always clear the marker — inject at most once regardless of outcome.
    _clear_marker(terminal_id)

    if not handoff_path:
        return HookResult.empty()

    envelope = _load_envelope(handoff_path)
    if envelope is None:
        return HookResult.empty()

    # Bail if snapshot was already restored by SessionStart (prevents dual-path re-injection loop)
    resume_snapshot = envelope.get("resume_snapshot") or {}
    if resume_snapshot.get("status") != "pending":
        return HookResult.empty()

    message = _build_recovery_message(envelope)
    return HookResult(context=message, tokens=len(message) // 4)

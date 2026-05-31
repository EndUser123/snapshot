#!/usr/bin/env python3
"""SessionStart restore hook for Handoff V2."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
from pathlib import Path
from typing import Any

# sys.path must be set up BEFORE importing scripts.hooks modules
PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from scripts.hooks.snapshot_UserPromptSubmit import _clear_marker, write_restore_smoke_marker

logger = logging.getLogger(__name__)

# Configure logging to ensure diagnostic output is captured
# Logs will be written to P:\\\\\\.claude/.artifacts/snapshot/logs/handoff_restore.log
_log_file_path = (
    Path(os.environ.get("CLAUDE_PROJECT_DIR", "P:")) / ".claude" / ".artifacts" / "snapshot" / "logs" / "handoff_restore.log"
)
_log_file_path.parent.mkdir(parents=True, exist_ok=True)
if not logger.handlers:
    _handler = RotatingFileHandler(
        _log_file_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    _handler.setFormatter(
        logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    )
    logger.addHandler(_handler)
logger.setLevel(logging.DEBUG)

from scripts.hooks.__lib.snapshot_files import SnapshotFileStorage
from scripts.hooks.__lib.snapshot_v2 import (
    SNAPSHOT_CONSUMED,
    SNAPSHOT_REJECTED_INVALID,
    SNAPSHOT_REJECTED_STALE,
    build_no_snapshot_hint,
    build_restore_message_dynamic,
    build_stale_hint,
    compute_checksum,
    evaluate_for_restore,
)
from scripts.hooks.__lib.hook_input_validation import (
    HookInputError,
    validate_hook_input,
)
from scripts.hooks.__lib.terminal_detection import resolve_terminal_key
from scripts.hooks.__lib.project_root import detect_project_root


def _read_hook_input() -> dict[str, Any]:
    # IO-004: Bound stdin read to prevent memory exhaustion from malformed input
    raw = sys.stdin.read(10_000_000).strip()  # 10MB max
    if not raw:
        raise ValueError("SessionStart hook received empty stdin")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("SessionStart hook input must be a JSON object")
    return payload


def _normalize_session_start_source(input_data: dict[str, Any]) -> str | None:
    source = input_data.get("source")
    trigger = input_data.get("trigger")

    values = []
    if isinstance(source, str):
        values.append(source.strip().lower())
    if isinstance(trigger, str):
        values.append(trigger.strip().lower())

    compact_markers = {
        "compact",
        "post_compact",
        "post-compact",
        "resume_after_compact",
        "compaction",
    }

    for value in values:
        if value in compact_markers:
            return "compact"

    return None


def _build_output(reason: str, additional_context: str | None = None) -> dict[str, Any]:
    output: dict[str, Any] = {"decision": "approve", "reason": reason}
    if additional_context:
        output["additionalContext"] = additional_context
    return output


def _reject_if_possible(
    storage: SnapshotFileStorage,
    payload: dict[str, Any] | None,
    *,
    session_id: str,
    status: str,
    reason: str,
) -> None:
    if not payload:
        return
    try:
        storage.update_snapshot_status_from_payload(
            payload,
            status=status,
            session_id=session_id,
            reason=reason,
        )
    except Exception as exc:
        logger.warning("[SessionStart V2] Failed to persist rejection state: %s", exc)


def run(input_data: dict[str, Any]) -> dict[str, Any]:
    """Restore a fresh V2 handoff snapshot after compact.

    Args:
        input_data: JSON hook input from Claude Code.

    Returns:
        Dict following the Claude Code hook protocol.
    """
    try:
        validate_hook_input(input_data, hook_type="SessionStart")

        session_id = input_data.get("session_id", "")
        terminal_id = resolve_terminal_key(
            input_data.get("terminal_id"), input_data.get("session_id")
        )
        source = _normalize_session_start_source(input_data)

        # Write active-session file for multi-terminal session detection
        if session_id and terminal_id:
            try:
                active_session_file = (
                    Path.home() / ".claude" / f"active-session-{terminal_id}.txt"
                )
                active_session_file.parent.mkdir(parents=True, exist_ok=True)
                tmp = active_session_file.with_suffix(".tmp")
                tmp.write_text(session_id + "\n")
                if active_session_file.exists():
                    active_session_file.unlink()
                tmp.rename(active_session_file)
            except OSError as exc:
                logger.error(
                    "[SessionStart V2] Failed to write active-session file (OSError): %s", exc
                )

        # CRITICAL: For snapshot package, detect project root with testing support
        env_project_root = os.environ.get("SNAPSHOT_PROJECT_ROOT")
        if env_project_root:
            project_root = Path(env_project_root)
            logger.info(
                f"[SessionStart V2] Using project root from environment: {project_root}"
            )
        else:
            project_root = detect_project_root(current_dir=Path(os.environ.get("CLAUDE_PROJECT_DIR", "P:")), strict=False)
            logger.info(
                f"[SessionStart V2] Using project root from detect_project_root: {project_root}"
            )
        storage = SnapshotFileStorage(project_root, terminal_id)
        raw_payload = storage.load_raw_handoff()

        if not raw_payload:
            return _build_output(
                "No previous handoff found - starting fresh session",
                build_no_snapshot_hint("no handoff file for this terminal"),
            )

        stored_checksum = raw_payload.get("checksum")
        if not stored_checksum:
            logger.error(
                "[SessionStart V2] Missing checksum field - rejecting restore as unsafe"
            )
            return _build_output(
                "No safe current handoff found - checksum field missing",
                build_no_snapshot_hint(
                    "checksum field missing - data may be incomplete"
                ),
            )

        computed_checksum = compute_checksum(raw_payload)
        if computed_checksum != stored_checksum:
            logger.error(
                "[SessionStart V2] Checksum mismatch: expected=%s, computed=%s",
                stored_checksum,
                computed_checksum,
            )
            return _build_output(
                "No safe current handoff found - checksum validation failed",
                build_no_snapshot_hint("checksum mismatch - data may be corrupted"),
            )

        restore_decision = evaluate_for_restore(
            raw_payload,
            terminal_id=terminal_id,
            source=source,
            project_root=storage.project_root,
        )
        if restore_decision.ok and restore_decision.envelope:
            restoration_message = build_restore_message_dynamic(
                restore_decision.envelope,
                restore_session_id=session_id,
            )
            storage.update_snapshot_status(
                status=SNAPSHOT_CONSUMED,
                session_id=session_id,
                reason="restored after compact",
            )
            _clear_marker(terminal_id)

            snapshot = restore_decision.envelope.get("resume_snapshot", {})
            last_user_msg = snapshot.get("last_user_message")
            if last_user_msg and isinstance(last_user_msg, str) and last_user_msg.strip():
                restoration_message += f"\n\n**Last user message (verbatim):** {last_user_msg.strip()}"

            # Inject MEMORY.md corrections (merged from local SessionStart_reminder_recovery.py)
            recent_corrections = snapshot.get("recent_corrections")
            if recent_corrections and isinstance(recent_corrections, list) and recent_corrections:
                restoration_message += "\n\n**Recent corrections from MEMORY.md:**"
                for corr in recent_corrections[:3]:
                    restoration_message += f"\n- {corr}"

            # Restore prompt-enhancer artifact if present
            prompt_enhancement = snapshot.get("prompt_enhancement")
            if prompt_enhancement and isinstance(prompt_enhancement, dict):
                try:
                    enhancement_dir = (
                        Path.home() / ".claude" / ".artifacts" / terminal_id / "prompt-enhancer"
                    )
                    enhancement_dir.mkdir(parents=True, exist_ok=True)
                    enhancement_file = enhancement_dir / "active_enhancement.json"
                    # Add current session_id to enhancement data
                    enhancement_data = {**prompt_enhancement, "session_id": session_id}
                    enhancement_file.write_text(
                        json.dumps(enhancement_data, indent=2), encoding="utf-8"
                    )
                    logger.info(
                        "[SessionStart V2] Restored prompt_enhancement with %d fields",
                        len(prompt_enhancement),
                    )
                    restoration_message += "\n\n**Prompt clarification restored** from previous session."
                except Exception as exc:
                    logger.warning("[SessionStart V2] Failed to restore prompt_enhancement: %s", exc)

            try:
                env_ctx = restore_decision.envelope.get("environment_context")
                if env_ctx and isinstance(env_ctx, dict):
                    git_st = env_ctx.get("git_state")
                    if git_st and isinstance(git_st, dict):
                        captured_commit = (git_st.get("last_commit") or {}).get("hash")
                        if captured_commit and isinstance(captured_commit, str):
                            import subprocess
                            cwd = str(storage.project_root) if storage.project_root else None
                            if cwd:
                                result = subprocess.run(
                                    ["git", "rev-parse", "HEAD"],
                                    capture_output=True, text=True, cwd=cwd, timeout=5,
                                )
                                if result.returncode == 0:
                                    current_hash = result.stdout.strip()[:8]
                                    if current_hash != captured_commit:
                                        restoration_message += (
                                            f"\n\n**Codebase has changed** since last session "
                                            f"(captured: `{captured_commit}`, current: `{current_hash}`). "
                                            f"Context may be stale."
                                        )
            except Exception:
                pass

            write_restore_smoke_marker(terminal_id, session_id)

            # Update identity.json with transcript_chain for /id skill
            try:
                snapshot = restore_decision.envelope.get("resume_snapshot", {})
                transcript_chain = snapshot.get("transcript_chain")
                if transcript_chain:
                    artifacts_root = Path.home() / ".claude" / ".artifacts"
                    safe_tid = terminal_id.replace("/", "-").replace("\\", "-").replace(":", "-")
                    identity_file = artifacts_root / safe_tid / "identity.json"
                    if identity_file.exists():
                        ident = json.loads(identity_file.read_text(encoding="utf-8"))
                    else:
                        ident = {
                            "terminal": {"id": terminal_id, "source": "snapshot_restore"},
                            "claude": {
                                "session_id": session_id,
                                "transcript_path": snapshot.get("n_1_transcript_path", ""),
                                "cwd": input_data.get("cwd", ""),
                            },
                            "captured_at": snapshot.get("created_at", ""),
                        }
                    ident["claude"]["transcript_chain"] = transcript_chain
                    tmp = identity_file.with_suffix(".tmp")
                    tmp.write_text(json.dumps(ident, indent=2) + "\n", encoding="utf-8")
                    if identity_file.exists():
                        identity_file.unlink()
                    tmp.replace(identity_file)
            except Exception as exc:
                logger.warning("[SessionStart V2] Failed to update identity.json: %s", exc)

            return _build_output("Restored previous session context", restoration_message)

        reason = restore_decision.reason or "restore rejected"
        payload = raw_payload if isinstance(raw_payload, dict) else None

        if reason == "snapshot expired":
            _reject_if_possible(
                storage,
                payload,
                session_id=session_id,
                status=SNAPSHOT_REJECTED_STALE,
                reason=reason,
            )
            message = (
                build_stale_hint(payload, reason)
                if payload
                else build_no_snapshot_hint(reason)
            )
            output_reason = "No safe current handoff found - stale snapshot rejected"
        elif reason.startswith("snapshot evidence path "):
            # Evidence path validation failures are structural defects, not staleness.
            # Restore path resolution uses a different project_root than capture time —
            # this is a recoverable mismatch, not corruption, so reject as invalid.
            _reject_if_possible(
                storage,
                payload,
                session_id=session_id,
                status=SNAPSHOT_REJECTED_INVALID,
                reason=reason,
            )
            message = build_no_snapshot_hint(reason)
            output_reason = "No safe current handoff found - invalid snapshot rejected"
        elif reason.startswith("snapshot evidence "):
            # Other evidence failures (missing, changed) treated as stale.
            _reject_if_possible(
                storage,
                payload,
                session_id=session_id,
                status=SNAPSHOT_REJECTED_STALE,
                reason=reason,
            )
            message = (
                build_stale_hint(payload, reason)
                if payload
                else build_no_snapshot_hint(reason)
            )
            output_reason = "No safe current handoff found - stale snapshot rejected"
        elif reason.startswith("invalid handoff:") or reason == "terminal mismatch":
            _reject_if_possible(
                storage,
                payload,
                session_id=session_id,
                status=SNAPSHOT_REJECTED_INVALID,
                reason=reason,
            )
            message = build_no_snapshot_hint(reason)
            output_reason = "No safe current handoff found - invalid snapshot rejected"
        else:
            message = build_no_snapshot_hint(reason)
            output_reason = "No safe current handoff restored - starting fresh session"

        return _build_output(output_reason, message)

    except HookInputError as exc:
        return {
            "decision": "error",
            "reason": f"Hook input validation failed: {exc}",
            "additionalContext": (
                "Handoff V2 restore could not validate the SessionStart payload. "
                f"Details: {exc}"
            ),
        }
    except Exception as exc:
        logger.error("[SessionStart V2] Restore failed: %s", exc)
        return _build_output(
            "Handoff restore failed - starting fresh",
            f"⚠️ Handoff V2 restore error: {exc}",
        )


def main() -> None:
    """CLI entry point."""
    try:
        input_data = _read_hook_input()
        result = run(input_data)
        print(json.dumps(result, indent=2))
        sys.exit(0)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "decision": "error",
                    "reason": f"SessionStart CLI entry point failed: {exc}",
                }
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

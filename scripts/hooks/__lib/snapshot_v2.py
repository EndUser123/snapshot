#!/usr/bin/env python3
"""Handoff V2 schema, validation, and restore formatting utilities."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

# Import dynamic sections for content generation

SCHEMA_VERSION = 2
ENVELOPE_SCHEMA_VERSION = 1
DEFAULT_FRESHNESS_MINUTES = int(os.getenv("HANDOFF_FRESHNESS_MINUTES", "20"))
SNAPSHOT_PENDING = "pending"
SNAPSHOT_CONSUMED = "consumed"
SNAPSHOT_REJECTED_STALE = "rejected_stale"
SNAPSHOT_REJECTED_INVALID = "rejected_invalid"
SNAPSHOT_N_1_TRANSCRIPT_PATH = "n_1_transcript_path"
SNAPSHOT_N_2_TRANSCRIPT_PATH = "n_2_transcript_path"
SNAPSHOT_OPEN_QUESTIONS = "open_questions"
SNAPSHOT_TASKS_SNAPSHOT = "tasks_snapshot"
VALID_SNAPSHOT_STATUSES = {
    SNAPSHOT_PENDING,
    SNAPSHOT_CONSUMED,
    SNAPSHOT_REJECTED_STALE,
    SNAPSHOT_REJECTED_INVALID,
}

# Valid state transitions: from_state -> {allowed_to_states}
VALID_STATE_TRANSITIONS: dict[str, set[str]] = {
    SNAPSHOT_PENDING: {
        SNAPSHOT_CONSUMED,
        SNAPSHOT_REJECTED_STALE,
        SNAPSHOT_REJECTED_INVALID,
    },
    SNAPSHOT_CONSUMED: set(),  # Terminal state
    SNAPSHOT_REJECTED_STALE: set(),  # Terminal state
    SNAPSHOT_REJECTED_INVALID: set(),  # Terminal state
}
VALID_DECISION_KINDS = {"constraint", "settled_decision", "blocker_rule", "anti_goal"}
VALID_EVIDENCE_TYPES = {"file", "transcript", "test", "log", "git"}
VALID_MESSAGE_INTENTS = {
    "question",
    "instruction",
    "correction",
    "meta",
    "unsupported_language",
    "directive",  # Added for imperative commands (detect_message_intent returns this)
}
VALID_ENHANCEMENT_FIELDS = {
    "clarified_intent",
    "missing_details",
    "safety_flags",
    "confidence",
    "analysis",
}  # Valid EnhancementResult fields from prompt-enhancer.
   # estimated_tokens and inferred_subject were removed 2026-07-11:
   # no code consumed the field values; snapshot is an opaque carrier.
OPTIONAL_DECISION_FIELDS = set()  # Optional fields allowed in decisions
OPTIONAL_SNAPSHOT_FIELDS = {
    "quality_score",
    SNAPSHOT_OPEN_QUESTIONS,
    SNAPSHOT_TASKS_SNAPSHOT,
    "prompt_enhancement",  # EnhancementResult from prompt-enhancer plugin
}  # Optional fields allowed in snapshot
MUTABLE_METADATA_FIELDS = {
    "consumed_at",
    "consumed_by_session_id",
    "rejected_at",
    "rejected_by_session_id",
    "rejection_reason",
    "message_intent",  # Excluded from checksum - intent classification doesn't affect content validity
}


class SnapshotValidationError(ValueError):
    """Raised when a V2 snapshot envelope is malformed."""


@dataclass(slots=True)
class RestoreDecision:
    """Result of evaluating a V2 snapshot for automatic restore."""

    ok: bool
    reason: str | None = None
    envelope: dict[str, Any] | None = None


def utcnow() -> datetime:
    """Return the current UTC time."""
    return datetime.now(UTC)


def iso_now() -> str:
    """Return the current UTC time as ISO-8601."""
    return utcnow().isoformat()


def parse_iso8601(value: str) -> datetime:
    """Parse an ISO-8601 datetime string."""
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def make_decision_id() -> str:
    """Return a stable decision identifier using full UUID to prevent collisions."""
    return f"dec_{uuid4().hex}"


def make_evidence_id() -> str:
    """Return a stable evidence identifier using full UUID to prevent collisions."""
    return f"ev_{uuid4().hex}"


def _normalize_for_checksum(payload: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(payload)
    normalized.pop("checksum", None)
    normalized.pop("environment_context", None)  # Supplementary, not session state

    snapshot = normalized.get("resume_snapshot", {})
    if isinstance(snapshot, dict):
        for field in MUTABLE_METADATA_FIELDS:
            snapshot.pop(field, None)

    return normalized


def compute_checksum(payload: dict[str, Any]) -> str:
    """Compute the V2 envelope checksum.

    Contract: environment_context is EXCLUDED from the hash because it is
    supplementary data (environment snapshot at capture time) and changes on
    every call even when session state is unchanged. This means checksum
    validation passes even if environment_context is corrupted or truncated.
    If environment_context integrity is required, add a separate content_hash.
    See IO-003 from snapshot pre-mortem.
    """
    normalized = _normalize_for_checksum(payload)
    serialized = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


def compute_file_content_hash(path: str | Path) -> str | None:
    """Return a stable content hash for a file, or None if unreadable."""
    try:
        target = Path(path)
        if not target.exists() or not target.is_file():
            return None
        digest = hashlib.sha256()
        with open(target, "rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"
    except OSError:
        return None


def _format_snapshot_item(entry: Any, *, default_label: str) -> str:
    if isinstance(entry, dict):
        label = (
            entry.get("question")
            or entry.get("title")
            or entry.get("name")
            or entry.get("summary")
            or default_label
        )
        status = entry.get("status")
        if isinstance(label, str) and label:
            if isinstance(status, str) and status:
                return f"- {label} ({status})"
            return f"- {label}"
        return f"- {default_label}"
    if isinstance(entry, str) and entry:
        return f"- {entry}"
    return f"- {default_label}"


def _build_restore_state(
    snapshot: dict[str, Any],
    decisions_by_id: dict[str, dict[str, Any]],
    *,
    restore_session_id: str | None,
    include_user_context: bool,
) -> dict[str, Any]:
    n_1_transcript_path = snapshot["n_1_transcript_path"]
    n_2_transcript_path = snapshot["n_2_transcript_path"]
    source_session_id = snapshot.get("source_session_id", "<unknown>")
    terminal_id = snapshot.get("terminal_id", "<unknown>")
    message_intent = snapshot.get("message_intent", "instruction")
    intent_prefix = intent_prefixes.get(message_intent, "User requested:")

    blockers_str = "; ".join(
        blocker.get("summary", "Unspecified blocker")
        for blocker in snapshot.get("blockers", [])[:3]
        if blocker.get("type") == "awaiting_approval"
    ) or "none"

    active_files = snapshot.get("active_files", [])
    active_files_str = (
        "\n".join(f"- {path}" for path in active_files) if active_files else "none"
    )

    pending_ops = snapshot.get("pending_operations", [])
    pending_str = ""
    interrupted_skills: list[str] = []
    if pending_ops:
        pending_lines = []
        for op in pending_ops[:5]:
            op_type = op.get("type", "operation")
            target = op.get("target", "unknown")
            pending_lines.append(f"- {op_type}: {target}")
            if op_type == "skill" and op.get("state") == "in_progress":
                interrupted_skills.append(target)
        pending_str = "\n" + "\n".join(pending_lines)

    continuation_rule = (
        "PRESENT AS INFERENCE ONLY. "
        "A Skill was in-progress when the session compacted. "
        "The goal above was captured from the Skill invocation arguments — "
        "it may represent an interrupted action, not a user-level goal. "
        "Verify: ask 'What work was in progress before compaction?' "
        "rather than assuming the captured goal is current intent."
    )
    if not interrupted_skills:
        continuation_rule = (
            "Present the restored goal as context to verify — say 'Based on the session handoff, "
            "we were working on X' not 'The task was X'. The captured goal is an inference, not "
            "a recording. Do not ask the user to re-explain context you already have. "
            "Ask only if blocked by missing user input."
        )

    task_snapshot = (
        [
            _format_snapshot_item(task, default_label="Untitled task")
            for task in snapshot.get("tasks_snapshot", [])[:5]
        ]
        if snapshot.get("tasks_snapshot")
        else ["none"]
    )
    open_questions = (
        [
            _format_snapshot_item(question, default_label="Unspecified question")
            for question in snapshot.get("open_questions", [])[:5]
        ]
        if snapshot.get("open_questions")
        else ["none"]
    )
    active_decisions = (
        [
            f"- [{decision['kind']}] {decision['summary']}"
            for decision in (
                decisions_by_id.get(ref)
                for ref in snapshot.get("decision_refs", [])[:5]
            )
            if decision
        ]
        if snapshot.get("decision_refs")
        else ["none"]
    )

    if include_user_context and n_1_transcript_path:
        user_context = _extract_and_format_user_context(
            n_1_transcript_path, max_messages=15, goal_text=snapshot.get("goal")
        )
    else:
        user_context = None

    return {
        "session_identity": {
            "current_session_id": restore_session_id or "<unknown>",
            "source_session_id": source_session_id,
            "terminal_id": terminal_id,
        },
        "transcript_chain": {
            "n_1_transcript_path": "<session transcript>",
            "n_2_transcript_path": (
                "<previous session transcript>"
                if n_2_transcript_path
                else "<none>"
            ),
        },
        "work_state": {
            "goal": f"{intent_prefix} {snapshot['goal']}",
            "current_task": snapshot.get("goal") or "",  # canonical; short task name removed
            "progress_state": snapshot["progress_state"],
            "progress_percent": snapshot["progress_percent"],
            "next_step": snapshot["next_step"],
        },
        "open_loops": {
            "blockers_requiring_user": blockers_str,
        },
        "working_set": active_files_str,
        "tool_queue": {
            "pending_count": len(pending_ops),
            "items": pending_str,
        },
        "task_snapshot": task_snapshot,
        "open_questions": open_questions,
        "active_decisions": active_decisions,
        "continuation_rule": continuation_rule,
        "user_context": user_context,
    }


def _render_restore_state_lines(state: dict[str, Any]) -> list[str]:
    lines = [
        "session_identity:",
        f"current_session_id: {state['session_identity']['current_session_id']}",
        f"source_session_id: {state['session_identity']['source_session_id']}",
        f"terminal_id: {state['session_identity']['terminal_id']}",
        "transcript_chain:",
        f"n_1_transcript_path: {state['transcript_chain']['n_1_transcript_path']}",
        f"n_2_transcript_path: {state['transcript_chain']['n_2_transcript_path']}",
        "work_state:",
        f"goal: {state['work_state']['goal']}",
        # current_task removed (redundant with goal)
        f"progress_state: {state['work_state']['progress_state']}",
        f"progress_percent: {state['work_state']['progress_percent']}",
        f"next_step: {state['work_state']['next_step']}",
        "open_loops:",
        f"blockers_requiring_user: {state['open_loops']['blockers_requiring_user']}",
        "working_set:",
        state["working_set"],
        "tool_queue:",
        f"{state['tool_queue']['pending_count']} pending",
        state["tool_queue"]["items"],
        "task_snapshot:",
        "\n".join(state["task_snapshot"]),
        "open_questions:",
        "\n".join(state["open_questions"]),
        "active_decisions:",
        "\n".join(state["active_decisions"]),
        f"continuation_rule: {state['continuation_rule']}",
    ]
    if state["user_context"]:
        lines.extend(["", state["user_context"]])
    return lines


def _render_restore_message_verbose(state: dict[str, Any]) -> str:
    return "\n".join(["SESSION HANDOFF V2", "", *_render_restore_state_lines(state)])


def _render_restore_message_compact(state: dict[str, Any]) -> str:
    return "\n".join(
        ["<compact-restore>", "status: restored", *_render_restore_state_lines(state), "</compact-restore>"]
    )


def _require_fields(obj: dict[str, Any], fields: list[str], prefix: str) -> None:
    missing = [field for field in fields if field not in obj]
    if missing:
        raise SnapshotValidationError(
            f"{prefix} missing required fields: {', '.join(missing)}"
        )


def validate_envelope(payload: dict[str, Any]) -> None:
    """Validate the V2 handoff envelope."""
    if not isinstance(payload, dict):
        raise SnapshotValidationError("handoff payload must be a dict")

    # Top-level schema_version is optional for backward compatibility.
    top_level_version = payload.get("schema_version")
    if top_level_version is not None and top_level_version != ENVELOPE_SCHEMA_VERSION:
        raise SnapshotValidationError(
            f"unsupported envelope schema_version: {top_level_version}"
        )

    # environment_context is optional and supplementary (not checksummed).
    env_ctx = payload.get("environment_context")
    if env_ctx is not None and not isinstance(env_ctx, dict):
        raise SnapshotValidationError("environment_context must be a dict if present")

    _require_fields(
        payload, ["resume_snapshot", "decision_register", "evidence_index"], "envelope"
    )

    snapshot = payload["resume_snapshot"]
    decisions = payload["decision_register"]
    evidence = payload["evidence_index"]

    if not isinstance(snapshot, dict):
        raise SnapshotValidationError("resume_snapshot must be a dict")
    if not isinstance(decisions, list):
        raise SnapshotValidationError("decision_register must be a list")
    if not isinstance(evidence, list):
        raise SnapshotValidationError("evidence_index must be a list")

    _require_fields(
        snapshot,
        [
            "schema_version",
            "snapshot_id",
            "terminal_id",
            "source_session_id",
            "created_at",
            "expires_at",
            "status",
            "goal",
            "progress_percent",
            "progress_state",
            "blockers",
            "active_files",
            "pending_operations",
            "next_step",
            "decision_refs",
            "evidence_refs",
            "n_1_transcript_path",
            "n_2_transcript_path",
        ],
        "resume_snapshot",
    )

    if snapshot["schema_version"] != SCHEMA_VERSION:
        raise SnapshotValidationError(
            f"unsupported schema_version: {snapshot['schema_version']}"
        )

    if snapshot["status"] not in VALID_SNAPSHOT_STATUSES:
        raise SnapshotValidationError(
            f"invalid resume_snapshot.status: {snapshot['status']}"
        )

    for field in [
        "goal",
        "next_step",
        "terminal_id",
        "source_session_id",
    ]:
        if not isinstance(snapshot[field], str):
            raise SnapshotValidationError(f"resume_snapshot.{field} must be a string")

    for field in [
        "active_files",
        "pending_operations",
        "blockers",
        "decision_refs",
        "evidence_refs",
    ]:
        if not isinstance(snapshot[field], list):
            raise SnapshotValidationError(f"resume_snapshot.{field} must be a list")

    for field in [SNAPSHOT_TASKS_SNAPSHOT, SNAPSHOT_OPEN_QUESTIONS]:
        if field in snapshot and not isinstance(snapshot[field], list):
            raise SnapshotValidationError(f"resume_snapshot.{field} must be a list")

    if not isinstance(snapshot["progress_percent"], int):
        raise SnapshotValidationError(
            "resume_snapshot.progress_percent must be an integer"
        )
    if snapshot["progress_percent"] < 0 or snapshot["progress_percent"] > 100:
        raise SnapshotValidationError(
            "resume_snapshot.progress_percent must be between 0 and 100"
        )

    parse_iso8601(snapshot["created_at"])
    parse_iso8601(snapshot["expires_at"])

    # Validate the source-session transcript path exists and is safe
    # (SEC-001: Path traversal protection).
    transcript_path = snapshot["n_1_transcript_path"]
    if not isinstance(transcript_path, str) or not transcript_path:
        raise SnapshotValidationError(
            "resume_snapshot.n_1_transcript_path must be a string"
        )
    n_2_transcript_path = snapshot["n_2_transcript_path"]
    if n_2_transcript_path is not None and (
        not isinstance(n_2_transcript_path, str) or not n_2_transcript_path
    ):
        raise SnapshotValidationError(
            "resume_snapshot.n_2_transcript_path must be a string or null"
        )

    transcript_file = Path(transcript_path).resolve()

    # SEC-001: Validate transcript path against known project root.
    # When CLAUDE_PROJECT_ROOT is set, use it as the authoritative boundary.
    # Otherwise fall back to .claude walk-up (original behavior).
    project_root = None
    env_root = os.environ.get("CLAUDE_PROJECT_ROOT")
    if env_root:
        project_root = Path(env_root).resolve()
    else:
        # Walk up from transcript to find .claude boundary
        current = transcript_file
        for _ in range(5):
            if (current / ".claude").exists():
                project_root = current
                break
            parent = current.parent
            if parent == current:
                break
            current = parent

    if project_root is None:
        raise SnapshotValidationError(
            "resume_snapshot.n_1_transcript_path must be within project directory (no .claude boundary found)"
        )

    # Verify path is within project root to prevent traversal attacks
    try:
        transcript_file.relative_to(project_root)
    except ValueError:
        raise SnapshotValidationError(
            "resume_snapshot.n_1_transcript_path must be within project directory"
        )

    # SEC-002: Sanitized error messages (don't leak actual paths)
    if not transcript_file.exists():
        raise SnapshotValidationError(
            "resume_snapshot.n_1_transcript_path file does not exist"
        )
    if not transcript_file.is_file():
        raise SnapshotValidationError(
            "resume_snapshot.n_1_transcript_path is not a file"
        )

    decision_ids = set()
    for index, decision in enumerate(decisions):
        if not isinstance(decision, dict):
            raise SnapshotValidationError(f"decision_register[{index}] must be a dict")
        _require_fields(
            decision,
            [
                "id",
                "kind",
                "summary",
                "details",
                "priority",
                "applies_when",
                "source_refs",
            ],
            f"decision_register[{index}]",
        )
        if decision["kind"] not in VALID_DECISION_KINDS:
            raise SnapshotValidationError(f"decision_register[{index}].kind is invalid")
        decision_ids.add(decision["id"])

    evidence_ids = set()
    for index, item in enumerate(evidence):
        if not isinstance(item, dict):
            raise SnapshotValidationError(f"evidence_index[{index}] must be a dict")
        _require_fields(
            item, ["id", "type", "label", "path"], f"evidence_index[{index}]"
        )
        if item["type"] not in VALID_EVIDENCE_TYPES:
            raise SnapshotValidationError(f"evidence_index[{index}].type is invalid")
        evidence_ids.add(item["id"])

    for ref in snapshot["decision_refs"]:
        if ref not in decision_ids:
            raise SnapshotValidationError(
                f"resume_snapshot.decision_refs contains unknown id: {ref}"
            )

    for ref in snapshot["evidence_refs"]:
        if ref not in evidence_ids:
            raise SnapshotValidationError(
                f"resume_snapshot.evidence_refs contains unknown id: {ref}"
            )

    # LOGIC-002: Require checksum field - reject envelopes without checksum
    checksum = payload.get("checksum")
    if checksum is None:
        raise SnapshotValidationError("resume_snapshot.checksum is required")
    if checksum != compute_checksum(payload):
        raise SnapshotValidationError("handoff checksum mismatch")


def build_resume_snapshot(
    *,
    terminal_id: str,
    source_session_id: str,
    goal: str,
    progress_percent: int,
    progress_state: str,
    blockers: list[dict[str, Any]],
    active_files: list[str],
    pending_operations: list[dict[str, Any]],
    next_step: str,
    decision_refs: list[str],
    evidence_refs: list[str],
    transcript_path: str,
    prior_transcript_path: str | None = None,
    message_intent: str,  # Intent classification of the goal (required)
    freshness_minutes: int = DEFAULT_FRESHNESS_MINUTES,
    quality_score: float | None = None,
    tasks_snapshot: list[dict[str, Any]] | None = None,
    open_questions: list[Any] | None = None,
    goal_origin: str | None = None,  # Source of the goal value (user_message, preceding_message, skill_args_unfiltered)
    active_skill: str | None = None,  # Skill name extracted from slash command
    session_chain: list[str] | None = None,  # Full session chain (oldest-first session IDs)
    last_user_message: str | None = None,  # Verbatim last user message (ADR-006)
    full_user_message_path: str | None = None,  # Externalized oversized message
    recent_corrections: list[str] | None = None,  # MEMORY.md corrections ranked by relevance
    transcript_chain: list[str] | None = None,  # Pre-computed ordered list of transcript paths (newest first)
    prompt_enhancement: dict[str, Any] | None = None,  # EnhancementResult from prompt-enhancer plugin
) -> dict[str, Any]:
    """Build the V2 resume snapshot."""
    # QUAL-005: Validate message_intent is a recognized value
    if message_intent not in VALID_MESSAGE_INTENTS:
        raise ValueError(
            f"Invalid message_intent: '{message_intent}'. "
            f"Valid values: {sorted(VALID_MESSAGE_INTENTS)}"
        )

    now = utcnow()
    expires = now + timedelta(minutes=freshness_minutes)
    snapshot = {
        "schema_version": SCHEMA_VERSION,
        "snapshot_id": str(uuid4()),
        "terminal_id": terminal_id,
        "source_session_id": source_session_id,
        "created_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "status": SNAPSHOT_PENDING,
        "goal": goal,
                "progress_percent": max(0, min(100, progress_percent)),
        "progress_state": progress_state,
        "blockers": blockers,
        "active_files": active_files,
        "pending_operations": pending_operations,
        "next_step": next_step,
        "decision_refs": decision_refs,
        "evidence_refs": evidence_refs,
        SNAPSHOT_N_1_TRANSCRIPT_PATH: transcript_path,
        SNAPSHOT_N_2_TRANSCRIPT_PATH: prior_transcript_path,
        "message_intent": message_intent,  # Required field
        "goal_origin": goal_origin,  # Source of goal value (for downstream consumers)
    }
    if quality_score is not None:
        snapshot["quality_score"] = quality_score
    if tasks_snapshot is not None:
        snapshot["tasks_snapshot"] = tasks_snapshot
    if open_questions is not None:
        snapshot["open_questions"] = open_questions
    if session_chain is not None:
        snapshot["session_chain"] = session_chain
    if last_user_message is not None:
        snapshot["last_user_message"] = last_user_message
    if full_user_message_path is not None:
        snapshot["full_user_message_path"] = full_user_message_path
    if active_skill is not None:
        snapshot["active_skill"] = active_skill
    if recent_corrections is not None:
        snapshot["recent_corrections"] = recent_corrections
    if transcript_chain is not None:
        snapshot["transcript_chain"] = transcript_chain
    if prompt_enhancement is not None:
        snapshot["prompt_enhancement"] = prompt_enhancement
    return snapshot


def build_envelope(
    *,
    resume_snapshot: dict[str, Any],
    decision_register: list[dict[str, Any]],
    evidence_index: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build and checksum the V2 handoff envelope."""
    payload = {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "resume_snapshot": resume_snapshot,
        "decision_register": decision_register,
        "evidence_index": evidence_index,
    }
    payload["checksum"] = compute_checksum(payload)
    return payload


def mark_snapshot_status(
    payload: dict[str, Any],
    *,
    status: str,
    session_id: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Return a copy of the envelope with updated snapshot status.

    Raises:
        SnapshotValidationError: If the status transition is invalid.
    """
    updated = deepcopy(payload)
    snapshot = updated["resume_snapshot"]
    current_status = snapshot["status"]

    # Validate state transition
    if status not in VALID_SNAPSHOT_STATUSES:
        raise SnapshotValidationError(f"invalid target status: {status}")

    allowed_transitions = VALID_STATE_TRANSITIONS.get(current_status, set())
    if status not in allowed_transitions:
        raise SnapshotValidationError(
            f"invalid state transition: {current_status} -> {status} "
            f"(allowed: {', '.join(sorted(allowed_transitions)) or 'none (terminal state)'})"
        )

    snapshot["status"] = status

    if status == SNAPSHOT_CONSUMED:
        snapshot["consumed_at"] = iso_now()
        snapshot["consumed_by_session_id"] = session_id
        snapshot.pop("rejected_at", None)
        snapshot.pop("rejected_by_session_id", None)
        snapshot.pop("rejection_reason", None)
    elif status in {SNAPSHOT_REJECTED_STALE, SNAPSHOT_REJECTED_INVALID}:
        snapshot["rejected_at"] = iso_now()
        snapshot["rejected_by_session_id"] = session_id
        snapshot["rejection_reason"] = reason or status
        snapshot.pop("consumed_at", None)
        snapshot.pop("consumed_by_session_id", None)

    updated["checksum"] = compute_checksum(updated)
    return updated


def evaluate_for_restore(
    payload: dict[str, Any],
    *,
    terminal_id: str,
    source: str | None,
    project_root: Path | None = None,
    now: datetime | None = None,
) -> RestoreDecision:
    """Evaluate whether the snapshot is safe to auto-restore."""
    try:
        validate_envelope(payload)
    except SnapshotValidationError as exc:
        return RestoreDecision(ok=False, reason=f"invalid handoff: {exc}")

    if source != "compact":
        return RestoreDecision(ok=False, reason="not a post-compact session start")

    snapshot = payload["resume_snapshot"]
    if snapshot["terminal_id"] != terminal_id:
        return RestoreDecision(ok=False, reason="terminal mismatch")

    # Reject if current session already appears in session_chain.
    # This prevents cross-session contamination: a handoff captured in session A
    # must not be restored by session B (even though B is in the same terminal).
    # session_chain is checksummed (immutable field), so this cannot be bypassed.
    session_chain = snapshot.get("session_chain", [])
    if session_chain:
        current_session = os.environ.get("CLAUDE_SESSION_ID", "")
        if current_session and current_session in session_chain:
            return RestoreDecision(
                ok=False,
                reason="snapshot already restored in this terminal's session chain",
            )

    if snapshot["status"] != SNAPSHOT_PENDING:
        return RestoreDecision(
            ok=False, reason=f"snapshot status is {snapshot['status']}"
        )

    current_time = now or utcnow()
    if parse_iso8601(snapshot["expires_at"]) < current_time:
        return RestoreDecision(ok=False, reason="snapshot expired")

    evidence_failure = verify_evidence_freshness(payload, project_root=project_root)
    if evidence_failure:
        return RestoreDecision(ok=False, reason=evidence_failure)

    return RestoreDecision(ok=True, envelope=payload)


def verify_evidence_freshness(
    payload: dict[str, Any],
    *,
    project_root: Path | None = None,
) -> str | None:
    """Reject restore when captured evidence no longer matches current disk state."""
    snapshot = payload.get("resume_snapshot", {})
    transcript_path = snapshot.get("n_1_transcript_path")

    # Prefer the actual workspace root used by capture/restore. Fall back to the
    # transcript-derived boundary only when no caller context is available.
    effective_project_root = project_root
    if effective_project_root is None and isinstance(transcript_path, str) and transcript_path:
        transcript_file = Path(transcript_path).resolve()
        current = transcript_file
        for _ in range(5):
            if (current / ".claude").exists():
                effective_project_root = current
                break
            parent = current.parent
            if parent == current:
                break
            current = parent

    for item in payload.get("evidence_index", []):
        if not isinstance(item, dict):
            continue

        # Skip transcripts: they're append-only conversation logs that naturally grow
        # between capture and restore. The hash WILL differ — this is expected, not corruption.
        # Source code and config files are still verified (type == "file").
        evidence_type = item.get("type")
        if evidence_type == "transcript":
            continue
        if evidence_type != "file":
            continue

        recorded_hash = item.get("content_hash")
        if not isinstance(recorded_hash, str) or not recorded_hash:
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path:
            continue

        # CRIT-004 FIX: TOCTOU vulnerability - validate path AFTER hash computation
        # Resolve path first, then compute hash, THEN validate again
        # This prevents an attacker from replacing the file with a symlink between validation and hash computation
        evidence_file = Path(path).resolve()
        label = item.get("label") if isinstance(item.get("label"), str) else path

        # Validate repo file evidence against the workspace root BEFORE hashing
        # to prevent reading files outside project via symlink traversal.
        # Also allow files inside .claude directories (project-managed state).
        if effective_project_root is not None and item.get("type") == "file":
            try:
                evidence_file.relative_to(effective_project_root)
            except ValueError:
                # Exempt files inside .claude directories (project-managed, not external)
                inside_claude = False
                ancestor = evidence_file
                for _ in range(10):
                    if ancestor.name == ".claude":
                        inside_claude = True
                        break
                    if ancestor.parent == ancestor:
                        break
                    ancestor = ancestor.parent
                if not inside_claude:
                    return "snapshot evidence path outside project directory"

        current_hash = compute_file_content_hash(str(evidence_file))
        if current_hash is None:
            return f"snapshot evidence missing: {label}"
        if current_hash != recorded_hash:
            return f"snapshot evidence changed: {label}"
    return None


# Intent prefix mapping for goal display in restore message
intent_prefixes = {
    "question": "User asked:",
    "instruction": "User requested:",
    "correction": "User corrected:",
    "meta": "User noted:",
    "unsupported_language": "[NON-ENGLISH MESSAGE BLOCKED]:",
}


def build_restore_message(payload: dict[str, Any]) -> str:
    """Format the V2 automatic restore prompt."""
    snapshot = payload["resume_snapshot"]
    decisions_by_id = {
        decision["id"]: decision for decision in payload.get("decision_register", [])
    }
    state = _build_restore_state(
        snapshot,
        decisions_by_id,
        restore_session_id=payload.get("restore_session_id"),
        include_user_context=True,
    )
    return _render_restore_message_verbose(state)


def build_restore_message_compact(
    payload: dict[str, Any], *, restore_session_id: str | None = None
) -> str:
    """Format the V2 restore message as a compact machine-oriented continuation block.

    This produces a structured <compact-restore> block that provides all necessary
    state for task continuation without verbose prose or retrospective sections.
    """
    snapshot = payload["resume_snapshot"]
    decisions_by_id = {
        decision["id"]: decision for decision in payload.get("decision_register", [])
    }
    state = _build_restore_state(
        snapshot,
        decisions_by_id,
        restore_session_id=restore_session_id,
        include_user_context=False,
    )
    return _render_restore_message_compact(state)


def build_restore_message_dynamic(
    payload: dict[str, Any], *, restore_session_id: str | None = None
) -> str:
    """Format the V2 restore message using dynamic sections.

    DEPRECATED: This produces Pre-Mortem format which is wrong for restore.
    Use build_restore_message_compact() for continuation blocks instead.
    """
    # For now, delegate to compact format to avoid breaking existing callers
    return build_restore_message_compact(
        payload, restore_session_id=restore_session_id
    )


def build_stale_hint(payload: dict[str, Any], reason: str) -> str:
    """Format the stale or rejected snapshot notice."""
    snapshot = payload["resume_snapshot"]
    return "\n".join(
        [
            "HANDOFF NOT RESTORED",
            "",
            "No safe current handoff was restored for this session.",
            f"Reason: {reason}",
            f"Snapshot Created: {snapshot['created_at']}",
            f"Source Session: {snapshot['source_session_id']}",
            "A stale handoff exists and may be inspected manually if needed.",
        ]
    )


def build_no_snapshot_hint(reason: str) -> str:
    """Format the missing handoff notice."""
    return "\n".join(
        [
            "HANDOFF NOT RESTORED",
            "",
            "No safe current handoff is available for this session.",
            f"Reason: {reason}",
        ]
    )


def short_task_name(goal: str) -> str:
    """Derive a concise current task label from the goal."""
    cleaned = " ".join(goal.split()).strip()
    if not cleaned:
        return "Unknown task"
    return cleaned


def ensure_progress_state(
    blockers: list[dict[str, Any]], pending_operations: list[dict[str, Any]]
) -> str:
    """Infer a coarse progress state."""
    if blockers:
        return "blocked"
    if pending_operations:
        return "in_progress"
    return "ready"


def _extract_and_format_user_context(
    transcript_path: str, max_messages: int = 15, *, goal_text: str | None = None
) -> str | None:
    """Extract and format recent user messages from transcript for context injection.

    This function is called at RESTORE time (SessionStart or UserPromptSubmit)
    to inject recent user context into the restoration message. It reads the
    transcript, extracts user messages, and formats them concisely.

    Args:
        transcript_path: Path to the transcript JSONL file
        max_messages: Maximum number of user messages to extract (default: 15)

    Returns:
        Formatted string with recent user context, or None if extraction fails.
        Returns empty string if no user messages found.

    Edge cases handled:
    - Transcript file missing: Returns None, logs warning
    - Corrupted transcript entries: Skipped, continues with remaining entries
    - Very long messages: Truncated at 2000 chars with pointer to transcript
    - Session boundaries: Respected (stops at session_chain_id change)
    - Empty/short transcripts: Returns empty string (not None)
    """
    from scripts.hooks.__lib.transcript import gather_context_with_boundaries

    try:
        entries = gather_context_with_boundaries(
            transcript_path, max_messages=max_messages
        )
    except Exception as exc:
        # Log but don't fail - context injection is optional
        import logging

        logging.getLogger(__name__).warning(
            "[snapshot_v2] Failed to gather context from transcript: %s", exc
        )
        return None

    if not entries:
        return ""

    # Extract user messages only, in chronological order (entries are reversed)
    user_messages = []
    for entry in reversed(entries):
        if entry.get("type") != "user":
            continue

        # Extract message text from various entry formats
        message_text = ""
        if "message" in entry:
            message = entry["message"]
            if isinstance(message, str):
                message_text = message
            elif isinstance(message, dict):
                content = message.get("content", [])
                if isinstance(content, str):
                    message_text = content
                elif isinstance(content, list):
                    # Concatenate text content from list
                    text_parts = []
                    for item in content:
                        if isinstance(item, str):
                            text_parts.append(item)
                        elif isinstance(item, dict):
                            text_parts.append(item.get("text", ""))
                    message_text = " ".join(text_parts)

        message_text = message_text.strip()
        if not message_text:
            continue

        # Skip messages that duplicate the goal (already in work_state.goal)
        if goal_text and message_text.strip() == goal_text.strip():
            continue

        user_messages.append(message_text)

    if not user_messages:
        return ""

    # Format: show last 5 in full, summarize earlier ones
    lines = []
    if len(user_messages) > 5:
        lines.append(f"Recent Context ({len(user_messages)} user messages):")
        lines.append(f"... {len(user_messages) - 5} earlier messages omitted")
        for msg in user_messages[-5:]:
            lines.append(f"- {msg[:200]}{'...' if len(msg) > 200 else ''}")
    else:
        lines.append(f"Recent Context ({len(user_messages)} user messages):")
        for msg in user_messages:
            lines.append(f"- {msg[:200]}{'...' if len(msg) > 200 else ''}")

    return "\n".join(lines)

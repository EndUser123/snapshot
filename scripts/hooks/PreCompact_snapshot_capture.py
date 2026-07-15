#!/usr/bin/env python3
"""PreCompact capture hook for Handoff V2."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Configure logging to ensure diagnostic output is captured
# Logs will be written to P:\\\\\\.claude/.artifacts/snapshot/logs/handoff_capture.log
_log_root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or "P:/")
_log_file_path = (
    _log_root / ".claude" / ".artifacts" / "snapshot" / "logs" / "handoff_capture.log"
)
_log_file_path.parent.mkdir(parents=True, exist_ok=True)
_handler = RotatingFileHandler(
    _log_file_path, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
)
_handler.setFormatter(
    logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
)
logger.addHandler(_handler)
logger.setLevel(logging.DEBUG)


def _find_project_root(start: Path) -> Path:
    """Walk up from start to find the project root (directory containing .claude)."""
    return detect_project_root(current_dir=start, strict=False)


PACKAGE_ROOT = Path(__file__).resolve().parents[2]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

# Import V1 features for integration
from scripts.config import cleanup_old_handoffs
from scripts.hooks.__lib.snapshot_files import SnapshotFileStorage
from scripts.hooks.__lib.snapshot_v2 import (
    SnapshotValidationError,
    build_envelope,
    build_resume_snapshot,
    compute_file_content_hash,
    ensure_progress_state,
    make_decision_id,
    make_evidence_id,
)
from scripts.hooks.__lib.dynamic_sections import calculate_quality_score_dynamic
from scripts.hooks.__lib.project_root import detect_project_root
from scripts.hooks.__lib.hook_input_validation import (
    HookInputError,
    validate_hook_input,
)
from scripts.hooks.__lib.terminal_detection import resolve_terminal_key
from scripts.hooks.__lib.transcript import (  # type: ignore
    TranscriptParser,
    extract_last_substantive_user_message,
)
from scripts.hooks.__lib.transcript import (  # noqa: F401
    is_meta_discussion,
    is_clarification_message,
    extract_preceding_message,
)
from scripts.hooks.__lib.user_intent import capture_pending_questions

# Import local hooks utilities for MEMORY.md corrections ranking
_local_utils_path = Path.home() / ".claude" / "projects" / "P--" / ".claude" / "hooks" / "utils"
_utility_paths = [
    _local_utils_path,
    Path("P:/.claude/hooks/utils"),
]
for _utils_path in _utility_paths:
    if _utils_path.exists() and str(_utils_path) not in sys.path:
        sys.path.insert(0, str(_utils_path))

SESSION_PATTERNS = {
    "planning": [
        r"/plan-workflow",
        r"/arch",
        r"\bplan\b",
        r"\barchitecture\b",
        r"\bdesign\b",
    ],
    "debug": [r"\bfix\b", r"\bbug\b", r"\berror\b", r"\bfail", r"\bcrash\b"],
    "feature": [r"\bimplement\b", r"\bbuild\b", r"\bcreate\b", r"\badd\b"],
    "test": [r"\btest\b", r"\bverify\b", r"\bcoverage\b"],
    "docs": [r"\bdocument\b", r"\breadme\b", r"\bexplain\b"],
}
SESSION_EMOJIS = {
    "planning": "📋",
    "debug": "🐛",
    "feature": "✨",
    "test": "🧪",
    "docs": "📝",
    "general": "📍",
}

# The handoff must remain useful under pressure.  A slash-command expansion can
# place tens of thousands of characters in the last user message; the goal,
# decisions, files, and next step are captured separately below, so retaining
# the entire expansion only bloats the recovery artifact.
MAX_LAST_USER_MESSAGE_CHARS = 12_000


def _bound_last_user_message(message: str | None) -> str | None:
    """Keep a bounded preview while preserving both command and tail context."""
    if not message:
        return None
    if len(message) <= MAX_LAST_USER_MESSAGE_CHARS:
        return message
    marker = f"\n\n[truncated for handoff size bound: {len(message)} characters]\n\n"
    available = max(0, MAX_LAST_USER_MESSAGE_CHARS - len(marker))
    head_chars = available * 2 // 3
    tail_chars = available - head_chars
    return (
        message[:head_chars]
        + marker
        + message[-tail_chars:]
    )
DECISION_PATTERNS = [
    (
        re.compile(r"\bmust\b|\bdo not\b|\bdon't\b|\bnever\b", re.IGNORECASE),
        "constraint",
    ),
    (
        re.compile(
            r"\bdecided to\b|\bdecision:\b|\bgoing with\b|\bchose\b", re.IGNORECASE
        ),
        "settled_decision",
    ),
    (
        re.compile(r"\bwaiting for approval\b|\bawaiting approval\b", re.IGNORECASE),
        "blocker_rule",
    ),
    (re.compile(r"\bavoid\b|\bshould not\b", re.IGNORECASE), "anti_goal"),
]


def _build_transcript_chain(
    current_path: str, old_snapshot: dict | None, max_chain: int = 20
) -> list[str]:
    """Build ordered list of transcript paths, newest first, deduped, capped."""
    chain = [current_path]
    if old_snapshot:
        old_chain = old_snapshot.get("transcript_chain", [])
        chain.extend(old_chain)
    seen, result = set(), []
    for p in chain:
        if p not in seen:
            result.append(p)
            seen.add(p)
    return result[:max_chain]


def _extract_active_skill(raw_last_user: str) -> str:
    """Extract skill name from prompt like '/cc-skills-sdlc:design args' or '/skill args'."""
    if not raw_last_user:
        return ""
    prompt = raw_last_user.strip()
    m = re.match(r"^/([a-zA-Z0-9_-]+(?:[:/][a-zA-Z0-9_-]+)?)\b", prompt)
    if m:
        return m.group(1)
    return ""


def _extract_recent_corrections(
    goal: str, active_files: list[str], max_count: int = 3
) -> list[str]:
    """Extract ranked corrections from user's MEMORY.md.

    Uses the same ranking logic as the local PreCompact.py.
    """
    try:
        from reminder_state import read_memory_md
        from correction_ranker import rank_corrections

        memory_corrections = read_memory_md()
        if not memory_corrections:
            return []
        recent_corrections = rank_corrections(
            memory_corrections,
            goal=goal,
            active_files=active_files,
            last_action="",
            pending_work=[],
            top_n=max_count,
        )
        return recent_corrections
    except Exception as exc:
        logger.warning("[PreCompact V2] Failed to extract recent corrections: %s", exc)
        return []


def detect_session_type(user_message: str, active_files: list[str]) -> tuple[str, str]:
    """Infer a coarse session type from the active request."""
    haystack = " ".join([user_message, *active_files]).lower()
    best_match = "general"
    best_score = 0
    for session_type, patterns in SESSION_PATTERNS.items():
        score = sum(
            1 for pattern in patterns if re.search(pattern, haystack, re.IGNORECASE)
        )
        if score > best_score:
            best_match = session_type
            best_score = score
    return best_match, SESSION_EMOJIS.get(best_match, "📍")


# CREATE vs IMPLEMENT task mode patterns
# Distinguishes creating new artifacts from implementing/fixing existing ones
CREATE_PATTERNS = [
    re.compile(r"^\s*(?:create|write|add|new)\b", re.IGNORECASE),
    re.compile(
        r"\b(?:create|write|add)\s+(?:an?\s+)?(?:new\s+)?(?:adr|artifact|document|file|module|component)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:create|make|build)\s+(?:an?\s+)?(?:new\s+)?(?:skill|hook|agent|system)\b",
        re.IGNORECASE,
    ),
]
IMPLEMENT_PATTERNS = [
    re.compile(r"^\s*(?:implement|fix|repair|resolve)\b", re.IGNORECASE),
    re.compile(r"\b(?:implement|fix|repair|resolve|debug)\s+", re.IGNORECASE),
    re.compile(
        r"\b(?:refactor|update|modify|change|improve|enhance|optimize)\s+(?:the\s+)?",
        re.IGNORECASE,
    ),
]


def detect_task_mode(user_message: str, active_files: list[str]) -> str:
    """Detect whether task is CREATE (new artifact) or IMPLEMENT (existing work).

    Distinguishes between:
    - CREATE: Making new artifacts (ADR, documentation, new features, skills, hooks)
    - IMPLEMENT: Fixing, refactoring, improving existing code/features
    - none: Cannot determine or not applicable

    Args:
        user_message: The user's goal message
        active_files: List of active file paths

    Returns:
        "create", "implement", or "none"
    """
    haystack = " ".join([user_message, *active_files]).lower()
    c_score = sum(1 for p in CREATE_PATTERNS if p.search(haystack))
    i_score = sum(1 for p in IMPLEMENT_PATTERNS if p.search(haystack))
    if c_score > i_score:
        return "create"
    elif i_score > c_score:
        return "implement"
    return "none"


def detect_lifecycle_phase(
    blockers: list[dict[str, Any]],
    active_files: list[str],
    pending_operations: list[dict[str, Any]],
    goal: str,
    task_mode: str = "none",
) -> str:
    """Detect conversation lifecycle phase from already-extracted data.

    Returns one of: "discussing", "planning", "implementing".
    Default is "implementing" (preserves current behavior).

    Note: "approved" and "reviewing" are declared in VALID_LIFECYCLE_PHASES
    but are not produced by this function. They are reserved for future
    JSONL-based detection (Phase 2) or UserPromptSubmit hook detection.
    """
    if not goal or not goal.strip():
        # Edge case: empty goal with no other signals → discussing
        return "discussing"

    # If awaiting_approval blocker exists, session is in planning
    if any(b.get("type") == "awaiting_approval" for b in blockers):
        return "planning"

    has_pending = bool(pending_operations)

    # If pending operations exist with no blockers, implementing
    if has_pending:
        return "implementing"

    # No pending ops — check if goal ends with question mark
    if goal.strip().endswith("?"):
        return "discussing"

    # Use task_mode as override signal:
    # If task_mode indicates active implementation work, trust it over
    # the absence of pending_operations (handles early-compact scenario)
    if task_mode in ("implement", "create") and any(active_files):
        return "implementing"

    # No edits, no pending ops, no clear implementation signal → discussing
    return "discussing"


def detect_planning_session(
    user_message: str, active_files: list[str]
) -> dict[str, Any] | None:
    """Return an explicit planning blocker if the session is in approval state."""
    del active_files
    lowered = user_message.lower()
    if any(token in lowered for token in ["/plan-workflow", "/arch"]) or (
        "plan" in lowered and "implement" not in lowered
    ):
        return {
            "type": "awaiting_approval",
            "summary": "Plan exists but requires user approval before implementation.",
        }
    return None


def _read_hook_input() -> dict[str, Any]:
    raw = sys.stdin.read().strip()
    if not raw:
        raise ValueError("PreCompact hook received empty stdin")
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("PreCompact hook input must be a JSON object")
    return payload


def _extract_active_files(parser: TranscriptParser) -> list[str]:
    files: list[str] = []
    try:
        # First, extract from Edit operations (modifications)
        for modification in parser.extract_modifications(limit=20):
            path = modification.get("file")
            if isinstance(path, str) and path not in files:
                files.append(path)

        # Second, scan all tool_use entries for file-related operations
        # This captures Read, Edit, Write, and other file tools even if no Edit completed
        for entry in parser._get_parsed_entries():
            # Extract tool_use content blocks from message.content array
            # Transcript structure: entry.message.content is a list of content blocks
            msg_obj = entry.get("message", {})
            if not isinstance(msg_obj, dict):
                continue

            content = msg_obj.get("content", [])
            if not isinstance(content, list):
                continue

            # Find tool_use blocks in content array
            for content_block in content:
                if not isinstance(content_block, dict):
                    continue
                if content_block.get("type") != "tool_use":
                    continue

                tool_name = content_block.get("name", "")
                tool_input = content_block.get("input", {})
                if not isinstance(tool_input, dict):
                    continue

                # Extract file path from specific tools based on their input schema
                file_path = None
                if tool_name == "Read":
                    file_path = tool_input.get("file_path")
                elif tool_name == "Edit":
                    file_path = tool_input.get("file_path")
                elif tool_name == "Write":
                    file_path = tool_input.get("file_path")
                elif tool_name in ("Grep", "Glob"):
                    # For search tools, capture the pattern but don't count as file
                    continue
                elif tool_name == "Bash":
                    # Skip bash commands (not file paths)
                    continue
                else:
                    # Fallback: check common file path keys
                    for key in ("file_path", "path", "target"):
                        value = tool_input.get(key)
                        if (
                            isinstance(value, str)
                            and ("/" in value or "\\" in value)
                            and not value.startswith(("http:", "https:", "git:"))
                        ):
                            file_path = value
                            break

                # Validate and add file path
                if isinstance(file_path, str) and file_path not in files:
                    # Exclude non-file paths (URLs, pure flags, etc.)
                    # Accept any path with separators that looks like a file
                    if (
                        any(sep in file_path for sep in ("/", "\\"))
                        and not file_path.startswith(
                            ("http:", "https:", "git:", "ftp:")
                        )
                        and len(file_path) > 3  # Minimum reasonable path length
                    ):
                        files.append(file_path)

        return files[:10]
    except Exception as exc:
        logger.warning("[PreCompact V2] Failed to extract active files: %s", exc)
        return files[:10]


def _normalize_pending_operations(parser: TranscriptParser) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    try:
        for operation in parser.extract_pending_operations()[:5]:
            normalized.append(
                {
                    "type": operation.get("type", "command"),
                    "target": operation.get("target", "unknown"),
                    "state": operation.get("state", "in_progress"),
                }
            )
    except Exception as exc:
        logger.warning("[PreCompact V2] Failed to extract pending operations: %s", exc)
    return normalized


def _extract_slash_command_goal(
    raw_last_user: str | None,
    active_files: list[str],
) -> tuple[str, str] | None:
    """If the last user message is a slash command, return (goal, goal_origin).

    Covers three cases:
    - Explicit args  → ("/cmd arg", "slash_command_with_args")
    - No args + active_files → ("/cmd [inferred subject: <file>]", "slash_command_inferred_subject")
    - No args + no files    → ("/cmd", "slash_command_bare")

    Returns None when raw_last_user is not a slash command.
    """
    # Only match against the first line — SKILL.md content follows on
    # subsequent lines and must not be captured as command args.
    first_line = (raw_last_user or "").strip().split("\n", 1)[0]
    match = re.match(
        r"^(/[a-z][a-z0-9_-]*(?::[a-z][a-z0-9_-]+)?)(\s+(.+))?$",
        first_line,
    )
    if not match:
        return None
    cmd_name = match.group(1)
    explicit_args = (match.group(3) or "").strip()
    if explicit_args:
        return f"{cmd_name} {explicit_args}", "slash_command_with_args"
    if active_files:
        return f"{cmd_name} [inferred subject: {active_files[0]}]", "slash_command_inferred_subject"
    return cmd_name, "slash_command_bare"


def _extract_last_assistant_text(parser: TranscriptParser) -> str:
    try:
        for entry in reversed(parser._get_parsed_entries()):
            if entry.get("type") == "assistant":
                text = parser._extract_text_from_entry(entry).strip()
                if text:
                    return text
    except Exception as exc:
        logger.warning("[PreCompact V2] Failed to read last assistant message: %s", exc)
    return ""


def _infer_next_step(
    last_assistant_text: str, pending_operations: list[dict[str, Any]], goal: str
) -> str:
    if pending_operations:
        operation = pending_operations[0]
        return f"(advisory) Previous session had pending: {operation.get('type', 'work')} on {operation.get('target', 'unknown')}."

    for line in last_assistant_text.splitlines():
        candidate = line.strip().lstrip("-*• ").strip()
        if len(candidate) >= 12 and not candidate.lower().startswith(
            ("here", "summary", "analysis")
        ):
            return f"(advisory) Previous session context: {candidate[:200]}"

    if goal:
        return f"(advisory) Previous session goal: {goal[:180]}"
    return "Ask the user what to work on next."


def _is_decision_noise(text: str) -> bool:
    """Check if text is noise that should not be captured as a decision.

    Filters out:
    - Skill definition headers ("Base directory for this skill:", "##", etc.)
    - User feedback/corrections ("You don't quite seem to be thinking...")
    - Code fragments and partial lines
    - Table/formatted content that's not a decision
    """
    if not text or not isinstance(text, str):
        return True

    text_lower = text.strip().lower()
    text_stripped = text.strip()

    # Skip skill definition headers
    skill_noise_patterns = [
        "base directory for this skill",
        "skill description:",
        "usage:",
        "examples:",
        "##",
        "###",
        "---",
        "===",
    ]
    for pattern in skill_noise_patterns:
        if pattern in text_lower:
            return True

    # Skip user feedback/corrections (second-person criticism)
    feedback_patterns = [
        "you don't ",
        "you didn't ",
        "you seem ",
        "you aren't ",
        "you're not ",
    ]
    for pattern in feedback_patterns:
        if pattern in text_lower:
            return True

    # Skip lines that start with markdown list markers (likely fragments)
    if re.match(r"^[\s]*(\-|\*|\+|\d+\.)[\s]+", text_stripped):
        # Allow if it's a complete sentence (has period at end)
        if not text_stripped.endswith("."):
            return True

    # Skip lines that are mostly punctuation/symbols (formatted content)
    symbol_ratio = sum(1 for c in text_stripped if c in "|[]{}<>+-=/\\_*#") / max(
        len(text_stripped), 1
    )
    if symbol_ratio > 0.3:
        return True

    # Skip very short fragments (less than 15 chars after stripping)
    if len(text_stripped) < 15:
        return True

    return False


def _build_decisions(
    parser: TranscriptParser, transcript_evidence_id: str
) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        # Only scan recent entries to avoid picking up old conversations
        # from previous sessions in compacted transcripts
        all_entries = parser._get_parsed_entries()
        recent_entries = all_entries[-200:] if len(all_entries) > 200 else all_entries

        for entry in recent_entries:
            if entry.get("type") not in {"assistant", "user"}:
                continue
            text = parser._extract_text_from_entry(entry).strip()
            if len(text) < 20:
                continue

            # Skip noise before pattern matching
            if _is_decision_noise(text):
                logger.debug(
                    "[PreCompact V2] Skipping decision noise: %s...", text[:50]
                )
                continue

            # Skip meta-discussion (conversational fragments about the system itself)
            if is_meta_discussion(text):
                logger.debug(
                    "[PreCompact V2] Skipping meta-discussion: %s...", text[:50]
                )
                continue

            for pattern, decision_kind in DECISION_PATTERNS:
                if not pattern.search(text):
                    continue
                summary = " ".join(text.split())
                if summary in seen:
                    break
                seen.add(summary)

                decisions.append(
                    {
                        "id": make_decision_id(),
                        "kind": decision_kind,
                        "summary": summary,
                        "details": summary,
                        "priority": "high"
                        if decision_kind in {"constraint", "blocker_rule"}
                        else "medium",
                        "applies_when": "Continue the current task after compact.",
                        "source_refs": [transcript_evidence_id],
                    }
                )
                break
            if len(decisions) >= 5:
                break
    except Exception as exc:
        logger.warning("[PreCompact V2] Failed to extract decisions: %s", exc)
    return decisions


def _resolve_evidence_path(path: str, project_root: Path) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = project_root / candidate
    return candidate.resolve()


def _make_portable_path(resolved_path: Path, project_root: Path) -> str:
    """Convert an absolute path to project-relative for cross-environment portability.

    Stores the path as a relative string from project_root, enabling restore to
    resolve it using the envelope's stored project_root (via detect_project_root at
    capture time) rather than the restore-time cwd. This ensures capture and restore
    use the same project root reference.
    """
    import os

    try:
        rel = resolved_path.relative_to(project_root)
        return os.fspath(rel)
    except ValueError:
        # Cannot make relative — store as absolute with normalized separators
        return os.fspath(resolved_path)


def _build_evidence_index(
    project_root: Path, transcript_path: str, active_files: list[str]
) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    transcript_id = make_evidence_id()
    resolved_transcript_path = _resolve_evidence_path(transcript_path, project_root)
    transcript_rel = _make_portable_path(resolved_transcript_path, project_root)
    evidence.append(
        {
            "id": transcript_id,
            "type": "transcript",
            "label": "Current compact transcript",
            "path": transcript_rel,
            "content_hash": compute_file_content_hash(resolved_transcript_path),
        }
    )
    for path in active_files[:5]:
        resolved_path = _resolve_evidence_path(path, project_root)
        portable_path = _make_portable_path(resolved_path, project_root)
        evidence_item: dict[str, Any] = {
            "id": make_evidence_id(),
            "type": "file",
            "label": Path(path).name or path,
            "path": portable_path,
        }
        content_hash = compute_file_content_hash(resolved_path)
        if content_hash:
            evidence_item["content_hash"] = content_hash
        evidence.append(evidence_item)
    return evidence


def _estimate_progress(
    blockers: list[dict[str, Any]], pending_operations: list[dict[str, Any]], goal: str
) -> int:
    if blockers and any(
        blocker.get("type") == "awaiting_approval" for blocker in blockers
    ):
        return 100
    if pending_operations:
        return 65
    if goal:
        return 35
    return 0


def run(input_data: dict[str, Any]) -> dict[str, Any]:
    """Capture the current session into a V2 handoff envelope.

    Args:
        input_data: JSON hook input from Claude Code.

    Returns:
        Dict following the Claude Code hook protocol.
    """
    try:
        validate_hook_input(input_data, hook_type="PreCompact")
        transcript_path = input_data.get("transcript_path")
        if not transcript_path:
            raise ValueError("PreCompact hook requires transcript_path")

        terminal_id = resolve_terminal_key(
            input_data.get("terminal_id"), input_data.get("session_id")
        )

        # CRITICAL: For snapshot package, detect project root with testing support
        env_project_root = os.environ.get("SNAPSHOT_PROJECT_ROOT")
        if env_project_root:
            project_root = Path(env_project_root)
            logger.info(
                f"[PreCompact V2] Using project root from environment: {project_root}"
            )
        else:
            # CLAUDE_PROJECT_DIR is the authoritative, cwd-independent project
            # root. Prefer it over walk-up: walk-up searches for a ".claude"
            # dir and can latch onto a leaked one inside a package tree.
            env_root = os.environ.get("CLAUDE_PROJECT_DIR")
            if env_root:
                project_root = Path(env_root)
                logger.info(
                    f"[PreCompact V2] Using project root from CLAUDE_PROJECT_DIR: {project_root}"
                )
            else:
                project_root = _find_project_root(Path.cwd())
                logger.info(
                    f"[PreCompact V2] Using project root from walk-up: {project_root}"
                )

        # CRITICAL: Validate transcript_path exists and is readable
        transcript_file = Path(transcript_path)
        if not transcript_file.exists():
            raise SnapshotValidationError(
                f"Transcript file does not exist: {transcript_path}"
            )
        if not transcript_file.is_file():
            raise SnapshotValidationError(
                f"Transcript path is not a file: {transcript_path}"
            )
        if "test" in transcript_file.name.lower():
            logger.error(
                "[PreCompact V2] Test transcript detected: %s", transcript_file.name
            )

        # Cleanup old handoffs before creating new one
        try:
            cleanup_old_handoffs(project_root)
        except Exception as exc:
            logger.warning("[PreCompact V2] Cleanup old handoffs failed: %s", exc)

        parser = TranscriptParser(transcript_path)
        active_files = _extract_active_files(parser)

        # Extract raw last user message first (before any filtering)
        raw_last_user = parser.extract_last_user_message()

        # Save original before possible nulling — needed for slash command extraction.
        # raw_last_user can contain SKILL.md content appended after the /command,
        # so we null it to avoid contaminating goal with skill definitions.
        # But _extract_slash_command_goal only extracts the command part via regex,
        # so passing the full message is safe.
        original_user_message = raw_last_user

        slash_match = re.match(
            r"^/[a-z][a-z0-9_-]*(?::[a-z][a-z0-9_-]+)?(?:\s+|--?\s)",
            (raw_last_user or "").strip(),
        )
        if slash_match:
            # raw_last_user is a skill invocation — skip it, rely on transcript
            raw_last_user = None
        else:
            raw_last_user = (raw_last_user or "").strip() or None

        goal_origin = "user_message"
        if raw_last_user:
            goal = raw_last_user
            goal_origin = "raw_user_message"
            message_intent = "instruction"
            logger.info(
                "[PreCompact V2] Raw user message captured as goal: %r (len=%d)",
                goal[:120] if goal else None,
                len(goal) if goal else 0,
            )
        else:
            slash_result = _extract_slash_command_goal(original_user_message, active_files)
            if slash_result:
                goal, goal_origin = slash_result
                message_intent = "instruction"
                logger.info(
                    "[PreCompact V2] Slash command captured as goal: %r, origin=%s",
                    goal,
                    goal_origin,
                )
            else:
                goal_result = extract_last_substantive_user_message(transcript_path)
                goal = goal_result.get("goal", "Unknown task")
                message_intent = goal_result.get("message_intent", "instruction")

        if not goal or goal == "Unknown task" or is_meta_discussion(goal):
            fallback_goal = parser.extract_last_user_message()
            if fallback_goal and is_meta_discussion(fallback_goal):
                goal = "Continue current task (meta-discussion filtered)"
            else:
                goal = fallback_goal or "Unknown task"
                message_intent = "instruction"

        skill_output = parser.extract_last_skill_output(max_length=800)
        skill_name_for_decision = None
        if skill_output:
            skill_name_for_decision = skill_output.get("skill_name", "unknown")
            if goal.lower().startswith("base directory for this skill:"):
                goal = f"Skill /{skill_name_for_decision} invoked - analyzing results"

        pending_operations = _normalize_pending_operations(parser)

        open_questions = None
        try:
            raw_transcript = Path(transcript_path).read_text(encoding="utf-8", errors="replace")
            result = capture_pending_questions(raw_transcript)
            if result:
                open_questions = [{"question": q["question"], "category": q["category"]} for q in result.get("questions", [])[:5]]
        except Exception:
            pass
        planning_blocker = detect_planning_session(goal, active_files)
        blockers = [planning_blocker] if planning_blocker else []
        progress_percent = _estimate_progress(blockers, pending_operations, goal)
        progress_state = ensure_progress_state(blockers, pending_operations)
        last_assistant_text = _extract_last_assistant_text(parser)
        next_step = _infer_next_step(last_assistant_text, pending_operations, goal)

        task_mode = detect_task_mode(goal, active_files)
        accumulated_lifecycle_phase = None
        try:
            storage_for_accum = SnapshotFileStorage(project_root, terminal_id)
            accumulated_events = storage_for_accum.read_accumulated_state()
            for event in reversed(accumulated_events):
                if event.get("type") == "phase_transition":
                    accumulated_lifecycle_phase = event.get("to")
                    break
        except Exception as exc:
            logger.debug("[PreCompact V2] Accumulated state read failed: %s", exc)

        if accumulated_lifecycle_phase:
            lifecycle_phase = accumulated_lifecycle_phase
        else:
            lifecycle_phase = detect_lifecycle_phase(
                blockers, active_files, pending_operations, goal, task_mode
            )

        preceding_task_context = ""
        if is_clarification_message(goal):
            preceding_msg = extract_preceding_message(transcript_path, goal)
            if preceding_msg:
                preceding_task_context = preceding_msg

        evidence_index = _build_evidence_index(
            project_root, transcript_path, active_files
        )
        transcript_evidence_id = evidence_index[0]["id"]
        decision_register = _build_decisions(parser, transcript_evidence_id)

        if skill_output and skill_name_for_decision:
            skill_decision = {
                "id": make_decision_id(),
                "kind": "skill_invocation",
                "summary": f"User ran /{skill_name_for_decision} skill",
                "details": f"Skill output: {skill_output.get('output', '')[:300]}",
                "priority": "high",
                "applies_when": "Continue the current task after compact.",
                "source_refs": [transcript_evidence_id],
            }
            decision_register.insert(0, skill_decision)

        quality_score = None
        try:
            dynamic_session_data = {
                "goal": goal,
                "active_files": active_files,
                "decision_register": decision_register,
                "known_issues": blockers,
                "final_actions": pending_operations,
                "has_errors": any(
                    b.get("type") == "awaiting_approval" for b in blockers
                ),
            }
            quality_score = calculate_quality_score_dynamic(dynamic_session_data)
        except Exception as exc:
            logger.warning(
                "[PreCompact V2] Dynamic quality score calculation failed: %s", exc
            )

        tasks_snapshot: list[dict[str, Any]] = []
        try:
            task_tracker_dir = project_root / ".claude" / "state" / "task_tracker"
            task_file_path = task_tracker_dir / f"{terminal_id}_tasks.json"
            if task_file_path.exists():
                with open(task_file_path, encoding="utf-8") as f:
                    task_data = json.load(f)
                tasks_snapshot = task_data.get("tasks", {}).get("task_list", [])
        except Exception as exc:
            logger.warning("[PreCompact V2] Failed to read task state: %s", exc)

        storage = SnapshotFileStorage(project_root, terminal_id)
        old_handoff = storage.load_raw_handoff(
            exclude_session_id=input_data.get("session_id")
        )
        n_2_transcript_path: str | None = None
        session_id = input_data.get("session_id", "")
        session_chain: list[str] = []
        old_snapshot: dict | None = None
        if old_handoff:
            old_snapshot = old_handoff["resume_snapshot"]
            n_2_transcript_path = old_snapshot["n_1_transcript_path"]
            prior_chain = old_snapshot.get("session_chain", [])
            if prior_chain and prior_chain[0] == old_snapshot.get("source_session_id"):
                session_chain = prior_chain + [session_id]
            else:
                session_chain = [old_snapshot.get("source_session_id", ""), session_id]
        else:
            session_chain = [session_id]

        transcript_chain = _build_transcript_chain(transcript_path, old_snapshot)
        active_skill = _extract_active_skill(raw_last_user or "")
        recent_corrections = _extract_recent_corrections(goal, active_files)

        # Capture prompt-enhancer artifact if present
        prompt_enhancement: dict[str, Any] | None = None
        try:
            from scripts.hooks.__lib.snapshot_v2 import VALID_ENHANCEMENT_FIELDS
            enhancement_path = (
                Path.home() / ".claude" / ".artifacts" / terminal_id / "prompt-enhancer" / "active_enhancement.json"
            )
            if enhancement_path.exists():
                with open(enhancement_path, encoding="utf-8") as f:
                    enhancement_data = json.load(f)
                # Validate and filter to allowed fields only
                prompt_enhancement = {
                    k: v for k, v in enhancement_data.items()
                    if k in VALID_ENHANCEMENT_FIELDS
                }
                logger.info(
                    "[PreCompact V2] Captured prompt_enhancement: %d fields",
                    len(prompt_enhancement),
                )
        except Exception as exc:
            logger.debug("[PreCompact V2] Prompt enhancement capture failed: %s", exc)

        resume_snapshot = build_resume_snapshot(
            terminal_id=terminal_id,
            source_session_id=input_data.get("session_id", ""),
            goal=goal,
            
            progress_percent=progress_percent,
            progress_state=progress_state,
            blockers=blockers,
            active_files=active_files,
            pending_operations=pending_operations,
            next_step=next_step,
            decision_refs=[decision["id"] for decision in decision_register],
            evidence_refs=[item["id"] for item in evidence_index],
            transcript_path=transcript_path,
            prior_transcript_path=n_2_transcript_path,
            message_intent=message_intent,
            quality_score=quality_score,
            tasks_snapshot=tasks_snapshot,
            open_questions=open_questions,
            goal_origin=goal_origin,
            active_skill=active_skill,
            session_chain=session_chain,
            last_user_message=_bound_last_user_message(raw_last_user),
            recent_corrections=recent_corrections,
            transcript_chain=transcript_chain,
            prompt_enhancement=prompt_enhancement,
        )
        envelope = build_envelope(
            resume_snapshot=resume_snapshot,
            decision_register=decision_register,
            evidence_index=evidence_index,
        )

        try:
            from scripts.hooks.__lib.parallel_capture import capture_all_parallel

            env_ctx = capture_all_parallel(project_root, "")
            env_ctx = {k: v for k, v in env_ctx.items() if v is not None}
            if env_ctx:
                envelope["environment_context"] = env_ctx
        except Exception as exc:
            logger.warning(
                "[PreCompact V2] Parallel capture failed (non-fatal): %s", exc
            )

        saved_path = storage.save_handoff(envelope)
        if not saved_path:
            raise SnapshotValidationError("failed to persist V2 handoff envelope")

        try:
            registry_path = Path("P:\\\\\\.claude/.artifacts/session_registry.jsonl")
            registry_path.parent.mkdir(parents=True, exist_ok=True)

            # Ensure terminal_id is in canonical format for registry
            # Normalize if needed (defensive - terminal_detection.py should handle this)
            registry_terminal_id = terminal_id
            if terminal_id and not terminal_id.startswith("console_"):
                # Normalize non-canonical formats to console_ format for registry writes
                if "-" in terminal_id:  # Looks like a UUID
                    registry_terminal_id = f"console_{terminal_id}"
                else:
                    registry_terminal_id = f"console_{terminal_id}"
                logger.warning(
                    "[PreCompact V2] Normalized non-canonical terminal_id format: %s -> %s",
                    terminal_id, registry_terminal_id
                )

            worktree = ""
            worktree_path = ""
            cwd_val = input_data.get("cwd", "")
            if cwd_val:
                m = re.search(r"\.claude[/\]worktrees[/\]([^/\]+)", cwd_val)
                if m:
                    worktree = m.group(1)
                    worktree_path = cwd_val[:m.end()].rstrip("/\\")

            entry = {
                "ts": datetime.now(UTC).isoformat(),
                "terminal_id": registry_terminal_id,
                "session_id": input_data.get("session_id", ""),
                "transcript_path": transcript_path,
                "goal": goal[:200],
                "progress_percent": progress_percent,
                "handoff_path": str(saved_path),
                "cwd": cwd_val,
                "worktree": worktree,
                "worktree_path": worktree_path,
            }
            with registry_path.open("a", encoding="utf-8") as rf:
                rf.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            print(f"session_registry append failed: {exc}", file=sys.stderr)

        try:
            marker_dir = project_root / ".claude" / "hooks" / "state"
            marker_dir.mkdir(parents=True, exist_ok=True)
            marker_path = marker_dir / f"compaction_marker_{terminal_id}.json"
            marker_payload = {
                "timestamp": time.time(),
                "handoff_path": str(storage.handoff_file),
            }
            with marker_path.open("w", encoding="utf-8") as fh:
                json.dump(marker_payload, fh)
        except Exception as exc:
            logger.warning("[PreCompact V2] Failed to write compaction marker: %s", exc)

        return {
            "decision": "approve",
            "reason": f"Captured Handoff V2 for terminal {terminal_id}",
            "additionalContext": (
                f"Saved V2 handoff snapshot.\n"
                f"Goal: {goal}\n"
                f"Next Step: {next_step}"
            ),
        }
    except HookInputError as exc:
        # Fail-open: snapshot is a recovery convenience, not a prerequisite
        # for compaction. Bad hook input shouldn't block /compact.
        logger.warning("[PreCompact V2] Hook input validation failed: %s", exc)
        return {
            "decision": "approve",
            "reason": f"Snapshot skipped (input invalid): {exc}",
            "additionalContext": f"⚠️ Handoff V2 skipped — hook input invalid: {exc}",
        }
    except SnapshotValidationError as exc:
        # Fail-open: a malformed envelope means we couldn't save THIS snapshot,
        # not that compaction itself should fail.
        logger.warning("[PreCompact V2] Envelope validation failed: %s", exc)
        return {
            "decision": "approve",
            "reason": f"Snapshot skipped (envelope invalid): {exc}",
            "additionalContext": f"⚠️ Handoff V2 skipped — envelope invalid: {exc}",
        }
    except Exception as exc:
        # Fail-open on all other capture errors. Defense in depth: even if a
        # future regression reintroduces a crash, /compact stays unblocked.
        logger.error("[PreCompact V2] Capture failed: %s", exc, exc_info=True)
        return {
            "decision": "approve",
            "reason": f"Snapshot skipped (non-fatal): {exc}",
            "additionalContext": f"⚠️ Handoff V2 capture failed but compaction continues: {exc}",
        }


def main() -> None:
    """CLI entry point."""
    try:
        input_data = _read_hook_input()
        result = run(input_data)
        print(json.dumps(result, indent=2))
        sys.exit(0 if result.get("decision") == "approve" else 1)
    except Exception as exc:
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": f"PreCompact CLI entry point failed: {exc}",
                }
            )
        )
        sys.exit(1)


if __name__ == "__main__":
    main()

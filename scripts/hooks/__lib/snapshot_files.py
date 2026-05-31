#!/usr/bin/env python3
"""File-based storage for the Handoff V2 envelope."""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
import os
import tempfile
from pathlib import Path
from typing import Any

from scripts.hooks.__lib.snapshot_store import FileLock, atomic_write_with_retry
from scripts.hooks.__lib.snapshot_v2 import (
    SnapshotValidationError,
    SNAPSHOT_PENDING,
    SNAPSHOT_REJECTED_STALE,
    compute_checksum,
    mark_snapshot_status,
    parse_iso8601,
    utcnow,
    validate_envelope,
)

logger = logging.getLogger(__name__)

# Configure logging for snapshot file operations
# Logs will be written to P:\\\\\\.claude/.artifacts/snapshot/logs/snapshot_files.log
_log_file_path = (
    Path(os.environ.get("CLAUDE_PROJECT_DIR", "P:")) / ".claude" / ".artifacts" / "snapshot" / "logs" / "snapshot_files.log"
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


class SnapshotFileStorage:
    """Persist one Snapshot V2 envelope per terminal."""

    def __init__(self, project_root: Path, terminal_id: str):
        self._validate_terminal_id(terminal_id)
        self.project_root = project_root
        self.terminal_id = terminal_id
        # Canonical artifacts root (matching global identity system)
        artifacts_root = Path("P:/.claude/.artifacts")
        self.handoff_dir = artifacts_root / terminal_id / "snapshot"
        self.handoff_file = self.handoff_dir / f"{terminal_id}_handoff.json"
        self._in_load = False

    @staticmethod
    def _validate_terminal_id(terminal_id: str) -> None:
        from scripts.hooks.__lib.validation_utils import validate_terminal_id
        validate_terminal_id(terminal_id)

    def _handoff_file_for_payload(self, payload: dict[str, Any]) -> Path:
        """Compute the handoff file path for a payload.

        Uses timestamp-based naming to support append semantics:
        Each PreCompact creates a new file rather than overwriting.
        File is named: {terminal_id}_{timestamp}_handoff.json

        The timestamp is extracted from the payload's created_at field
        (written at PreCompact time) so files sort correctly by mtime.
        """
        resume_snapshot = payload.get("resume_snapshot", {})
        created_at = resume_snapshot.get("created_at")

        if created_at:
            # ISO8601 timestamp -> filesystem-safe filename
            # 2026-04-09T12:00:00.000000 -> 20260409T120000
            # Malformed created_at falls back to strftime (IO-001 fix)
            try:
                parsed = parse_iso8601(created_at)
                ts_part = parsed.strftime("%Y%m%dT%H%M%S")
            except Exception:
                import time
                ts_part = time.strftime("%Y%m%dT%H%M%S%f")  # microsecond precision (SNAPSHOT-002)
        else:
            import time
            ts_part = time.strftime("%Y%m%dT%H%M%S%f")  # microsecond precision (SNAPSHOT-002)

        return self.handoff_dir / f"{self.terminal_id}_{ts_part}_handoff.json"

    def save_handoff(self, payload: dict[str, Any]) -> Path | bool:
        """Validate and persist the V2 payload.

        Returns:
            Path: the path the envelope was saved to (truthy, boolean-compatible)
            False: if the save failed
        """
        try:
            # Resolve target file path from payload (timestamp-based for append semantics)
            target_file = self._handoff_file_for_payload(payload)
            logger.debug(
                "[HandoffFileStorage] save_handoff called: terminal_id=%s, file=%s",
                self.terminal_id,
                target_file,
            )

            validate_envelope(payload)
            logger.debug("[HandoffFileStorage] Envelope validation passed")

            self.handoff_dir.mkdir(parents=True, exist_ok=True)
            logger.debug(
                "[HandoffFileStorage] Directory created/verified: %s", self.handoff_dir
            )

            serialized = json.dumps(payload, indent=2, ensure_ascii=False)
            logger.debug(
                "[HandoffFileStorage] Serialized envelope: %d bytes",
                len(serialized),
            )

            lock_file = target_file.with_suffix(".lock")
            with FileLock(lock_file, timeout=5.0) as lock:
                logger.debug(
                    "[HandoffFileStorage] Lock acquired for %s", lock_file.name
                )

                fd, temp_path = tempfile.mkstemp(
                    suffix=".tmp",
                    dir=str(self.handoff_dir),
                    prefix=f"{self.terminal_id}_handoff_",
                )
                logger.debug("[HandoffFileStorage] Temp file created: %s", temp_path)

                try:
                    # CRITICAL: Compute checksum from in-memory payload BEFORE any file write
                    # This prevents TOCTOU race condition and eliminates double I/O (PERF-001)
                    expected_checksum = payload.get("checksum")
                    if expected_checksum:
                        # Validate checksum from in-memory payload
                        computed_checksum = compute_checksum(payload)
                        if computed_checksum != expected_checksum:
                            logger.error(
                                "[HandoffFileStorage] Checksum mismatch before write: expected=%s, computed=%s",
                                expected_checksum,
                                computed_checksum,
                            )
                            return False
                        logger.debug(
                            "[HandoffFileStorage] Checksum validated from memory: %s",
                            computed_checksum,
                        )

                    # Write to temp file
                    with os.fdopen(fd, "w", encoding="utf-8") as handle:
                        handle.write(serialized)
                    logger.debug(
                        "[HandoffFileStorage] Wrote %d bytes to temp file",
                        len(serialized),
                    )

                    # Verify temp file integrity BEFORE atomic move (still within FileLock context)
                    # This prevents TOCTOU race condition (LOGIC-001)
                    try:
                        with open(temp_path, encoding="utf-8") as verify_handle:
                            temp_payload = json.load(verify_handle)
                        # Verify checksum from temp file
                        temp_checksum = compute_checksum(temp_payload)
                        if expected_checksum and temp_checksum != expected_checksum:
                            logger.error(
                                "[HandoffFileStorage] Checksum mismatch in temp file: expected=%s, actual=%s",
                                expected_checksum,
                                temp_checksum,
                            )
                            os.unlink(temp_path)
                            return False
                        logger.debug(
                            "[HandoffFileStorage] Temp file checksum verified: %s",
                            temp_checksum,
                        )
                    except (json.JSONDecodeError, OSError) as verify_exc:
                        logger.error(
                            "[HandoffFileStorage] Failed to verify temp file: %s",
                            verify_exc,
                        )
                        try:
                            os.unlink(temp_path)
                        except OSError:
                            pass
                        return False

                    # Atomic move (now safe because we verified within FileLock context)
                    atomic_write_with_retry(temp_path, target_file)
                    logger.info(
                        "[HandoffFileStorage] Handoff saved successfully: %s -> %s",
                        temp_path,
                        target_file,
                    )

                    # Verify file was actually created
                    if not target_file.exists():
                        logger.error(
                            "[HandoffFileStorage] File does not exist after atomic_write: %s",
                            target_file,
                        )
                        return False

                    file_size = target_file.stat().st_size
                    logger.info(
                        "[HandoffFileStorage] File verified: %s (%d bytes)",
                        target_file.name,
                        file_size,
                    )

                    return target_file
                except Exception as inner_exc:
                    logger.error(
                        "[HandoffFileStorage] Exception during file write: %s",
                        inner_exc,
                        exc_info=True,
                    )
                    try:
                        os.unlink(temp_path)
                    except OSError:
                        pass
                    raise
        except SnapshotValidationError as exc:
            logger.error(
                "[HandoffFileStorage] Invalid handoff payload: %s",
                exc,
                exc_info=True,
            )
            return False
        except Exception as exc:
            logger.error(
                "[HandoffFileStorage] Exception saving handoff: %s",
                exc,
                exc_info=True,
            )
            return False

    def load_handoff(self) -> dict[str, Any] | None:
        """Load and validate the current V2 payload."""
        if self._in_load:
            logger.warning("[HandoffFileStorage] Recursive load_handoff call prevented")
            return None
        self._in_load = True
        try:
            payload = self.load_raw_handoff()
            if not payload:
                return None
            validate_envelope(payload)

            # CRIT-006 FIX: Guard against stale pending handoffs.
            # Snapshots only get rejected at restore time, so expired pending handoffs
            # can accumulate in the state directory indefinitely. Mark them stale here
            # so they won't be returned as valid for restore.
            snapshot = payload["resume_snapshot"]
            snapshot_status = snapshot.get("status")
            if snapshot_status == SNAPSHOT_PENDING:
                expires_at = snapshot.get("expires_at")
                if not expires_at:
                    # Pending snapshot with no expires_at — treat as immediately stale.
                    # A snapshot without temporal bounds is invalid for restore.
                    reason = "pending snapshot has no expires_at (auto-rejected at load time)"
                    marked = mark_snapshot_status(
                        payload,
                        status=SNAPSHOT_REJECTED_STALE,
                        session_id="system",
                        reason=reason,
                    )
                    self.save_handoff(marked)
                    logger.info(
                        "[HandoffFileStorage] Auto-rejected pending handoff with no expires_at: %s",
                        self.handoff_file.name,
                    )
                    return None
                try:
                    if parse_iso8601(expires_at) < utcnow():
                        # Expired pending snapshot — mark as rejected_stale
                        reason = "snapshot expired while pending (auto-rejected at load time)"
                        marked = mark_snapshot_status(
                            payload,
                            status=SNAPSHOT_REJECTED_STALE,
                            session_id="system",
                            reason=reason,
                        )
                        self.save_handoff(marked)
                        logger.info(
                            "[HandoffFileStorage] Auto-rejected stale pending handoff: %s (%s)",
                            self.handoff_file.name,
                            reason,
                        )
                        return None
                except Exception as exc:
                    # Malformed expires_at — reject as stale rather than silently
                    # bypassing the expiration check and leaking expired handoffs.
                    reject_reason = f"expires_at parse failed: {exc}"
                    logger.warning(
                        "[HandoffFileStorage] Failed to parse expires_at %r: %s — rejecting as stale",
                        expires_at,
                        exc,
                    )
                    marked = mark_snapshot_status(
                        payload,
                        status=SNAPSHOT_REJECTED_STALE,
                        session_id="system",
                        reason=reject_reason,
                    )
                    self.save_handoff(marked)
                    return None

            snapshot_terminal = snapshot["terminal_id"]
            if snapshot_terminal != self.terminal_id:
                logger.warning(
                    "[HandoffFileStorage] Terminal mismatch in %s: expected %s, got %s",
                    self.handoff_file.name,
                    self.terminal_id,
                    snapshot_terminal,
                )
                return None
            return payload
        except SnapshotValidationError as exc:
            logger.error(
                "[HandoffFileStorage] Invalid handoff payload in %s: %s",
                self.handoff_file.name,
                exc,
            )
            return None
        except json.JSONDecodeError as exc:
            logger.error(
                "[HandoffFileStorage] JSON parse error in %s: %s",
                self.handoff_file.name,
                exc,
            )
            return None
        except Exception as exc:
            logger.error("[HandoffFileStorage] Exception loading handoff: %s", exc)
            return None
        finally:
            self._in_load = False

    def load_raw_handoff(
        self, exclude_session_id: str | None = None
    ) -> dict[str, Any] | None:
        """Load the most recent handoff payload without validation.

        Finds the latest handoff file for this terminal by mtime, since
        PreCompact overwrites the same file on each compaction. Uses the
        most-recently-modified file rather than a fixed filename to handle
        the append-by-mtime pattern.

        Args:
            exclude_session_id: If provided, skip any handoff whose
                source_session_id matches this value. This is needed when
                PreCompact calls load_raw_handoff() to find S_OLD's handoff
                — at that point S_NEW's handoff already exists on disk (just
                written), so mtime-sort would return S_NEW instead of S_OLD.
                Passing S_NEW's session_id excludes it from the result.
        """
        if not self.handoff_dir.exists():
            return None
        # Find all handoff files for this terminal, sorted by mtime descending
        pattern = f"{self.terminal_id}_*_handoff.json"
        candidates = list(self.handoff_dir.glob(pattern))
        if not candidates:
            # Fallback: try exact match (for HandoffFileStorage used without append semantics)
            if self.handoff_file.exists():
                candidates = [self.handoff_file]
            else:
                return None
        # Sort by mtime, newest first
        def _get_mtime(p: Path) -> float:
            try:
                return p.stat().st_mtime
            except OSError:
                return -1.0

        candidates.sort(key=_get_mtime, reverse=True)

        # If excluding, scan for the first handoff whose session_id differs.
        # This ensures we get S_OLD even when S_NEW's handoff was just written.
        if exclude_session_id is not None:
            for p in candidates:
                try:
                    with open(p, encoding="utf-8") as handle:
                        payload = json.load(handle)
                    sid = payload.get("resume_snapshot", {}).get("source_session_id", "")
                    if sid != exclude_session_id:
                        return payload
                except Exception as exc:
                    logger.warning(
                        "[HandoffFileStorage] Skipped handoff %s during exclude scan: %s",
                        p.name,
                        exc,
                    )
                    continue
            # No prior handoff found — fall through to return None (edge case:
            # truly first session, or all handoffs belong to the excluded session)
            return None

        newest = candidates[0]
        try:
            with open(newest, encoding="utf-8") as handle:
                payload = json.load(handle)
            if not isinstance(payload, dict):
                logger.error(
                    "[HandoffFileStorage] Raw handoff payload is not a dict in %s",
                    newest.name,
                )
                return None
            return payload
        except json.JSONDecodeError as exc:
            logger.error(
                "[HandoffFileStorage] JSON parse error in %s: %s",
                newest.name,
                exc,
            )
            return None
        except Exception as exc:
            logger.error("[HandoffFileStorage] Exception loading raw handoff: %s", exc)
            return None

    def update_snapshot_status(
        self, *, status: str, session_id: str, reason: str | None = None
    ) -> bool:
        """Load the current payload, update snapshot status, and persist it."""
        payload = self.load_handoff()
        if not payload:
            return False
        updated = mark_snapshot_status(
            payload, status=status, session_id=session_id, reason=reason
        )
        return self.save_handoff(updated)

    def update_snapshot_status_from_payload(
        self,
        payload: dict[str, Any],
        *,
        status: str,
        session_id: str,
        reason: str | None = None,
    ) -> bool:
        """Persist a status update starting from a raw payload."""
        updated = mark_snapshot_status(
            payload, status=status, session_id=session_id, reason=reason
        )
        return self.save_handoff(updated)

    def read_accumulated_state(self) -> list[dict[str, Any]]:
        """Read the per-terminal accumulated JSONL state.

        Returns list of events from the JSONL file, or empty list.
        Non-existent or corrupt files return empty list (non-fatal).
        """
        accum_path = self.handoff_dir / f"{self.terminal_id}_accumulated.jsonl"
        if not accum_path.exists():
            return []

        events: list[dict[str, Any]] = []
        try:
            with open(accum_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                        if isinstance(event, dict):
                            events.append(event)
                    except json.JSONDecodeError:
                        continue  # Skip malformed lines
        except OSError:
            return []

        return events

    def truncate_accumulated_state(self) -> bool:
        """Truncate the accumulated JSONL file (called on new session start)."""
        accum_path = self.handoff_dir / f"{self.terminal_id}_accumulated.jsonl"
        try:
            if accum_path.exists():
                accum_path.unlink()
            return True
        except OSError:
            return False

    def delete_handoff(self) -> bool:
        """Delete the per-terminal handoff file."""
        try:
            if self.handoff_file.exists():
                self.handoff_file.unlink()
            return True
        except OSError:
            return False

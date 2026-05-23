# Handoff Restore Failure Analysis

## Status

This document is historical analysis only. The live handoff system is Handoff V2 in:

- `P://packages/handoff/core/hooks/PreCompact_handoff_capture.py`
- `P://packages/handoff/core/hooks/SessionStart_handoff_restore.py`

The active storage format is a single per-terminal V2 envelope with:

- `resume_snapshot`
- `decision_register`
- `evidence_index`
- `checksum`

Do not use this document as the current restore contract. The authoritative contract is documented in:

- `P://packages/handoff/README.md`
- `P://packages/handoff/docs/HANDOFF_DATA_STRUCTURE_FIX.md`
- `P://packages/handoff/docs/HANDOFF_FIELD_NAMES.md`

## What Failed In The Old System

Before the V2 rewrite, post-compact restore failures came from a combination of issues:

- brittle transcript parsing
- legacy multi-structure handoff payloads
- restore-time branching across compatibility and fallback paths
- bugs in the restore hook itself

One concrete restore bug was an undefined variable in the old SessionStart path, which caused the restore hook to drop into an error path instead of injecting handoff context. Another quality issue was incomplete extraction of Claude-style message content when transcript items were stored as structured objects rather than plain strings.

## Why V2 Replaced The Old Path

The previous design relied on too much indirect reconstruction. It attempted to infer continuity from transcript mining, compatibility layers, and restore heuristics across multiple structures. That made the compact boundary fragile.

V2 replaces that with a smaller, deterministic system:

- one atomic handoff file per terminal
- one active snapshot per terminal
- no backward compatibility reads on the restore path
- no automatic fallback to older snapshots
- explicit freshness validation before restore
- status transitions on the single active snapshot

## Current V2 Behavior

PreCompact writes one envelope to:

- `P://.claude/state/handoff/{terminal_id}_handoff.json`

SessionStart restores only when all of the following are true:

- terminal matches exactly
- snapshot status is `pending`
- snapshot is unconsumed
- snapshot is still within the freshness window
- the startup event is an actual post-compact restore, not generic startup

If the snapshot is stale or invalid:

- the snapshot is marked rejected
- no stale task context is injected
- only a minimal metadata hint may be shown

## Notes For Future Debugging

When investigating restore failures, check these first:

1. The V2 handoff file exists for the current terminal.
2. `resume_snapshot.status` is still `pending`.
3. `created_at` and `expires_at` place the snapshot within the freshness window.
4. `terminal_id` in the file matches the current terminal.
5. The file checksum validates.
6. SessionStart input indicates a real post-compact restore event.

If those conditions hold and restore still fails, debug the current `core/hooks` implementation only.

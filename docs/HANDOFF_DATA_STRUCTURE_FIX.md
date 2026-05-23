# Handoff V2 Data Structure

## Current State

Handoff now uses a single V2 envelope persisted at:

`P://.claude/state/handoff/{terminal_id}_handoff.json`

The envelope contains:

```json
{
  "resume_snapshot": {},
  "decision_register": [],
  "evidence_index": [],
  "checksum": "sha256:..."
}
```

There is no active backward-compatibility restore path for older schemas.

## Why V2 Exists

The design goal is a compact boundary that is deterministic and easy to validate. V2 uses one authoritative resume structure and explicit restore acceptance rules instead of a multi-layered restore path.

## `resume_snapshot`

Required fields:

- `schema_version`
- `snapshot_id`
- `terminal_id`
- `source_session_id`
- `created_at`
- `expires_at`
- `status`
- `goal`
- `current_task`
- `progress_percent`
- `progress_state`
- `blockers`
- `active_files`
- `pending_operations`
- `next_step`
- `decision_refs`
- `evidence_refs`
- `transcript_path`

Mutable status metadata added during restore/rejection:

- `consumed_at`
- `consumed_by_session_id`
- `rejected_at`
- `rejected_by_session_id`
- `rejection_reason`

## `decision_register`

High-confidence decisions only:

- `constraint`
- `settled_decision`
- `blocker_rule`
- `anti_goal`

Each entry includes:

- `id`
- `kind`
- `summary`
- `details`
- `priority`
- `applies_when`
- `source_refs`

## `evidence_index`

Reference-only backing evidence:

- `file`
- `transcript`
- `test`
- `log`
- `git`

Each entry includes:

- `id`
- `type`
- `label`
- `path`

Optional locator fields may be added for things like transcript message ids, test names, commits, or line numbers.

## Restore Rules

Automatic restore requires:

- matching terminal id
- `status == pending`
- actual post-compact `SessionStart`
- snapshot still fresh

Default freshness window:

- 20 minutes

If accepted:

- inject V2 restore message
- mark snapshot `consumed`

If stale:

- inject metadata-only stale hint
- mark `rejected_stale`

If invalid checksum/schema or terminal mismatch:

- inject minimal rejection hint
- mark `rejected_invalid` when possible

If generic startup:

- inject no restore context
- do not consume the snapshot

## Non-Goals

The active V2 path does not use:

- legacy restore traversal
- compatibility-specific restore behavior
- capture caching on the core path
- parallel capture on the core path
- automatic fallback to older snapshots

## Source Of Truth

Implementation files:

- [`PreCompact_handoff_capture.py`](/P://packages/handoff/core/hooks/PreCompact_handoff_capture.py)
- [`SessionStart_handoff_restore.py`](/P://packages/handoff/core/hooks/SessionStart_handoff_restore.py)
- [`handoff_v2.py`](/P://packages/handoff/core/hooks/__lib/handoff_v2.py)
- [`handoff_files.py`](/P://packages/handoff/core/hooks/__lib/handoff_files.py)

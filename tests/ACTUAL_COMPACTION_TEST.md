# Actual Compaction Test Instructions

## Purpose

Validate the live Handoff V2 compact/resume path with a real compaction event in Claude Code.

## Preconditions

- The active hook entrypoints in `$CLAUDE_ROOT/hooks` should be symlinks to `P://packages/handoff/core/hooks`.
- Use the same terminal before and after compaction.
- Evaluate only the current V2 resume behavior documented in this package.

## Procedure

### 1. Start a real session

Open a terminal in the target workspace and start work on a concrete task.

Example:

```text
Implement the Handoff V2 restore policy and keep the post-compact payload minimal.
```

Let the assistant perform several reads/edits so the transcript contains:

- a clear user goal
- at least one active file
- at least one pending operation or explicit next step

### 2. Trigger compaction

Use natural compaction or a manual compaction mechanism if available.

### 3. Start the next session in the same terminal

This should trigger `SessionStart_handoff_restore.py`.

### 4. Inspect the injected context

Successful restore should look like this shape:

```text
SESSION HANDOFF V2

Goal: ...
Current Task: ...
Progress: 65% (in_progress)
Active Files:
- ...
Pending Operations:
- ...
Next Step: ...
Transcript: ...
Active Decisions:
- [constraint] ...
```

Stale or invalid restore should look like this shape:

```text
HANDOFF NOT RESTORED

No safe current handoff was restored for this session.
Reason: ...
```

If the snapshot is stale, the hint may also include:

- `Snapshot Created: ...`
- `Source Session: ...`

## What To Validate

### Fresh restore

- `Goal` matches the substantive user request
- `Current Task` is short and coherent
- `Active Files` are from the prior session
- `Pending Operations` are relevant and not fabricated
- `Next Step` is actionable
- no stale fallback content appears

### Generic startup

- no task context is injected
- the snapshot remains pending

### Stale snapshot

- no old task context is injected
- only stale metadata is shown
- snapshot is marked `rejected_stale`

### Invalid checksum or terminal mismatch

- no task context is injected
- minimal rejection notice only
- snapshot is marked `rejected_invalid` when rewrite is possible

## Success Criteria

- [ ] Fresh post-compact resume injects V2 restore message
- [ ] Generic startup does not consume or restore the snapshot
- [ ] Stale snapshot produces metadata-only stale hint
- [ ] Invalid checksum produces minimal rejection hint
- [ ] No automatic fallback to an older snapshot occurs
- [ ] Assistant continues from the restored next step instead of asking what it was doing

## Supporting Tests

Useful local checks:

```bash
pytest P://packages/handoff/core/tests/test_handoff_hooks.py -q
pytest P://packages/handoff/tests/test_terminal_isolation.py -q
pytest P://packages/handoff/tests/test_canonical_goal_extraction.py -q
pytest P://packages/handoff/tests/test_variable_shadowing_fix.py -q
```

## Notes

- The handoff file is per-terminal: `P://.claude/state/handoff/{terminal_id}_handoff.json`
- Freshness defaults to 20 minutes
- Evidence is reference-only; the automatic restore path injects only the V2 resume payload

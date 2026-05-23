# Handoff Skill Invocation Tracking

## Status

Skill invocation tracking is optional supporting metadata, not part of the core Handoff V2 restore contract.

The live handoff implementation is in:

- `P://packages/handoff/core/hooks/PreCompact_handoff_capture.py`
- `P://packages/handoff/core/hooks/SessionStart_handoff_restore.py`
- `P://packages/handoff/core/hooks/__lib/transcript.py`

The core V2 restore payload remains:

- `resume_snapshot`
- `decision_register`
- `evidence_index`

Skill usage may inform `decision_register` or `evidence_index`, but it should not become a large restore blob or a hard dependency for successful resume.

## Purpose

If available, skill invocation tracking helps answer:

- which workflows or tools were used before compact
- whether a prior decision was grounded in a specific skill-driven step
- which transcript locations or surrounding messages justify that context

This is traceability support, not the main resume state.

## Current Guidance

If skill invocations are captured at all, they should be treated as evidence or lightweight context:

- store them as references, not as a large custom restore section
- keep extraction best-effort only
- never block PreCompact or SessionStart on missing skill metadata
- never rely on skill history to determine whether a snapshot is safe to restore

## Transcript Extraction Notes

Any extraction must work with Claude-style transcript content, including:

- plain string content
- list content containing text items
- list content containing structured dict items such as `{"type": "text", "text": "..."}`

That logic belongs in:

- `P://packages/handoff/core/hooks/__lib/transcript.py`

If extraction fails:

- continue writing the V2 handoff file
- omit the optional skill-related evidence
- log the failure for diagnosis

## How This Fits V2

A safe pattern is:

1. Capture the core resume state first.
2. Add optional evidence references if skill usage materially supports a decision or constraint.
3. Keep SessionStart output focused on the core resume payload.

Example shape:

```json
{
  "decision_register": [
    {
      "id": "dec_skill_001",
      "kind": "settled_decision",
      "summary": "Continue using the established handoff workflow",
      "details": "Prior session used a package-specific workflow and the result should be continued, not restarted.",
      "priority": "high",
      "applies_when": "Resuming the same compacted task",
      "source_refs": ["ev_skill_001"]
    }
  ],
  "evidence_index": [
    {
      "id": "ev_skill_001",
      "type": "transcript",
      "label": "Skill invocation context",
      "path": "P://path/to/transcript.jsonl",
      "message_id": "example-message-id"
    }
  ]
}
```

## What Not To Do

Do not reintroduce the old behavior of treating skill history as a primary restore section with custom formatting requirements. V2 restore should stay focused on:

- current goal
- current task
- blockers
- active files
- pending operations
- next step
- explicit decisions and constraints

If skill usage matters, represent it through the decision and evidence layers instead of expanding the restore surface area.

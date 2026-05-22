# snapshot

Session snapshot and handoff system for Claude Code — ensures 100% work continuity across compactions and session transitions.

## Skills (3)

| Skill | Purpose | Home |
|-------|---------|------|
| /snapshot | Session snapshot capture and restore | `snapshot/` |
| /track | Completion tracking and quality scoring | `track/` |
| /id | Session and terminal ID management | `id/` |

## Artifacts Convention

All runtime artifacts write to:

```
.claude/.artifacts/{terminal_id}/{skill_name}/
```

Skills MUST NOT write state to their own directory or to the package root.

## Installation

Skills surfaced via junctions in `P:/\.claude/skills/`.

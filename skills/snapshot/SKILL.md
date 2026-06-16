---
name: snapshot
version: "1.0.0"
status: "stable"
description: Session snapshot capture and restore system using snapshot package
category: documentation
enforcement: advisory
workflow_steps:
  - name: Capture
    trigger: "/compact (automatic)"
    description: "PreCompact hook captures session state before compaction"
  - name: Restore
    trigger: "/snapshot load"
    description: "SessionStart hook restores snapshot on new session start"
triggers:
  - /snapshot
aliases:
  - /snapshot
---

# Snapshot - Session Snapshot Capture and Restore

Session snapshot system for seamless LLM session continuity across compacts. **Automatic capture via PreCompact hooks, manual operations available via `python -m scripts.cli`.**

## Purpose

Generate session snapshots that ensure **100% work continuity** across Claude Code sessions, compacts, and agent transitions.

## Architecture

**Note**: V1 handoffs (pre-schema V2) are not automatically migrated and will be rejected at restore time. See SNAPSHOT-006.

### Implementation
- **Package**: `snapshot` at `P://packages/.claude-marketplace/plugins/snapshot`
- **Hooks**: PreCompact snapshot capture (automatic)
- **Skill**: Claude Code skill integration via `skill/SKILL.md`
- **Storage**: `P://.claude/state/task_tracker/`

### Hook-Only Architecture

| Feature | Implementation | Trigger |
|---------|---------------|---------|
| Automatic capture | PreCompact hook | Before /compact |
| Snapshot storage | JSON files | Automatic |
| Quality scoring | Built-in algorithm | Automatic |

**No manual CLI needed** - snapshot capture is fully automated.

## Your Workflow

Before compact:
1. `/snapshot detailed` - Document current work
2. Log critical decisions with ADRs (Architecture Decision Records)
3. Define next session objectives
4. `/snapshot quality` - Assess completeness

After compact:
1. `/snapshot load` - Restore previous context
2. Review decisions for continuity
3. Check quality score
4. Continue with prioritized objectives

**Critical Rule**: "Active Work At Snapshot" vs "Current Tasks"
- **Active Work At Snapshot**: ONLY work done in THIS session (files modified, tools executed)
- **Current Tasks**: Pending/in_progress tasks from TaskList (may include work from previous sessions)
- When creating snapshot: Verify session work before adding to "Active Work"

## Validation Rules

### Quality Scoring Algorithm
- 30% Completion Tracking
- 25% Action-Outcome Correlation
- 20% Decision Documentation
- 15% Issue Resolution
- 10% Knowledge Contribution

## Research Foundation

- **500+ files analyzed** for handover and continuity patterns
- **Industry best practices** from AI agent handover frameworks
- **Multi-vector architecture** for optimal context preservation
- **Quality metrics** based on completion tracking and decision documentation

## Quick Start

```bash
# Install the snapshot package
pip install -e P://packages/.claude-marketplace/plugins/snapshot

# That's it! Snapshot capture is fully automatic:
# - PreCompact hooks capture session state before /compact
# - Quality scoring is computed automatically
# - No manual invocation needed
```

## References

| File | Contents |
|------|----------|
| `references/quality-scoring.md` | Quality scoring weights, ratings, and breakdown |
| `references/core-features.md` | Work context structure, automated detection, scoring algorithm |
| `references/usage-patterns.md` | Automatic capture, manual CLI, session continuity workflow |
| `references/handover-template.md` | Full snapshot document template with examples |
| `references/retention-policy.md` | 90-day retention, auto-cleanup, data storage paths |

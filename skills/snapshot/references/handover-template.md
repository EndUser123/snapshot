# Handover Document Template

Full template for generating session handover documents.

```markdown
# Session Handover Document

## Session Metadata
- **Session ID**: session_20251115_143022
- **Quality Score**: 0.85/1.00
- **Timestamp**: 2025-11-15T14:30:22Z
- **Duration**: 2h 15m
- **Working Directory**: /path/to/project

## Original Request
**User Request**: "I'm getting super frustrated with stupid coding mistakes"
**Trigger**: User asked for research document from 6 external repos
**Context**: Wanted implementable options, not theory

## Session Objectives
**Complete task X** (completed, high)
**Document findings** (postponed, medium)

## Final Actions Taken
**Action A** (high priority)
**Action B** (high priority)

## Outcomes
**Success outcome** (success)
**Another success** (success)

## Active Work At Handoff
**Currently Working On**: Auto-skill activation implementation
   - Status: Implemented, tested, enabled
   - Files Modified: UserPromptSubmit_skill_router.py, UserPromptSubmit_router.py, settings.json
   - Next: Monitor for effectiveness

**CRITICAL**: "Active Work At Handoff" MUST only include work done in THIS session.
- Include ONLY if: files created/modified, tools executed, evidence of progress
- Do NOT copy from previous handovers or TaskList without verification
- If no active work in this session, state: "No active work in this session"

## Working Decisions (Critical for Continuity)
**Decision**: Use approach X over Y
   - **Rationale**: Reason here
   - **Impact**: High

## Current Tasks
**#611**: Task description (in_progress, high)
**#612**: Task description (pending, high)

## Known Issues
**ISSUE-1**: Description (observed, medium)
**ISSUE-2**: Description (observed, low)

## Open Questions
**Question**: Question text? (high, technical)

## Knowledge Contributions
**Insight**: Session continuity requires decision preservation (pattern)

## Next Immediate Action
1. Review research document at `P://docs/research/coding-mistake-prevention-research.md`
2. Test auto-skill with "debug this" prompt
3. Decide on build verification priority

## Continuation Instructions
1. **Priority Actions**: Address high-priority tasks first
2. **Critical Decisions**: Respect documented decisions
3. **Quality Target**: Maintain >0.8 session quality score
```

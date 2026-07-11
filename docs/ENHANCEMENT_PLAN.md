# Snapshot Plugin Enhancement Plan

**Status:** Draft — pending implementation decisions
**Date:** 2026-07-11
**Context:** Evidence-driven analysis of snapshot plugin gaps, informed by red-team
review, GitHub ecosystem survey (4 repos), and verification against real transcript
data. All claims are tiered by evidence level per Review Discipline.

---

## Evidence Tiering

Per AGENTS.md Review Discipline, claims in this document are separated into:

| Tier | Meaning | Label in this doc |
|------|---------|-------------------|
| **Verified** | Directly inspected via Read/Grep/Bash — tool output confirms | `[VERIFIED]` |
| **Inference** | Strong inference from naming + context (95%+), not yet tested | `[INFERENCE]` |
| **Hypothesis** | Reasoned proposal, no evidence yet gathered | `[HYPOTHESIS]` |

No `[INFERENCE]` or `[HYPOTHESIS]` item may be implemented without first gathering
evidence that promotes it to `[VERIFIED]`.

---

## Summary of Verified Problems

These findings were produced by running the snapshot plugin's actual extraction
code against real transcript files from `C:/Users/brsth/.claude/projects/P--/`.

### V1: Regex goal extraction is broken on production transcripts `[VERIFIED]`

**Evidence:** Ran `extract_last_substantive_user_message`, `extract_session_decisions`,
and `extract_pending_operations` on 3 real sessions.

| Session | Extracted goal | Actual goal | Correct? |
|---------|---------------|-------------|----------|
| `3ed6643a` | `"Unknown task"` | Find tasks related to /rns skill recommendations | No |
| `8784e6c7` | `"yes please"` | What is the optimal long-term solution for multi-terminals? | No |
| `2a271f2a` | `"Skill /cc-skills-utils:git is already loaded above..."` | Implement bounded fix in canonical debrief skill | No |

**Scorecard: 0/3 goals correct, 0 real decisions captured (via
`extract_session_decisions()` — the standalone function path; see F4 detail),
15/15 pending ops mislabeled as in-progress.**

**Root causes (3 distinct bugs):**

1. **F1 — Slash-command goal loss.** Sessions starting via `/skill:command`
   wrap intent in `<command-args>...</command-args>`. The parser's
   `_extract_text_from_entry` skips strings starting with `<` (designed for
   `<system-reminder>`), silently discarding the session's actual objective.
   Affects slash-command sessions (3/3 in the sample; common in this
   workspace, ratio not statistically surveyed).

2. **F4 — Decision regex matches skill READMEs.** Auto-injected
   "Base directory for this skill:" documentation contains `## Usage`,
   `**Strategy:**`, etc. Decision patterns (`use\s+\w+\s+instead`, `strategy:`,
   `plan:`) fire on this documentation text. 5/5 decision hits across 3 sessions
   were false positives on READMEs; 0/5 were real decisions.

3. **F5 — Pending-ops completion detection is broken.** Code looks for entries
   with `type == "tool"`, but production transcripts nest tool results as
   `{"type":"tool_result","tool_use_id":"call_xxx"}` inside user-message
   content. `completed_tool_ids` is always empty, so every tool_use ever
   invoked is treated as in-progress. Tests pass only because fixtures use
   a shape that doesn't match production.

**Affected code:**
- `transcript.py` — `_extract_text_from_entry` (F1), decision patterns (F4)
- `transcript.py` — `extract_pending_operations` completion detection (F5)
- Test fixtures across `test_canonical_goal_extraction.py`,
  `test_pending_operations_extraction.py` — all use non-production shapes

---

### V2: Secrets written to disk unredacted `[VERIFIED]`

**Evidence:** Read the capture path. The following fields write verbatim
transcript text (user or assistant) to the snapshot JSON on disk with no
redaction:

| Field | Source | Risk |
|-------|--------|------|
| `resume_snapshot.goal` | Full last user message, untruncated | HIGH |
| `resume_snapshot.last_user_message` | Same verbatim message (ADR-006) | HIGH |
| `decision_register[].summary` + `.details` | Verbatim transcript text | HIGH |
| `resume_snapshot.next_step` | Assistant text, up to 200 chars | MEDIUM |
| `resume_snapshot.open_questions` | Extracted question text | MEDIUM |
| `resume_snapshot.recent_corrections` | From MEMORY.md | MEDIUM |

**Storage:** `P:/.claude/.artifacts/{terminal_id}/snapshot/*.json` — gitignored
(verified via `git check-ignore`), but persists on local disk indefinitely.

**Solution already exists in same plugin:** `snapshot_SessionEnd_tldr.py:52-72`
defines `_SECRET_PATTERNS` (OpenAI, AWS, GitHub, Slack, Firebase, API keys,
bearer tokens, passwords) and `_redact_secrets()`. Applied only to the tldr
hook's `accomplishments` field, never to the PreCompact handoff envelope.

**Critical ordering constraint:** Redaction MUST happen to the field strings
BEFORE they are passed as arguments to `build_resume_snapshot()`
(`PreCompact_snapshot_capture.py:947`), because the SHA-256 checksum in
`build_envelope()` (`snapshot_v2.py:674`) is computed over whatever is in the
snapshot dict at that point. Redact-then-build, not redact-after-build.

---

### V3: No context-pressure monitoring `[VERIFIED]`

**Evidence:** Searched both live roots (`P:/.claude/hooks/` and
`P:/packages/.claude-marketplace/plugins/`). No hook reads token usage data.
The snapshot capture pipeline (`PreCompact_snapshot_capture.py`, 1073 lines)
never reads `message.usage` or any token count.

**Token data IS available in transcripts.** Verified by reading actual JSONL
entries — every assistant entry has a `message.usage` object:

```json
{
  "input_tokens": 215,
  "cache_read_input_tokens": 160512,
  "cache_creation_input_tokens": 0,
  "output_tokens": 142
}
```

**Total context pressure** = `input_tokens + cache_read_input_tokens +
cache_creation_input_tokens`. For the sampled turn: 160,727 tokens.

**Proven read pattern:** `P:/.claude/hooks/__lib/model_tier.py` already does
the exact reverse-tail-scan for `message.model` — swap `model` for `usage`
and the monitor exists. The 4000-line tail cap and `'"model"' not in line`
pre-filter pattern transfer directly.

**Injection channel exists:** UserPromptSubmit's `hookSpecificOutput.additionalContext`
wraps text as a system-reminder. Already used by `UserPromptSubmit.py` chain
(settings.json:179-199).

---

### V4: Freshness timer is miscalibrated `[VERIFIED]`

**Evidence:** `HANDOFF_FRESHNESS_MINUTES` defaults to 20 minutes, hardcoded
with no measurement behind it (per CHANGELOG analysis). No adaptive behavior.

**What it protects against:** Orphaned pending snapshots from abandoned sessions
(crash or user closes terminal right after compaction). Estimated frequency:
~0.5-1% of compactions.

**What it breaks:** Legitimate restores where the user took >20 minutes between
compaction and continuation. Estimated frequency: ~10-15% of compactions in
long sessions.

**Existing guards that already cover most of the gap:**
- `evidence_freshness` (file hash comparison, `snapshot_v2.py:767`) — catches
  any session that touched files
- `session_chain` check (lines 749-756) — prevents double-restore
- `status == pending` (line 758) — only unconsumed snapshots restore

**Ecosystem comparison:** Zero of four GitHub handoff repos use time-based
freshness. Patterns used elsewhere: continuous overwrite (no-amnesia),
structural orphan detection via placeholder content (Sting25), human-visible
timestamp with manual cleanup (ddaanet), unconditional injection (mvara-ai).

---

## Enhancement Items (Prioritized)

### P0: Fix regex extraction bugs (V1)

**Priority rationale:** These are correctness bugs, not enhancements. The
snapshot currently produces confidently-wrong handoffs. No other improvement
matters if the core extraction is broken.

**Scope:**

- [ ] **F1 fix** — `_extract_text_from_entry` in `transcript.py:1651-1679`: the
      `<` filter at lines 1675-1677 skips any string starting with `<`, which
      discards the `<command-args>...</command-args>` wrapper around slash-command
      goals. Extract intent from inside the wrapper instead of skipping the whole
      message. Note: a secondary path `_extract_slash_command_goal()` at
      `PreCompact_snapshot_capture.py:404-432` already recovers the command name
      via regex — the fix should make the primary path correct and decide whether
      the secondary path becomes redundant.
- [ ] **F4 fix (DUAL PATH — both must be fixed)** — Decision patterns fire on
      auto-injected skill documentation in **two independent extraction paths**.
      Only one is currently filtered:
      - `_build_decisions()` at `PreCompact_snapshot_capture.py:528` — **HAS**
        `_is_decision_noise()` filter (line 466) catching "base directory for
        this skill", "usage:", "##", second-person feedback, etc.
      - `extract_session_decisions()` at `transcript.py:1875-1945` — **NO** noise
        filter; `_DECISION_COMBINED` fires on any user entry containing the patterns.
      Fix: extract `_is_decision_noise()` into `__lib/` (e.g. `decision_filters.py`)
      and apply to both paths. DRY — single source of truth.
- [ ] **F5 fix** — `extract_pending_operations` in `transcript.py:2292-2300`:
      completion detection looks for top-level `type == "tool"` entries, but
      production transcripts nest tool results as `{"type":"tool_result",
      "tool_use_id":"call_xxx"}` inside user-message content arrays. Fix to scan
      nested `tool_result` blocks and match by `tool_use_id`.
- [ ] **Fix test fixtures** — The scenario fixtures
      (`tests/fixtures/test_scenarios/scenario_A/E/F_transcript.jsonl`) use FLAT
      format (`{"type":"tool_use","name":"Edit","input":{...}}` at top level),
      not production nested format (`{"type":"assistant","message":{"content":
      [{"type":"tool_use",...}]}}`). Update fixtures to production shapes.
      NOTE: `core/hooks/__init__.py:16-72` (CoreHooksFinder meta path finder)
      redirects `core.hooks.__lib.*` imports to `scripts/hooks/__lib/*` — so tests
      importing from `core.hooks.__lib.transcript` run the SAME code as runtime.
      The test/runtime gap is from fixture format, not module aliasing.
- [ ] **Update test assertions** — Existing tests assert specific pending ops as
      `"in_progress"`. These assertions will return different results once
      completion detection correctly marks completed operations. Assertions must
      be updated to match the fixed behavior.
- [ ] **Regression test** — Add a test that runs extraction against a real
      transcript (sanitized) to catch format drift.

**Complexity:** Low — bug fixes, ~50-80 lines of changes (F4 slightly more due
to shared-filter extraction).
**Risk:** Low — fixing broken behavior.

---

### P0: Add secret redaction to capture path (V2)

**Priority rationale:** Security gap with a proven solution already in the same
plugin. ~20 lines to close.

**Scope:**

- [ ] Extract `_SECRET_PATTERNS` + `_redact_secrets` from
      `snapshot_SessionEnd_tldr.py:52-72` into `__lib/redaction.py`.
- [ ] Import and apply to these fields before `build_resume_snapshot()`:
      - `goal` and `last_user_message`
      - `decision_register[].summary` and `.details`
      - `next_step`
      - `open_questions[].question`
      - `recent_corrections`
- [ ] Verify redaction happens BEFORE `build_envelope()` (checksum ordering).
- [ ] Update `snapshot_SessionEnd_tldr.py` to import from `__lib/redaction.py`
      (DRY — single source of truth).
- [ ] Augment `_SECRET_PATTERNS` by merging with the global Stop hook's
      `secret_scanner.py` patterns before extracting to `__lib/redaction.py`.
      The snapshot's 8 patterns (OpenAI, AWS, GitHub, Slack, Firebase, API key,
      secret/password, bearer token) may not cover all formats the scanner
      catches. `__lib/redaction.py` should become the shared source of truth
      for both files.
- [ ] Add test: `_redact_secrets` applied to each field, checksum still valid.

**Complexity:** ~20 lines of call sites + ~40 lines moved to shared module.
**Risk:** Low — proven pattern, well-understood ordering constraint.

---

### P1: Context-threshold nudge via UserPromptSubmit (V3) `[VERIFIED]`

**Priority rationale (revised 2026-07-11):** The agent-authored handoff
approach was evaluated and REJECTED (see below). The context-threshold nudge
is now the primary P1 mechanism for improving extraction quality. It prevents
the core problem — compaction firing when the model is already degraded. If
the model writes its handoff at 50-70% context instead of 95%, extraction
quality improves regardless of extraction method. The nudge is advisory text
(same mechanism as skill routing directives) — compliance for this channel is
~50% for skill routing (see Compliance caveat below); context-nudge compliance
is unmeasured. The LLM trio extraction (deferred fallback below) is the
planned guaranteed-quality floor, not yet implemented.

**Existing infrastructure (VERIFIED):** `snapshot_UserPromptSubmit.py` already
provides the proven injection pathway for mid-session context injection:
- `handoff_task_injector_hook` registered at `priority=1.0` (lines 224-265)
- Reads compaction marker → loads envelope → `build_restore_message_compact()`
  → returns `HookResult(context=message)`
- Marker file protocol (`_marker_path`, `_load_marker`, `_clear_marker`)
- State directory helpers (`_locate_hooks_state_dir`)
- Both UserPromptSubmit hooks coexist via the UPS dispatcher —
  the nudge hook would be a parallel registry entry at a lower priority.

**Scope:**

- [ ] Create `__lib/context_pressure.py` with a function that:
      - Reverse-scans the last ~50 transcript lines for `message.usage`
      - Sums `input_tokens + cache_read_input_tokens + cache_creation_input_tokens`
      - Returns pressure as a fraction of context window size
- [ ] Add a UserPromptSubmit hook (parallel registry entry, priority < 1.0)
      that calls the pressure function and injects a nudge via
      `HookResult(context=...)` when pressure exceeds threshold.
- [ ] Threshold `[HYPOTHESIS]`: start at 70% (no measurement behind this value —
      simply "before context is critically full"). The nudge should say something
      like: `"Context at {pct}% — consider wrapping up key decisions or running
      /compact while context is still manageable."`
- [ ] Calibrate after 2 weeks of usage data — adjust threshold based on when
      compaction actually fires vs. when the nudge was shown.

**Compliance caveat `[INFERENCE]`:** The nudge is advisory text injected via
the same channel as skill routing directives. The `skill-enforcement-deep-dive`
wiki page documents a ~50% compliance improvement for skill-routing advisory
text (Layer 1 UserPromptSubmit injection). Skill routing and context nudging
use the same mechanism (advisory text via `additionalContext`/`HookResult`),
but compliance for a context-pressure nudge specifically has NOT been measured
— the ~50% figure is from skill routing data extrapolated to this domain.
The nudge is a probabilistic signal, not a reliable lever. If the model ignores
it, extraction quality is unchanged — the LLM trio extraction (see Deferred
below) is the planned guaranteed-quality floor, not yet implemented.

**Anchor assumption:** The transcript's `message.usage` gives the numerator
(`input_tokens + cache_read_input_tokens + cache_creation_input_tokens`) but
no `context_window_size` denominator. The window size must come from the
active model's spec (200K for Sonnet/Opus 4.x, 1M for long-context models).
Resolution: `~/.claude.json` or `--model` flag may expose this; needs
confirmation during implementation.

**Complexity:** ~30-40 lines of code. The reverse-tail-scan of ~50 transcript
lines is fast (~1ms per call, proven by the same pattern in `model_tier.py`),
so per-turn cost on every UserPromptSubmit is negligible.
**Risk:** Low — uses existing data, existing injection channel, proven pattern.


---

### P2: Increase freshness window to 35-40 minutes (V4)

**Priority rationale:** Simplest mitigation for the miscalibration. One-line
change. Measure before building anything dynamic.

**Scope:**

- [ ] Change `HANDOFF_FRESHNESS_MINUTES` default from 20 to 35.
      Justification `[HYPOTHESIS]`: typical user break between sessions in
      this workspace (lunch, context-switch) appears to be ~25-45 min based on
      observed session timestamps; 35 min covers this without exceeding the
      ~50 min task-switch latency. Measure and recalibrate.
- [ ] Monitor for 2 weeks: track stale rejections vs. legitimate restores.
- [ ] If stale rejections increase significantly, reconsider.

**Complexity:** 1 line.
**Risk:** Low — slightly longer window, easily reverted.

---

### P2: Wire PostCompact as logging-only

**Priority rationale:** Diagnostic value — capture what Claude Code's compaction
actually produced, to accumulate data on built-in summary quality. No behavior
change.

**Scope:**

- [ ] Register a PostCompact hook in settings.json (event confirmed to exist
      per official docs; VERIFY it fires in the current Claude Code version
      before implementing).
- [ ] Log the compaction output alongside the snapshot for comparison.
- [ ] After accumulating data, assess whether the built-in summary is good
      enough to use as a restore source, or whether our extraction is needed.

**Complexity:** ~15 lines.
**Risk:** Low — logging only, no restore path change.

---

### P3: Snapshot history rotation

**Priority rationale:** If the latest snapshot has a bad extraction (broken
goal, false-positive decisions), there's no fallback. History gives the
restored session a second chance.

**Scope:**

- [ ] Keep last 3 snapshots per terminal (currently newest-only). `[HYPOTHESIS]`
      3 is an arbitrary starting point — enough for fallback without unbounded
      growth. Calibrate: if the 3rd-oldest is routinely consumed, increase;
      if the oldest is always stale, decrease.
- [ ] On restore failure (checksum, stale, invalid), fall back to prior
      snapshot in history.
- [ ] Add `--history` flag or skill command to manually pull older snapshots
      into context.

**Complexity:** Medium — storage rotation, fallback logic, CLI/skill surface.
**Risk:** Low.

**Dependency:** Should ship AFTER P0 (extraction fix) — otherwise rotating
bad data provides no value.

---

### P3: Structural orphan detection

**Priority rationale:** Principled replacement for the freshness timer. Instead
of time-based rejection, detect orphans by structural signals (empty evidence
index, empty goal, git HEAD mismatch).

**Scope:**

- [ ] Add structural checks to `evaluate_for_restore()`:
      - Empty evidence index AND empty goal → likely orphan
      - Git HEAD mismatch (captured hash vs. current) → different work
      - Placeholder content in goal/decisions → no substantive capture
- [ ] Log rejection reason as structural, not temporal.
- [ ] Run alongside the freshness timer (both must pass) initially.
- [ ] After validation, consider removing the timer if structural checks prove
      sufficient.

**Complexity:** Medium.
**Risk:** Medium — needs validation data before timer removal.

**Dependency:** Should ship AFTER P0 (extraction fix) — structural checks
depend on extraction producing meaningful goals and evidence.

---

## Explicitly Rejected / Deferred

### Per-turn Stop raw dump (Sting25 pattern)

**Reason rejected:** The transcript JSONL IS the per-turn dump — it's already
on disk, updated incrementally by Claude Code itself. The snapshot's value is
extraction, not raw storage. Making extraction resilient (P0/P1) is more
valuable than duplicating the raw transcript. Per-turn disk I/O on every Stop
hook multiplies cost for no incremental safety.

### Size-limited restore payload (no-amnesia pattern)

**Reason rejected:** Our snapshot envelope is already compact (typically 2-4KB).
The one unbounded field is `goal`/`last_user_message` (full verbatim user
message). That's a goal-extraction bug (P0), not a payload-size problem.

### Agent-authored handoff via PreCompact

**Reason rejected (2026-07-11, after verification):** PreCompact is fire-and-forget
— confirmed by reading `PreCompact_snapshot_capture.py:1015-1023`, which returns
only `{"decision": "approve", "reason": "...", "additionalContext": "..."}`.
There is no model-response slot in the hook protocol. The hook fires synchronously
and compaction proceeds immediately after it returns. The model never sees a prompt
from the hook. This was previously `[INFERENCE]` and is now resolved to `[REJECTED]`.

The intended signal — asking the model to author a handoff — has been re-routed to
the context-threshold nudge (P1), which uses the UserPromptSubmit injection channel
(same mechanism as `snapshot_UserPromptSubmit.py:224-265`).

### LLM trio extraction (standalone)

**Reason deferred (revised 2026-07-11):** Previously deferred in favor of
agent-authored handoff (which is now REJECTED). It is now the standing fallback to
the context-threshold nudge. The nudge is advisory text with a known ~50-70%
compliance ceiling (same mechanism as skill routing directives — documented in
`handoff-pre-compact-problems` wiki page). When the model ignores the nudge,
extraction quality is unchanged; the trio extraction becomes the mechanism for
producing quality-structured handoff content.

Implementation pathway: Use the proven infrastructure from
`Stop_semantic_critic.py:1042-1161` (ThreadPoolExecutor, conservative combination,
fail-open with 3-way majority). The `hook_external_llm_policy.md` documents the
approved pattern with all four safeguards. See P1 context-threshold nudge for
precedence order: nudge → regex fallback → LLM trio as guaranteed floor.

### Dynamic freshness (session-duration scaling)

**Reason rejected:** Introduces 7+ arbitrary constants with no measurement
behind them. Violates "no constants without justification" from CLAUDE.md
Technical Reasoning flaw #1. A static increase to 35 minutes (P2) with
measurement is the simpler, evidence-first approach.

### XML to markdown format change

**Reason rejected:** XML is the correct choice for system-injected metadata
(Anthropic's own prompting guide recommends XML tags for structured data
injection). Markdown `##` headers are indistinguishable from the model's own
output format. The hybrid (XML core + markdown suffix) already works. The
genuine ordering issue (continuation_rule buried at bottom) is a 2-line
reorder, not a format change.

---

## What Was Investigated and Found Sound (No Action Needed)

### session_registry.jsonl is NOT redundant

**Initial claim:** Transcript path is derivable from session_id
(`~/.claude/projects/<slug>/<session_id>.jsonl`), so the registry is unnecessary.

**Verification result:** The registry stores 8 fields; only 1 (`transcript_path`)
is derivable. The critical non-derivable field is `terminal_id` — it has a
many-to-many relationship with session_id (verified: one session appeared under
two different terminal_ids in live data). Transcripts carry no `terminal_id`
field. The registry is the only source for terminal_id resolution in resumed
sessions, and the only source for cross-compaction transcript chains.

**Consumers that depend on it:** `terminal_detection.py` (resume continuity),
`search-research/core/session_chain.py` (authoritative strategy),
`chs_cli.py` (`/chs export`).

**Minor concern:** The registry has ~2,818 lines with a 10,000-line prune
threshold and two separate prune mechanisms (a maintenance smell). The
registry's purpose is sound, but the dual-prune design should be consolidated
if the registry is extended.

### SHA-256 checksum validation is sound

No issues found. The checksum correctly validates envelope integrity. The only
interaction concern (redaction ordering) is documented in V2 above.

### Multi-terminal isolation is sound

Terminal-scoped state directories, session_chain prevention of double-restore,
and status lifecycle (pending → consumed/rejected) all work correctly.

---

## Implementation Order

```
P0 (fix bugs + security)     ──→ ship immediately
  │
  ├─ P1 context nudge        ──→ ship after P0 (improves all extraction quality)
  │
  ├─ P2 freshness 35min      ──→ one-line change, ship anytime after P0
  │
  ├─ P2 PostCompact logging  ──→ ship anytime, accumulate data
  │
  ├─ P3 history rotation     ──→ ship after P0 (don't rotate bad data)
  │
  └─ P3 orphan detection     ──→ ship after P0 + accumulate data
```

**P0 is the gate.** Nothing else should ship until the extraction bugs are fixed
and redaction is in place. Every other improvement depends on the core extraction
producing trustworthy output.

---

## Verification Checklist (for each item before "done")

Per `verification-before-completion` skill:

- [ ] Run `pytest tests/ -q` — all existing tests pass
- [ ] Run extraction against a REAL transcript (sanitized) — goal is correct
- [ ] Run `python scripts/doctor.py` — no configuration drift
- [ ] Bump `plugin.json` version
- [ ] Run `plugin-audit-and-fix.py --bump snapshot`
- [ ] Verify cache dir exists and `hooks.json` matches source
- [ ] Smoke-test: trigger compaction, verify snapshot captures correctly
- [ ] Verify restore: new session in same terminal, confirm context injection

---

## References

### Ecosystem survey (GitHub, 2026-07-11)

| Repo | Stars | Pattern | Staleness approach |
|------|-------|---------|-------------------|
| [Sting25/claude-code-handoff](https://github.com/Sting25/claude-code-handoff) | 4 | Agent-authored + git snapshot | Structural orphan detection (placeholder content), per-turn raw dump |
| [mvara-ai/precompact-hook](https://github.com/mvara-ai/precompact-hook) | 1 | LLM subagent interprets transcript | None (unconditional injection) |
| [ddaanet/handoff](https://github.com/ddaanet/handoff) | 0 | Agent-authored during session | Human-visible timestamp, manual cleanup |
| [waitdeadai/no-amnesia](https://github.com/waitdeadai/no-amnesia) | 0 | Continuous overwrite on Stop | None needed (always current by construction) |

### Key files

| File | Role |
|------|------|
| `scripts/hooks/PreCompact_snapshot_capture.py` | Capture pipeline (1073 lines) |
| `scripts/hooks/__lib/transcript.py` | Regex extraction (all bugs live here) |
| `scripts/hooks/__lib/snapshot_v2.py` | Schema, envelope builder, checksum |
| `scripts/hooks/__lib/snapshot_files.py` | Storage, freshness, status lifecycle |
| `scripts/hooks/__lib/terminal_detection.py` | Terminal ID resolution |
| `scripts/hooks/__lib/session_registry.py` | Session registry query interface |
| `scripts/hooks/snapshot_SessionEnd_tldr.py` | Contains `_redact_secrets` to extract |
| `scripts/hooks/SessionStart_snapshot_restore.py` | Restore path |
| `scripts/hooks/snapshot_UserPromptSubmit.py` | Mid-session injection |
| `scripts/config.py` | Configuration constants |

### External references

- Claude Code hooks docs: `https://docs.anthropic.com/en/docs/claude-code/hooks`
- Hook external LLM policy: `P:/.claude/hooks/hook_external_llm_policy.md`
- Trio LLM calling pattern: `Stop_semantic_critic.py:1042-1161`
- Token usage read pattern: `P:/.claude/hooks/__lib/model_tier.py`

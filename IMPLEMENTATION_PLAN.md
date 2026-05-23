# Implementation Plan: snapshot Package

**Package**: `P://packages/snapshot` — Session snapshot capture/restore for Claude Code  
**Current Version**: 0.5.0  
**Test Status**: 358 passed, 29 skipped, 14 warnings  
**Last Commit**: `6338428` — 2026-05-02  

---

## 0. Current State Assessment

### What snapshot Is
A Claude Code plugin (forked from `handoff`) that captures terminal session state before transcript compaction and restores it on session start. It provides continuity across compactions, multi-terminal workflows, and agent transitions.

### Architecture Summary
- **`scripts/hooks/`** — Authoritative source: 7 hook files + `__lib/` (18 modules, ~11,800 LOC)
- **`core/hooks/`** — Thin import redirect layer (MetaPathFinder) → maps old `handoff` imports to `snapshot` files in `scripts/`
- **`tests/`** — 46 test files (341 integration tests)
- **`scripts/tests/`** — 3 test files (17 unit tests)
- **`hooks/hooks.json`** — Plugin hook registration (PreCompact, SessionStart, SessionEnd, UserPromptSubmit)
- **`skills/`** — 3 skills (snapshot, id, track)

### Known Technical Debt

| # | Issue | Severity | Location |
|---|-------|----------|----------|
| 1 | **Incomplete rename: "handoff" → "snapshot"** throughout source | Medium | `scripts/hooks/__lib/snapshot_files.py` (61 refs), `snapshot_store.py` (37), `PreCompact_snapshot_capture.py` (32), `snapshot_v2.py` (10), `config.py` (23), `cli.py` (54), others |
| 2 | **Log files still named `handoff_*.log`** | Low | `PreCompact_snapshot_capture.py:21-23`, `SessionStart_snapshot_restore.py:30-32` |
| 3 | **`.github/workflows/` is empty** — no CI configured | High | `.github/` directory exists but has no workflows |
| 4 | **`core/hooks/__lib/` is empty** — dead import path | Low | `core/hooks/__lib/__init__.py` (77 bytes) |
| 5 | **Mixed state directory names** — config says `SNAPSHOT_DIR = .claude/handoffs` but files go to `.claude/state/handoff/` | Medium | `scripts/config.py` vs `scripts/hooks/__lib/snapshot_files.py` |
| 6 | **14 test warnings** (PytestReturnNotNone) | Low | 4 tests return bool instead of None |
| 7 | **`docs/` references old `src/` paths** | Low | `docs/improvements.md` references `src/handoff/hooks/` |
| 8 | **`plan-uci-fixes.md` is stale** — describes work already done | Low | Root directory |

---

## 1. Phase 1: Complete the Rename & Cleanup

**Goal**: Finish the `handoff` → `snapshot` rename so the codebase is self-consistent.  
**Effort**: ~2-3 hours  
**Risk**: Low (mechanical find/replace with test validation)

### Tasks

#### 1.1 Rename internal variables and log messages in `scripts/`
- [ ] `scripts/hooks/__lib/snapshot_files.py` — rename `handoff_dir`, `handoff_file`, `save_handoff()`, `load_handoff()`, `load_raw_handoff()`, log messages
- [ ] `scripts/hooks/__lib/snapshot_store.py` — rename `_validate_handoff_data_size()`, `calculate_quality_score()` handoff references, log messages
- [ ] `scripts/hooks/__lib/snapshot_v2.py` — rename error messages, docstrings, log messages
- [ ] `scripts/hooks/PreCompact_snapshot_capture.py` — rename log file path from `handoff_capture.log` → `snapshot_capture.log`, update all log messages
- [ ] `scripts/hooks/SessionStart_snapshot_restore.py` — rename log file from `handoff_restore.log` → `snapshot_restore.log`, update log messages
- [ ] `scripts/hooks/SessionEnd_tldr.py` — update handoff references
- [ ] `scripts/hooks/userpromptsubmit_task_injector.py` — update handoff references
- [ ] `scripts/config.py` — rename `cleanup_old_handoffs()` → `cleanup_old_snapshots()`, update `HANDOFF_RETENTION_DAYS` → `SNAPSHOT_RETENTION_DAYS`
- [ ] `scripts/cli.py` — update all handoff references in CLI output

#### 1.2 Fix state directory consistency
- [ ] Decide: keep `.claude/state/handoff/` or move to `.claude/state/snapshot/`
- [ ] Update `snapshot_files.py` `SnapshotFileStorage` to use chosen path
- [ ] Update `config.py` `SNAPSHOT_DIR` to match
- [ ] Add migration logic (read old dir, write to new dir) if path changes

#### 1.3 Remove dead code
- [ ] Delete or repurpose `core/hooks/__lib/__init__.py` (empty)
- [ ] Update `docs/improvements.md` — fix `src/handoff/hooks/` → `scripts/hooks/`
- [ ] Remove or archive `plan-uci-fixes.md` (stale, describes completed work)
- [ ] Remove `DOCUMENTATION_UPDATE_SUMMARY.md` and `GITHUB_READY_REPORT.md` if no longer needed

#### 1.4 Fix test warnings
- [ ] Fix 4 tests that return bool instead of asserting (PytestReturnNotNone):
  - `test_last_user_message.py::test_last_user_message_skips_dict_items`
  - `test_visual_context.py::test_extract_visual_context`
  - `test_visual_context.py::test_extract_visual_context_from_screenshot_reference`

### Validation
```bash
pytest P://packages/snapshot/tests/ P://packages/snapshot/scripts/tests/ -q
# Expect: 358 passed, 29 skipped, 0 warnings
grep -r "handoff" scripts/hooks/ scripts/config.py scripts/cli.py | wc -l
# Expect: 0 (or near-zero, only in comments explaining fork history)
```

---

## 2. Phase 2: CI/CD Pipeline

**Goal**: Automated testing on push/PR.  
**Effort**: ~1-2 hours  
**Risk**: Low

### Tasks

#### 2.1 Create GitHub Actions workflows
- [ ] `.github/workflows/test.yml` — Multi-platform pytest (Ubuntu, Windows, macOS) with Python 3.9-3.13
- [ ] `.github/workflows/lint.yml` — ruff + mypy checks
- [ ] Use existing `.benchmarks/` and `.coverage` as reference for coverage targets

#### 2.2 Update badges in README.md
- [ ] Fix badge URLs to point to correct repo/workflow
- [ ] Update coverage badge to reflect current test count (358, not 103)

### Reference workflow structure
```yaml
# test.yml skeleton
on: [push, pull_request]
jobs:
  test:
    strategy:
      matrix:
        os: [ubuntu-latest, windows-latest, macos-latest]
        python: ["3.9", "3.11", "3.13"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "${{ matrix.python }}" }
      - run: pip install pytest ruff mypy
      - run: pytest tests/ scripts/tests/ -q
```

### Validation
- [ ] Push to branch, verify Actions run green
- [ ] Confirm all matrix combinations pass

---

## 3. Phase 3: Documentation & AGENTS.md Overhaul

**Goal**: Make AGENTS.md reflect `snapshot` (not `handoff`), and ensure all docs are accurate.  
**Effort**: ~1-2 hours  
**Risk**: Low

### Tasks

#### 3.1 Rewrite AGENTS.md for snapshot
- [ ] Package overview: snapshot, not handoff
- [ ] Directory structure: `scripts/` as authoritative, `core/` as redirect layer
- [ ] Update all path references (`P://packages/snapshot/...`)
- [ ] Update test count (358 passed, not 103)
- [ ] Remove handoff-specific terminology
- [ ] Document the fork relationship (snapshot forked from handoff)
- [ ] Document state directory conventions

#### 3.2 Update README.md
- [ ] Fix version badge (0.5.0)
- [ ] Update test count references
- [ ] Fix any remaining `handoff` references
- [ ] Document the relationship between `scripts/` and `core/`

#### 3.3 Update CHANGELOG.md
- [ ] Add v0.5.0 entry documenting the fork from handoff
- [ ] Document the rename to snapshot

### Validation
- [ ] Read through AGENTS.md — zero confusion for a new AI agent
- [ ] All paths in docs resolve to real files

---

## 4. Phase 4: Code Quality & Architecture

**Goal**: Reduce complexity, improve maintainability.  
**Effort**: ~3-4 hours  
**Risk**: Medium

### Tasks

#### 4.1 Reduce `transcript.py` complexity
- [ ] File is 2,784 lines — extract logical sections into focused modules:
  - `transcript_parser.py` — Raw transcript parsing
  - `transcript_goals.py` — Goal extraction (canonical goal, last substantive message)
  - `transcript_boundaries.py` — Session boundary and topic shift detection
  - `transcript_operations.py` — Pending operations extraction
- [ ] Maintain backward compatibility via `transcript.py` re-exports

#### 4.2 Reduce `PreCompact_snapshot_capture.py` complexity
- [ ] File is 1,016 lines — extract into focused modules:
  - `capture_core.py` — Main capture orchestration
  - `capture_context.py` — Context gathering from transcript
  - `capture_quality.py` — Quality score computation
- [ ] Update imports in all dependent code

#### 4.3 Reduce `snapshot_v2.py` and `snapshot_store.py` complexity
- [ ] `snapshot_v2.py` (1,013 lines) — Review for extraction opportunities
- [ ] `snapshot_store.py` (1,006 lines) — Review for extraction opportunities

#### 4.4 Improve type safety
- [ ] Run `mypy --strict` and address findings
- [ ] Add return type annotations to all public functions
- [ ] Fix pyrightconfig.json if needed

### Validation
```bash
pytest P://packages/snapshot/tests/ P://packages/snapshot/scripts/tests/ -q
ruff check scripts/ tests/
mypy scripts/ --ignore-missing-imports
```

---

## 5. Phase 5: Feature Enhancements

**Goal**: Address functional gaps and add polish.  
**Effort**: ~4-6 hours  
**Risk**: Medium

### Tasks

#### 5.1 Fix state directory inconsistency
- [ ] Unify on one state directory path (currently split between `.claude/state/handoff/` and `.claude/handoffs/`)
- [ ] Add migration path for existing state files
- [ ] Update all code and config to match

#### 5.2 Improve error messages
- [ ] Standardize error message format across all hooks
- [ ] Add actionable suggestions to error messages (e.g., "Check that the transcript path is valid")
- [ ] Ensure no internal paths leak to user-facing output

#### 5.3 Add snapshot diff/comparison
- [ ] Add CLI command to compare two snapshots: `python -m scripts.cli diff <id1> <id2>`
- [ ] Show what changed between sessions (files, goals, decisions)

#### 5.4 Add snapshot search/filter
- [ ] Add CLI command to search snapshots: `python -m scripts.cli search <query>`
- [ ] Filter by date range, quality score, terminal ID

### Validation
```bash
pytest P://packages/snapshot/tests/ P://packages/snapshot/scripts/tests/ -q
python -m scripts.cli diff <id1> <id2>
python -m scripts.cli search "test"
```

---

## 6. Phase 6: Release Preparation

**Goal**: Prepare for v1.0.0 release.  
**Effort**: ~1-2 hours  
**Risk**: Low

### Tasks

#### 6.1 Version bump and changelog
- [ ] Update `.claude-plugin/plugin.json` version to `1.0.0`
- [ ] Write comprehensive CHANGELOG entry
- [ ] Tag release in git

#### 6.2 Final validation
- [ ] Full test suite passes: `pytest tests/ scripts/tests/ -v`
- [ ] Lint clean: `ruff check .`
- [ ] Type check clean: `mypy scripts/`
- [ ] Manual end-to-end test: trigger compaction, verify capture, start new session, verify restore
- [ ] Test on Windows (primary platform)

#### 6.3 Plugin distribution
- [ ] Test `/plugin P://packages/snapshot` installation
- [ ] Verify all hooks register correctly
- [ ] Test uninstall and reinstall

---

## Execution Priority

For a solo developer, work through phases in order:

```
Phase 1 (Rename)     → 2-3 hrs  → Clean codebase foundation
Phase 2 (CI/CD)      → 1-2 hrs  → Safety net for all future work
Phase 3 (Docs)       → 1-2 hrs  → Accurate context for AI agents
Phase 4 (Quality)    → 3-4 hrs  → Reduce maintenance burden
Phase 5 (Features)   → 4-6 hrs  → Polish and usability
Phase 6 (Release)    → 1-2 hrs  → Ship v1.0.0
```

**Total estimated effort**: ~12-19 hours of focused work.

### Recommended Session Breakdown

| Session | Phases | Duration | Deliverable |
|---------|--------|----------|-------------|
| 1 | Phase 1 | 2-3 hrs | Clean rename, 0 warnings |
| 2 | Phase 2 + 3 | 2-3 hrs | CI green, docs accurate |
| 3 | Phase 4 | 3-4 hrs | Modular architecture |
| 4 | Phase 5 | 4-6 hrs | Feature enhancements |
| 5 | Phase 6 | 1-2 hrs | v1.0.0 release |

---

## Key Principles

1. **Test after every change** — Run full suite after each task
2. **One phase at a time** — Don't mix rename work with feature work
3. **Commit per task** — Atomic commits make rollback easy
4. **`scripts/` is authoritative** — All code changes go to `scripts/hooks/`, `core/` is just redirect
5. **Windows-first** — Primary platform, test symlinks and path handling carefully

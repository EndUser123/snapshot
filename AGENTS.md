# AGENTS.md

> AI-maintainable documentation for the handoff package. This file provides context and constraints for AI assistants (Claude, Copilot, etc.) working on this codebase.

## Package Overview

**handoff** is a Claude Code plugin that provides compact/resume continuity for Claude Code sessions. It captures terminal state before transcript compaction and restores it on session start.

**Architecture**: Research-backed V2 handoff envelope with resume snapshot, decision register, evidence index, and checksum validation.

**Key Constraints**:
- Multi-terminal isolation (each terminal has independent handoff state)
- Stateless design (no shared state between terminals)
- SHA256 checksum validation for data integrity
- Freshness window (default: 20 minutes) for automatic restoration
- No backward compatibility reads (V2-only design)

## Directory Structure

```
handoff/
├── .claude-plugin/         # Plugin metadata (plugin.json)
├── core/                   # Python source code (authoritative)
│   ├── hooks/             # Hook entry points
│   │   ├── PreCompact_handoff_capture.py
│   │   ├── SessionStart_handoff_restore.py
│   │   └── __lib/         # Core library modules
│   │       ├── handoff_v2.py
│   │       ├── handoff_files.py
│   │       ├── project_root.py
│   │       └── transcript.py
│   └── tests/             # Unit tests for core modules
├── hooks/                 # Hook configuration
│   └── hooks.json         # Hook registration
├── skill/                 # Standalone skill (alternative invocation)
│   └── SKILL.md
├── tests/                 # Integration and feature tests
│   ├── conftest.py        # Test fixtures (temp-root isolation)
│   ├── test_canonical_goal_extraction.py
│   ├── test_pending_operations_extraction.py
│   └── ACTUAL_COMPACTION_TEST.md
├── examples/              # Usage examples
├── docs/                  # Additional documentation
├── assets/                # Media assets (badges, videos, diagrams)
├── .github/workflows/     # CI/CD workflows
├── README.md              # Package overview
├── CHANGELOG.md           # Version history
├── CONTRIBUTING.md        # Contribution guidelines
├── LICENSE                # MIT license
└── AGENTS.md              # This file
```

## Development Setup

### Local Development (Hooks)

For active development, use symlinks for instant feedback:

```powershell
# Windows (symlinks require admin or Developer Mode)
cd P://.claude/hooks
cmd /c "mklink PreCompact_handoff_capture.py P://packages/handoff/core/hooks/PreCompact_handoff_capture.py"
cmd /c "mklink SessionStart_handoff_restore.py P://packages/handoff/core/hooks/SessionStart_handoff_restore.py"
```

### Running Tests

```bash
# Quick test
pytest P://packages/handoff/tests/ -q

# Specific test suites
pytest P://packages/handoff/core/tests/test_handoff_hooks.py -q
pytest P://packages/handoff/tests/test_canonical_goal_extraction.py -q
pytest P://packages/handoff/tests/test_pending_operations_extraction.py -q

# With coverage
pytest P://packages/handoff/tests/ --cov=core --cov-report=term-missing
```

**Expected**: All 103 tests pass.

### Test Hygiene

**CRITICAL**: Tests must never write into live `$CLAUDE_ROOT/state\handoff`.

Protections in place:
- `HANDOFF_PROJECT_ROOT` override in `project_root.py`
- Temp-root autouse fixtures in `tests/conftest.py` and `core/tests/conftest.py`

## Core Constraints

### Multi-Terminal Isolation

Each terminal has independent handoff state. No cross-terminal state sharing.

- Terminal ID derivation: From environment or session metadata
- State directories: Per-terminal subdirectories under handoff root
- No global state: All state is terminal-scoped

### Session Boundaries

The system stops extraction at session boundaries to prevent crossing into unrelated sessions.

- Detection: `session_chain_id` changes in transcript
- Behavior: Stop scanning when new session detected
- Validation: Test with `test_session_boundary_detection`

### Topic Shift Detection

Semantic similarity check (30% threshold) detects topic changes.

- Purpose: Prevent restoration of stale context
- Implementation: `is_same_topic()` in `transcript.py`
- Validation: Test with `test_topic_shift_detection`

### Checksum Validation

SHA256 checksums validate data integrity.

- Invalid snapshots: Rejected with error message
- Validation: Test with `test_checksum_validation`
- Status: `rejected_invalid` for checksum failures

## Known Issues and Anti-Patterns

### Broken Symlinks After Brownfield Conversion

After converting from Python library (src/) to plugin (core/), symlinks in `P://.claude/hooks/` may still point to old `src/` paths.

**Fix**: Remove and recreate symlinks with correct `core/` paths:
```powershell
cd P://.claude/hooks
rm PreCompact_handoff_capture.py SessionStart_handoff_restore.py
cmd /c "mklink PreCompact_handoff_capture.py P://packages/handoff/core/hooks/PreCompact_handoff_capture.py"
cmd /c "mklink SessionStart_handoff_restore.py P://packages/handoff/core/hooks/SessionStart_handoff_restore.py"
```

### Test Pollution

Writing to live handoff state during tests pollutes the real state directory.

**Fix**: Always use temp-root fixtures in tests:
```python
@pytest.fixture(autouse=True)
def temp_handoff_root(tmp_path):
    """Override handoff root for test isolation."""
    import os
    os.environ["HANDOFF_PROJECT_ROOT"] = str(tmp_path)
    yield
    del os.environ["HANDOFF_PROJECT_ROOT"]
```

### Ignoring Transcript Entry Types

Claude transcript content can be strings or list items like `{"type":"text","text":"..."}`. The parser must handle both formats.

**Fix**: Use `_extract_text_from_entry()` which handles both formats correctly.

## Feature Architecture

### Canonical Goal Extraction

Extracts the last substantive user message from the transcript.

- Algorithm: Backward scan with session boundary and topic shift detection
- Meta-instruction filtering: Skips "thanks", "summarize", continuation markers
- Tool_result skipping: Ignores user entries containing only tool_result content
- Test coverage: 7 tests in `test_canonical_goal_extraction.py`

### Pending Operations Detection

Detects pending work from tool_use events and assistant text.

- Two-pass approach: Parse tool_use events first, then keyword fallback
- Tool types: Read, Grep, Glob, Edit, Bash, Skill
- Keyword patterns: review, analyze, investigate, debug, search
- Investigation operations: Review/analysis work mapped to investigation type
- Test coverage: 17 tests in `test_pending_operations_extraction.py`

### Next Step Inference

Infers the next step from pending operations or assistant text.

- Priority 1: Pending operations (tool_use events)
- Priority 2: Assistant text (analysis of last assistant message)
- Priority 3: Goal fallback (extracted user goal)
- Test coverage: Integrated in goal extraction tests

### Decision Register and Evidence Index

Captures explicit decisions and supporting evidence.

- Decision kinds: constraint, settled_decision, blocker_rule, anti_goal
- Evidence types: file, transcript, test, log, git
- Reference-only: Evidence is not a second restore payload
- Test coverage: Integrated in handoff hooks tests

## CI/CD Workflows

- **test.yml**: Multi-platform pytest (Ubuntu, Windows, macOS) with Python 3.9-3.13
- **lint.yml**: Code quality checks (ruff, mypy)
- **ci.yml**: Continuous integration checks

**Note**: No external coverage uploads (local only with `--cov-report=term-missing`)

## Release Checklist

Before releasing a new version:

1. **All tests pass**: `pytest tests/ -q` → 103 passed
2. **CHANGELOG updated**: Document all changes with version number
3. **Version bumped**: Update `.claude-plugin/plugin.json` version field
4. **Documentation updated**: README.md, AGENTS.md, and any relevant docs
5. **CI/CD green**: GitHub Actions workflows passing
6. **Manual testing**: Test actual compaction/restoration workflow

## Platform-Specific Notes

### Windows

- Symlinks require admin or Developer Mode
- Use `cmd /c "mklink"` for creating symlinks
- Path separators: Use forward slashes in Python, backslashes in PowerShell

### macOS/Linux

- Use `ln -sf` for creating symlinks
- Path separators: Use forward slashes consistently
- Permissions: Ensure execute permissions on hook files

## Communication Patterns

### Error Handling

- Hook stderr is treated as error by Claude Code
- Use stdout for output or silence (no output)
- Log to file for debugging: `P://packages/handoff/.claude/state/handoff/debug.log`

### State Management

- Handoff state: `P://packages/handoff/.claude/state/handoff/{terminal}_handoff.json`
- Per-terminal isolation: Each terminal has independent state file
- Status values: pending, consumed, rejected_stale, rejected_invalid
- Freshness window: 20 minutes (override with `HANDOFF_FRESHNESS_MINUTES`)

### Performance Considerations

- Transcript parsing: Backward scan stops at first substantive message
- Test performance: 103 tests in ~4 seconds
- Hook performance: Hooks should complete in < 1 second

## Debugging Tips

### Enable Debug Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Check Handoff State

```bash
# View current handoff state
cat P://packages/handoff/.claude/state/handoff/{terminal}_handoff.json
```

### Verify Hook Registration

```bash
# Check hooks are registered
cat P://.claude/settings.json | grep -A5 "hooks"
```

### Test Isolation

```bash
# Run tests with verbose output
pytest P://packages/handoff/tests/ -v -s
```

## Related Documentation

- **README.md**: User-facing overview and quick start
- **CHANGELOG.md**: Version history and changes
- **CONTRIBUTING.md**: Contribution guidelines
- **HANDOFF_*.md**: Feature-specific documentation in package root
- **tests/ACTUAL_COMPACTION_TEST.md**: Operational compaction procedure

## Summary for AI Assistants

When working on this codebase:

1. **Multi-terminal isolation is critical** - Never share state across terminals
2. **Test hygiene is mandatory** - Always use temp-root fixtures
3. **Backward compatibility is not a concern** - V2-only design
4. **Checksum validation is required** - SHA256 for all snapshots
5. **Session boundaries matter** - Stop extraction at session_chain_id changes
6. **Topic shifts prevent stale context** - Use semantic similarity threshold
7. **CI/CD is comprehensive** - Multi-platform, multi-version Python testing
8. **Documentation is AI-maintainable** - This file (AGENTS.md) is for AI assistants

**Current Status**: All 103 tests passing, production-ready for Claude Code plugin deployment.

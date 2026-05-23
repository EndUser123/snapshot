# Contributing to handoff

Thank you for your interest in contributing to handoff!

## Development Setup

```bash
# Clone the repository
git clone https://github.com/csf-nip/handoff.git P://packages/handoff
cd P://packages/handoff

# Create junction for local skill development
powershell -Command "New-Item -ItemType Junction -Path '$CLAUDE_ROOT/skills\handoff' -Target 'P://packages/handoff/skill'"

# Install in editable mode
pip install -e .

# Install development dependencies
pip install pytest pytest-cov ruff
```

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest --cov=handoff tests/ --cov-report=html

# Run specific test file
pytest tests/test_checkpoint_chain.py -v
```

## Code Style

We use **ruff** for linting and formatting:

```bash
# Check code style
ruff check src/ tests/

# Auto-fix issues
ruff check --fix src/ tests/

# Format code
ruff format src/ tests/
```

## Submitting Changes

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes
4. Add tests for new functionality
5. Ensure all tests pass (`pytest tests/ -v`)
6. Ensure code style passes (`ruff check src/ tests/`)
7. Commit your changes (use [Conventional Commits](https://www.conventionalcommits.org/))
8. Push to the branch (`git push origin feature/amazing-feature`)
9. Open a Pull Request

## Commit Message Format

We follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add checkpoint chain traversal API
fix: resolve transcript offset calculation bug
docs: update README with installation instructions
test: add tests for pending operation validation
```

## Development Workflow

### Before Making Changes

1. Check existing [issues](https://github.com/csf-nip/handoff/issues) or create a new one
2. Comment on the issue to indicate you're working on it
3. Fork and create your branch

### During Development

1. Write tests first (TDD approach preferred)
2. Make small, focused commits
3. Ensure all tests pass before pushing
4. Keep changes minimal and focused

### After Submitting

1. Respond to review feedback promptly
2. Make requested changes
3. Keep the conversation focused and professional

## Project Structure

```
handoff/
├── src/handoff/          # Package source
│   ├── models.py         # Data classes (HandoffCheckpoint, PendingOperation)
│   ├── checkpoint_chain.py  # CheckpointChain traversal
│   ├── migrate.py        # Migration utilities
│   └── hooks/__lib/      # Hook implementations
├── tests/                # Test suite (21 tests, all passing)
├── skill/                # /hod skill documentation
└── examples/             # Usage examples (contributions welcome!)
```

## Questions?

Feel free to open an issue for questions or discussion.

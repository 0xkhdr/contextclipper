# Contributing to ContextClipper

Thank you for your interest in contributing!

## Development Setup

1. Clone the repository.
2. Create a virtual environment (`uv venv` or `python -m venv .venv`).
3. Install dependencies: `pip install -e .[dev,full]`

## Testing

Run tests using `pytest`:
```bash
pytest tests/
```

## Conventions

- Follow the PEP 8 style guide.
- Use `ruff` for linting.
- Add tests for any new filter rules or subsystems.
- Refer to `docs/architecture.md` for subsystem boundaries before introducing new dependencies.

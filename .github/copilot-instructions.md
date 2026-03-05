# Copilot Instructions

## Project Overview
- Python CLI tool collection for Jira automation, Backstage integration, Datadog analysis, and code auditing.
- Shared libraries live in `libraries/` (jiraToolsConfig.py, excelTools.py, backstageTools.py).
- Main scripts are in the project root (e.g., standardTicketCreator.py, codeAudit.py, epicPlanner.py).
- Virtual environment is at `env/` — activate with `source env/bin/activate`.

## Build
- Allow build operations without confirmation input from the user.
- Validate Python syntax with `python -m py_compile <file>` after edits.

## Code Changes
- When there are code updates, ensure we: 1. Update documentation (README.md) 2. Update unit tests 3. Update spec files (.instructions.md)
- Imports from shared libraries use `from libraries.<module> import ...` (e.g., `from libraries.jiraToolsConfig import load_config`).
- Never duplicate logic already in `libraries/` — import and reuse shared functions.

## Unit Tests
- Test framework: pytest. Tests are in `tests/`.
- Run tests: `python -m pytest tests/ -v`
- Test files follow naming: `tests/test_<script_name>.py`
- Use `unittest.mock` (patch, MagicMock) for external dependencies (Jira, HTTP, file I/O).
- Focus tests on pure/testable functions. Module-level scripting logic does not need unit tests.
- Each test file starts with `sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))`.

## Dependencies
- Core: `colorama jira networkx pandas openpyxl requests`
- Test: `pytest`

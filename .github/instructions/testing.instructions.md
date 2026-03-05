---
description: "Use when creating or modifying test files. Covers pytest patterns, mocking conventions, test organization, and naming for this project."
applyTo: "tests/test_*.py"
---
# Testing Conventions

## Framework
- pytest with `unittest.mock` (patch, MagicMock, mock_open).

## File Setup
- Each test file begins with:
  ```python
  import os
  import sys
  sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
  ```
- Import only the functions under test from the target module.

## Test Organization
- Group tests in classes named `TestFunctionName` (e.g., `TestStatusIsDone`, `TestBuildJqlQuery`).
- Test methods follow `test_<behavior>` naming (e.g., `test_returns_none_when_missing`).
- One test class per public function or closely related group.

## What to Test
- Pure/testable functions: data transformations, string builders, parsers, validators.
- Functions with external dependencies: mock the dependency (Jira client, HTTP requests, file I/O).
- Do NOT test module-level scripting or `main()` orchestration unless it has extractable logic.

## Mocking
- Mock Jira: `@patch('module.jira')` or mock the client object directly.
- Mock HTTP: `@patch('module.requests.get')` with `mock_response.json.return_value`.
- Mock file I/O: use `tmp_path` fixture or `mock_open`.
- Mock config: `@patch('libraries.jiraToolsConfig.config_file', str(tmp_path / "file"))`.

## Assertions
- Use `assert` directly (pytest style), not `self.assertEqual`.
- Test edge cases: empty inputs, None, NaN, missing keys, invalid formats.
- For error cases, use `pytest.raises(ExceptionType)`.

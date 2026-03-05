---
description: "Use when creating or modifying Python scripts in the project root. Covers CLI patterns, argument parsing, Jira/Backstage/Datadog integration, colorama output conventions, and shared library usage."
applyTo: "*.py"
---
# Python Script Conventions

## Structure
- Scripts in root are CLI tools with `argparse` for argument parsing.
- Shared utilities live in `libraries/` — always import from there instead of duplicating logic.
- Use `from libraries.jiraToolsConfig import load_config, statusIsDone, get_backstage_url, ...` for Jira config.
- Use `from libraries.excelTools import ...` for Excel reading, team mapping, and data processing.
- Use `from libraries.backstageTools import ...` for Backstage API queries.

## CLI Patterns
- Use `argparse.ArgumentParser` with a descriptive `description`.
- Support `--perform-update` or `-c`/`--create` flags for write operations (default is dry-run).
- Print colored output using `colorama` (`Fore`, `Style`, `Back`).

## Output Conventions
- Success: `Fore.GREEN`
- Warning/skip: `Fore.YELLOW`
- Error: `Fore.RED`
- Info/progress: `Fore.CYAN`
- Use `Style.BRIGHT` for headings and emphasis.
- Always `Style.RESET_ALL` after colored output.

## Error Handling
- Catch `jira.exceptions.JIRAError` for Jira operations.
- Catch `requests.exceptions.RequestException` for HTTP calls.
- Print user-friendly error messages with color, don't crash silently.

## Configuration
- Jira config is loaded from `~/.jiraTools` via `load_config()`.
- Backstage URL comes from `~/.jiraTools` `backstageUrl` key or `--backstageUrl` CLI override (via `get_backstage_url()`).
- Datadog config is in `~/.datadog.cfg`.

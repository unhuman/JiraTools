---
description: "Use when creating or modifying shared library modules. Covers library contracts, import patterns, and reuse principles."
applyTo: "libraries/**"
---
# Libraries Conventions

## Purpose
Shared libraries in `libraries/` eliminate duplication across scripts.

## Modules
- `jiraToolsConfig.py` — Config loading, Jira client creation, common JQL helpers.
- `excelTools.py` — Excel/CSV export utilities (openpyxl, pandas).
- `backstageTools.py` — Backstage API integration and catalog queries.

## Import Pattern
Scripts import from libraries as:
```python
from libraries.jiraToolsConfig import load_config, get_jira_client
from libraries.excelTools import export_to_excel
from libraries.backstageTools import get_backstage_entities
```

## Principles
- Any logic needed by 2+ scripts belongs here — extract and share.
- Never duplicate logic from a library in a script; import it.
- Libraries must not import from root-level scripts.
- Keep functions focused: one responsibility per function.
- New shared functions go in the most relevant existing module; create a new module only if none fits.

## Config Files
- Jira config: `~/.jiraTools` (INI format parsed by jiraToolsConfig).
- Backstage config: `~/.backstage` (API base URL, token).
- Datadog config: `~/.datadog` (API key, app key).

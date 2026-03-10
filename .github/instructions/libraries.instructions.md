---
description: "Use when creating or modifying shared library modules. Covers library contracts, import patterns, and reuse principles."
applyTo: "libraries/**"
---
# Libraries Conventions

## Purpose
Shared libraries in `libraries/` eliminate duplication across scripts.

## Modules
- `jiraToolsConfig.py` — Config loading (`~/.jiraTools` JSON), Jira client creation, Backstage URL resolution, common JQL helpers.
- `excelTools.py` — Excel/CSV export utilities (openpyxl, pandas).
- `backstageTools.py` — Backstage API integration and catalog queries.
- `jiraTicketTools.py` — Shared Jira ticket creation utilities (issue dict preparation, field formatting, epic linking, error handling).

## Import Pattern
Scripts import from libraries as:
```python
from libraries.jiraToolsConfig import load_config, get_jira_client, get_backstage_url
from libraries.excelTools import export_to_excel
from libraries.backstageTools import get_all_components, filter_components_for_team
```

## Principles
- Any logic needed by 2+ scripts belongs here — extract and share.
- Never duplicate logic from a library in a script; import it.
- Libraries must not import from root-level scripts.
- Keep functions focused: one responsibility per function.
- New shared functions go in the most relevant existing module; create a new module only if none fits.

## Config Files
- Jira config: `~/.jiraTools` (JSON format parsed by jiraToolsConfig). Keys: `jira_server`, `personal_access_token`, `backstageUrl`.
- Datadog config: `~/.datadog` (API key, app key).

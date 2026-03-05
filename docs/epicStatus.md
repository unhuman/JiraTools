# Epic Status Documentation

## Overview

`epicStatus.py` evaluates the current plan status of an epic by organizing its issues into sprints and categorizing them by status (planned, completed, unplanned).

## Usage

```bash
python epicStatus.py <epic_key>
```

### Arguments

| Argument | Description |
|----------|-------------|
| `epic_key` | The Jira epic key (e.g., `PROJ-123`) |

## Configuration

Requires `~/.jiraTools` with Jira credentials (`jira_server`, `personal_access_token`).

## Examples

```bash
python epicStatus.py PROJ-123
```

## Workflow

1. Fetch the epic and all linked issues from Jira
2. Parse sprint assignments from issues (handles multiple data formats)
3. Categorize issues by sprint and status
4. Fetch sprint metadata (names, start/end dates)
5. Generate a report with sections:
   - **Completed** (withdrawn/done without sprint)
   - **Completed work** (by sprint, sorted by date)
   - **Planned work** (by sprint, sorted by date)
   - **Unplanned work** (open issues not assigned to sprints)

# Epic Creation Time Documentation

## Overview

`epicCreationTime.py` analyzes development time of open epics by calculating creation spans and ticket creation activity ranges for a given sprint team.

## Usage

```bash
python epicCreationTime.py <sprint_team_name> [--project_key <KEY>]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `sprint_team_name` | Team identifier (e.g., `Team Alpha`). Filters epics by Sprint Team custom field (case-insensitive). |
| `--project_key` | Optional Jira project key (e.g., `PROJ`). Narrows the epic search. |

## Configuration

Requires `~/.jiraTools` with Jira credentials (`jira_server`, `personal_access_token`).

## Examples

```bash
# Analyze all open epics for a team
python epicCreationTime.py "Team Alpha"

# Narrow to a specific project
python epicCreationTime.py "Team Alpha" --project_key PROJ
```

## Workflow

1. Query open epics matching the sprint team name (and optional project key)
2. For each epic, analyze:
   - Epic creation date
   - All tickets linked to the epic
   - Development span (epic creation to last relevant ticket)
   - Ticket creation activity span (first to last relevant ticket)
3. Sort epics by development span
4. Display detailed analysis including shortest and greatest development spans

# Subtasks User Different Parent Owner Documentation

## Overview

`subtasksUserDifferentParentOwner.py` finds all subtasks assigned to a specific user within a date range where the parent ticket is owned by a different user. Useful for tracking cross-team contributions.

## Usage

```bash
python subtasksUserDifferentParentOwner.py --user <USER> --start-date <DATE> --end-date <DATE>
```

### Arguments

| Argument | Description |
|----------|-------------|
| `--user` | Jira username/ID of the subtask assignee to check |
| `--start-date` | Start date in `YYYY-MM-DD` format |
| `--end-date` | End date in `YYYY-MM-DD` format |

## Configuration

Requires `~/.jiraTools` with Jira credentials (`jira_server`, `personal_access_token`).

## Examples

```bash
python subtasksUserDifferentParentOwner.py --user jdoe --start-date 2026-01-01 --end-date 2026-03-01
```

## Workflow

1. Build a JQL query for subtasks assigned to the specified user, updated within the date range
2. For each subtask, fetch the parent issue
3. Check if the subtask assignee differs from the parent assignee and parent is unresolved
4. Display mismatched subtasks with details for both the subtask and parent (key, summary, status, assignee)

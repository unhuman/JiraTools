# Find Custom Fields Documentation

## Overview

`findCustomFields.py` is a discovery tool to identify custom field IDs in Jira by examining a specific issue. Useful for configuring other scripts that need custom field IDs.

## Usage

```bash
python findCustomFields.py <issue_key>
```

### Arguments

| Argument | Description |
|----------|-------------|
| `issue_key` | A Jira issue key to examine (e.g., `PROJ-123`) |

## Configuration

Requires `~/.jiraTools` with Jira credentials (`jira_server`, `personal_access_token`).

## Examples

```bash
python findCustomFields.py PROJ-123
```

## Output

For each custom field that has a value on the specified issue, displays:
- Field ID (`customfield_XXXXX`)
- Field name
- Current value
- Data type

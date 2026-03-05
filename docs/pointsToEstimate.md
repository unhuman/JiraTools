# Points to Estimate Documentation

## Overview

`pointsToEstimate.py` converts story points to original time estimates for completed tickets that have story points but no original estimate.

## Usage

```bash
python pointsToEstimate.py <type> <name> [--perform-update]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `type` | Query filter type: `assignee` or `team` |
| `name` | Assignee name or team name to filter by |
| `--perform-update` | Actually perform updates (default is dry-run mode) |

## Configuration

Requires `~/.jiraTools` with Jira credentials (`jira_server`, `personal_access_token`).

The conversion ratio is defined by `MINUTES_PER_POINT` in `libraries/jiraToolsConfig.py` (default: 360 minutes = 1 day = 6 hours per story point).

## Examples

```bash
# Dry run — see what would be updated for a team
python pointsToEstimate.py team "My Team"

# Actually update estimates for an assignee
python pointsToEstimate.py assignee jdoe --perform-update
```

## Workflow

1. Build a JQL query for completed issues with story points but no original estimate
2. Search Jira for matching issues
3. For each issue, convert story points to a time estimate using `MINUTES_PER_POINT`
4. In dry-run mode, show what would be updated; with `--perform-update`, apply changes
5. Print summary of updated/skipped counts

## Related Scripts

Run `populateRemainingEstimate.py` after this script to copy original estimates to remaining estimates for in-progress work.

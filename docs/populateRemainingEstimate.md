# Populate Remaining Estimate Documentation

## Overview

`populateRemainingEstimate.py` copies original estimate to remaining estimate for in-progress tickets that have story points and an original estimate but no remaining estimate.

## Usage

```bash
python populateRemainingEstimate.py <type> <name> [--perform-update]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `type` | Query filter type: `assignee` or `team` |
| `name` | Assignee name or team name to filter by |
| `--perform-update` | Actually perform updates (default is dry-run mode) |

## Configuration

Requires `~/.jiraTools` with Jira credentials (`jira_server`, `personal_access_token`).

## Examples

```bash
# Dry run — see what would be updated
python populateRemainingEstimate.py team "My Team"

# Actually update remaining estimates
python populateRemainingEstimate.py assignee jdoe --perform-update
```

## Workflow

1. Build a JQL query for in-progress issues with original estimates but no remaining estimate
2. Search Jira for matching issues
3. For each issue, set remaining estimate equal to original estimate
4. In dry-run mode, show what would be updated; with `--perform-update`, apply changes
5. Print summary statistics

## Related Scripts

Run `pointsToEstimate.py` first to populate original estimates from story points.

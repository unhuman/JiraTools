# Epic Planner Documentation

## Overview

`epicPlanner.py` resolves ticket ordering based on dependencies within an epic. It builds a dependency graph of issues linked to an epic and displays them grouped by execution rounds — batches of work that can be done in parallel.

## Usage

```bash
python epicPlanner.py <epic_key> [-t]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `epic_key` | The Jira epic key (e.g., `PROJ-123`) |
| `-t, --transitive` | Include transitive dependencies in output |

## Configuration

Requires `~/.jiraTools` with Jira credentials (`jira_server`, `personal_access_token`).

## Examples

```bash
# Show dependency-ordered plan for an epic
python epicPlanner.py PROJ-123

# Include transitive dependencies
python epicPlanner.py PROJ-123 -t
```

## Workflow

1. Connect to Jira and fetch the epic and all linked issues
2. Build a dependency graph from issue links (blocks/follows relationships)
3. Perform topological sort to determine execution order
4. Optionally calculate transitive closure for indirect dependencies
5. Group issues into rounds (parallel work batches)
6. Display color-coded output showing round-by-round organization with dependency and status info

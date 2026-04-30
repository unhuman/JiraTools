# Claude Code + Jira MCP Quick Reference

Fast lookup for common Jira MCP operations with JiraTools.

## MCP Tool Quick Commands

### Create Issues

```python
jira_create_issue(
  project_key="PROJ",
  summary="Issue title",
  issue_type="Task",
  description="Markdown text",
  assignee="user@example.com",
  components="Frontend,API",
  additional_fields='{"customfield_10000": "value", "epicKey": "PROJ-123"}'
)
```

### Batch Create Issues

```python
jira_batch_create_issues(
  issues='[
    {"project_key": "PROJ", "summary": "Task 1", "issue_type": "Task"},
    {"project_key": "PROJ", "summary": "Task 2", "issue_type": "Bug"}
  ]'
)
```

### Search Issues

```python
jira_search(
  jql='project = PROJ AND status = "To Do" AND assignee = currentUser()',
  fields='summary,status,assignee,priority',
  limit=50
)
```

### Get Issue Details

```python
jira_get_issue(
  issue_key="PROJ-123",
  fields='*all',  # or specific: 'summary,status,assignee'
  comment_limit=10
)
```

### Update Issue

```python
jira_update_issue(
  issue_key="PROJ-123",
  fields='{"assignee": "user@example.com", "summary": "New title"}',
  additional_fields='{"epicKey": "PROJ-500"}'
)
```

### Transition Issue

```python
jira_transition_issue(
  issue_key="PROJ-123",
  transition_id="11",  # Get from jira_get_transitions
  fields='{"resolution": {"name": "Fixed"}}',
  comment="Resolved the issue"
)
```

### Add Comment

```python
jira_add_comment(
  issue_key="PROJ-123",
  body="Comment text in **markdown**"
)
```

### Add Worklog

```python
jira_add_worklog(
  issue_key="PROJ-123",
  time_spent="2h 30m",
  comment="Completed implementation",
  started="2026-04-30T14:00:00.000+0000"
)
```

### Get Available Transitions

```python
jira_get_transitions(issue_key="PROJ-123")
# Returns list with transition_id needed for jira_transition_issue
```

### Create Link Between Issues

```python
jira_create_issue_link(
  link_type="Blocks",
  inward_issue_key="PROJ-123",
  outward_issue_key="PROJ-456",
  comment="This issue blocks PROJ-456"
)
```

### Get Custom Field Options

```python
jira_get_field_options(
  field_id="customfield_10001",
  project_key="PROJ",  # Required for Server/DC
  issue_type="Task"    # Required for Server/DC
)
```

### Batch Update Issues

For updating many issues with the same change:

```python
# Query all issues first
issues = jira_search(jql='project = PROJ AND status = "To Do"')

# Update each (loop manually in Claude)
for issue in issues:
  jira_update_issue(
    issue_key=issue['key'],
    fields='{...}'
  )
```

## Custom Field Mapping Patterns

### Epic Link (Standard)
```json
{"epicKey": "PROJ-123"}
```
✅ Use this, not raw customfield ID

### Sprint Team (Value-Wrapped)
```json
{"customfield_10019": {"value": "Team-Rocket"}}
```
Check your Excel CustomFields sheet for actual ID and data wrapper type.

### Sprint (Numeric ID)
```json
{"customfield_10020": 1001}
```
Get sprint ID from `jira_get_sprints_from_board`.

### Priority (Standard)
```json
{"priority": {"name": "4-Medium"}}
```
Common values: "1-Critical", "2-High", "3-Medium", "4-Low", "5-Lowest"

### Components (Array)
```json
{"components": ["Frontend", "API"]}
```
Or in additional_fields for custom field:
```json
{"customfield_10015": ["Comp1", "Comp2"]}
```

### Select Field
```json
{"customfield_10001": {"value": "Option Name"}}
```

### Multi-Select Field
```json
{"customfield_10002": [{"value": "Option1"}, {"value": "Option2"}]}
```

## JQL Quick Ref

### Status Queries
```jql
status = "To Do"
status in ("To Do", "In Progress")
status = "Done"
```

### Date Queries
```jql
created >= -30d
updated <= -7d
duedate > now()
resolutiondate >= 2026-04-01
```

### Assignment
```jql
assignee = currentUser()
assignee is EMPTY
assignee in (user1, user2)
```

### Epics & Sprints
```jql
parent = PROJ-123
sprint = "Team-Rocket Sprint 47"
```

### Text Search
```jql
summary ~ "keyword"
description ~ "phrase"
text ~ "anywhere"
```

### Complex Queries
```jql
project = PROJ AND status = "In Progress" AND 
  assignee = currentUser() AND 
  updated >= -7d AND 
  priority >= "High"
```

## Field IDs Lookup

When you need to find customfield IDs:

```bash
# Local Python script
python findCustomFields.py <issue_key>

# Or in Claude Code (if script available)
# Ask Claude to examine a sample issue with fields='*all'
# and extract customfield_* entries
```

## Standard Field List

Common fields available in all Jira instances:

| Field | Usage | Example |
|-------|-------|---------|
| `assignee` | Assign to user | `"user@example.com"` |
| `summary` | Issue title | `"Add login feature"` |
| `description` | Issue body | `"## Requirements\n- ..."` |
| `priority` | Priority level | `{"name": "4-Medium"}` |
| `labels` | Tags | `["frontend", "urgent"]` |
| `duedate` | Due date | `"2026-05-15"` |
| `reporter` | Creator (usually read-only) | `"user@example.com"` |
| `components` | Code components | `["Frontend", "API"]` |
| `status` | Workflow status | `"To Do"` (usually set via transition) |

## Batch Operations Pattern

### For 10-100 items:

```python
# Get items
items = jira_search(jql="...")

# Batch in groups of 5
for i in range(0, len(items), 5):
  batch = items[i:i+5]
  
  for item in batch:
    # Process item
    jira_update_issue(...)
  
  # Wait between batches to avoid rate limits
  time.sleep(1)
```

### For 100+ items:

Use same pattern but:
- Batch size: 5-10
- Delay between batches: 2-3 seconds
- Log progress every batch
- Collect errors separately
- Report summary at end

## Common Workflows One-Liner Style

### Create ticket from CSV row
```
jira_create_issue(project_key="PROJ", summary=row['summary'], 
  issue_type=row['type'], additional_fields='{"epicKey": "PROJ-123"}')
```

### Get all issues for epic
```
jira_search(jql='parent = PROJ-500 ORDER BY priority DESC', fields='*all')
```

### Find blocking issues
```
jira_search(jql='project = PROJ AND status != "Done" AND linked is EMPTY')
```

### Update status with comment
```
jira_transition_issue(issue_key="PROJ-123", transition_id="11", 
  comment="Completed per review")
```

### Assign to team members
```
for user in ['user1@example.com', 'user2@example.com']:
  jira_update_issue(issue_key="PROJ-123", fields=f'{{"assignee": "{user}"}}')
```

## Error Recovery

| Error | Fix |
|-------|-----|
| "Field customfield_XXXXX not found" | Use correct field ID from findCustomFields.py |
| "Permission denied" | Check user permissions; try epicKey instead of customfield for epics |
| "Invalid transition" | Use jira_get_transitions to find valid transition IDs |
| "Value not in allowed options" | Use jira_get_field_options to list valid values |
| "Issue not found" | Verify issue key (case-sensitive: PROJ-123 not proj-123) |
| "Sprint not found" | Verify sprint ID; get from jira_get_sprints_from_board |

## Configuration Checklist

Before using Claude Code + Jira:

- [ ] Jira MCP server configured in Claude Code settings
- [ ] Jira PAT (Personal Access Token) valid and current
- [ ] Custom field IDs mapped (use findCustomFields.py)
- [ ] Sample issue reviewed with fields='*all' for custom field structure
- [ ] Test with 1-2 tickets before bulk operations
- [ ] Verify epic keys and sprint IDs exist
- [ ] Check user permissions for custom fields

## Links & Resources

- [Full Integration Guide](./claudeCodeIntegration.md) — Setup and best practices
- [Workflow Recipes](./claudeCodeRecipes.md) — 10 complete examples
- [Standard Ticket Creator](./standardTicketCreator.md) — CSV format
- [Find Custom Fields](./findCustomFields.md) — Discover field IDs locally

---

**TL;DR:** Use epicKey for epics, query with JQL, batch in small groups with delays, and always test with 1-2 items first.

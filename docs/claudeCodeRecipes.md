# Claude Code Recipes for JiraTools

Practical examples for common workflows using Claude Code with Jira and Backstage MCP servers.

## Recipe 1: Create Standardized Sprint Tickets

**Goal:** Create a batch of sprint planning tickets with proper epic, sprint, and team assignment.

**Prerequisites:**
- Excel config file with CustomFields sheet
- CSV file with ticket data (from `standardTicketCreator.py --csv`)
- Jira MCP server configured

**Steps:**

1. **Generate the CSV:**
   ```bash
   python standardTicketCreator.py teams.xlsx --csv output.csv
   ```

2. **In Claude Code:**
   ```
   I need to create Jira tickets from a CSV file. Here's what I have:
   
   - Excel config: [path]/teams.xlsx (with CustomFields sheet mapping custom field IDs)
   - CSV file: [path]/Team-Rocket_tickets.csv
   
   Please:
   1. Read the Excel file and extract the custom field mappings from the CustomFields sheet
   2. Read the CSV file
   3. For each row, create a ticket with jira_create_issue using:
      - project_key and issue_type from config
      - summary, description, assignee, components from CSV columns
      - Additional custom fields (Epic Link, Sprint, Sprint Team) using proper field format
   4. Use Epic Link as {"epicKey": "EPIC-123"} format, not raw customfield
   5. Return created issue keys and any errors
   ```

3. **Expected output:**
   - List of created ticket keys
   - Summary of successes/failures
   - Clickable Jira links for review

---

## Recipe 2: Analyze Epic Progress

**Goal:** Get detailed status report for an epic across sprints.

**Prerequisites:**
- Jira MCP server configured
- Epic key (e.g., PROJ-1000)

**Steps:**

```
Query epic PROJ-1000 and provide a progress analysis:

1. Get all child issues: jira_search with JQL "parent = PROJ-1000"
2. For each issue, retrieve full details including:
   - summary
   - status
   - assignee
   - sprint (if any)
   - story_points (if available)
   - labels
   - due date

3. Group by sprint and provide:
   - Issue count per status (To Do, In Progress, In Review, Done)
   - Story points per status
   - Current assignees and workload
   - Blocked issues (issues linked with "Blocks" relationship)

4. Generate markdown report with:
   - Epic overview (key, name, target date)
   - Sprint-by-sprint table
   - At-risk items (overdue, no assignee, blocked)
   - Recommendations for unblocking progress
```

---

## Recipe 3: Team Onboarding Ticket Package

**Goal:** Create a complete onboarding ticket package for a new team member.

**Prerequisites:**
- Jira MCP server
- Existing team project

**Steps:**

```
Create a structured onboarding ticket package for a new engineer:

1. Create parent epic: "Onboarding: [Name] ([Start Date])"
   - Add label: "onboarding"
   - Assign to: [Engineering Lead]

2. Create child subtasks:
   a) "Environment Setup" (subtask)
      - Description: [Standard setup checklist]
      - Assignee: [Team Lead]
      - Due: Day 1
   
   b) "System Access & Credentials" (subtask)
      - Description: [Access request template]
      - Assignee: [IT Contact]
      - Due: Before Day 1
   
   c) "Architecture Review" (subtask)
      - Description: [Links to arch docs, diagrams]
      - Assignee: [Tech Lead]
      - Due: Day 2-3
   
   d) "Codebase Walkthrough" (subtask)
      - Description: [Key repos, build instructions]
      - Assignee: [Team Lead]
      - Due: Day 2-3
   
   e) "First Task Assignment" (subtask)
      - Description: [Simple bugfix or feature for first week]
      - Assignee: [New Team Member]
      - Due: Day 5

Return the parent epic key and all subtask keys.
```

---

## Recipe 4: Backlog Health Check

**Goal:** Identify and report on backlog issues that need attention.

**Prerequisites:**
- Jira MCP server
- Project key

**Steps:**

```
Analyze the backlog for project PROJ and identify health issues:

1. Query issues with JQL: "project = PROJ AND status = 'To Do' AND created <= -30d"
   (Backlog items not started for 30+ days)

2. For each issue, check:
   - Assignee (unassigned = risk)
   - Due date (overdue = risk)
   - Dependencies (linked blockers = risk)
   - Story points estimate (missing = risk)
   - Labels (indicates priority/category)

3. Generate report with:
   - Stale issues table (created >30 days ago, not started)
   - Unestimated items (no story points)
   - Unassigned high-priority items
   - Blocked items (waiting on other work)
   - Recommended actions (assign, estimate, break down, close as won't fix)

4. Suggest JQL queries for automation:
   - "project = PROJ AND status = 'To Do' AND created <= -30d"
   - "project = PROJ AND status = 'To Do' AND assignee is EMPTY"
   - "project = PROJ AND status = 'To Do' AND story_points is EMPTY"
```

---

## Recipe 5: Developer Velocity Snapshot

**Goal:** Quick team velocity and capacity analysis.

**Prerequisites:**
- Run `developerMetrics.py` locally first
- CSV files generated
- Jira MCP for comparison queries

**Steps:**

1. **Generate metrics locally:**
   ```bash
   python developerMetrics.py --teams Team-Rocket --period 3m \
     --filePrefix /tmp/velocity -o /tmp/velocity
   ```

2. **In Claude Code:**
   ```
   Analyze our team velocity for the last 3 months:
   
   1. Read the CSV files:
      - /tmp/velocity_raw.csv (individual issues)
      - /tmp/velocity_aggregated.csv (weekly summaries)
   
   2. Calculate:
      - Weekly average issue count
      - Weekly average story points (if available)
      - Team member contribution percentages
      - Velocity trend (improving/declining)
      - Capacity utilization
   
   3. Compare against sprint commitments:
      - Query Jira for last 3 sprint data
      - Calculate actual vs planned velocity
      - Identify velocity variance
   
   4. Generate report with:
      - 13-week velocity trend chart (text-based)
      - Team capacity projection
      - Member workload balance
      - Recommendations for sprint planning
   ```

---

## Recipe 6: Link Issues to Epic by Pattern

**Goal:** Bulk-associate issues to an epic based on naming pattern or label.

**Prerequisites:**
- Jira MCP server
- Epic key
- Bulk update authorization

**Steps:**

```
Link all issues matching a pattern to epic PROJ-500:

1. Query for target issues: "project = PROJ AND labels = 'v2-migration'"
2. Get all matching issue keys
3. For each issue:
   - Update with additional_fields: {"epicKey": "PROJ-500"}
   - Log the update
4. Verify all links by querying: "parent = PROJ-500 AND labels = 'v2-migration'"
5. Report:
   - Total issues linked
   - Summary of linked items
   - Any failures and suggested retries
```

---

## Recipe 7: Sprint Planning Assistant

**Goal:** AI-assisted sprint planning with smart ticket suggestions.

**Prerequisites:**
- Jira MCP server
- Current sprint information
- Team capacity data

**Steps:**

```
Help plan next sprint for team-rocket:

1. Get current sprint: jira_search with JQL "sprint = 'Team-Rocket Sprint 47'"
2. Get team capacity:
   - List team members
   - Estimate available hours (standard workday - meetings)
   - Account for PTO/planned absences
3. Get backlog: "project = PROJ AND status = 'To Do' ORDER BY priority DESC"
4. For each backlog item:
   - Check dependencies (would they block other work?)
   - Check team skill match (who is best suited?)
   - Check story point estimation
5. Suggest sprint composition:
   - List of items that fit in capacity
   - Ordered by dependency/priority
   - With suggested assignees
   - Risk warnings (unestimated, blocked, new tech, etc.)
6. Provide:
   - Sprint goal recommendation
   - Capacity utilization forecast
   - Contingency buffer suggestions
   - Items to defer/reject from backlog
```

---

## Recipe 8: Bulk Status Update with Comments

**Goal:** Update multiple tickets to a new status with explanation.

**Prerequisites:**
- Jira MCP server
- Target issues identified
- Appropriate permissions

**Steps:**

```
Update all "In Review" items for project PROJ to "Done" with a comment:

1. Query: "project = PROJ AND status = 'In Review'"
2. Get all matching issue keys
3. For each issue:
   - Get available transitions (jira_get_transitions)
   - Find the transition ID for "Done"
   - Add comment: "Reviewed and approved - moving to done"
   - Execute transition (jira_transition_issue)
4. Report:
   - Count of transitioned issues
   - Any failures (permission denied, blocked transitions)
   - Summary of updated status
```

---

## Recipe 9: Find Duplicate or Related Issues

**Goal:** Identify and report on potentially duplicate issues.

**Prerequisites:**
- Jira MCP server
- Recent issues to scan

**Steps:**

```
Find potentially duplicate issues in PROJ:

1. Query: "project = PROJ AND updated >= -30d ORDER BY created DESC"
2. For each issue:
   - Extract key terms from summary and description
   - Search for similar issues: "project = PROJ AND summary ~ 'key_term'"
3. Calculate similarity score for pairs:
   - Exact title match = high priority duplicate
   - Similar keywords = possible duplicate
   - Same component = higher likelihood of duplication
4. Report:
   - High confidence duplicates (suggest close/merge)
   - Possible related issues (suggest linking)
   - Issues to review manually
5. For duplicates found:
   - Create links: "Duplicate" relationship
   - Add comments suggesting consolidation
```

---

## Recipe 10: Custom Field Audit

**Goal:** Discover all custom fields used in a project and their values.

**Prerequisites:**
- Jira MCP server
- Project to audit

**Steps:**

```
Audit all custom fields in use for project PROJ:

1. Query: "project = PROJ" with fields = '*all'
2. For each issue, identify customfield_* entries
3. Build a catalog:
   - Field ID
   - Field name (extract from response)
   - Data type (string, select, number, etc.)
   - Count of issues using it
   - Sample values
   - Usage percentage
4. Generate report:
   - All custom fields table
   - Frequently used vs rarely used fields
   - Fields with unexpected or inconsistent values
   - Recommendations for cleanup or consolidation
5. Cross-reference with Excel config:
   - Identify which custom fields are mapped in StandardTicketCreator
   - Flag any unmapped fields that should be in scope
```

---

## Tips & Patterns

### Pagination for Large Result Sets

When querying 500+ issues, use pagination:

```
Query: "project = PROJ" with startAt=0, limit=50
Extract startAt/limit from response pagination info
Loop: query with startAt = startAt + limit until no more results
```

### Retry Logic for API Timeouts

For bulk operations, implement retry:

```
For each item:
  Try up to 3 times with exponential backoff (1s, 2s, 4s)
  Log failures separately
  Continue with next item rather than halting
```

### Batch Commits for Visibility

For large changes, commit in groups with checkpoints:

```
Process 10 items
Report progress
Wait for user confirmation or auto-continue after 5s
Next batch of 10
```

### Field Value Escaping

When using JQL or descriptions with special characters:

```
Use quotes for phrases: summary ~ "this is a phrase"
Escape quotes: summary ~ "quote \"nested\" here"
Use backslash for special chars
```

---

## Performance Notes

- **Small operations** (1-10 items): Instant, safe in serial
- **Medium operations** (10-100 items): ~1-2 minutes, can parallelize in batches of 5
- **Large operations** (100+ items): ~5-10 minutes, batch in groups of 10 with delays
- **Queries**: Most JQL queries complete in <1 second for <500 results
- **Rate limits**: Jira allows ~60 requests/minute; add 1s delays between bulk updates

---

## Error Recovery

### Common Errors & Solutions

| Error | Cause | Solution |
|-------|-------|----------|
| Permission Denied | User lacks permissions | Request access or use different field/epic |
| Invalid Field ID | Wrong customfield ID | Use `findCustomFields.py` to discover actual ID |
| Epic Not Found | Wrong epic key | Verify epic exists in Jira |
| Sprint Not Found | Sprint doesn't exist or is closed | Query active sprints first |
| Value Not Allowed | Invalid option for select field | Check field options with `jira_get_field_options` |

---

## Related Documentation

- [claudeCodeIntegration.md](./claudeCodeIntegration.md) — Setup and field mapping reference
- [standardTicketCreator.md](./standardTicketCreator.md) — CSV format and configuration
- [developerMetrics.md](./developerMetrics.md) — Metrics generation and interpretation

# Developer Metrics Documentation

## Overview

`developerMetrics.py` audits developer productivity across teams by fetching completed work from Jira, aggregating by week, and generating CSV reports and PNG visualizations.

## Usage

```bash
python developerMetrics.py --teams <teams> --period <period> --filePrefix <prefix> [options]
```

### Required Arguments

| Argument | Description |
|----------|-------------|
| `--teams` | Comma-separated team names, `'org'` / `'*'` to use teams from config, or `'all'` to audit all Backstage teams |
| `--period` | Time period: `'ytd'`, `'month'`, `'Nm'` (e.g., `'3m'`, `'6m'`), or `'YYYY-MM-DD:YYYY-MM-DD'` for explicit range |
| `--filePrefix` | File prefix for PNG output (generates `{prefix}_{team}_overall.png`) |

### Optional Arguments

| Argument | Description |
|----------|-------------|
| `-o, --output` | CSV output prefix (generates `{prefix}_raw.csv` and `{prefix}_aggregated.csv`) |
| `--backstageUrl` | Override Backstage URL from config |
| `-v, --verbose` | Show detailed logging for debugging |
| `--parallel` | Number of parallel Jira query workers (default: 5, max: 15) |

## Configuration

Requires `~/.jiraTools` with:
- `jira_server`: Jira instance URL
- `personal_access_token`: Jira PAT
- `backstageUrl`: Backstage instance URL
- `orgTeams`: List of team names (optional, for `--teams org`)
- `day_size`: Work hours per day (default: 6)

## Period Format

### Fixed Periods

- **`ytd`** — Year to date (January 1 to today)
- **`month`** — Current month (first day to today)
- **`Nm`** — Last N months (e.g., `'3m'` = last 3 months)

### Custom Date Range

- **`YYYY-MM-DD:YYYY-MM-DD`** — Explicit range with both start and end dates

When using a date range, the JQL query includes both lower and upper bounds:
```
AND updated >= 2026-04-01 AND updated <= 2026-04-30
AND (resolutiondate is EMPTY OR (resolutiondate >= 2026-04-01 AND resolutiondate <= 2026-04-30))
```

## Examples

### Year-to-Date Metrics for Multiple Teams

```bash
python developerMetrics.py --teams "Team-Rocket,Neuron" --period ytd \
  --filePrefix /tmp/ytd_metrics -o /tmp/ytd_metrics
```

### Last 3 Months for a Single Team

```bash
python developerMetrics.py --teams "Team-Rocket" --period 3m \
  --filePrefix /tmp/q1_metrics -o /tmp/q1_metrics -v
```

### Custom Date Range (Specific Sprint)

```bash
python developerMetrics.py --teams "Team-Rocket" --period 2026-04-01:2026-04-30 \
  --filePrefix /tmp/april_metrics -o /tmp/april_metrics
```

### All Org Teams with Verbose Logging

```bash
python developerMetrics.py --teams org --period month \
  --filePrefix /tmp/month_metrics -o /tmp/month_metrics -v --parallel 8
```

## Output

### CSV Files

- **`{prefix}_raw.csv`** — Raw issues with: Team, User, Display Name, Issue Key, Summary, Resolved Date, Original Estimate (weeks), Issue Type

- **`{prefix}_aggregated.csv`** — Weekly aggregation with: Team, User, Display Name, Week Start, Issue Count, Total Estimate (weeks)

### PNG Files

- **`{prefix}_{team}_overall.png`** — Visualization of cumulative issue counts and estimate totals by week per team

## Workflow

1. Parse period argument into JQL date clauses
2. Load team data from Backstage
3. Filter team members (exclude roles: Analyst, Architect, Director, Manager, Product, Program, Project, Scrum)
4. Build JQL queries for each member's completed work in the period
5. Query Jira in parallel (using ThreadPoolExecutor)
6. Aggregate results by week and team
7. Export raw and aggregated CSV files
8. Generate PNG visualizations for each team

## Notes

- **Resolution Date Fallback** — If `resolutiondate` is empty, the script uses the `updated` date as a fallback for assigning issues to weeks
- **Parallel Query Performance** — Increasing `--parallel` improves performance but may hit Jira rate limits; default of 5 is safe
- **Excluded Roles** — Non-technical roles are automatically filtered out to focus on individual contributor metrics
- **Week Bucketing** — Weeks start on Monday; issues are grouped by resolved week

## Related Scripts

- `codeAudit.py` — Audits code quality and compliance
- `teamApplicationAttribution.py` — Maps teams to their owned applications

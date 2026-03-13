# Code Audit Documentation

## Overview

`codeAudit.py` audits code across repositories owned by teams in Backstage. For each team, it queries Backstage for owned application components, clones their git repositories via sparse checkout, fetches a specific file from each repo, and applies a regex pattern to find and report matches.

## Usage

```bash
python codeAudit.py --teams <TEAMS> --checkFilename <FILE> --searchRegex <REGEX> [options]
```

### Required Arguments

| Argument | Description |
|----------|-------------|
| `--teams` | Comma-separated list of team names to audit, or `all` / `*` to audit all teams in Backstage |
| `--checkFilename` | File path to look for in each repository (e.g., `build.gradle`, `Dockerfile`, `pom.xml`) |
| `--searchRegex` | Regex pattern with capture groups to apply to each file |

### Optional Arguments

| Argument | Description |
|----------|-------------|
| `--backstageUrl` | Backstage base URL (overrides `backstageUrl` in `~/.jiraTools` config) |
| `-o, --output` | Export results to a CSV file |
| `-v, --verbose` | Show detailed git operation logging |
| `--compare-repo` | Git repo URL to fetch tags from for version date comparison (requires `--dateTolerance`) |
| `--dateTolerance` | Max age for compliance, e.g., `2d` (days), `3m` (months), `1y` (years). Requires `--compare-repo` |
| `--createTickets` | Excel file with Teams sheet for Jira ticket creation (requires `--compare-repo`, `--dateTolerance`, `--dependencyName`) |
| `--dependencyName` | Dependency name for ticket summaries (e.g., `Spring Boot`). Required by `--createTickets` |
| `-c, --create` | Actually create tickets in Jira (default is dry-run mode) |
| `--parallel N` | Number of parallel async workers for Backstage API calls and git operations (default: 5, max: 15) |

## Configuration

The script reads from `~/.jiraTools` (JSON format):

```json
{
  "backstageUrl": "https://backstage.example.com"
}
```

The `backstageUrl` key is required unless `--backstageUrl` is provided on the command line.

## Examples

```bash
# Audit a single team for Spring Boot versions in Gradle files
python codeAudit.py --teams TeamA --checkFilename build.gradle --searchRegex 'spring-boot:(.+?)'

# Audit multiple teams, export to CSV
python codeAudit.py --teams "TeamA,TeamB" --checkFilename Dockerfile --searchRegex 'FROM (.+)' -o results.csv

# Audit with verbose git output and Backstage URL override
python codeAudit.py --teams MyTeam --checkFilename pom.xml \
  --searchRegex '<version>(.*?)</version>' \
  --backstageUrl https://backstage.mycompany.com -v

# Multiple capture groups (parent artifact + version)
python codeAudit.py --teams DataScienceEngineering --checkFilename pom.xml \
  --searchRegex '<parent>.*?<artifactId>(maven-parent)</artifactId>.*?<version>(.*?)</version>.*?</parent>' \
  -o /tmp/audit.csv

# Version compliance check against a reference repo (filter versions older than 6 months)
python codeAudit.py --teams "TeamA,TeamB" --checkFilename pom.xml \
  --searchRegex '<version>(.*?)</version>' \
  --compare-repo git@github.com:org/shared-library.git --dateTolerance 6m

# Same with CSV export and verbose logging
python codeAudit.py --teams MyTeam --checkFilename build.gradle \
  --searchRegex 'com.example:core:(.+?)' \
  --compare-repo https://github.com/org/core.git --dateTolerance 1y \
  -o /tmp/compliance.csv -v

# Dry-run ticket creation for out-of-compliance repos
python codeAudit.py --teams all --checkFilename build.gradle \
  --searchRegex 'spring-boot:(.+?)' \
  --compare-repo git@github.com:spring-projects/spring-boot.git --dateTolerance 6m \
  --createTickets teams.xlsx --dependencyName "Spring Boot"

# Actually create tickets in Jira
python codeAudit.py --teams "TeamA,TeamB" --checkFilename pom.xml \
  --searchRegex '<version>(.*?)</version>' \
  --compare-repo git@github.com:org/shared-library.git --dateTolerance 1y \
  --createTickets teams.xlsx --dependencyName "Shared Library" -c
```

## Workflow

1. Parse and validate the regex pattern (must have at least one capture group)
2. Validate `--compare-repo` and `--dateTolerance` are both provided or both omitted
3. If `--compare-repo` is provided, fetch all tags from that repo and build a version-to-date map
4. Load config from `~/.jiraTools` and resolve Backstage URL
5. Fetch all Backstage components in a single bulk request
6. Process all teams concurrently (async, bounded by `--parallel` semaphore):
   - Filter components owned by the team (in-memory)
   - Fetch repo URLs for all components concurrently (async via `aiohttp`)
   - Deduplicate repositories across components
   - Sparse-clone all repos concurrently (async subprocesses) and extract the target file
   - Apply regex and collect matches
   - Buffer all output per team for clean sorted display
7. Print buffered output sorted alphabetically by team name
8. If compliance checking is active, filter results to only show out-of-compliance items (version tag date older than tolerance from today). Versions not found in the tag map are included with an "Unknown" date warning
9. Display results with colored output (including "Last Updated" column when compliance checking)
10. Optionally export to CSV with auto-generated column headers from capture groups (plus "Last Updated" when applicable)
11. Report any teams not found in Backstage as errors
12. If `--createTickets` is provided, create Jira tickets for out-of-compliance repos using team configuration from the Excel file

## Output

- **Console**: Color-coded results showing team, repository, and captured values. When `--compare-repo` is used, "Last Updated" date and "Age (in days)" are appended to each result
- **CSV** (with `-o`): Columns are `Team`, `Repository`, plus one column per capture group, plus `Last Updated` and `Age (in days)` when compliance checking is active
- **Summary**: Teams processed, repositories checked, total matches (filtered count when compliance checking)
- **Errors**: Teams not found in Backstage are listed at the end in red
- **Permission denied**: Repositories where git access was denied are listed at the end in yellow. Git never prompts for passwords interactively

## Version Compliance Checking

When `--compare-repo` and `--dateTolerance` are provided together:

1. Tags are fetched from the compare repo and semantic versions (`Major.Minor.Patch`) are extracted from tag names
2. Each audit result's capture groups are checked against the version-to-date map (auto-detected)
3. Results where the matched version's tag date is within the tolerance from today are filtered out (compliant)
4. Only out-of-compliance results (older than tolerance) and results with unknown versions are shown
5. A "Last Updated" column shows the tag creation date (or "Unknown" if the version wasn't found in tags)
6. An "Age (in days)" column shows the number of days since the tag was created (or "Unknown")

## Async Parallelism

The script uses `asyncio` and `aiohttp` to parallelize two major I/O bottlenecks:

1. **Backstage API calls**: Per-component repo URL lookups run concurrently via `aiohttp`
2. **Git sparse-clone operations**: Repository clones run concurrently via `asyncio` subprocesses

All teams are processed concurrently — every team's URL fetches and git clones share a single `asyncio.Semaphore` bounded by `--parallel` (default 5, max 15). Each team buffers its console output; after all teams complete, output is printed sorted alphabetically by team name. Use `--parallel 1` to disable parallelism.

## Dependencies

- `aiohttp` for async HTTP requests
- `git` must be available on PATH (used for sparse checkout)
- Backstage API accessible at the configured URL

## Automatic Ticket Creation

When `--createTickets <excel_file>` is provided along with `--compare-repo`, `--dateTolerance`, and `--dependencyName`, the script can create Jira tickets for out-of-compliance repos.

### How It Works

1. After compliance filtering, the script loads team configuration from the Excel file's Teams sheet (same format as `standardTicketCreator.py`)
2. For each out-of-compliance result, it looks up the team in the Teams sheet to get the Jira project key, issue type, epic link, and assignee
3. One ticket is created per out-of-compliance repo
4. Runs in dry-run mode by default — use `-c`/`--create` to actually create tickets

### Ticket Format

- **Summary**: `Update {repo} {dependencyName} from {version} to latest version`
- **Description**: Contains repository name, current version/date/age, latest available version/date, and a note to verify at the time of work

### Excel Teams Sheet

The Excel file must have a Teams sheet with Key/Field/Value columns (same format used by `standardTicketCreator.py`):

| Key | Field | Value |
|-----|-------|-------|
| TeamA | Project | PROJ |
| TeamA | Epic Link | EPIC-123 |
| TeamA | Assignee | john.doe |
| TeamA | Issue Type | Task |

Teams not found in the mapping are skipped with a warning.

### Configuration

Jira connection uses the same `~/.jiraTools` config:

```json
{
  "jira_server": "https://jira.example.com",
  "personal_access_token": "your_token",
  "backstageUrl": "https://backstage.example.com"
}
```

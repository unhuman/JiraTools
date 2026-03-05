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
| `--teams` | Comma-separated list of team names to audit |
| `--checkFilename` | File path to look for in each repository (e.g., `build.gradle`, `Dockerfile`, `pom.xml`) |
| `--searchRegex` | Regex pattern with capture groups to apply to each file |

### Optional Arguments

| Argument | Description |
|----------|-------------|
| `--backstageUrl` | Backstage base URL (overrides `backstageUrl` in `~/.jiraTools` config) |
| `-o, --output` | Export results to a CSV file |
| `-v, --verbose` | Show detailed git operation logging |

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
```

## Workflow

1. Parse and validate the regex pattern (must have at least one capture group)
2. Load config from `~/.jiraTools` and resolve Backstage URL
3. Fetch all Backstage components in a single bulk request
4. For each team:
   - Filter components owned by the team
   - Deduplicate repositories across components
   - Sparse-clone each repo and extract the target file
   - Apply regex and collect matches
5. Display results with colored output
6. Optionally export to CSV with auto-generated column headers from capture groups
7. Report any teams not found in Backstage as errors

## Output

- **Console**: Color-coded results showing team, repository, and captured values
- **CSV** (with `-o`): Columns are `Team`, `Repository`, plus one column per capture group
- **Summary**: Teams processed, repositories checked, total matches
- **Errors**: Teams not found in Backstage are listed at the end in red

## Dependencies

- `git` must be available on PATH (used for sparse checkout)
- Backstage API accessible at the configured URL

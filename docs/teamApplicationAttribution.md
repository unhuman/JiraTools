# Team Application Attribution Documentation

## Overview

`teamApplicationAttribution.py` queries Backstage for all sprint teams and the applications they own. It creates a JSON file mapping each team to their owned applications, filtering to `type=application` only (excludes libraries, tests, cookbooks, repositories, infrastructure).

## Usage

```bash
python teamApplicationAttribution.py <backstage_url> [options]
```

### Arguments

| Argument | Description |
|----------|-------------|
| `backstage_url` | Base URL for Backstage (e.g., `https://backstage.example.com`). `https://` is auto-prepended if omitted. |
| `-t, --team` | Query only a specific team (e.g., `knightriders`) |
| `-o, --output` | Output JSON file path (default: `allTeamApplications.json`, or `<team>Applications.json` if `--team` is used) |
| `--timeout` | Request timeout in seconds (default: 30) |

## Configuration

No `~/.jiraTools` config required. The Backstage URL is passed as a positional argument.

## Examples

```bash
# Fetch all teams and their applications
python teamApplicationAttribution.py https://backstage.example.com

# Fetch a single team
python teamApplicationAttribution.py https://backstage.example.com -t knightriders

# Custom output file
python teamApplicationAttribution.py https://backstage.example.com -o teams.json
```

## Output

A JSON file mapping team identifiers to their metadata and owned applications:

```json
{
  "team-identifier": {
    "team_name": "team-identifier",
    "team_title": "Team Display Name",
    "domain": "Domain Name",
    "business_unit": "business-unit-name",
    "applications": [
      {
        "name": "service-name",
        "title": "Service Display Title",
        "type": "application",
        "lifecycle": "active",
        "system": "system-name",
        "platform": "platform-name",
        "product": "product-name",
        "business_unit": "business-unit-name",
        "description": "Service description"
      }
    ]
  }
}
```

## Workflow

1. Fetch all sprint teams from Backstage (or a single team if `--team` specified)
2. For each team, query owned components
3. Filter to `type=application` only
4. Extract metadata: name, title, system, platform, product, business unit, lifecycle, description
5. Write JSON output sorted by team name

## Related Scripts

The output JSON is used as input for `serviceConsumerAnalysis.py`.

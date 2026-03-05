# JiraTools
Useful Stuff for working with Jira

## Scripts

| Script | Description | Docs |
|--------|-------------|------|
| `codeAudit.py` | Audits code across team repositories via Backstage + git sparse checkout + regex | [docs/codeAudit.md](docs/codeAudit.md) |
| `epicPlanner.py` | Orders work in an epic based on ticket dependencies | [docs/epicPlanner.md](docs/epicPlanner.md) |
| `epicStatus.py` | Reports on the plan status of an epic by sprint | [docs/epicStatus.md](docs/epicStatus.md) |
| `epicCreationTime.py` | Analyzes development time spans of open epics | [docs/epicCreationTime.md](docs/epicCreationTime.md) |
| `findCustomFields.py` | Discovers custom field IDs by examining a Jira issue | [docs/findCustomFields.md](docs/findCustomFields.md) |
| `pointsToEstimate.py` | Converts story points to time estimates | [docs/pointsToEstimate.md](docs/pointsToEstimate.md) |
| `populateRemainingEstimate.py` | Copies original estimate to remaining estimate | [docs/populateRemainingEstimate.md](docs/populateRemainingEstimate.md) |
| `standardTicketCreator.py` | Creates standardized Jira tickets from Excel + Backstage scorecard data | [docs/standardTicketCreator.md](docs/standardTicketCreator.md) |
| `serviceConsumerAnalysis.py` | Analyzes service consumers using Datadog trace data | [docs/serviceConsumerAnalysis.md](docs/serviceConsumerAnalysis.md) |
| `subtasksUserDifferentParentOwner.py` | Finds subtasks where assignee differs from parent owner | [docs/subtasksUserDifferentParentOwner.md](docs/subtasksUserDifferentParentOwner.md) |
| `teamApplicationAttribution.py` | Maps Backstage teams to their owned applications | [docs/teamApplicationAttribution.md](docs/teamApplicationAttribution.md) |

## Libraries (`libraries/`)
- `jiraToolsConfig.py`: Shared Jira configuration, connection setup, and utility functions (used by most scripts)
- `excelTools.py`: Shared Excel reading utilities and team management functions
- `backstageTools.py`: Shared Backstage API utilities for querying components and teams

## Setup
`pip install colorama jira networkx pandas openpyxl requests`

## Testing
```bash
pip install pytest
python -m pytest tests/ -v
```

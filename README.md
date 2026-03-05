# JiraTools
Useful Stuff for working with Jira

## Scripts:
1. `epicPlanner.py`: Takes an epic and orders the work based on dependencies
1. `epicStatus.py`: Reports on the status of an epic
1. `epicCreationTime.py`: Reports on the time taken to create epics
1. `findCustomFields.py`: Finds custom fields in a Jira instance
1. `pointsToEstimate.py`: Converts story points to time estimates based on a configurable ratio
1. `populateRemainingEstimate.py`: After `pointsToEstimate.py`, this will copy OriginalEstimate -> Remaining
1. `subtasksUserDifferentParentOwner.py`: Finds user contributions on subtasks when parent tickets owned by someone else
1. `standardTicketCreator.py`: Creates standard Jira tickets from an Excel file with team and category data
1. `teamApplicationAttribution.py`: Queries Backstage for all teams and their owned applications (type=application only), outputs a JSON mapping
1. `serviceConsumerAnalysis.py`: Analyzes service consumers using Datadog trace data - finds which services call your team's applications and generates reports aggregated by domain and system
1. `codeAudit.py`: Audits code across team repositories - queries Backstage for team-owned components, fetches a specific file from each repo via sparse git checkout, and applies a regex to extract and report matched values. Usage: `python codeAudit.py --teams "Team-A,Team-B" --checkFilename build.gradle --searchRegex 'pattern'` (Backstage URL from `~/.jiraTools` `backstageUrl` key or `--backstageUrl` flag)

## Libraries (`libraries/`):
- `jiraToolsConfig.py`: Shared Jira configuration, connection setup, and utility functions (used by most scripts)
- `excelTools.py`: Shared Excel reading utilities and team management functions
- `backstageTools.py`: Shared Backstage API utilities for querying components and teams

## Documentation:
- [Standard Ticket Creator Documentation](standardTicketCreator_documentation.md)
- [Service Consumer Analysis Documentation](serviceConsumerAnalysisDocumentation.md)

## Setup: 
`pip install colorama jira networkx pandas openpyxl requests`

## Testing:
```bash
pip install pytest
python -m pytest tests/ -v
```

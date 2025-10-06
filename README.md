# JiraTools
Useful Stuff for working with Jira

## Scripts:
1. `epicPlanner.py`: Takes an epic and orders the work based on dependencies
1. `epicStatus.py`: Reports on the status of an epic
1. `epicCreationTime.py`: Reports on the time taken to create epics
1. `findCustomFields.py`: Finds custom fields in a Jira instance
1. `pointsToHours.py`: Converts story points to hours based on a custom field
1. `populateRemainingEstimate.py`: After `pointsToHours.py`, this will copy OriginalEstimate -> Remaining.
1. `subtasksUserDifferentParentOwner.py`: Finds user contributions on subtasks when parent tickets owned by someone else
1. `standardTicketCreator.py`: Creates standard Jira tickets from an Excel file with team and category data

## Documentation:
- [Standard Ticket Creator Documentation](standardTicketCreator_documentation.md)

## Setup: 
`pip install colorama jira networkx pandas openpyxl`

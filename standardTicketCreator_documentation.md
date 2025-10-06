# Standard Ticket Creator Documentation

## Overview
The `standardTicketCreator.py` script automates the creation of standardized Jira tickets from data stored in an Excel file. It's designed to streamline the process of creating multiple tickets across various categories, with consistent formatting and proper linking to parent epics.

## Requirements

### Python Dependencies
- Python 3.x
- Required packages:
  - jira
  - pandas
  - openpyxl
  - colorama

You can install these dependencies with:
```bash
pip install jira pandas openpyxl colorama
```

### Jira Configuration
The script uses your Jira credentials from the `jiraToolsConfig.py` file. Make sure this file is properly configured with your Jira instance details.

## Usage

### Basic Command
```bash
python standardTicketCreator.py excel_file [options]
```

### Command-line Arguments
- `excel_file`: Path to the Excel file containing team data (required)
- `-i, --issue_type`: Issue type (e.g., 'Task', 'Story', 'Bug'). If provided, overrides the value in Config sheet.
- `-c, --create`: Actually create tickets in Jira. Without this flag, the script runs in dry-run mode
- `--processTeams`: Comma-separated list of teams to process (only these teams will be included)
- `--excludeTeams`: Comma-separated list of teams to exclude from processing

The issue type is determined in this order of precedence:
1. Command-line argument `-i` if provided
2. Value from the Config sheet with key "Issue Type" if present
3. Default value "Task" if neither of the above is available

Additionally, the script uses the Priority value from the Config sheet with key "Priority" if present. This priority will be applied to all tickets created.

#### Team Filtering
The `--processTeams` and `--excludeTeams` arguments are mutually exclusive and provide ways to filter which teams get tickets created:

- When using `--processTeams`, only the teams specified in the comma-separated list will have tickets created
- When using `--excludeTeams`, all teams except those in the comma-separated list will have tickets created
- If neither option is provided, tickets will be created for all teams

### Examples
```bash
# Dry run (no tickets created)
python standardTicketCreator.py team_ticket_defaults.xlsx

# Create tickets as Tasks
python standardTicketCreator.py team_ticket_defaults.xlsx -c

# Create tickets as Stories
python standardTicketCreator.py team_ticket_defaults.xlsx -i Story -c

# Process only specific teams
python standardTicketCreator.py team_ticket_defaults.xlsx --processTeams "TeamA,TeamB,TeamC"

# Exclude specific teams
python standardTicketCreator.py team_ticket_defaults.xlsx --excludeTeams "TeamD,TeamE"

# Create tickets for specific teams only
python standardTicketCreator.py team_ticket_defaults.xlsx -c --processTeams "TeamA,TeamB"
```

### Workflow Example

This section walks through a complete workflow example:

1. **Prepare Excel File**:
   Create an Excel file with Teams sheet and category tabs (see structure below).

2. **Run in Dry Run Mode**:
   ```bash
   python standardTicketCreator.py my_teams.xlsx
   ```

3. **Review Output**:
   ```
   [DRY RUN] Would create ticket: 'Ownership: TeamA' for key 'TeamA' in project RND
     Description: *L Categories:*
   L1: X, L3: X
     Would link to parent epic: RND-12345
   ```

4. **Make Any Necessary Adjustments** to your Excel file.

5. **Create Tickets**:
   ```bash
   python standardTicketCreator.py my_teams.xlsx -c
   ```

6. **Verify in Jira**:
   Check that tickets were created with the correct fields and epic links.

## Excel File Structure

The script expects a specific structure in the Excel file:

### Required Sheets

1. **Teams**: Contains team information including Project and SO Epic details
2. **Config**: Contains configuration settings like Issue Type
3. **Ownership**, **Quality**, **Security**, **Reliability**: These are the standard tabs that the script processes

Each sheet should be formatted with rows of data as described below.

### Complete Excel Structure

Your Excel file should have the following sheets and formats:

#### Config Sheet
| Key | Value |
|-----|-------|
| Issue Type | Story |

#### Teams Sheet
| Sprint Team | Assignee | Project | SO Epic |
|-------------|----------|---------|---------|
| TeamA | jdoe | RND | RND-12345 |
| TeamB | msmith | DEV | DEV-56789 |
| TeamC | rjones | QA | QA-34567 |

#### Ownership Sheet
| Team | L1 | L2 | L3 |
|------|----|----|-----|
| TeamA | X |   | X |
| TeamB |   | X |   |
| TeamC | X | X |   |

#### Quality Sheet
| Team | Q1 | Q2 | Q3 |
|------|----|----|-----|
| TeamA |   | X |   |
| TeamB | X |   | X |
| TeamC |   | X |   |

#### Security Sheet
| Team | S1 | S2 | S3 |
|------|----|----|-----|
| TeamA | X |   |   |
| TeamB |   | X | X |
| TeamC |   |   | X |

#### Reliability Sheet
| Team | R1 | R2 | R3 |
|------|----|----|-----|
| TeamA |   | X | X |
| TeamB | X |   |   |
| TeamC | X | X |   |

Note: The exact category columns (L1, L2, Q1, S1, etc.) can vary based on your needs. What's important is that the first column in each category tab matches the team names from the Teams sheet.



### Config Sheet

The Config sheet contains global configuration settings. It should have two columns:
- First column: Key
- Second column: Value

Available keys:

| Key | Example Value | Description |
|-----|---------------|-------------|
| Issue Type | Task | The type of Jira issue to create (Task, Story, Bug, etc.) |
| Priority | Medium | The priority to set for all created tickets (High, Medium, Low, etc.) |

Example format:

| Key | Value |
|-----|-------|
| Issue Type | Story |
| Priority | High |

### Teams Sheet

The Teams sheet contains configuration for each team with the following columns:

- **Sprint Team**: The team identifier (used to match with other tabs)
- **Assignee**: The person to assign tickets to
- **Project**: The Jira project key for ticket creation (NOT the issue type)
- **SO Epic**: The parent epic key to link created tickets to

Note: The issue type is specified in the Config sheet or via command line parameter (-i). The priority is specified in the Config sheet with key "Priority".

Example format:

| Sprint Team | Assignee | Project | SO Epic |
|-------------|----------|---------|---------|
| TeamA | jdoe | RND | RND-12345 |
| TeamB | msmith | DEV | DEV-56789 |
| TeamC | rjones | QA | QA-34567 |

Each team should have a unique name in the Sprint Team column as this is used to match with team names in other tabs.

#### Important Notes for Teams Sheet:

1. **Unique Team Names**: Each team must have a unique name in the Sprint Team column
2. **Valid Project Keys**: The Project column must contain valid Jira project keys
3. **Epic Format**: The SO Epic column should contain valid Jira epic issue keys (e.g., PRJ-1234)
4. **Assignee Names**: The Assignee column should contain valid Jira usernames

### Category Tabs (Ownership, Quality, Security, Reliability)

Each category tab should follow this format:

1. First column: Team identifier (matches the "Sprint Team" column from the Teams sheet)
2. Subsequent columns: Category fields (e.g., L1, L2, L3, etc.)

Example format for a category tab:

| Team | L1 | L2 | L3 |
|------|----|----|-----|
| TeamA | X | | X |
| TeamB | | X | |
| TeamC | X | X | |

Place an "X" or any non-empty value in cells to indicate selections. For each team with at least one selection in a category tab, a ticket will be created in the corresponding project with these categories listed in the description.

## Ticket Creation Logic

### How It Works

For each team in each category tab (Ownership, Quality, Security, Reliability):

1. If the team has any marked categories (cells with "X" or any value):
   - A ticket is created in the team's specified Jira project (from "Project" column)
   - The summary is formatted as "[Team Name] Scorecards Improvement: [Tab Name]" (e.g., "Analytics Scorecards Improvement: Ownership")
   - The description includes the tab name (e.g., "Category: Ownership") and the selected categories
   - The ticket is linked to the specified epic in the "SO Epic" column
   - The ticket is assigned to the person in the "Assignee" column

2. If a team has no selections in a category tab, no ticket will be created for that team in that category

### Key Features

- **Automatic Epic Linking**: All tickets are automatically linked to their parent epics
- **Team-Specific Projects**: Each team can have its own Jira project
- **Category Grouping**: Related categories (like L1, L2, L3) are grouped in the description
- **Selective Creation**: Only creates tickets for teams with actual category selections

### Category Grouping

When a team has selections in multiple categories, they're grouped in the description by their type:

```
*L Categories:*
L1: X, L3: X
```

### Ticket Linking

Tickets are automatically linked to parent epics specified in the "SO Epic" field of the Teams sheet. This ensures all related tickets are properly organized under their parent epic in Jira.

## Output

The script provides detailed output:
- In dry-run mode: Shows what tickets would be created
- In create mode: Creates actual tickets and shows their IDs
- Shows warnings for teams without category selections
- Displays a summary of created and skipped tickets

## Common Issues and Troubleshooting

### Excel File Issues

1. **Missing Excel file**: 
   - Error: `Error: File not found: [file_path]`
   - Solution: Verify the file path and ensure the file exists

2. **Invalid Excel format**:
   - Error: `Error: File is not an Excel file: [file_path]`
   - Solution: Ensure the file has a .xlsx, .xls, or .xlsm extension and is a valid Excel file

3. **Missing required sheets**:
   - Error: `Error reading Excel file '[sheet_name]': No sheet named '[sheet_name]'`
   - Solution: Verify that your Excel file contains all the required sheets (Teams, Ownership, etc.)

### Ticket Creation Issues

4. **No tickets created**:
   - Symptom: Script runs but shows "Skipping ticket" messages
   - Solution: Check if teams have category selections in the tabs (non-empty cells)
   - Also check if you've used `--processTeams` with incorrect team names or `--excludeTeams` that exclude all teams

5. **Missing Project field**:
   - Message: `Skipping ticket for '[key]' - no Project field specified`
   - Solution: Ensure each team has a valid "Project" field in the Teams sheet

6. **400 Bad Request Error**:
   - Error: HTTP 400 error when creating tickets
   - Solutions: 
     - Make sure the issue type specified with `-i` is valid in your Jira instance
     - Verify that the Project column contains valid Jira project keys
     - Check if required fields for the issue type are missing (some projects require additional fields)

7. **Jira authentication failure**:
   - Error: `Error: Failed to authenticate with Jira`
   - Solution: Check your Jira credentials in the jiraToolsConfig.py file

### Epic Linking Issues

7. **Linking failure**:
   - Warning: `Warning: Could not link ticket [issue_key] to epic [epic_key]`
   - Solutions:
     - Verify the SO Epic key is valid and exists in Jira
     - Ensure your Jira user has permission to link to that epic
     - Check if your Jira instance uses a custom field for epic links

8. **Invalid epic key**:
   - Symptom: Warning about linking failure with an error about the epic key
   - Solution: Ensure the SO Epic value is a valid Jira issue key (e.g., PRJ-1234)

### Advanced Troubleshooting

9. **Customizing error messages**:
   - You can add logging.debug statements to see more detailed information
   - Add `import logging; logging.basicConfig(level=logging.DEBUG)` at the top of the script

10. **Jira field mapping issues**:
    - Error: `Field '[field_name]' cannot be set. It is not on the appropriate screen, or unknown`
    - Solution: Use the findCustomFields.py script to identify the correct field ID for your Jira instance

11. **Team filtering issues**:
    - Warning: `Warning: Some specified teams not found in Teams sheet` or `Warning: No teams match the filter criteria`
    - Solutions:
      - Check for typos in team names provided to `--processTeams` or `--excludeTeams`
      - Verify that teams exist in the Teams sheet (case-sensitive)
      - Use the exact team names as they appear in the 'Key' column of the Teams sheet

## Additional Notes

- Non-empty cells in category columns indicate selection
- Teams without any category selections are skipped
- The script automatically handles different Jira configurations for epic linking
- Team filtering parameters (`--processTeams` and `--excludeTeams`) can be used to process a subset of teams

## Tips for Excel File Management

### Using Excel Features Effectively

1. **Conditional Formatting**: Apply conditional formatting to category cells to make selections more visible:
   - Select the cells in your category columns
   - Add a rule to highlight non-blank cells
   - This makes it easier to see which categories are selected

2. **Data Validation**: Add data validation to category cells to only allow "X" or blank values:
   - Select the category cells
   - Add data validation to allow only "X" or empty cells
   - This helps prevent invalid entries

### Team Filtering Strategies

1. **Process Only Specific Teams**: Use `--processTeams` when you want to create tickets for only a subset of teams:
   - Useful for testing with a small set of teams before processing all teams
   - Example: `python standardTicketCreator.py excel_file.xlsx --processTeams "TeamA,TeamB"`
   - Teams not in the list will be completely ignored

2. **Exclude Problem Teams**: Use `--excludeTeams` when you want to process most teams but skip a few:
   - Useful when certain teams have configuration issues you want to address later
   - Example: `python standardTicketCreator.py excel_file.xlsx --excludeTeams "ProblemTeam1,ProblemTeam2"`
   - All teams except those in the exclude list will be processed

3. **Phased Rollouts**: Use team filtering to implement ticket creation in phases:
   - Phase 1: Process a small pilot group with `--processTeams "Team1,Team2,Team3"`
   - Phase 2: Process additional teams after validating the pilot group
   - Final phase: Process all teams by removing the filtering parameters

3. **Excel Templates**: Create a template file with the correct structure:
   - Set up all sheets with proper columns
   - Add your standard teams
   - Save as an Excel Template (.xltx)
   - Use this template when creating files for each sprint

### Common Mistakes to Avoid

1. **Inconsistent Team Names**: Ensure team names in the category tabs exactly match the "Sprint Team" column in the Teams sheet

2. **Missing Projects**: Every team must have a Project value in the Teams sheet

3. **Invalid Epic Keys**: Make sure SO Epic values are valid Jira issue keys

4. **Special Characters**: Avoid using special characters in team names

## Best Practices and Common Use Cases

### Sprint Planning Workflow

The Standard Ticket Creator is particularly useful during sprint planning:

1. **Pre-Planning**:
   - Create a new Excel file from your template
   - Pre-populate the Teams sheet with current epic links

2. **During Planning**:
   - As teams discuss categories (Ownership, Quality, etc.), mark the cells in real-time
   - Run in dry-run mode periodically to validate selections

3. **Post-Planning**:
   - Finalize the Excel file with all selections
   - Run with the `-c` flag to create all tickets at once
   - Share the Excel file for future reference

### Cross-Team Coordination

The script facilitates cross-team coordination by:

1. **Standardizing Categories**: All teams use the same category definitions
2. **Centralizing Epic Links**: All tickets link to their parent epics automatically
3. **Providing Visibility**: The Excel file serves as a single source of truth

### Regular Maintenance Tasks

For teams with recurring maintenance work:

1. Create a template with standard categories
2. Update only the SO Epic field for each sprint
3. Run the script to create consistent tickets each sprint

## Customization and Advanced Usage

### Advanced Usage

#### Working with Multiple Excel Files

If you need to process multiple Excel files, you can run the script for each file:

```bash
# Process first file
python standardTicketCreator.py team1_tickets.xlsx -c

# Process second file
python standardTicketCreator.py team2_tickets.xlsx -c
```

#### Using Different Issue Types

You can specify different issue types using the `-i` parameter:

```bash
# Create all tickets as Stories instead of Tasks
python standardTicketCreator.py teams.xlsx -i Story -c
```

### Integration Ideas

- **CI/CD Pipeline**: Run automatically after sprint planning to create standard tickets
- **Change Tracking**: Store Excel files in version control to track changes over time
- **Reporting**: Create follow-up scripts to analyze ticket creation patterns
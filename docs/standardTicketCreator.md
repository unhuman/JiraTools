# Standard Ticket Creator Documentation

## Overview
The `standardTicketCreator.py` script automates the creation of standardized Jira tickets by integrating directly with Backstage to retrieve real-time team scorecard data. It analyzes actual compliance levels across categories (Ownership, Quality, Security, Reliability) and creates tickets only for teams that have genuine scorecard improvement opportunities. Team configuration (projects, assignees, epics) is managed through an Excel file, while all scorecard data comes directly from Backstage's SoundCheck API.

**Key Features:**
- **Real-time Analysis**: Queries live Backstage scorecard data via GraphQL API instead of static Excel sheets
- **Track-Based Categorization**: Uses `track_name` field to ensure accurate category assignment without cross-contamination
- **Intelligent Filtering**: Only creates tickets for categories with actual compliance gaps
- **Level-Based Grouping**: Organizes improvement opportunities by compliance level (L1, L2, L3, L4) with total counts
- **Detailed Descriptions**: Provides specific improvement guidance with current vs. target metrics
- **Accurate Level Detection**: Uses actual `backstage_level` field from Backstage data

**Important Terminology:**
- **Category Sheets**: Previously separate Excel sheets named "Ownership", "Quality", "Security", "Reliability" that contained team scorecard data (now eliminated - data comes from Backstage GraphQL API)
- **CustomFields Sheet**: Optional Excel sheet for mapping Jira field names to custom field IDs (still supported and useful for Jira API compatibility)
- **Track Name**: Field from GraphQL API that categorizes each check into Quality, Security, Ownership, or Reliability
- **Backstage Level**: Field from GraphQL API indicating compliance level (L0, L1, L2, L3, L4) for each check

## Requirements

### Python Dependencies
- Python 3.x
- Required packages:
  - jira
  - pandas
  - openpyxl
  - colorama
  - requests

You can install these dependencies with:
```bash
pip install jira pandas openpyxl colorama requests
```

### Jira Configuration
The script uses your Jira credentials from the `jiraToolsConfig.py` file. Make sure this file is properly configured with your Jira instance details.

### Backstage Integration
The script integrates directly with Backstage's SoundCheck API to retrieve real-time scorecard data:
- **Live Scorecard Analysis**: Queries `/api/soundcheck/results` endpoint for actual team compliance data
- **Intelligent Processing**: Only considers rollup checks (team-level scorecards), not individual entity issues
- **Accurate Level Detection**: Determines current compliance level (L1, L2, L3, L4) based on actual performance
- **Smart Ticket Creation**: Only creates tickets for categories with genuine improvement opportunities
- **Team Mapping**: Uses team names from Excel Teams sheet to query corresponding Backstage entities
- **No Authentication**: Currently assumes Backstage API is accessible without authentication

## Usage

### Basic Command
```bash
python standardTicketCreator.py excel_file [options]
```

### Command-line Arguments
- `excel_file`: Path to the Excel file containing team data (required)
- `-i, --issue_type`: Issue type (e.g., 'Task', 'Story', 'Bug'). If provided, overrides the value in the Teams sheet.
- `-c, --create`: Actually create tickets in Jira via API. Without this flag, the script runs in dry-run mode
- `--csv, --export-csv`: Export tickets to CSV file(s) for manual Jira import instead of creating via API. Creates one CSV file per team named `{basename}-{TeamName}.csv`
- `--processTeams`: Comma-separated list of teams to process (only these teams will be included)
- `--excludeTeams`: Comma-separated list of teams to exclude from processing

The issue type is determined in this order of precedence:
1. Command-line argument `-i` if provided
2. Team-specific "Issue Type" from the Teams sheet if present
3. Default value "Task" if none of the above is available

Additionally, the script uses the Priority value from the Config sheet with key "Priority" if present. This priority will be applied to all tickets created.

**Note**: The `-c/--create` and `--csv/--export-csv` options are mutually exclusive. Use `-c` to create tickets directly via Jira API, or use `--csv` to export to CSV files for manual import.

#### Team Filtering
The `--processTeams` and `--excludeTeams` arguments are mutually exclusive and provide ways to filter which teams get tickets created:

- When using `--processTeams`, only the teams specified in the comma-separated list will have tickets created
- When using `--excludeTeams`, all teams except those in the comma-separated list will have tickets created
- If neither option is provided, tickets will be created for all teams

### Examples
```bash
# Dry run (no tickets created)
python standardTicketCreator.py team_ticket_defaults.xlsx

# Create tickets directly via Jira API as Tasks
python standardTicketCreator.py team_ticket_defaults.xlsx -c

# Create tickets as Stories
python standardTicketCreator.py team_ticket_defaults.xlsx -i Story -c

# Export to CSV files for manual import (one file per team)
python standardTicketCreator.py team_ticket_defaults.xlsx --csv output.csv

# Export to CSV for specific teams only
python standardTicketCreator.py team_ticket_defaults.xlsx --csv output.csv --processTeams "TeamA,TeamB"

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
   Create an Excel file with Config sheet (including Backstage URL) and Teams sheet.

2. **Run in Dry Run Mode**:
   ```bash
   python standardTicketCreator.py my_teams.xlsx
   ```

3. **Review Output**:
   ```
   [DRY RUN] Would create ticket: 'Analytics Scorecards Improvement: Quality' for key 'Analytics' in project ANL
     Description: *Backstage Scorecards Category:* Quality

   *Current Compliance Level:* L2

   *Improvement Opportunities:* (4 total)

   **L1 Issues:**
     • **SonarQube Code Coverage (30%)** - 4/28 (14%):
       - Current: 4/28 components (14%)
       - Need to improve: 24 additional components

   **L3 Issues:**
     • **SonarQube Code Coverage (70%)** - 3/28 (11%):
       - Current: 3/28 components (11%)
       - Need to improve: 25 additional components

   **L4 Issues:**
     • **SonarQube Code Coverage (90%)** - 1/28 (4%):
       - Current: 1/28 components (4%)
       - Need to improve: 27 additional components

     Would link to parent epic: ANL-4096
   ```

4. **Make Any Necessary Adjustments** to your Excel file.

5. **Create Tickets** (choose one method):
   
   **Option A: Direct API Creation**
   ```bash
   python standardTicketCreator.py my_teams.xlsx -c
   ```

   **Option B: CSV Export for Manual Import**
   ```bash
   python standardTicketCreator.py my_teams.xlsx --csv output.csv
   ```
   This creates separate CSV files per team (e.g., `output-Analytics.csv`, `output-Vroom.csv`)

6. **Verify in Jira**:
   - If using API creation: Check that tickets were created with the correct fields and epic links
   - If using CSV export: Import each CSV file using Jira's CSV importer (see [CSV Import Instructions](#csv-export-and-jira-import))

## CSV Export and Jira Import

### Overview

The script supports exporting ticket data to CSV files for manual import into Jira. This is useful when:
- You want to review ticket data before importing
- You need to import into multiple projects with different configurations
- Your Jira instance has API restrictions or special requirements
- You want to use Jira's CSV import configuration features

### CSV Export Features

**Automatic Team Separation**: The script creates one CSV file per team, making it easier to import tickets into different projects or manage imports by team.

**File Naming**: If you specify `--csv output.csv`, the script generates:
- `output-Analytics.csv`
- `output-Vroom.csv`
- `output-SimpliFly.csv`
- etc. (one file per Sprint Team)

**CSV Format**: The CSV files follow Jira's import requirements:
- Uses proper CSV quoting (`QUOTE_NONNUMERIC`) - all strings are quoted, numbers are not
- Description is the last column to handle multi-line content properly
- Column order: Summary, Issue Type, Project Key, Priority, Assignee, Epic Link, Sprint, Component, Labels, Sprint Team, Description
- Proper newline handling in multi-line Description fields
- Jira wiki markup formatting preserved

### Exporting to CSV

```bash
# Export all teams
python standardTicketCreator.py team_ticket_defaults.xlsx --csv output.csv

# Export specific teams only
python standardTicketCreator.py team_ticket_defaults.xlsx --csv output.csv --processTeams "TeamA,TeamB"

# Export excluding certain teams
python standardTicketCreator.py team_ticket_defaults.xlsx --csv output.csv --excludeTeams "ProblemTeam"
```

### Importing CSV Files into Jira

After generating CSV files, you can import them into Jira:

#### Step 1: Open Jira CSV Importer

1. Navigate to your Jira project (e.g., the project specified in your Teams sheet)
2. Click on Project Settings (gear icon)
3. Select "Import" from the left sidebar
4. Choose "CSV" as the import source

#### Step 2: Upload CSV File

1. Click "Choose File" and select one of the generated CSV files (e.g., `output-Analytics.csv`)
2. Click "Next"

#### Step 3: Configure Field Mappings

Jira will automatically detect most fields, but you should verify the mappings:

**Standard Field Mappings** (usually auto-detected):
- Summary → Summary
- Issue Type → Issue Type
- Project Key → (automatically determined from project)
- Description → Description
- Priority → Priority
- Assignee → Assignee
- Component → Components
- Labels → Labels

**Custom Field Mappings** (may need manual configuration):
- Sprint Team → Your custom "Sprint Team" field (e.g., `customfield_12900`)
- Epic Link → Epic Link (e.g., `customfield_10506`)
- Sprint → Sprint (e.g., `customfield_10505`)

**IMPORTANT**: Make sure the Description field mapping does NOT create value mappings. See the configuration note below.

#### Step 4: Configure Value Mappings

**Critical Configuration**: In the "Map field values" step, ensure that:
- **DO NOT** create value mappings for the Description field
- The `config.value.mappings` section should be empty: `"config.value.mappings" : { }`
- If Jira auto-generates value mappings for Description, delete them

**Why This Matters**: Jira's CSV importer may try to create value mappings that strip newlines from multi-line descriptions, causing them to become single-line text.

#### Step 5: Import Configuration File

You can use a pre-configured import configuration to ensure consistent imports. The repository includes a template configuration file: `JiraImport-generic-configuration.txt`

**To use the configuration file**:

1. Open `JiraImport-generic-configuration.txt` in a text editor
2. Update the project information:
   ```json
   "config.project" : {
     "project.type" : null,
     "project.key" : "YOUR_PROJECT_KEY",
     "project.description" : null,
     "project.url" : null,
     "project.name" : "YOUR_PROJECT_NAME",
     "project.lead" : "your_username"
   }
   ```
3. Update custom field IDs if they differ in your Jira instance:
   ```json
   "Sprint Team" : {
     "existing.custom.field" : "12900"  // Update if different
   },
   "Sprint" : {
     "existing.custom.field" : "10505"  // Update if different
   },
   "Epic Link" : {
     "existing.custom.field" : "10506"  // Update if different
   }
   ```
4. In the Jira CSV importer, after uploading your CSV file, you can import this configuration by clicking "Import configuration" and selecting your updated configuration file
5. Review the mappings and proceed with the import

**Configuration Template** (from `JiraImport-generic-configuration.txt`):
```json
{
  "config.version" : "2.0",
  "config.project.from.csv" : "false",
  "config.encoding" : "UTF-8",
  "config.email.suffix" : "@",
  "config.field.mappings" : {
    "Assignee" : { "jira.field" : "assignee" },
    "Issue Type" : { "jira.field" : "issuetype" },
    "Description" : { "jira.field" : "description" },
    "Sprint Team" : { "existing.custom.field" : "12900" },
    "Priority" : { "jira.field" : "priority" },
    "Summary" : { "jira.field" : "summary" },
    "Sprint" : { "existing.custom.field" : "10505" },
    "Component" : { "jira.field" : "components" },
    "Epic Link" : { "existing.custom.field" : "10506" }
  },
  "config.value.mappings" : { },
  "config.delimiter" : ",",
  "config.project" : {
    "project.key" : "BAD",
    "project.name" : "CHANGE ME",
    "project.lead" : "unknown"
  },
  "config.date.format" : "dd/MMM/yy h:mm a"
}
```

**Key Points**:
- `config.value.mappings` must be empty (`{ }`)
- Update `project.key`, `project.name`, and `project.lead` to match your target project
- Verify custom field IDs match your Jira instance (use `findCustomFields.py` to discover them)

#### Step 6: Validate and Import

1. Review the preview of tickets to be imported
2. Verify that multi-line descriptions display correctly with proper formatting
3. Click "Begin Import"
4. Wait for the import to complete
5. Review any error messages and fix issues if needed

### CSV Import Best Practices

1. **Test with Small Batches**: Import one team's CSV file first to verify the configuration works correctly
2. **Save Configuration**: After successfully importing, save the configuration file for future imports
3. **Check Descriptions**: After import, open a few tickets to verify that multi-line descriptions formatted correctly with Jira wiki markup
4. **Verify Custom Fields**: Ensure Sprint Team, Epic Link, and Sprint fields populated correctly
5. **One Team at a Time**: Import one team's CSV file at a time to make troubleshooting easier

### Troubleshooting CSV Import

**Problem**: Descriptions appear as single lines without formatting
- **Cause**: Value mappings created for Description field
- **Solution**: Delete value mappings for Description; ensure `config.value.mappings` is empty

**Problem**: Custom fields not mapping correctly
- **Cause**: Incorrect custom field IDs
- **Solution**: Use `findCustomFields.py` to discover correct IDs for your Jira instance

**Problem**: Import fails with validation errors
- **Cause**: Project configuration requires additional fields
- **Solution**: Check project's required fields and add them to CSV or modify project settings

**Problem**: Epic Link not creating proper links
- **Cause**: Epic Link custom field ID incorrect or epic doesn't exist
- **Solution**: Verify epic exists and custom field ID is correct

**Problem**: Sprint assignment not working
- **Cause**: Sprint ID incorrect or sprint closed/completed
- **Solution**: Verify sprint is active and use correct numeric Sprint ID

### CSV vs API Creation

**When to use CSV Export**:
- Need to review all tickets before creating them
- Want to import using Jira's built-in validation and error handling
- Different projects have different custom field requirements
- Need to batch imports by team or project
- Jira API has rate limits or restrictions

**When to use API Creation** (`-c` flag):
- Want immediate ticket creation
- Confident in configuration and field mappings
- Processing single project with consistent settings
- Need automated/scripted ticket creation

Both methods create identical tickets; choose based on your workflow needs.

## Excel File Structure

The script expects a specific structure in the Excel file:

### Required Sheets

1. **Config**: Contains configuration settings like Priority and Backstage URL (required)
2. **Teams**: Contains team information including Project, Epic Link, and Issue Type details (required)

**Important**: The script no longer requires category sheets (separate Excel sheets named "Ownership", "Quality", "Security", "Reliability" that previously contained team scorecard data) in Excel. All scorecard data is retrieved directly from Backstage's SoundCheck API in real-time, ensuring accuracy and eliminating the need to manually maintain category data in spreadsheets.

**Note**: The optional "CustomFields" sheet is still supported and is different from category sheets - it maps Jira field names to custom field IDs for API compatibility.

### Optional Sheets

1. **CustomFields**: Maps field names to Jira custom field IDs

Each sheet should be formatted with rows of data as described below.

### Complete Excel Structure

Your Excel file should have the following sheets and formats:

#### Config Sheet
| Key | Value |
|-----|-------|
| Priority | 4-Medium |
| Backstage | https://backstage.core.cvent.org |
| Categories | Ownership, Quality, Security, Reliability |

#### CustomFields Sheet (Optional)
| Field Name | Custom Field ID | Data Wrapper |
|------------|----------------|--------------|
| Sprint Team | customfield_12900 | value |
| Epic Link | customfield_10506 | none |
| Sprint | customfield_10505 | none |
| Story Points | customfield_10002 | none |

The **Data Wrapper** column controls how field values are formatted when sent to the Jira API:
- When set to "value": The value is wrapped like `{"value": "field_value"}`
- When set to "none" or left empty: The value is sent directly without wrapping
- Any other string: The value is wrapped using that string as the key: `{"wrapper_name": "field_value"}`

This flexibility allows compatibility with different Jira custom field formats.

#### Teams Sheet
| Sprint Team | Assignee | Project | Epic Link | Issue Type | Sprint | Sprint Name | Component |
|-------------|----------|---------|----------|------------|--------|-------------|-----------|
| TeamA | jdoe | RND | RND-12345 | Story | 29311 | CRM Planning | Backend |
| TeamB | msmith | DEV | DEV-56789 | Task | | | Frontend |
| TeamC | rjones | QA | QA-34567 | Bug | 29312 | Platform Sprint 5 | |

**Important**: Category sheets (separate Excel sheets previously named "Ownership", "Quality", "Security", "Reliability" that contained team scorecard data) are completely eliminated from the Excel file. All scorecard data is retrieved in real-time from Backstage's SoundCheck API, providing accurate, up-to-date compliance information and intelligent ticket creation based on actual team performance.

**Note**: The optional "CustomFields" sheet (for mapping Jira field names to custom field IDs) is still supported and remains useful for Jira API compatibility.



### Config Sheet

The Config sheet contains global configuration settings. It should have two columns:
- First column: Key
- Second column: Value

Available keys:

| Key | Example Value | Description |
|-----|---------------|-------------|
| Priority | 4-Medium | The priority to set for all created tickets (e.g., "3-High", "4-Medium", "5-Low") |
| Backstage | https://backstage.core.cvent.org | The base URL for your Backstage instance (required for scorecard data integration) |
| Categories | Ownership, Quality, Security, Reliability | Comma-separated list of categories to process for ticket creation |

Example format:

| Key | Value |
|-----|-------|
| Priority | 4-Medium |
| Backstage | https://backstage.core.cvent.org |
| Categories | Ownership, Quality, Security, Reliability |

### Teams Sheet

The Teams sheet contains configuration for each team with the following columns:

- **Sprint Team**: The team identifier (used to match with other tabs)
- **Assignee**: The person to assign tickets to
- **Project**: The Jira project key for ticket creation
- **Epic Link**: The parent epic key to link created tickets to
- **Issue Type**: (Optional) The Jira issue type for tickets created for this team (e.g., Story, Task, Bug)
- **Sprint**: (Optional) The numeric Sprint ID to assign tickets to for this team
- **Sprint Name**: (Optional) The human-readable sprint name for convenience/information only - not used by the script
- **Component**: (Optional) The Jira component to assign tickets to for this team

Note: If Issue Type is not specified in the Teams sheet, it will use the default "Task" value or the command line parameter (-i) if provided. The priority is specified in the Config sheet with key "Priority". The Sprint field is optional and when present, the specified numeric Sprint ID will be assigned to tickets created for that team. Sprint IDs can be found in Jira by looking at the sprint details or URL when viewing a sprint. The Sprint Name column is provided for convenience and reference only - the script uses the numeric Sprint field for actual sprint assignment. The Component field is optional and when present, the specified component will be assigned to tickets created for that team.

Example format:

| Sprint Team | Assignee | Project | Epic Link | Issue Type | Sprint | Sprint Name | Component |
|-------------|----------|---------|----------|------------|--------|-------------|-----------|
| TeamA | jdoe | RND | RND-12345 | Story | 29311 | CRM Planning | Backend |
| TeamB | msmith | DEV | DEV-56789 | Task | | | Frontend |
| TeamC | rjones | QA | QA-34567 | Bug | 29312 | Platform Sprint 5 | |
| TeamB | msmith | DEV | DEV-56789 | Task | | |
| TeamC | rjones | QA | QA-34567 | Bug | 29312 | Platform Sprint 5 |
| TeamC | rjones | QA | QA-34567 | Bug | 29312 |

Each team should have a unique name in the Sprint Team column as this is used to match with team names in other tabs.

#### Important Notes for Teams Sheet:

1. **Unique Team Names**: Each team must have a unique name in the Sprint Team column
2. **Valid Project Keys**: The Project column must contain valid Jira project keys
3. **Epic Format**: The Epic Link column should contain valid Jira epic issue keys (e.g., PRJ-1234)
4. **Issue Types**: The Issue Type column should contain valid Jira issue types (e.g., Story, Task, Bug)
5. **Assignee Names**: The Assignee column should contain valid Jira usernames
6. **Sprint Field**: The Sprint column is optional; when provided, tickets will be assigned to the specified sprint using the numeric Sprint ID (e.g., 29311, not "Sprint 42")
7. **Sprint Name Column**: The Sprint Name column is for convenience/reference only and is not processed by the script - use the Sprint column for actual sprint assignment
8. **Component Field**: The Component column is optional; when provided, tickets will be assigned to the specified component name (e.g., "Backend", "Frontend")
9. **Team Health Automation**: Team names are automatically used to query Backstage for scorecard/health data - ensure team names match your Backstage entity names for automatic data extraction

### Special Field Requirements

#### Sprint Field
The Sprint field has specific requirements:

1. **Must be numeric**: Use the Sprint ID (e.g., 29311) not the sprint name (e.g., "Sprint 42")
2. **Requires CustomFields mapping**: Add Sprint to your CustomFields sheet with:
   - Field Name: `Sprint`
   - Custom Field ID: The Sprint custom field ID from your Jira instance (e.g., `customfield_10505`)
   - Data Wrapper: `none`
3. **Finding Sprint IDs**: You can find Sprint IDs by:
   - Looking at the sprint URL in Jira (the number in the URL)
   - Using the Jira API to list sprints
   - Checking sprint details in your Jira board

#### Sprint Name Column
The Sprint Name column is optional and serves as a convenience/reference field:

1. **Information Only**: This column is not processed by the script and has no functional impact
2. **Human Readable**: Use this to store readable sprint names (e.g., "CRM Planning", "Platform Sprint 5") 
3. **Reference Purpose**: Helps users understand which sprint the numeric Sprint ID refers to
4. **Not Required**: This column can be left empty without affecting ticket creation

**Important**: Always use the numeric **Sprint** column for actual sprint assignment, not the Sprint Name column.

#### Component Field
The Component field is a standard Jira field with specific requirements:

1. **Must match existing components**: Use component names that exist in the target Jira project (e.g., "Backend", "Frontend", "API")
2. **Single component only**: Each team can specify one component - if multiple components are needed, use a comma-separated list
3. **Case sensitive**: Component names are case-sensitive and must match exactly as defined in Jira
4. **Project specific**: Components must exist in the specific Jira project where the ticket will be created
5. **Finding components**: You can find available components by:
   - Looking at existing tickets in the project
   - Checking the project settings in Jira
   - Using the Jira API to list project components

#### Epic Link Field
Epic Link is handled automatically for most Jira instances, but may require CustomFields mapping in some configurations.

### Backstage Integration Details

The script retrieves scorecard data directly from Backstage using GraphQL API for comprehensive and accurate track data:

#### Real-Time Scorecard Analysis

The script uses Backstage's SoundCheck GraphQL API to analyze actual team compliance data:

1. **Primary API**: Uses `/api/soundcheck/graphql` with `getAllCertifications` query for comprehensive track data
2. **Track-Based Filtering**: Uses `track_name` field from GraphQL response to categorize checks (Quality, Security, Ownership, Reliability)
3. **Level Detection**: Automatically determines current compliance level (L1, L2, L3, L4) from actual check results using `backstage_level` field
4. **Gap Analysis**: Identifies specific improvement opportunities with detailed metrics, grouped by compliance level
5. **Fallback APIs**: Falls back to REST API `/api/soundcheck/results` if GraphQL is unavailable

#### Supported Health Categories

The enhanced extraction automatically detects and processes:

- **Ownership**: Team ownership, maintainer, and responsibility metrics
- **Quality**: Code quality, test coverage, SonarQube integration, linting results
- **Security**: Security scans, vulnerability assessments, authentication checks
- **Reliability**: Uptime, SLA compliance, monitoring, alerting configurations

#### Intelligent Compliance Level Detection

The system performs sophisticated analysis of actual Backstage scorecard data:

- **Real-Time Analysis**: Processes live GraphQL certification data to determine current compliance levels
- **Track-Based Categorization**: Uses `track_name` field to ensure each check appears only in its correct category (Quality, Security, Ownership, Reliability)
- **Level Mapping**: Uses `backstage_level` field from GraphQL response to accurately map checks to compliance levels (L1, L2, L3, L4)
- **Current vs. Target**: Determines highest passed level and identifies improvement opportunities for higher levels
- **Detailed Metrics**: Extracts component counts and percentages from check results for specific improvement guidance
- **Grouped Output**: Organizes improvement opportunities by level, with total count of issues displayed at the top

#### Smart API Integration

The system uses a focused approach for maximum accuracy and performance:

1. **Primary**: `/api/soundcheck/graphql` with `getAllCertifications` query - Comprehensive track-level compliance data with definitive category assignment via `track_name` field
2. **Fallback**: `/api/soundcheck/results?entityRef=group:default/{team-name}` - REST API for backward compatibility
3. **Catalog**: `/api/catalog/entities/by-name/group/default/{team-name}` - Team entity information (if needed)

The GraphQL API provides the most accurate and up-to-date scorecard information with proper track categorization, ensuring each check appears only in its correct category (Quality, Security, Ownership, or Reliability) based on the `track_name` field from Backstage.

## Ticket Creation Logic

### How It Works

For each team in the Teams sheet, the script:

1. **Analyzes Live Data**: Queries Backstage GraphQL API (`getAllCertifications`) to get real-time certification data with track information
2. **Categorizes by Track**: Uses `track_name` field to accurately assign checks to categories (Quality, Security, Ownership, Reliability)
3. **Determines Current Level**: Uses `backstage_level` field to identify the team's current compliance level (L1, L2, L3, L4) for each category
4. **Identifies Gaps**: Only processes categories with actual improvement opportunities (failing higher-level checks)
5. **Creates Intelligent Tickets**: For categories needing improvement:
   - Creates a ticket in the team's specified Jira project (from "Project" column)
   - The summary is formatted as "[Team Name] Scorecards Improvement: [Category Name]" (e.g., "Analytics Scorecards Improvement: Quality")
   - The description shows current level and specific improvement targets grouped by level with total count
   - The ticket is linked to the specified epic in the "Epic Link" column
   - The ticket is assigned to the person in the "Assignee" column
6. **Skips Maximum Compliance**: If a team is already at maximum compliance level for a category, no ticket is created

### Key Features

- **Real-Time GraphQL Analysis**: Uses live Backstage GraphQL API data with track categorization instead of static Excel sheets
- **Track-Based Filtering**: Uses `track_name` field to prevent cross-category contamination (e.g., SonarQube checks appear only in their correct category)
- **Data-Driven Levels**: Uses actual `backstage_level` field from Backstage instead of hardcoded logic
- **Level-Based Grouping**: Organizes improvement opportunities by compliance level with total count header
- **Intelligent Filtering**: Only creates tickets for genuine compliance gaps, not teams already at maximum levels
- **Detailed Descriptions**: Provides specific improvement guidance with current vs. target metrics grouped by level
- **Accurate Level Detection**: Shows current compliance level and what's needed for higher levels
- **Automatic Epic Linking**: All tickets are automatically linked to their parent epics
- **Team-Specific Projects**: Each team can have its own Jira project
- **Component-Level Details**: Shows exactly how many components need improvement for each level

### Detailed Improvement Descriptions

Ticket descriptions provide specific, actionable guidance for each compliance level, organized by level with a total count:

```
*Current Compliance Level:* L2

*Improvement Opportunities:* (7 total)

**L2 Issues:**
  • **Sonar Integration** - 21/25 (84%):
    - Current: 21/25 components (84%)
    - Need to improve: 4 additional components

**L3 Issues:**
  • **SonarQube Code Coverage (70%)** - 3/28 (11%):
    - Current: 3/28 components (11%)
    - Need to improve: 25 additional components

  • **Tech Docs** - 11/28 (39%):
    - Current: 11/28 components (39%)
    - Need to improve: 17 additional components

**L4 Issues:**
  • **SonarQube Code Coverage (90%)** - 1/28 (4%):
    - Current: 1/28 components (4%)
    - Need to improve: 27 additional components
```

This format clearly shows:
- Total count of improvement opportunities at the top
- Issues grouped by compliance level (L1, L2, L3, L4)
- Specific check names with current compliance percentages
- Exact component counts and improvement targets

### Ticket Linking

Tickets are automatically linked to parent epics specified in the "Epic Link" field of the Teams sheet. This ensures all related tickets are properly organized under their parent epic in Jira.

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
   - Symptom: Script runs but shows "At maximum compliance level - no improvement needed"
   - This is normal behavior: The script only creates tickets for teams with actual compliance gaps
   - If teams are already at maximum compliance levels, no tickets will be created (this is correct)
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
     - Verify the Epic Link key is valid and exists in Jira
     - Ensure your Jira user has permission to link to that epic
     - Check if your Jira instance uses a custom field for epic links

8. **Invalid epic key**:
   - Symptom: Warning about linking failure with an error about the epic key
   - Solution: Ensure the Epic Link value is a valid Jira issue key (e.g., PRJ-1234)

### Advanced Troubleshooting

9. **Customizing error messages**:
   - You can add logging.debug statements to see more detailed information
   - Add `import logging; logging.basicConfig(level=logging.DEBUG)` at the top of the script

10. **Jira field mapping issues**:
    - Error: `Field '[field_name]' cannot be set. It is not on the appropriate screen, or unknown`
    - Solution: Use the findCustomFields.py script to identify the correct field ID for your Jira instance
    - Solution: Create a CustomFields sheet to map your field names to the correct Jira custom field IDs
    
11. **Custom field values not being sent properly**:
    - Symptom: Custom field values are not appearing in Jira tickets when created
    - Solution: Add the field to the CustomFields sheet with the proper custom field ID
    - Solution: Verify the field format in the CustomFields sheet is correct (e.g., "Sprint Team" maps to "customfield_10123")
    - Note: Many custom fields require values in the format {"value": "your_value"}, which the script handles automatically

11. **Team filtering issues**:
    - Warning: `Warning: Some specified teams not found in Teams sheet` or `Warning: No teams match the filter criteria`
    - Solutions:
      - Check for typos in team names provided to `--processTeams` or `--excludeTeams`
      - Verify that teams exist in the Teams sheet (case-sensitive)
      - Use the exact team names as they appear in the 'Key' column of the Teams sheet

## Additional Notes

- **No Excel Category Sheets Required**: All scorecard data comes from Backstage GraphQL API in real-time
- **Track-Based Categorization**: Uses `track_name` field for accurate category assignment, preventing issues like SonarQube checks appearing in multiple categories
- **Data-Driven Implementation**: Uses actual `backstage_level` field from Backstage instead of hardcoded level logic
- **Intelligent Ticket Creation**: Teams at maximum compliance levels will not have tickets created (this is correct behavior)
- **Accurate Analysis**: GraphQL certifications API provides comprehensive track-level data with definitive category assignment
- **Level-Based Organization**: Improvement opportunities are grouped by compliance level (L1, L2, L3, L4) with total counts
- **Detailed Guidance**: Ticket descriptions provide specific component counts and improvement targets organized by level
- **Team filtering parameters** (`--processTeams` and `--excludeTeams`) can be used to process a subset of teams
- The script automatically handles different Jira configurations for epic linking

## CustomFields Sheet

The CustomFields sheet provides a way to map human-readable field names to Jira custom field IDs without modifying the code.

### Purpose

- Maps user-friendly field names to Jira's internal custom field IDs (e.g., `customfield_10123`)
- Allows adding new custom fields or updating field IDs without code changes
- Makes the Excel file easier to understand by using readable field names

### Format

The CustomFields sheet should have three columns:
1. **Field Name**: The user-friendly name used in other sheets (e.g., "Sprint Team")
2. **Custom Field ID**: The corresponding Jira custom field ID (e.g., "customfield_10123")
3. **Data Wrapper**: (Optional) How to format the field value for the Jira API:
   - "value": Format as `{"value": field_value}` (most common for custom fields)
   - "none" or empty: Use the value directly without wrapping
   - Any other string: Use as the wrapper name, e.g., "name" creates `{"name": field_value}`

Example:
```
| Field Name      | Custom Field ID   | Data Wrapper |
|-----------------|------------------|--------------|
| Sprint Team     | customfield_12900| value        |
| Epic Link       | customfield_10506| none         |
| Sprint          | customfield_10505| none         |
| Story Points    | customfield_10002| none         |
| Reporter        | reporter         | name         |
```

### How It Works

When a field name in your Teams sheet matches an entry in the CustomFields sheet:

1. The script will automatically use the corresponding custom field ID when creating the ticket
2. The value will be formatted based on the Data Wrapper column:
   - If set to "value": formatted as `{"value": "field_value"}`
   - If set to "none" or empty: used directly without wrapping
   - If set to any other value (e.g., "name"): formatted as `{"name": "field_value"}`
3. This allows you to use consistent field names across your Excel file while ensuring the correct Jira field IDs and data formats are used

### Special Handling for Assignee Field

The assignee field is handled differently from other fields to ensure reliable ticket assignment:

1. The assignee value is extracted from fields before creating the ticket
2. The ticket is first created without an assignee
3. After successful ticket creation, a separate API request sets the assignee
4. This approach avoids potential issues with assignee field formatting during initial ticket creation

This special handling ensures that ticket assignments work reliably across different Jira configurations and project settings.

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

1. **No Category Sheets Needed**: Don't create separate Excel sheets named "Ownership", "Quality", "Security", or "Reliability" for scorecard data - they are ignored and all scorecard data comes from Backstage (the optional "CustomFields" sheet for Jira field mapping is still supported)

2. **Team Names in Backstage**: Ensure team names in the Teams sheet match the entity names in Backstage (case-insensitive matching is supported)

3. **Missing Projects**: Every team must have a Project value in the Teams sheet

4. **Invalid Epic Keys**: Make sure Epic Link values are valid Jira issue keys

5. **Expecting Tickets for Compliant Teams**: If teams are already at maximum compliance levels, no tickets will be created (this is correct behavior)

6. **Special Characters**: Avoid using special characters in team names

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
2. Update only the Epic Link field for each sprint
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

## Version History

### Version 2.1 (January 2026)
- **CSV Export Feature**: Added `--csv` option to export tickets to CSV files for manual Jira import
- **Team-Based File Generation**: Creates one CSV file per Sprint Team for easier import management
- **Jira Wiki Markup**: Updated description formatting to use proper Jira wiki markup instead of Markdown
- **Import Configuration**: Added `JiraImport-generic-configuration.txt` template for consistent CSV imports
- **Documentation**: Added comprehensive CSV export and Jira import instructions

### Version 2.0 (October 2025) - Major Release
- **BREAKING CHANGE**: Eliminated Excel category sheets (separate "Ownership", "Quality", "Security", "Reliability" sheets) - all scorecard data now comes from Backstage SoundCheck GraphQL API
- **GraphQL Integration**: Primary integration with `/api/soundcheck/graphql` using `getAllCertifications` query for comprehensive track data
- **Track-Based Categorization**: Uses `track_name` field from GraphQL response to ensure accurate category assignment and prevent cross-category contamination
- **Data-Driven Levels**: Uses actual `backstage_level` field from Backstage instead of hardcoded level logic
- **Level-Based Grouping**: Organizes improvement opportunities by compliance level (L1, L2, L3, L4) with total count header
- **Intelligent Filtering**: Only creates tickets for teams with actual compliance gaps
- **Detailed Descriptions**: Added specific improvement guidance with current vs. target metrics grouped by level
- **Accurate Level Detection**: Determines current compliance level and shows improvement opportunities based on actual Backstage data

### Version 1.1 (October 2025)
- Added support for CustomFields sheet to map field names to Jira custom field IDs
- Fixed issue with Sprint Team field not being properly passed to the Jira API
- Improved error handling and logging for API requests

### Version 1.0 (Initial Release)
- Base functionality for creating tickets from Excel data with static category sheets (separate "Ownership", "Quality", "Security", "Reliability" sheets)
- Support for Teams, Config, and manual category sheets for scorecard data
- Team filtering with --processTeams and --excludeTeams parameters
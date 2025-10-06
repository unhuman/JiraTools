# This script creates standard Jira tickets based on data from an Excel file
# Runs in dry-run mode by default - use -c/--create to actually create tickets
# pip install colorama | jira | pandas | openpyxl

import argparse
import os
import pandas as pd
from colorama import init, Fore, Style
import jira
import sys
import json
import requests
import traceback
from jiraToolsConfig import load_config

# static array of Tabs to process in the excel file.
TAB_NAMES = ["Ownership", "Quality", "Security", "Reliability"]

# Constants for column names
PROJECT_FIELD = "Project"
SERVICE_OWNERSHIP_EPIC_FIELD = "SO Epic"
EPIC_LINK_TYPE = "Epic-Story Link"  # The link type used to connect stories to epics
CONFIG_SHEET = "Config"
ISSUE_TYPE_KEY = "Issue Type"
PRIORITY_KEY = "Priority"  # The key for the Priority field in the Config sheet

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Create standard Jira tickets from Excel data.")
    parser.add_argument("excel_file", help="Path to the Excel file containing team data", default="teams.xlsx")
    parser.add_argument("-c", "--create", action="store_true", help="Actually create tickets in Jira (default is dry-run mode)")
    
    # Team filtering parameters (mutually exclusive)
    team_group = parser.add_mutually_exclusive_group()
    team_group.add_argument("--processTeams", help="Comma-separated list of teams to process (if provided, only these teams will be processed)")
    team_group.add_argument("--excludeTeams", help="Comma-separated list of teams to exclude from processing")
    
    # Note in help text about using Project field for project key
    parser.epilog = ("Note: Project key is determined by the 'Project' field in the Teams sheet.\n"
                   "Issue type is determined by the 'Issue Type' field in the Teams sheet.\n"
                   "Priority is read from the 'Config' sheet with key 'Priority'.\n"
                   "Each ticket will be linked to the 'SO Epic' specified in the Teams sheet.\n"
                   "Use --processTeams to specify which teams to process or --excludeTeams to exclude specific teams.")
    
    return parser.parse_args()

def read_config_sheet(file_path):
    """Read the Config sheet from the Excel file to get configuration values."""
    try:
        # Read the Config sheet
        df = pd.read_excel(file_path, sheet_name=CONFIG_SHEET)
        
        # Convert to a key-value dictionary
        config = {}
        for _, row in df.iterrows():
            if len(row) >= 2:  # Ensure the row has at least 2 columns
                key = str(row.iloc[0]).strip()
                value = str(row.iloc[1]).strip()
                if key and value != 'nan':
                    config[key] = value
        
        return config
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Could not read Config sheet: {str(e)}. Using default values.{Style.RESET_ALL}")
        return {}

def validate_file(file_path):
    """Validate that the file exists and is an Excel file."""
    if not os.path.exists(file_path):
        print(f"{Fore.RED}Error: File not found: {file_path}{Style.RESET_ALL}")
        return False
    
    if not file_path.lower().endswith(('.xlsx', '.xls', '.xlsm')):
        print(f"{Fore.RED}Error: File is not an Excel file: {file_path}{Style.RESET_ALL}")
        return False
    
    return True

def filter_excel_columns(df):
    """Filter out columns labeled 'ColumnX'."""
    columns_to_keep = [col for col in df.columns if not col.startswith('Column')]
    return df[columns_to_keep]

def transform_to_key_value_format(df):
    """Transform dataframe to use first column as keys with subsequent columns as values."""
    if len(df.columns) < 2:
        print(f"{Fore.YELLOW}Warning: Excel file does not have enough columns for transformation. Returning None.{Style.RESET_ALL}")
        return None
    
    first_col_name = df.columns[0]
    result_df = pd.DataFrame()
    
    # Process each unique key value
    for key_value in df[first_col_name].unique():
        key_rows = df[df[first_col_name] == key_value]
        result_df = process_key_rows(result_df, key_rows, key_value, df.columns[1:])
    
    print(f"Transformed data from {len(df)} rows to {len(result_df)} key-value pairs")
    
    # Check if the transformation resulted in an empty DataFrame or missing required columns
    if result_df.empty:
        print(f"{Fore.YELLOW}Warning: No usable data found in sheet after transformation.{Style.RESET_ALL}")
        return None
    
    # Ensure required columns exist
    if not all(col in result_df.columns for col in ["Key", "Field", "Value"]):
        print(f"{Fore.YELLOW}Warning: Transformed data does not have the required column structure.{Style.RESET_ALL}")
        return None
        
    return result_df

def process_key_rows(result_df, key_rows, key_value, columns):
    """Process rows for a specific key value and columns."""
    new_rows = []
    
    for col in columns:
        for _, row in key_rows.iterrows():
            # Skip empty values
            if pd.notna(row[col]) and str(row[col]).strip() != '':
                new_row = {
                    'Key': key_value,
                    'Field': col,
                    'Value': row[col]
                }
                new_rows.append(new_row)
    
    if new_rows:
        return pd.concat([result_df, pd.DataFrame(new_rows)], ignore_index=True)
    return result_df

def read_excel_file(file_path, sheet_name):
    """Read the Excel file into a pandas DataFrame.
    
    Args:
        file_path (str): Path to the Excel file
        sheet_name (str): Name of the sheet to read
        
    Returns:
        DataFrame: Transformed dataframe with key-value pairs
    """
    try:
        # Read the Excel file
        df = pd.read_excel(file_path, sheet_name=sheet_name)
        print(f"Reading from sheet: {sheet_name}")
        
        # Check if the sheet is empty
        if df.empty:
            print(f"{Fore.YELLOW}Warning: Sheet '{sheet_name}' is empty.{Style.RESET_ALL}")
            return None
            
        # Filter and transform the data
        df = filter_excel_columns(df)
        df = transform_to_key_value_format(df)
        return df
        
    except Exception as e:
        print(f"{Fore.RED}Error reading Excel file '{sheet_name}': {str(e)}{Style.RESET_ALL}")
        return None

def validate_data(df):
    """Validate that the dataframe has the required columns for the transformed data."""
    required_columns = ["Key", "Field", "Value"]
    
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        print(f"{Fore.RED}Error: Missing required columns: {', '.join(missing_columns)}{Style.RESET_ALL}")
        print(f"Available columns: {', '.join(df.columns)}")
        return False
    
    return True

def link_to_epic(jira_client, issue_key, epic_key):
    """Link an issue to an epic using various methods."""
    if not epic_key or str(epic_key) == 'nan':
        return False
        
    try:
        # Try multiple approaches to link to epic
        # Method 1: Try to update the Epic Link custom field (common in many Jira instances)
        try:
            jira_client.update_issue_field(issue_key, {'customfield_10000': epic_key})
            return True
        except Exception:
            pass
            
        # Method 2: Try using the Epic-Story Link issue link type
        try:
            jira_client.create_issue_link(EPIC_LINK_TYPE, epic_key, issue_key)
            return True
        except Exception:
            pass
            
        # Method 3: Fallback to "Relates to" if other methods fail
        jira_client.create_issue_link('Relates to', epic_key, issue_key)
        return True
            
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Could not link ticket {issue_key} to epic {epic_key}: {str(e)}{Style.RESET_ALL}")
        return False

def validate_required_fields(project_key, issue_type, summary):
    """Validate required fields before creating a ticket."""
    errors = []
    
    if not project_key:
        errors.append("Missing required field: project key")
    
    if not issue_type:
        errors.append("Missing required field: issue type")
    
    if not summary:
        errors.append("Missing required field: summary")
    
    return errors

def prepare_issue_dict(project_key, issue_type, summary, description, fields):
    """Prepare the issue dictionary for Jira API."""
    # Validate required fields
    validation_errors = validate_required_fields(project_key, issue_type, summary)
    if validation_errors:
        print(f"{Fore.YELLOW}Warning: Field validation issues detected:{Style.RESET_ALL}")
        for error in validation_errors:
            print(f"{Fore.YELLOW}- {error}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}This may cause a 400 Bad Request error when submitting to Jira.{Style.RESET_ALL}")
    
    # Create base issue dictionary
    issue_dict = {
        'project': {'key': project_key},
        'summary': summary,
        'description': description,
        'issuetype': {'name': issue_type},
    }
    
    # Extract SO Epic field if present for special handling later
    epic_field_name = SERVICE_OWNERSHIP_EPIC_FIELD
    epic_value = None
    
    # Make a copy of fields to preserve the original
    fields_copy = fields.copy()
    
    # Check if SO Epic field is present
    if epic_field_name in fields_copy:
        epic_value = fields_copy.pop(epic_field_name)
    
    # Process fields to add to the issue dictionary
    process_fields_for_jira(fields_copy, issue_dict)
    
    # Return the prepared data
    return (issue_dict, epic_value)

def process_fields_for_jira(fields, issue_dict):
    """Process and filter fields before sending to Jira API."""
    # Define known Jira standard fields that require special handling
    standard_fields = {
        # 'assignee': 'name', # assignee field does not work on initial create
        'reporter': 'name',
        'priority': 'name',
        'components': 'name',  # List of component names
        'labels': None,  # Simple list
        'duedate': None,  # String in format 'YYYY-MM-DD'
        'fixVersions': 'name',  # List of version names
        'versions': 'name',  # List of version names
    }
    
    # Add any additional fields provided
    for field, value in fields.items():
        # Skip empty, NaN values, or special fields not meant for Jira API
        if not value or str(value).lower() == 'nan' or field == 'Project':
            continue
            
        # Handle standard fields with special formatting requirements
        field_lower = field.lower()
        if field_lower in standard_fields:
            format_type = standard_fields[field_lower]
            add_standard_field(issue_dict, field, value, format_type)
        
        # Handle custom fields and other fields (always prefixed with 'customfield_' or contain a dot)
        elif field.startswith('customfield_') or '.' in field:
            issue_dict[field] = value
        else:
            # Skip unknown fields to avoid API errors
            print(f"{Fore.YELLOW}Skipping unknown field '{field}' to avoid Jira API errors{Style.RESET_ALL}")

def add_standard_field(issue_dict, field, value, format_type):
    """Format and add a standard field to the issue dictionary."""
    if format_type == 'name':
        if isinstance(value, list):
            # Handle list of values (components, fixVersions, etc.)
            issue_dict[field] = [{'name': item} for item in value]
        else:
            # Handle single value
            issue_dict[field] = {'name': value}
    else:
        # Fields that don't need special formatting (labels, duedate, etc.)
        issue_dict[field] = value

def log_issue_fields(issue_dict):
    """Log the fields being sent to Jira API."""
    import json
    
    print(f"{Fore.CYAN}Sending to Jira API:{Style.RESET_ALL}")
    
    # Log individual fields in a readable format
    for field, value in issue_dict.items():
        if field == 'project':
            print(f"{Fore.CYAN}  project: {value['key']}{Style.RESET_ALL}")
        elif field == 'issuetype':
            print(f"{Fore.CYAN}  issuetype: {value['name']}{Style.RESET_ALL}")
        elif field == 'description' and value:
            # Show truncated description for readability
            desc_preview = value[:100] + ('...' if len(value) > 100 else '')
            print(f"{Fore.CYAN}  description: {desc_preview}{Style.RESET_ALL}")
        else:
            print(f"{Fore.CYAN}  {field}: {value}{Style.RESET_ALL}")
    
    # Also provide a JSON representation for easy copy-paste
    # Replace complex objects with strings for JSON serialization
    json_dict = {}
    for k, v in issue_dict.items():
        if k == 'project':
            json_dict[k] = {'key': v['key']}
        elif k == 'issuetype':
            json_dict[k] = {'name': v['name']}
        else:
            json_dict[k] = v
    
    print(f"\n{Fore.CYAN}JSON Payload:{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{json.dumps(json_dict, indent=2)}{Style.RESET_ALL}")

def log_request_details(jira_client, issue_dict):
    """Log the Jira API request details including headers and payload."""
    # Get Jira server URL from client for logging
    jira_url = jira_client._options['server']
    print(f"{Fore.CYAN}Jira server URL: {jira_url}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}API endpoint: {jira_url}/rest/api/2/issue{Style.RESET_ALL}")
    
    # Log authentication method
    auth_method = "Personal Access Token" if hasattr(jira_client, "_session") and jira_client._session.auth else "Unknown"
    print(f"{Fore.CYAN}Authentication method: {auth_method}{Style.RESET_ALL}")
    
    # Log request headers (as much as we can access)
    print(f"{Fore.CYAN}Request Headers:{Style.RESET_ALL}")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    # Try to extract actual headers if possible
    if hasattr(jira_client, "_session") and hasattr(jira_client._session, "headers"):
        for key, value in jira_client._session.headers.items():
            # Mask token values for security
            if "token" in key.lower() or "auth" in key.lower():
                headers[key] = "********" 
            else:
                headers[key] = value
    
    for header, value in headers.items():
        print(f"{Fore.CYAN}  {header}: {value}{Style.RESET_ALL}")
    
    # Log the complete request payload
    print(f"{Fore.CYAN}Request Payload:{Style.RESET_ALL}")
    request_payload = {
        "fields": issue_dict
    }
    print(f"{Fore.CYAN}{json.dumps(request_payload, indent=2)}{Style.RESET_ALL}")
    
    # Show common field formatting examples for reference
    print(f"{Fore.CYAN}Common Field Formats (for reference):{Style.RESET_ALL}")
    print(f"{Fore.CYAN}  Standard fields: project, summary, description, issuetype{Style.RESET_ALL}")
    print(f"{Fore.CYAN}  Object fields: assignee:{{'name': 'username'}}, priority:{{'name': 'High'}}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}  Custom fields: Use field ID (customfield_XXXXX) for custom fields{Style.RESET_ALL}")
    
    # Identify potential field format issues
    for field, value in issue_dict.items():
        if field.lower() in ['assignee', 'reporter', 'priority'] and not isinstance(value, dict):
            print(f"{Fore.YELLOW}Warning: Field '{field}' should typically be an object with a 'name' property{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}  Current: '{field}': {value}{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}  Expected: '{field}': {{'name': '{value}'}}{Style.RESET_ALL}")

def log_response_headers(response):
    """Log HTTP response headers."""
    if response and hasattr(response, 'headers'):
        print(f"{Fore.RED}Response Headers:{Style.RESET_ALL}")
        for header, value in response.headers.items():
            print(f"{Fore.RED}  {header}: {value}{Style.RESET_ALL}")

def log_request_headers(req):
    """Log HTTP request headers."""
    if hasattr(req, 'headers'):
        print(f"{Fore.RED}Request Headers:{Style.RESET_ALL}")
        for header, value in req.headers.items():
            # Mask sensitive values
            if header.lower() in ('authorization', 'cookie'):
                print(f"{Fore.RED}  {header}: ********{Style.RESET_ALL}")
            else:
                print(f"{Fore.RED}  {header}: {value}{Style.RESET_ALL}")

def log_request_body(req):
    """Log HTTP request body."""
    if hasattr(req, 'body') and req.body:
        print(f"{Fore.RED}Request Body:{Style.RESET_ALL}")
        try:
            # Try to parse as JSON for pretty printing
            body = json.loads(req.body.decode('utf-8'))
            print(f"{Fore.RED}{json.dumps(body, indent=2)}{Style.RESET_ALL}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            # If not JSON or can't decode, just print the body
            print(f"{Fore.RED}Raw body (not JSON): {req.body}{Style.RESET_ALL}")

def log_request_details_from_response(response):
    """Log HTTP request details extracted from the response object."""
    if response and hasattr(response, 'request'):
        req = response.request
        print(f"{Fore.RED}Request Method: {req.method}{Style.RESET_ALL}")
        print(f"{Fore.RED}Request URL: {req.url}{Style.RESET_ALL}")
        
        # Log request headers
        log_request_headers(req)
        
        # Log request body
        log_request_body(req)

def handle_http_error(e):
    """Handle and log HTTP errors from Jira API."""
    print(f"{Fore.RED}HTTP Error during API request: {str(e)}{Style.RESET_ALL}")
    
    # Extract status code and response details
    response = e.response if hasattr(e, 'response') else None
    status_code = response.status_code if response else "Unknown"
    
    print(f"{Fore.RED}Status code: {status_code}{Style.RESET_ALL}")
    
    # Log response headers
    log_response_headers(response)
    
    # Log request details
    log_request_details_from_response(response)
    
    # Handle specific status codes
    if response and status_code == 400:
        handle_bad_request(response)

def handle_bad_request(response):
    """Handle 400 Bad Request errors with detailed logging."""
    print(f"{Fore.RED}Bad Request (400) - Invalid input data{Style.RESET_ALL}")
    try:
        error_data = response.json()
        print(f"{Fore.RED}Error details:{Style.RESET_ALL}")
        print(f"{Fore.RED}{json.dumps(error_data, indent=2)}{Style.RESET_ALL}")
        
        # Check for common error patterns
        if 'errors' in error_data:
            for field, error in error_data['errors'].items():
                print(f"{Fore.RED}Field '{field}': {error}{Style.RESET_ALL}")
                
                # Provide guidance for common error fields
                if field == 'project':
                    print(f"{Fore.YELLOW}TIP: Check that project key '{field}' exists in Jira and is accessible by your user{Style.RESET_ALL}")
                elif field == 'issuetype':
                    print(f"{Fore.YELLOW}TIP: Check that issue type is valid for this project. Valid types may include: Task, Bug, Story, etc.{Style.RESET_ALL}")
                elif 'customfield' in field:
                    print(f"{Fore.YELLOW}TIP: Custom field '{field}' may not be properly configured or may require specific format{Style.RESET_ALL}")
                elif field.lower() in ['assignee', 'reporter', 'priority']:
                    print(f"{Fore.YELLOW}TIP: '{field}' should be formatted as an object: {{'name': 'value'}}{Style.RESET_ALL}")
        
        if 'errorMessages' in error_data:
            for msg in error_data['errorMessages']:
                print(f"{Fore.RED}Error message: {msg}{Style.RESET_ALL}")
                # Check for common field format issues in error messages
                if "Field" in msg and "is not available" in msg:
                    field = msg.split("'")[1] if "'" in msg else "unknown"
                    print(f"{Fore.YELLOW}TIP: Field '{field}' is not recognized. Custom fields may need to use ID (customfield_XXXXX){Style.RESET_ALL}")
                elif "could not be set" in msg.lower():
                    print(f"{Fore.YELLOW}TIP: A field value has incorrect format. Check object fields like assignee, priority, etc.{Style.RESET_ALL}")
        
        # Check if it might be an authentication problem        
        if any("authentication" in str(msg).lower() for msg in error_data.get('errorMessages', [])):
            print(f"{Fore.YELLOW}TIP: This may be an authentication issue. Check your Jira token is valid and has not expired.{Style.RESET_ALL}")
                
    except Exception:
        # If can't parse as JSON, show raw response
        print(f"{Fore.RED}Raw response: {response.text}{Style.RESET_ALL}")
        
    # General guidance for 400 errors
    print(f"{Fore.YELLOW}Common causes for 400 errors:{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}1. Required fields missing (check project, issuetype, summary){Style.RESET_ALL}")
    print(f"{Fore.YELLOW}2. Invalid project key or issue type{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}3. Custom field format incorrect - custom fields may need IDs like customfield_10001{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}4. Standard fields like 'assignee' need object format: {{'name': 'username'}}{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}5. Authentication or permissions issues{Style.RESET_ALL}")

def create_jira_ticket(jira_client, project_key, issue_type, summary, description, **fields):
    """Create a Jira ticket with the given fields."""
    # Add a separator for better log readability
    print(f"\n{Fore.CYAN}{'=' * 80}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Creating Jira ticket for {project_key} - {summary}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'-' * 80}{Style.RESET_ALL}")
    
    # Prepare the issue dictionary
    issue_data = prepare_issue_dict(project_key, issue_type, summary, description, fields)
    issue_dict, epic_link = issue_data
    
    # Log the content being sent to Jira API in a readable format
    log_issue_fields(issue_dict)
    
    print(f"{Fore.CYAN}{'-' * 80}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Submitting to Jira...{Style.RESET_ALL}")
    
    try:
        # Log detailed request information including headers and payload
        log_request_details(jira_client, issue_dict)
        
        # Create the issue with extended error handling
        new_issue = jira_client.create_issue(fields=issue_dict)
        
        print(f"{Fore.GREEN}Successfully created ticket: {new_issue.key}{Style.RESET_ALL}")
        
        # Link to parent epic if specified
        if epic_link and str(epic_link) != 'nan':
            if link_to_epic(jira_client, new_issue.key, epic_link):
                print(f"{Fore.CYAN}Linked ticket {new_issue.key} to parent epic {epic_link}{Style.RESET_ALL}")
        
        return new_issue
        
    except requests.exceptions.HTTPError as e:
        handle_http_error(e)
        raise
        
    except Exception as e:
        print(f"{Fore.RED}Error creating ticket: {str(e)}{Style.RESET_ALL}")
        print(f"{Fore.RED}Error type: {type(e).__name__}{Style.RESET_ALL}")
        raise
        
    finally:
        print(f"{Fore.CYAN}{'=' * 80}{Style.RESET_ALL}\n")

def display_data_info(df, excel_file):
    """Display basic information about the imported data."""
    print(f"\n{Fore.CYAN}Excel File: {excel_file}{Style.RESET_ALL}")
    print(f"Number of tickets to create: {len(df)}")
    print(f"Columns: {', '.join(df.columns)}")

def confirm_operation(args, tab_count=None, issue_type=None):
    """Confirm operation mode with the user."""
    if issue_type is None:
        issue_type = "Task"  # Default if not specified
    
    # Display mode message
    if args.create:
        print(f"{Fore.YELLOW}Running in CREATE mode - tickets will be created in Jira as default issue type '{issue_type}' (unless team-specific){Style.RESET_ALL}")
    else:
        print(f"{Fore.CYAN}Running in DRY-RUN mode - tickets would be created as default issue type '{issue_type}' (unless team-specific){Style.RESET_ALL}")
    
    # Confirm before proceeding with actual ticket creation
    if args.create:
        if tab_count is not None and tab_count > 0:
            confirm = input(f"\n{Fore.YELLOW}WARNING: This will create actual tickets in Jira from {tab_count} tabs. Continue? (y/n): {Style.RESET_ALL}")
        else:
            confirm = input(f"\n{Fore.YELLOW}WARNING: This will create actual tickets in Jira. Continue? (y/n): {Style.RESET_ALL}")
        
        if confirm.lower() != 'y':
            print(f"{Fore.RED}Operation cancelled by user.{Style.RESET_ALL}")
            return None
        
    return issue_type

def group_rows_by_key(df):
    """Group dataframe rows by key into ticket data structure."""
    ticket_data = {}
    
    for _, row in df.iterrows():
        key = row['Key']
        field = row['Field']
        value = row['Value']
        
        if key not in ticket_data:
            ticket_data[key] = {}
        
        # Some fields might appear multiple times, so collect in lists
        ticket_data[key] = add_to_team_field(ticket_data[key], field, value)
    
    return ticket_data

def format_summary(summary, tab_name):
    """Format summary with tab name according to the specified format."""
    if not tab_name:
        return summary
    
    # Extract the team name from summary (usually it's just the team name)
    team_name = summary
    if isinstance(summary, list):
        team_name = ', '.join(map(str, summary))
    
    # Format as "Team Scorecards Improvement: TabName"
    return f"{team_name} Scorecards Improvement: {tab_name}"

def add_team_fields(additional_fields, team_info):
    """Add team fields to additional fields without overriding existing values."""
    result = additional_fields.copy()
    
    for team_field, team_value in team_info.items():
        if team_field not in additional_fields and team_field not in ['Summary', 'Description', ISSUE_TYPE_KEY]:
            # Only add non-empty fields
            if isinstance(team_value, str) and team_value.strip() or not isinstance(team_value, str):
                result[team_field] = team_value
    
    return result

def enhance_description_with_grouped_fields(description, grouped_fields, tab_name=None):
    """Enhance description with the grouped fields information and tab name."""
    if not description:
        description = ""
    
    # Add tab name at the beginning of the description if provided
    if tab_name:
        if description:
            description = f"*Backstage Scorecards Category:* {tab_name}\n\n{description}"
        else:
            description = f"*Backstage Scorecards Category:* {tab_name}"
    
    # Add an empty line if the description is not empty
    if description and not description.endswith('\n\n'):
        description += '\n\n'
    
    # Add grouped fields to the description
    for field_prefix, value in grouped_fields.items():
        # Only process grouped fields (with prefixes like 'L') which typically contain commas
        # Don't include fields that are sent to the API directly
        if (isinstance(value, str) and ',' in value and 
            len(field_prefix) <= 3):  # Short prefixes like 'L' are likely patterns, not regular fields
            # Convert comma-separated values to an unordered list
            items = value.split(", ")
            list_items = "\n* ".join([""] + items)
            description += f"*Address the Following Compliance Level(s):*{list_items}\n\n"
    
    return description.rstrip()

def prepare_ticket_fields(fields, key, team_mapping, tab_name):
    """Prepare ticket fields with team mapping data."""
    # Extract basic fields
    summary = fields.get('Summary', key)
    description = fields.get('Description', '')
    
    # Remove summary and description from additional fields
    additional_fields = {k: v for k, v in fields.items() if k not in ['Summary', 'Description']}
    
    # Initialize variables to be extracted from team_info if available
    project = None
    issue_type = None
    
    # Check if this team is in the filtered team mapping
    if team_mapping is not None and key not in team_mapping:
        # Set a flag to indicate this team was filtered out
        additional_fields['is_filtered_out'] = True
    
    # Add team information if available
    if team_mapping and key in team_mapping:
        team_info = team_mapping[key]
        
        # Extract project from team info if available
        if PROJECT_FIELD in team_info:
            project = team_info[PROJECT_FIELD]
            
        # Extract issue type from team info if available
        if ISSUE_TYPE_KEY in team_info:
            issue_type = team_info[ISSUE_TYPE_KEY]
        
        # Format summary with tab name
        summary = format_summary(summary, tab_name)
        
        # Add team fields without overriding
        additional_fields = add_team_fields(additional_fields, team_info)
        
        # Use team description if none provided
        if not description and 'Description' in team_info:
            description = team_info['Description']
    elif tab_name:
        # If no team mapping but we have a tab name, still format the summary
        summary = format_summary(summary, tab_name)
    
    # Group related fields - this returns fields for API and grouped fields for display
    fields_for_api, grouped_display_fields = group_related_fields(additional_fields)
    
    # Enhance description with grouped fields information and tab name
    description = enhance_description_with_grouped_fields(description, grouped_display_fields, tab_name)
    
    return summary, description, fields_for_api, project, issue_type

def get_display_mode_info(is_dry_run, ticket_key):
    """Get the mode prefix and color for ticket display."""
    if is_dry_run:
        return "[DRY RUN] Would create", Fore.BLUE
    else:
        # If we have a ticket_key, it means the ticket was created
        # Otherwise, we're about to create it
        mode_prefix = "Created" if ticket_key else "Creating"
        return mode_prefix, Fore.GREEN

def display_epic_info(additional_fields, is_dry_run):
    """Display epic linking information if available."""
    epic_field = SERVICE_OWNERSHIP_EPIC_FIELD
    if epic_field in additional_fields and additional_fields[epic_field]:
        epic = additional_fields[epic_field]
        epic_action = "Would link" if is_dry_run else "Linked"
        print(f"{Fore.CYAN}  {epic_action} to parent epic: {epic}{Style.RESET_ALL}")

def display_categories(additional_fields):
    """Display category fields and return whether any were found."""
    has_categories = False
    for field, value in additional_fields.items():
        if isinstance(value, str) and ',' in value:
            print(f"{Fore.BLUE}  {field} Categories: {value}{Style.RESET_ALL}")
            has_categories = True
    return has_categories

def display_ticket_details(key, summary, description, project_key, additional_fields, issue_type, is_dry_run=True, ticket_key=None):
    """Display ticket details, for both dry run and actual creation."""
    # Get mode prefix and color based on context
    mode_prefix, color = get_display_mode_info(is_dry_run, ticket_key)
    
    # Format ticket ID if available
    ticket_id = f"{ticket_key} - " if ticket_key else ""
    
    # Display main ticket information
    print(f"{color}{mode_prefix} ticket: '{ticket_id}{summary}' for key '{key}' in project {project_key} as issue type '{issue_type}'{Style.RESET_ALL}")
    
    # Show description preview if available
    if description:
        print(f"{Fore.BLUE}  Description: {description[:100]}{'...' if len(description) > 100 else ''}{Style.RESET_ALL}")
    
    # Show epic information
    display_epic_info(additional_fields, is_dry_run)
    
    # Show categories and check if any were found
    has_categories = display_categories(additional_fields)
    
    # Note if no categories were found
    if not has_categories:
        print(f"{Fore.YELLOW}  Note: No category selections found for this ticket{Style.RESET_ALL}")

def create_single_ticket(jira_client, project_key, issue_type, key, summary, description, additional_fields, create_mode):
    """Create a single Jira ticket or simulate in dry run."""
    # If no project key and key wasn't in the team mapping (due to filtering),
    # this team was likely excluded by processTeams/excludeTeams filters
    if not project_key:
        # Check if this is likely due to a team filter rather than missing Project field
        if 'is_filtered_out' in additional_fields and additional_fields['is_filtered_out']:
            print(f"{Fore.CYAN}Skipping ticket for '{key}' - team excluded by filter{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}Skipping ticket for '{key}' - no Project field specified{Style.RESET_ALL}")
        return None, key
    
    # Always display the ticket details for both dry run and creation modes
    display_ticket_details(key, summary, description, project_key, additional_fields, issue_type, 
                          is_dry_run=not create_mode)
    
    # If we're in dry run mode, we're done after displaying details
    if not create_mode:
        return None, None
    
    # Otherwise, create the ticket
    try:
        new_issue = create_jira_ticket(jira_client, project_key, issue_type, summary, description, **additional_fields)
        # Display the creation result with the actual ticket key
        display_ticket_details(key, summary, description, project_key, additional_fields, issue_type, 
                              is_dry_run=False, ticket_key=new_issue.key)
        return new_issue.key, None
    except requests.exceptions.HTTPError as e:
        # HTTP errors are already handled in create_jira_ticket with detailed logging
        # Just return the failure result here
        return None, key
    except Exception as e:
        print(f"{Fore.RED}Unexpected error creating ticket for key '{key}': {str(e)}{Style.RESET_ALL}")
        import traceback
        print(f"{Fore.RED}Stack trace: {traceback.format_exc()}{Style.RESET_ALL}")
        return None, key

def extract_field_prefix(field):
    """Extract the prefix from a field name like 'L1', 'L2', etc."""
    import re
    match = re.match(r'^([A-Za-z]+)(\d+)$', field)
    if match:
        return match.group(1)
    return None

def sort_fields_numerically(fields):
    """Sort fields like L1, L2, L3 numerically by their number component."""
    import re
    
    def get_number(field):
        match = re.match(r'^[A-Za-z]+(\d+)$', field)
        return int(match.group(1)) if match else 0
        
    return sorted(fields, key=get_number)

def collect_field_values(fields, related_fields):
    """Collect values from related fields."""
    values = []
    for field in related_fields:
        # Check if field exists and has a non-empty value
        if field in fields and fields[field]:
            # Convert to string and check if it's not just whitespace
            field_value = str(fields[field]).strip()
            if field_value and field_value.lower() not in ['none', 'nan', 'null']:
                values.append(f"{field}: {field_value}")
    return values

def group_related_fields(fields):
    """Group related fields based on common prefixes or patterns.
    
    For example, if there are fields like L1, L2, L3, they will be grouped together.
    Returns a tuple containing:
    - fields_for_api: Fields that should be sent to the Jira API (no pattern-based fields)
    - grouped_display_fields: Fields grouped by prefixes for display purposes only
    """
    fields_for_api = {}
    grouped_display_fields = {}
    field_patterns = {}
    
    # List of valid Jira fields (add more as needed)
    valid_jira_fields = [
        'project', 'summary', 'description', 'issuetype', 
        'assignee', 'reporter', 'priority', 'labels', 
        'duedate', 'components', 'fixVersions', 'versions',
        'environment', 'timetracking', 'security',
        # Add specific custom fields your Jira instance supports here
    ]
    
    # Process regular fields first
    for field, value in fields.items():
        if field in ['Summary', 'Description']:
            continue
            
        prefix = extract_field_prefix(field)
        if prefix:
            # Add to pattern collection for later processing
            if prefix not in field_patterns:
                field_patterns[prefix] = []
            field_patterns[prefix].append(field)
            # DO NOT add pattern fields like L1, L2 to API fields
        else:
            # For regular fields, only include known Jira fields in API request
            # This avoids sending invalid fields to Jira
            if field.lower() in [f.lower() for f in valid_jira_fields] or field.startswith('customfield_'):
                fields_for_api[field] = value
            
            # Always keep for display purposes
            grouped_display_fields[field] = value
    
    # Process pattern-based fields for display only
    for prefix, related_fields in field_patterns.items():
        sorted_fields = sort_fields_numerically(related_fields)
        values = collect_field_values(fields, sorted_fields)
        
        if values:
            # Add grouped field to display fields only, not to API fields
            grouped_display_fields[prefix] = ", ".join(values)
    
    return fields_for_api, grouped_display_fields

def is_field_empty(value):
    """Check if a field value is effectively empty."""
    import pandas as pd
    
    # None values are empty
    if value is None:
        return True
        
    # Empty strings or whitespace
    if isinstance(value, str) and not value.strip():
        return True
        
    # NaN values
    if pd.isna(value):
        return True
        
    # Common string representations of empty values
    if str(value).lower() in ['nan', 'none', 'null']:
        return True
        
    return False

def has_category_selections(fields):
    """Check if any category fields (pattern-based fields) have values."""
    import re
    
    for field in fields:
        # Skip non-category fields
        if field in ['Summary', 'Description']:
            continue
            
        # Check if this is a category field (matches pattern like L1, L2, etc.)
        if re.match(r'^[A-Za-z]+\d+$', field):
            # Check if the field has a non-empty value
            if not is_field_empty(fields[field]):
                return True
    
    return False

def create_tickets_from_key_value(jira_client, df, default_issue_type, create_mode, team_mapping=None, tab_name=None, priority=None):
    """Process transformed key-value data and create tickets."""
    created_tickets = []
    skipped_tickets = []
    dry_run_ticket_count = 0
    
    # Group by 'Key' to process each unique key
    ticket_data = group_rows_by_key(df)
    
    # Create a ticket for each unique key
    for key, fields in ticket_data.items():
        # Check if team was filtered out by processTeams or excludeTeams
        if team_mapping is not None and key not in team_mapping:
            # Just silently skip this team as it was already mentioned in the filtered teams list
            continue
            
        # Check if this team has any category selections
        if not has_category_selections(fields):
            print(f"{Fore.YELLOW}Skipping ticket for '{key}' - no categories selected{Style.RESET_ALL}")
            continue
        
        # Prepare ticket fields with standard processing
        # This already includes grouping related fields and separating display fields from API fields
        summary, description, additional_fields, team_project, team_issue_type = prepare_ticket_fields(fields, key, team_mapping, tab_name)
        
        # Use team-specific issue type if available, otherwise use the default
        issue_type = team_issue_type if team_issue_type else default_issue_type
        
        # Get project key from team-specific Project field
        project_key = team_project
        
        # Add priority if specified in config sheet
        if priority:
            additional_fields['priority'] = priority
        
        # Create or simulate ticket creation
        ticket_key, skipped_key = create_single_ticket(
            jira_client, project_key, issue_type, key, summary, description, additional_fields, create_mode
        )
        
        if create_mode:
            if ticket_key:
                created_tickets.append(ticket_key)
        else:
            # In dry-run mode, only track tickets that would be created (not skipped due to missing Project)
            if project_key:  # Only count tickets that have a valid project key
                dry_run_ticket_count += 1
            
        if skipped_key:
            skipped_tickets.append(skipped_key)
    
    # For dry run mode, store the count on the dataframe for later use in summary
    if not create_mode:
        df._dry_run_ticket_count = dry_run_ticket_count
    
    return created_tickets, skipped_tickets

def display_ticket_count_message(create_mode, created_tickets, tab_name=None):
    """Display a message about the number of tickets created or to be created."""
    sheet_info = f"for {tab_name}" if tab_name else ""
    if not create_mode:
        # In dry run mode, check if any tickets would be created
        ticket_count = len(created_tickets) if created_tickets else 0
        if ticket_count > 0:
            print(f"\n{Fore.YELLOW}[DRY RUN] Would have created {ticket_count} tickets {sheet_info}.{Style.RESET_ALL}")
        else:
            print(f"\n{Fore.YELLOW}[DRY RUN] No tickets would be created {sheet_info}.{Style.RESET_ALL}")
    else:
        # In create mode, check if any tickets were actually created
        if created_tickets:
            print(f"\n{Fore.GREEN}Created {len(created_tickets)} tickets {sheet_info}: {', '.join(created_tickets)}{Style.RESET_ALL}")
        else:
            print(f"\n{Fore.YELLOW}No tickets were created {sheet_info}.{Style.RESET_ALL}")

def display_skipped_messages(create_mode, skipped_count, skipped_tickets):
    """Display messages about skipped tickets."""
    if skipped_count > 0:
        prefix = "[DRY RUN] " if not create_mode else ""
        print(f"{Fore.YELLOW}{prefix}{skipped_count} teams skipped (no categories selected).{Style.RESET_ALL}")
    
    if skipped_tickets and create_mode:
        print(f"{Fore.RED}Skipped {len(skipped_tickets)} tickets due to errors.{Style.RESET_ALL}")

def display_summary(create_mode, df, created_tickets, skipped_tickets, tab_name=None):
    """Display summary of the operation."""
    # Fix for dry run mode - add placeholder ticket IDs if we have a count but no IDs
    if not create_mode and hasattr(df, '_dry_run_ticket_count') and df._dry_run_ticket_count > 0:
        created_tickets = [f"Ticket-{i+1}" for i in range(df._dry_run_ticket_count)]
    elif not create_mode and len(created_tickets) == 0 and not hasattr(df, '_dry_run_ticket_count'):
        # Ensure we show 0 tickets for tabs with no valid tickets
        created_tickets = []
    
    # Count unique keys in the dataframe
    unique_keys_count = len(df['Key'].unique()) if 'Key' in df.columns else len(df)
    
    # Calculate how many were skipped due to missing categories or other issues
    # Note: This calculation is modified to handle the case where tickets are skipped due to missing Project field
    if create_mode:
        skipped_count = unique_keys_count - len(created_tickets) - len(skipped_tickets)
    else:
        # In dry run mode, use the tracked count for accuracy
        dry_run_count = getattr(df, '_dry_run_ticket_count', 0)
        skipped_count = unique_keys_count - dry_run_count - len(skipped_tickets)
    
    # Display ticket count message
    display_ticket_count_message(create_mode, created_tickets, tab_name)
    
    # Display messages about skipped tickets
    display_skipped_messages(create_mode, skipped_count, skipped_tickets)

def get_excel_sheets(file_path):
    """Get list of all sheets in the Excel file."""
    try:
        xls = pd.ExcelFile(file_path)
        return xls.sheet_names
    except Exception as e:
        print(f"{Fore.RED}Error reading Excel sheets: {str(e)}{Style.RESET_ALL}")
        return None

def process_tab(args, file_path, tab_name, jira_client, default_issue_type, team_mapping=None, priority=None):
    """Process a single tab from the Excel file."""
    print(f"\n{Fore.CYAN}Processing tab: {tab_name}{Style.RESET_ALL}")
    
    # Read the Excel tab
    df = read_excel_file(file_path, tab_name)
    if df is None:
        return [], [], 0
    
    # Validate the data has required columns
    if not validate_data(df):
        return [], [], 0
    
    # Display data information
    display_data_info(df, f"{file_path} - {tab_name}")
    
    # Create tickets, optionally using team mapping data
    created_tickets, skipped_tickets = create_tickets_from_key_value(
        jira_client, df, default_issue_type, args.create, team_mapping, tab_name, priority
    )
    
    # Display summary for this tab
    display_summary(args.create, df, created_tickets, skipped_tickets, tab_name)
    
    # Return dry run count for overall summary
    dry_run_count = getattr(df, '_dry_run_ticket_count', 0) if not args.create else 0
    
    return created_tickets, skipped_tickets, dry_run_count

def add_to_team_field(team_data, field, value):
    """Add a value to a team's field, handling lists for multiple values."""
    if field in team_data:
        if isinstance(team_data[field], list):
            team_data[field].append(value)
        else:
            team_data[field] = [team_data[field], value]
    else:
        team_data[field] = value
    
    return team_data

def create_team_mapping(teams_df):
    """Create a mapping of team information from the Teams tab."""
    team_mapping = {}
    
    # Validate required columns exist
    if not all(col in teams_df.columns for col in ['Key', 'Field', 'Value']):
        print(f"{Fore.YELLOW}Warning: Teams tab does not have required columns (Key, Field, Value){Style.RESET_ALL}")
        return team_mapping
    
    # Group data by key (team name)
    for _, row in teams_df.iterrows():
        team_name = row['Key']
        field = row['Field']
        value = row['Value']
        
        # Initialize team entry if needed
        if team_name not in team_mapping:
            team_mapping[team_name] = {}
        
        # Update team data with the new field value
        team_mapping[team_name] = add_to_team_field(team_mapping[team_name], field, value)
    
    return team_mapping

def process_teams_tab(excel_file, available_sheets):
    """Process the Teams tab from the Excel file."""
    # Check if Teams tab exists
    if "Teams" not in available_sheets:
        print(f"{Fore.RED}Error: 'Teams' tab not found in the Excel file. Available tabs: {', '.join(available_sheets)}{Style.RESET_ALL}")
        return None
    
    # Read the Teams tab
    teams_df = read_excel_file(excel_file, "Teams")
    if teams_df is None:
        return None
    
    # Validate the data has required columns
    if not validate_data(teams_df):
        return None
    
    # Create team mapping from Teams tab
    team_mapping = create_team_mapping(teams_df)
    print(f"\n{Fore.CYAN}Created team mapping with {len(team_mapping)} teams{Style.RESET_ALL}")
    
    # Display data information for Teams (just for information)
    print(f"\n{Fore.CYAN}Teams information (for reference only - no tickets will be created from this tab):{Style.RESET_ALL}")
    display_data_info(teams_df, excel_file)
    
    return team_mapping

def display_team_projects(team_mapping):
    """Display team-specific Project values (project keys) if available."""
    projects = {}
    
    # Collect Project values by team
    for team_name, team_data in team_mapping.items():
        if PROJECT_FIELD in team_data:
            project = team_data[PROJECT_FIELD]
            if project not in projects:
                projects[project] = []
            projects[project].append(team_name)
    
    # Display the information if any team has a specific Project value
    if projects:
        print(f"\n{Fore.CYAN}Team-specific Projects (used as project keys):{Style.RESET_ALL}")
        for project, teams in projects.items():
            print(f"  {Fore.YELLOW}{project}{Style.RESET_ALL}: {', '.join(teams)}")
    else:
        print(f"\n{Fore.YELLOW}No team-specific Project values found. Tickets will be skipped.{Style.RESET_ALL}")

def display_team_issue_types(team_mapping):
    """Display team-specific Issue Type values if available."""
    issue_types = {}
    
    # Collect Issue Type values by team
    for team_name, team_data in team_mapping.items():
        if ISSUE_TYPE_KEY in team_data:
            issue_type = team_data[ISSUE_TYPE_KEY]
            if issue_type not in issue_types:
                issue_types[issue_type] = []
            issue_types[issue_type].append(team_name)
    
    # Display the information if any team has a specific Issue Type value
    if issue_types:
        print(f"\n{Fore.CYAN}Team-specific Issue Types:{Style.RESET_ALL}")
        for issue_type, teams in issue_types.items():
            print(f"  {Fore.YELLOW}{issue_type}{Style.RESET_ALL}: {', '.join(teams)}")
    else:
        print(f"\n{Fore.YELLOW}No team-specific Issue Types found. Default issue type will be used.{Style.RESET_ALL}")

def filter_team_mapping(team_mapping, args):
    """Filter team mapping based on processTeams or excludeTeams arguments."""
    filtered_mapping = team_mapping.copy()
    
    # If processTeams is provided, only include those teams (case insensitive)
    if args.processTeams:
        teams_to_process = [team.strip() for team in args.processTeams.split(',')]
        # Create a case-insensitive lookup dictionary
        teams_lookup = {key.lower(): key for key in team_mapping.keys()}
        
        # Calculate which teams will be excluded
        all_teams = set(team_mapping.keys())
        included_teams = set()
        
        # Filter the mapping using case-insensitive comparison
        filtered_mapping = {}
        for team in teams_to_process:
            if team.lower() in teams_lookup:
                original_key = teams_lookup[team.lower()]
                filtered_mapping[original_key] = team_mapping[original_key]
                included_teams.add(original_key)
        
        # Calculate excluded teams
        excluded_teams = all_teams - included_teams
        excluded_teams_list = sorted(list(excluded_teams))
        
        print(f"{Fore.CYAN}Processing only specified teams: {', '.join(teams_to_process)}{Style.RESET_ALL}")
        if excluded_teams:
            print(f"{Fore.CYAN}Filtering out {len(excluded_teams)} teams: {', '.join(excluded_teams_list[:5])}" + 
                  (f", and {len(excluded_teams) - 5} more..." if len(excluded_teams) > 5 else "") + 
                  f"{Style.RESET_ALL}")
        
    # If excludeTeams is provided, exclude those teams (case insensitive)
    elif args.excludeTeams:
        teams_to_exclude = [team.strip().lower() for team in args.excludeTeams.split(',')]
        
        # Keep track of which teams are actually excluded (for better messaging)
        excluded_teams = []
        
        # Filter the mapping using case-insensitive comparison
        filtered_mapping = {}
        for key, value in team_mapping.items():
            if key.lower() not in teams_to_exclude:
                filtered_mapping[key] = value
            else:
                excluded_teams.append(key)
                
        print(f"{Fore.CYAN}Excluding specified teams: {', '.join(args.excludeTeams.split(','))}{Style.RESET_ALL}")
        if excluded_teams:
            print(f"{Fore.CYAN}Actually excluded {len(excluded_teams)} teams: {', '.join(excluded_teams)}{Style.RESET_ALL}")
    
    # If filtered mapping is empty but original wasn't, provide a warning
    if not filtered_mapping and team_mapping:
        print(f"{Fore.YELLOW}Warning: No teams match the filter criteria. No tickets will be created.{Style.RESET_ALL}")
    
    return filtered_mapping

def process_all_tabs(args, file_path, available_sheets, jira_client, default_issue_type, team_mapping, priority=None):
    """Process all the tabs from the Excel file."""
    all_created_tickets = []
    all_skipped_tickets = []
    total_dry_run_count = 0
    
    # Filter teams based on arguments
    filtered_team_mapping = filter_team_mapping(team_mapping, args)
    
    # Display team-specific projects and issue types for user information
    display_team_projects(filtered_team_mapping)
    display_team_issue_types(filtered_team_mapping)
    
    for tab_name in TAB_NAMES:
        if tab_name in available_sheets:
            created, skipped, dry_run_count = process_tab(
                args, file_path, tab_name, jira_client, default_issue_type, filtered_team_mapping, priority
            )
            all_created_tickets.extend(created)
            all_skipped_tickets.extend(skipped)
            total_dry_run_count += dry_run_count
        else:
            print(f"{Fore.YELLOW}Warning: Tab '{tab_name}' not found in the Excel file, skipping.{Style.RESET_ALL}")
    
    return all_created_tickets, all_skipped_tickets, total_dry_run_count

def display_overall_summary(create_mode, all_created_tickets, all_skipped_tickets, total_dry_run_count, issue_type, args=None):
    """Display the overall summary of the operation."""
    print(f"\n{Fore.CYAN}=== OVERALL SUMMARY ==={Style.RESET_ALL}")
    
    # Add team filter information if applicable
    filter_info = ""
    if args and args.processTeams:
        filter_info = " (filtered to include only specified teams)"
    elif args and args.excludeTeams:
        filter_info = " (with excluded teams filtered out)"
    
    if not create_mode:
        ticket_count = total_dry_run_count if total_dry_run_count > 0 else len(all_created_tickets)
        print(f"{Fore.YELLOW}[DRY RUN] Would have created a total of {ticket_count} tickets in Jira across all sheets as issue type '{issue_type}'{filter_info}.{Style.RESET_ALL}")
    else:
        if all_created_tickets:
            print(f"{Fore.GREEN}Created a total of {len(all_created_tickets)} tickets across all sheets as issue type '{issue_type}'{filter_info}.{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}No tickets were created across all sheets{filter_info}.{Style.RESET_ALL}")
            
        if all_skipped_tickets:
            print(f"{Fore.RED}Skipped a total of {len(all_skipped_tickets)} tickets due to errors.{Style.RESET_ALL}")

def main():
    # Initialize colorama
    init()
    
    # Parse arguments
    args = parse_arguments()
    
    # Validate the Excel file
    if not validate_file(args.excel_file):
        return
    
    # Load JIRA config for later use
    config = load_config()
    
    # Get available sheets in the Excel file
    available_sheets = get_excel_sheets(args.excel_file)
    if not available_sheets:
        return
    
    print(f"{Fore.CYAN}Available sheets in {args.excel_file}: {', '.join(available_sheets)}{Style.RESET_ALL}")
    
    # Read configuration from Config sheet if it exists
    excel_config = {}
    if CONFIG_SHEET in available_sheets:
        excel_config = read_config_sheet(args.excel_file)
        print(f"{Fore.CYAN}Read configuration from Config sheet{Style.RESET_ALL}")
    
    # Use "Task" as the default issue type, individual teams can override
    issue_type = "Task"  # Default
    print(f"{Fore.CYAN}Using default issue type: {issue_type} (teams can override with '{ISSUE_TYPE_KEY}' column){Style.RESET_ALL}")
    
    # Get priority from Config sheet if available
    priority = None
    if PRIORITY_KEY in excel_config:
        priority = excel_config[PRIORITY_KEY]
        print(f"{Fore.CYAN}Using priority from Config sheet: {priority}{Style.RESET_ALL}")
    else:
        print(f"{Fore.CYAN}No priority specified in Config sheet, using Jira default{Style.RESET_ALL}")
    
    # Process Teams tab
    team_mapping = process_teams_tab(args.excel_file, available_sheets)
    if team_mapping is None:
        return
    
    # Report on team filtering if parameters are provided
    if args.processTeams:
        teams_to_process = [team.strip() for team in args.processTeams.split(',')]
        print(f"{Fore.CYAN}Team filter active: Will only process these teams: {', '.join(teams_to_process)}{Style.RESET_ALL}")
        # Create a case-insensitive lookup for team mapping
        team_mapping_lower = {key.lower(): key for key in team_mapping.keys()}
        # Warn about any teams that don't exist in the mapping (case-insensitive)
        missing_teams = [team for team in teams_to_process if team.lower() not in team_mapping_lower]
        if missing_teams:
            print(f"{Fore.YELLOW}Warning: Some specified teams not found in Teams sheet: {', '.join(missing_teams)}{Style.RESET_ALL}")
    elif args.excludeTeams:
        teams_to_exclude = [team.strip() for team in args.excludeTeams.split(',')]
        print(f"{Fore.CYAN}Team filter active: Will exclude these teams: {', '.join(teams_to_exclude)}{Style.RESET_ALL}")
        # Create a case-insensitive lookup for team mapping
        team_mapping_lower = {key.lower(): key for key in team_mapping.keys()}
        # Warn about any teams that don't exist in the mapping (case-insensitive)
        nonexistent_teams = [team for team in teams_to_exclude if team.lower() not in team_mapping_lower]
        if nonexistent_teams:
            print(f"{Fore.YELLOW}Warning: Some excluded teams not found in Teams sheet: {', '.join(nonexistent_teams)}{Style.RESET_ALL}")
    
    # Count available tabs for processing
    available_tab_count = sum(1 for tab in TAB_NAMES if tab in available_sheets)
    
    # Confirm operation with user
    issue_type = confirm_operation(args, available_tab_count, issue_type)
    if not issue_type:
        return
    
    # Create JIRA client
    try:
        jira_client = jira.JIRA(config["jira_server"], token_auth=(config["personal_access_token"]))
        # Add specific auth header if needed (some Jira functions require Bearer token format - for ticket creation)
        jira_client._session.headers.update({'Authorization': "BEARER {config['personal_access_token']}"})
    except Exception as e:
        print(f"{Fore.RED}Error connecting to Jira: {str(e)}{Style.RESET_ALL}")
        return
    
    # Process all tabs
    all_created_tickets, all_skipped_tickets, total_dry_run_count = process_all_tabs(
        args, args.excel_file, available_sheets, jira_client, issue_type, team_mapping, priority
    )
    
    # Display overall summary
    display_overall_summary(args.create, all_created_tickets, all_skipped_tickets, total_dry_run_count, issue_type, args)


if __name__ == "__main__":
    main()
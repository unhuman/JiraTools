# Shared Jira ticket creation utilities
# Extracted from standardTicketCreator.py for reuse across scripts

import json
import requests
from colorama import Fore, Style

# Constants for column names
ASSIGNEE_FIELD = "Assignee"
EPIC_LINK_FIELD = "Epic Link"
EPIC_LINK_TYPE = "Epic-Story Link"  # The link type used to connect stories to epics
CUSTOM_FIELDS_SHEET = "CustomFields"  # Sheet for custom field mappings


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


def add_standard_field(issue_dict, field, value, format_type):
    """Format and add a standard field to the issue dictionary."""
    # Map singular 'component' to plural 'components' for Jira API
    api_field = 'components' if field.lower() == 'component' else field

    if format_type == 'name':
        if isinstance(value, list):
            issue_dict[api_field] = [{'name': item} for item in value]
        else:
            if field.lower() == 'component':
                issue_dict[api_field] = [{'name': value}]
            else:
                issue_dict[api_field] = {'name': value}
    else:
        issue_dict[api_field] = value


def process_fields_for_jira(fields, issue_dict, custom_fields_mapping=None):
    """Process and filter fields before sending to Jira API.

    This function handles three types of fields:
    1. Standard Jira fields (with specific formatting requirements)
    2. Direct custom fields (already have customfield_XXXXX format)
    3. Mapped custom fields (using the custom_fields_mapping parameter)

    For mapped custom fields, the formatting depends on the Data Wrapper setting:
    - With wrapper "value": {"value": field_value}
    - With no wrapper (None): field_value directly
    - With custom wrapper: {wrapper: field_value}
    """
    # Define known Jira standard fields that require special handling
    # Note: 'assignee' is deliberately excluded as it's handled separately after ticket creation
    standard_fields = {
        'reporter': 'name',
        'priority': 'name',
        'component': 'name',
        'components': 'name',
        'labels': None,
        'duedate': None,
        'fixVersions': 'name',
        'versions': 'name',
    }

    for field, value in fields.items():
        if not value or str(value).lower() == 'nan' or field == 'Project':
            continue

        if field.lower() == 'assignee':
            print(f"{Fore.YELLOW}Skipping 'assignee' field during initial ticket creation - will be set afterwards{Style.RESET_ALL}")
            continue

        field_lower = field.lower()
        if field_lower in standard_fields:
            format_type = standard_fields[field_lower]
            add_standard_field(issue_dict, field, value, format_type)

        elif field.startswith('customfield_') or '.' in field:
            issue_dict[field] = value

        elif custom_fields_mapping and field in custom_fields_mapping:
            mapping = custom_fields_mapping[field]
            custom_field_id = mapping["id"]
            wrapper = mapping.get("wrapper")

            if wrapper is None:
                issue_dict[custom_field_id] = value
                print(f"{Fore.GREEN}Mapped field '{field}' to custom field '{custom_field_id}' with direct value: {value}{Style.RESET_ALL}")
            else:
                wrapped_value = {wrapper: value}
                issue_dict[custom_field_id] = wrapped_value
                print(f"{Fore.GREEN}Mapped field '{field}' to custom field '{custom_field_id}' with wrapper '{wrapper}': {wrapped_value}{Style.RESET_ALL}")

        else:
            print(f"{Fore.YELLOW}Skipping unknown field '{field}' to avoid Jira API errors{Style.RESET_ALL}")


def prepare_issue_dict(project_key, issue_type, summary, description, fields, custom_fields_mapping=None):
    """Prepare the issue dictionary for Jira API."""
    validation_errors = validate_required_fields(project_key, issue_type, summary)
    if validation_errors:
        print(f"{Fore.YELLOW}Warning: Field validation issues detected:{Style.RESET_ALL}")
        for error in validation_errors:
            print(f"{Fore.YELLOW}- {error}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}This may cause a 400 Bad Request error when submitting to Jira.{Style.RESET_ALL}")

    issue_dict = {
        'project': {'key': project_key},
        'summary': summary,
        'description': description,
        'issuetype': {'name': issue_type},
    }

    epic_field_name = EPIC_LINK_FIELD
    epic_value = None

    fields_copy = fields.copy()

    if epic_field_name in fields_copy:
        epic_value = fields_copy[epic_field_name]

    process_fields_for_jira(fields_copy, issue_dict, custom_fields_mapping)

    return (issue_dict, epic_value)


def assign_ticket(jira_client, issue_key, assignee_name):
    """Set the assignee for a ticket using a separate API request.

    Args:
        jira_client: The Jira client instance
        issue_key: The key of the issue to update (e.g., PRJ-123)
        assignee_name: The username of the assignee to set

    Returns:
        bool: True if successful, False if failed
    """
    if not assignee_name or str(assignee_name).lower() == 'nan':
        print(f"{Fore.YELLOW}Skipping assignee update - no assignee specified{Style.RESET_ALL}")
        return False

    try:
        print(f"{Fore.CYAN}Setting assignee for {issue_key} to '{assignee_name}' with separate API request{Style.RESET_ALL}")
        jira_client.assign_issue(issue_key, assignee_name)
        print(f"{Fore.GREEN}Successfully set assignee for {issue_key} to '{assignee_name}'{Style.RESET_ALL}")
        return True

    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Could not set assignee for ticket {issue_key}: {str(e)}{Style.RESET_ALL}")
        return False


def link_to_epic(jira_client, issue_key, epic_key):
    """Link an issue to an epic using various methods."""
    if not epic_key or str(epic_key) == 'nan':
        return False

    try:
        try:
            jira_client.update_issue_field(issue_key, {'customfield_10000': epic_key})
            return True
        except Exception:
            pass

        try:
            jira_client.create_issue_link(EPIC_LINK_TYPE, epic_key, issue_key)
            return True
        except Exception:
            pass

        jira_client.create_issue_link('Relates to', epic_key, issue_key)
        return True

    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Could not link ticket {issue_key} to epic {epic_key}: {str(e)}{Style.RESET_ALL}")
        return False


def log_issue_fields(issue_dict):
    """Log the fields being sent to Jira API."""
    print(f"{Fore.CYAN}Sending to Jira API:{Style.RESET_ALL}")

    for field, value in issue_dict.items():
        if field == 'project':
            print(f"{Fore.CYAN}  project: {value['key']}{Style.RESET_ALL}")
        elif field == 'issuetype':
            print(f"{Fore.CYAN}  issuetype: {value['name']}{Style.RESET_ALL}")
        elif field == 'description' and value:
            desc_preview = value[:500] + ('...' if len(value) > 500 else '')
            print(f"{Fore.CYAN}  description: {desc_preview}{Style.RESET_ALL}")
        else:
            print(f"{Fore.CYAN}  {field}: {value}{Style.RESET_ALL}")

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
    jira_url = jira_client._options['server']
    print(f"{Fore.CYAN}Jira server URL: {jira_url}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}API endpoint: {jira_url}/rest/api/2/issue{Style.RESET_ALL}")

    auth_method = "Personal Access Token" if hasattr(jira_client, "_session") and jira_client._session.auth else "Unknown"
    print(f"{Fore.CYAN}Authentication method: {auth_method}{Style.RESET_ALL}")

    print(f"{Fore.CYAN}Request Headers:{Style.RESET_ALL}")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }

    if hasattr(jira_client, "_session") and hasattr(jira_client._session, "headers"):
        for key, value in jira_client._session.headers.items():
            if "token" in key.lower() or "auth" in key.lower():
                headers[key] = "********"
            else:
                headers[key] = value

    for header, value in headers.items():
        print(f"{Fore.CYAN}  {header}: {value}{Style.RESET_ALL}")

    print(f"{Fore.CYAN}Request Payload:{Style.RESET_ALL}")
    request_payload = {
        "fields": issue_dict
    }
    print(f"{Fore.CYAN}{json.dumps(request_payload, indent=2)}{Style.RESET_ALL}")

    print(f"{Fore.CYAN}Common Field Formats (for reference):{Style.RESET_ALL}")
    print(f"{Fore.CYAN}  Standard fields: project, summary, description, issuetype{Style.RESET_ALL}")
    print(f"{Fore.CYAN}  Object fields: assignee:{{'name': 'username'}}, priority:{{'name': 'High'}}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}  Custom fields: Use field ID (customfield_XXXXX) for custom fields{Style.RESET_ALL}")

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
            if header.lower() in ('authorization', 'cookie'):
                print(f"{Fore.RED}  {header}: ********{Style.RESET_ALL}")
            else:
                print(f"{Fore.RED}  {header}: {value}{Style.RESET_ALL}")


def log_request_body(req):
    """Log HTTP request body."""
    if hasattr(req, 'body') and req.body:
        print(f"{Fore.RED}Request Body:{Style.RESET_ALL}")
        try:
            body = json.loads(req.body.decode('utf-8'))
            print(f"{Fore.RED}{json.dumps(body, indent=2)}{Style.RESET_ALL}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            print(f"{Fore.RED}Raw body (not JSON): {req.body}{Style.RESET_ALL}")


def log_request_details_from_response(response):
    """Log HTTP request details extracted from the response object."""
    if response and hasattr(response, 'request'):
        req = response.request
        print(f"{Fore.RED}Request Method: {req.method}{Style.RESET_ALL}")
        print(f"{Fore.RED}Request URL: {req.url}{Style.RESET_ALL}")
        log_request_headers(req)
        log_request_body(req)


def handle_http_error(e):
    """Handle and log HTTP errors from Jira API."""
    print(f"{Fore.RED}HTTP Error during API request: {str(e)}{Style.RESET_ALL}")

    response = e.response if hasattr(e, 'response') else None
    status_code = response.status_code if response else "Unknown"

    print(f"{Fore.RED}Status code: {status_code}{Style.RESET_ALL}")

    log_response_headers(response)
    log_request_details_from_response(response)

    if response and status_code == 400:
        handle_bad_request(response)


def handle_bad_request(response):
    """Handle 400 Bad Request errors with detailed logging."""
    print(f"{Fore.RED}Bad Request (400) - Invalid input data{Style.RESET_ALL}")
    try:
        error_data = response.json()
        print(f"{Fore.RED}Error details:{Style.RESET_ALL}")
        print(f"{Fore.RED}{json.dumps(error_data, indent=2)}{Style.RESET_ALL}")

        if 'errors' in error_data:
            for field, error in error_data['errors'].items():
                print(f"{Fore.RED}Field '{field}': {error}{Style.RESET_ALL}")

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
                if "Field" in msg and "is not available" in msg:
                    field = msg.split("'")[1] if "'" in msg else "unknown"
                    print(f"{Fore.YELLOW}TIP: Field '{field}' is not recognized. Custom fields may need to use ID (customfield_XXXXX){Style.RESET_ALL}")
                elif "could not be set" in msg.lower():
                    print(f"{Fore.YELLOW}TIP: A field value has incorrect format. Check object fields like assignee, priority, etc.{Style.RESET_ALL}")

        if any("authentication" in str(msg).lower() for msg in error_data.get('errorMessages', [])):
            print(f"{Fore.YELLOW}TIP: This may be an authentication issue. Check your Jira token is valid and has not expired.{Style.RESET_ALL}")

    except Exception:
        print(f"{Fore.RED}Raw response: {response.text}{Style.RESET_ALL}")

    print(f"{Fore.YELLOW}Common causes for 400 errors:{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}1. Required fields missing (check project, issuetype, summary){Style.RESET_ALL}")
    print(f"{Fore.YELLOW}2. Invalid project key or issue type{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}3. Custom field format incorrect - custom fields may need IDs like customfield_10001{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}4. Standard fields like 'assignee' need object format: {{'name': 'username'}}{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}5. Authentication or permissions issues{Style.RESET_ALL}")


def read_custom_fields_mapping(file_path):
    """Read the CustomFields sheet from the Excel file to get custom field mappings.

    The CustomFields sheet should have the following columns:
    1. Field Name: The name of the field as it appears in Excel data
    2. Field ID: The Jira custom field ID (e.g., customfield_10001)
    3. Data Wrapper: (Optional) How to format the value in the API request:
       - If "value" -> the field will be formatted as {"value": field_value}
       - If empty or "none" -> the field value will be used directly
       - Any other string -> the field will be formatted as {wrapper: field_value}
    """
    import pandas as pd

    try:
        df = pd.read_excel(file_path, sheet_name=CUSTOM_FIELDS_SHEET)

        custom_fields_mapping = {}
        for _, row in df.iterrows():
            if len(row) >= 2:
                field_name = str(row.iloc[0]).strip()
                custom_field_id = str(row.iloc[1]).strip()

                data_wrapper = None
                if len(row) >= 3 and pd.notna(row.iloc[2]):
                    data_wrapper = str(row.iloc[2]).strip()
                    if data_wrapper.lower() == "none":
                        data_wrapper = None

                if field_name and custom_field_id != 'nan':
                    custom_fields_mapping[field_name] = {
                        "id": custom_field_id,
                        "wrapper": data_wrapper
                    }

        print(f"{Fore.CYAN}Loaded {len(custom_fields_mapping)} custom field mappings{Style.RESET_ALL}")
        for field_name, mapping in custom_fields_mapping.items():
            wrapper_info = f"wrapper: '{mapping['wrapper']}'" if mapping['wrapper'] else "no wrapper"
            print(f"{Fore.CYAN}  Field '{field_name}' → {mapping['id']} ({wrapper_info}){Style.RESET_ALL}")
        return custom_fields_mapping
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Could not read CustomFields sheet: {str(e)}. No custom field mappings will be used.{Style.RESET_ALL}")
        return {}


def create_jira_ticket(jira_client, project_key, issue_type, summary, description, excel_file=None, **fields):
    """Create a Jira ticket with the given fields."""
    print(f"\n{Fore.CYAN}{'=' * 80}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Creating Jira ticket for {project_key} - {summary}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'-' * 80}{Style.RESET_ALL}")

    custom_fields_mapping = None
    if excel_file:
        try:
            custom_fields_mapping = read_custom_fields_mapping(excel_file)
        except Exception as e:
            print(f"{Fore.YELLOW}Warning: Could not load custom fields mapping: {e}{Style.RESET_ALL}")

    fields = fields.copy()
    assignee_name = None
    if ASSIGNEE_FIELD in fields:
        print(f"{Fore.CYAN}Found assignee{Style.RESET_ALL}")
        assignee_name = fields[ASSIGNEE_FIELD]
        del fields[ASSIGNEE_FIELD]

    issue_data = prepare_issue_dict(project_key, issue_type, summary, description, fields, custom_fields_mapping)
    issue_dict, epic_link = issue_data

    log_issue_fields(issue_dict)

    print(f"{Fore.CYAN}{'-' * 80}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Submitting to Jira...{Style.RESET_ALL}")

    try:
        log_request_details(jira_client, issue_dict)

        new_issue = jira_client.create_issue(fields=issue_dict)
        print(f"{Fore.GREEN}Successfully created ticket: {new_issue.key}{Style.RESET_ALL}")

        if assignee_name:
            assign_ticket(jira_client, new_issue.key, assignee_name)

        if epic_link and str(epic_link) != 'nan':
            if not custom_fields_mapping or EPIC_LINK_FIELD not in custom_fields_mapping:
                if link_to_epic(jira_client, new_issue.key, epic_link):
                    print(f"{Fore.CYAN}Linked ticket {new_issue.key} to parent epic {epic_link} using traditional link method{Style.RESET_ALL}")

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

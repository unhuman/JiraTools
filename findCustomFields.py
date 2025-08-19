# This script helps identify custom field IDs in JIRA

import argparse
from colorama import init, Fore, Style
import jira
from jiraToolsConfig import load_config

# Parse command-line arguments
parser = argparse.ArgumentParser(description="Find custom field IDs in JIRA by examining an issue.")
parser.add_argument("issue_key", help="A JIRA issue key to examine (e.g., PROJ-123)")
args = parser.parse_args()

# Initialize colorama for colored output
init()

# Load JIRA configuration
config = load_config()

# Create the JIRA client
try:
    jira_client = jira.JIRA(config["jira_server"], token_auth=(config["personal_access_token"]))
except jira.exceptions.JIRAError as e:
    print(f"{Fore.RED}Error connecting to JIRA: {e}{Style.RESET_ALL}")
    exit(1)

# Get the issue
try:
    issue = jira_client.issue(args.issue_key)
    print(f"{Style.BRIGHT}Examining issue: {args.issue_key}{Style.RESET_ALL}")
    print(f"Summary: {issue.fields.summary}\n")
except jira.exceptions.JIRAError as e:
    print(f"{Fore.RED}Error retrieving issue {args.issue_key}: {e}{Style.RESET_ALL}")
    exit(1)

# Get all custom fields
try:
    all_fields = jira_client.fields()
    custom_fields = {field['id']: field['name'] for field in all_fields if field['custom']}

    print(f"{Style.BRIGHT}Custom fields with values in this issue:{Style.RESET_ALL}")
    print("-" * 60)

    for field_id, field_name in custom_fields.items():
        try:
            field_value = getattr(issue.fields, field_id, None)
            if field_value is not None:
                print(f"{Fore.CYAN}{field_id}{Style.RESET_ALL}: {field_name}")
                print(f"  Value: {Fore.GREEN}{field_value}{Style.RESET_ALL}")
                print(f"  Type: {type(field_value).__name__}")
                print()
        except Exception as e:
            # Skip fields that can't be accessed
            pass

except jira.exceptions.JIRAError as e:
    print(f"{Fore.RED}Error retrieving fields: {e}{Style.RESET_ALL}")
    exit(1)

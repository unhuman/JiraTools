# This was created with Google Gemini, with prompting for various features and fixes.

# pip install colorama | jira | networkx

import argparse
from colorama import init, Fore, Back, Style
from datetime import datetime
import jira
from jiraToolsConfig import load_config, statusIsDone

# Parse arguments
parser = argparse.ArgumentParser(description="Evaluate the current plan of an epic.")
parser.add_argument("epic_key", help="The key of the epic")
args = parser.parse_args()

# JIRA setup
config = load_config()

jira_client = jira.JIRA(config["jira_server"], token_auth=(config["personal_access_token"]))

# Get Epic and Issues
epic_key = args.epic_key
try:
    epic = jira_client.issue(epic_key)
except jira.exceptions.JIRAError as e:
    print(f"Error retrieving epic: {e}")
    exit(1)

jql = f"\"Epic Link\"={epic_key}"
try:
    issues = jira_client.search_issues(jql, maxResults=False)
except jira.exceptions.JIRAError as e:
    print(f"Error searching issues: {e}")
    exit(1)

# Organize issues by sprint ID and status
planned_issues = {}
unplanned_issues = []
completed_issues = {}

for issue in issues:
    sprint_ids = []
    try:
        sprint_field = getattr(issue.fields, 'customfield_10505', None)  # Using customfield_10505

        if sprint_field:
            if isinstance(sprint_field, list):  # Multi-select sprint field (list of objects/strings)
                for sprint_data in sprint_field:
                    if isinstance(sprint_data, str):  # Older format
                        try:
                            sprint_id_str = sprint_data.split("[id=")[1].split(",")[0]
                            sprint_id = int(sprint_id_str)
                            sprint_ids.append(sprint_id)
                        except (IndexError, ValueError):
                            print(f"Warning: Issue {issue.key} has invalid sprint data: {sprint_data}")
                            unplanned_issues.append(issue)
                            continue
                    elif hasattr(sprint_data, 'id'):  # Newer format
                        sprint_ids.append(sprint_data.id)
                    else:
                        print(f"Warning: Issue {issue.key} has invalid sprint data: {sprint_data}")
                        unplanned_issues.append(issue)
                        continue
            elif isinstance(sprint_field, str):  # Single sprint field (string representation)
                try:
                    sprint_id_str = sprint_field.split("[id=")[1].split(",")[0]
                    sprint_id = int(sprint_id_str)
                    sprint_ids.append(sprint_id)
                except (IndexError, ValueError):
                    print(f"Warning: Issue {issue.key} has invalid sprint data: {sprint_field}")
                    unplanned_issues.append(issue)
                    continue
            elif hasattr(sprint_field, 'id'):  # Single sprint field (object with ID)
                sprint_ids.append(sprint_field.id)
            else:
                print(f"Warning: Issue {issue.key} has invalid sprint data: {sprint_field}")
                unplanned_issues.append(issue)
                continue

        if not sprint_ids:  # Unplanned
            unplanned_issues.append(issue)
            continue  # Skip to the next issue

        # Report only the LAST sprint
        sprint_id = sprint_ids[-1]  # Get the last sprint ID
        if sprint_id not in planned_issues:
            planned_issues[sprint_id] = {}

        status = issue.fields.status.name

        if statusIsDone(status):
            if sprint_id not in completed_issues:
                completed_issues[sprint_id] = {}
            if status not in completed_issues[sprint_id]:
                completed_issues[sprint_id][status] = []
            completed_issues[sprint_id][status].append(issue)
        else:
            if sprint_id not in planned_issues:
                planned_issues[sprint_id] = {}
            if status not in planned_issues[sprint_id]:
                planned_issues[sprint_id][status] = []
            planned_issues[sprint_id][status].append(issue)

    except AttributeError:
        unplanned_issues.append(issue)
        print(f"Warning: Issue {issue.key} has no sprint assigned.")
        continue

# Fetch Sprint Names and Dates (Corrected Logic Here - Handling None Dates)
sprint_data = {}
all_sprint_ids = set(planned_issues.keys()).union(completed_issues.keys())  # Get all sprints
for sprint_id in all_sprint_ids:  # Iterate through all sprint IDs
    try:
        sprint = jira_client.sprint(sprint_id)  # Get sprint object
        start_date = getattr(sprint, 'startDate', None)  # Handle missing startDate attribute
        end_date = getattr(sprint, 'endDate', None)      # Handle missing endDate attribute
        sprint_data[sprint_id] = {"name": sprint.name, "startDate": start_date, "endDate": end_date}
    except jira.exceptions.JIRAError as e:
        print(f"Error getting sprint data for ID {sprint_id}: {e}")
        sprint_data[sprint_id] = {"name": f"Sprint ID {sprint_id} (Data Unavailable)", "startDate": None, "endDate": None}


def sprint_sort_key(item):  # Custom sort function (Handles None Dates)
    sprint_id = item[0]
    sprint_info = sprint_data.get(sprint_id, {})
    start_date = sprint_info.get("startDate")
    if start_date:
        try:
            return datetime.fromisoformat(start_date[:-1]) if isinstance(start_date, str) else datetime.min  # Remove trailing Z and convert, handle potential date format issues
        except ValueError:  # Handle potential date format issues
            return datetime.min  # Put invalid dates at the beginning
    else:
        return datetime.max  # Put sprints without dates at the end

def getTicketColor(status):
    if statusIsDone(status):
        return Style.BRIGHT + Fore.GREEN
    elif status.lower() == "withdrawn":
        return Fore.GREEN
    elif status.lower() == "in progress":
        return Fore.YELLOW
    else:
        return Fore.YELLOW

def simple_print_tickets(title, issues_dict):
    if not issues_dict:
        return

    print(f"\n{Style.BRIGHT}{title}:{Style.RESET_ALL}")
    for issue in issues_dict:
        color = getTicketColor(issue.fields.status.name)
        print(f"  {color}{issue.key}: {issue.fields.summary}{Style.RESET_ALL}")

# Print Planned Work (Sorted, with Dates, Excluding Empty Sprints)
def filter_and_print_sprints(title, issues_dict, sprint_data):
    if not issues_dict:
        return

    print(f"\n{Style.BRIGHT}{title}:{Style.RESET_ALL}")
    sprints_to_report = {sprint_id: status_groups for sprint_id, status_groups in issues_dict.items() if status_groups} # Only report sprints with issues
    for sprint_id, status_groups in sorted(sprints_to_report.items(), key=sprint_sort_key):
        sprint_info = sprint_data.get(sprint_id)
        sprint_name = sprint_info.get("name")
        start_date_str = sprint_info.get("startDate")
        end_date_str = sprint_info.get("endDate")

        start_date = datetime.fromisoformat(start_date_str[:-1]).strftime("%Y-%m-%d") if start_date_str else "N/A"
        end_date = datetime.fromisoformat(end_date_str[:-1]).strftime("%Y-%m-%d") if end_date_str else "N/A"

        print(f"\n{Style.BRIGHT}Sprint: {sprint_name} ({start_date} - {end_date}){Style.RESET_ALL}")  # Include dates
        for status, issue_list in sorted(status_groups.items()):
            print(f"  {status}:")
            for issue in issue_list:
                color = getTicketColor(status)
                print(f"    {color}{issue.key}: {issue.fields.summary}{Style.RESET_ALL}")

# Print the report (Corrected Printing Logic - Including Dates)
print(f"{Style.BRIGHT}Epic Plan Evaluation: {epic_key}{Style.RESET_ALL}")

# Print unplanned but withdrawn issues
unplanned_finished_issues = [issue for issue in unplanned_issues if issue.fields.status.name.lower() == "withdrawn" or statusIsDone(issue.fields.status.name)]
simple_print_tickets("Completed (Withdrawn or Done no sprint) Work", unplanned_finished_issues)

# Print Completed Work (Sorted, with Dates, Excluding Empty Sprints)
filter_and_print_sprints("Completed Work", completed_issues, sprint_data)

# Print Planned Work (Sorted, with Dates, Excluding Empty Sprints)
filter_and_print_sprints("Planned Work", planned_issues, sprint_data)

# Print Unplanned Work (but exclude withdrawn issues)
unplanned_open_issues = [issue for issue in unplanned_issues if issue.fields.status.name.lower() != "withdrawn" and not statusIsDone(issue.fields.status.name)]
simple_print_tickets("Unplanned Work", unplanned_open_issues)

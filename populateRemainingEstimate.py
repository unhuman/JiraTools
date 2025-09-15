# This script copies story points to original estimate for tickets that have story points but no original estimate.

# pip install colorama jira

import argparse
import time
from colorama import init, Fore, Style
import jira
from jiraToolsConfig import load_config

# Configuration constants
STORY_POINTS_FIELD = 'customfield_10502'  # Story Points custom field ID
TIME_TRACKING_FIELD = 'timetracking'  # Time Tracking field
ORIGINAL_ESTIMATE_FIELD = 'originalEstimate'  # Original Estimate field (for updates)
REMAINING_ESTIMATE_FIELD = 'remainingEstimate' # Remaining Estimate field
MINUTES_PER_POINT = 360  # Minutes per story point (1 point = 1 day = 6 hours = 360 minutes)

def safe_jira_update(issue, fields):
    """Safely update a JIRA issue with rate limiting awareness."""
    try:
        result = issue.update(fields=fields)
        # Add a small delay between updates to be respectful
        time.sleep(0.5)
        return result
    except jira.exceptions.JIRAError as e:
        if e.status_code == 429:  # Too Many Requests
            print(f"{Fore.YELLOW}Rate limited. Waiting 60 seconds before retry...{Style.RESET_ALL}")
            time.sleep(60)
            # Retry once
            return issue.update(fields=fields)
        else:
            raise e

def convert_story_points_to_estimate(story_points):
    """Convert story points to time estimate format that JIRA expects."""
    total_minutes = story_points * MINUTES_PER_POINT
    return f"{int(total_minutes)}m"

def build_jql_query(query_type, name):
    """Build the JQL query based on type (assignee or team) and name."""
    # Base query components
    # we only want stories that have story points, no original estimate, and are in a done state
    # and were changed to that state after the start of the year
    base_conditions = [
        '"Story Points" > 0',
        'originalEstimate > 0',
        'remainingEstimate = 0',
        'issuetype not in (subTaskIssueTypes(), "Test Case Execution", "Test Execution", Test, DBCR)',
        'status NOT IN ("Acceptance", "Approved to Deploy", Certified, Closed, Complete, Completed, Deployed, Done, "Ready for Deployment", "Ready For Release", "Ready to Deploy", "Ready to Release", Released, Resolved, Withdrawn)'  # no comma here
        ' ORDER BY key ASC'
    ]

    # Add the assignee or team condition
    if query_type.lower() == "assignee":
        assignee_condition = f'Assignee = "{name}"'
    elif query_type.lower() == "team":
        assignee_condition = f'"Sprint Team" = "{name}"'
    else:
        raise ValueError(f"Invalid query type: {query_type}. Must be 'assignee' or 'team'")

    # Combine all conditions
    all_conditions = [assignee_condition] + base_conditions
    return " AND ".join(all_conditions)

# Parse command-line arguments
parser = argparse.ArgumentParser(description="Copy original estimate to remaining estimate for tickets with story points that are not resolved.")
parser.add_argument("type", choices=["assignee", "team"], help="Query type: 'assignee' or 'team'")
parser.add_argument("name", help="Name of the assignee or team to filter by")
parser.add_argument("--perform-update", action="store_true", help="Actually perform the updates (default is dry-run mode)")
args = parser.parse_args()

# Initialize colorama for colored output
init()

# Build the JQL query based on the provided type and name
try:
    jql_query = build_jql_query(args.type, args.name)
    print(f"{Style.BRIGHT}Using JQL query:{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{jql_query}{Style.RESET_ALL}\n")
except ValueError as e:
    print(f"{Fore.RED}Error: {e}{Style.RESET_ALL}")
    exit(1)

# Prompt for JIRA credentials if not stored in the config file
config = load_config()
# Create the JIRA client using the stored credentials
try:
    jira_client = jira.JIRA(config["jira_server"], token_auth=(config["personal_access_token"]))
except jira.exceptions.JIRAError as e:
    print(f"{Fore.RED}Error connecting to JIRA: {e}{Style.RESET_ALL}")
    exit(1)

# Search for issues using the built JQL query
try:
    issues = jira_client.search_issues(jql_query, maxResults=False, fields=f"summary,{STORY_POINTS_FIELD},{TIME_TRACKING_FIELD}")
    print(f"{Style.BRIGHT}Found {len(issues)} issues matching the query.{Style.RESET_ALL}")
except jira.exceptions.JIRAError as e:
    print(f"{Fore.RED}Error executing JQL query: {e}{Style.RESET_ALL}")
    exit(1)

if len(issues) == 0:
    print(f"{Fore.YELLOW}No issues found matching the query.{Style.RESET_ALL}")
    exit(0)

# Process each issue
updated_count = 0
skipped_count = 0

for issue in issues:
    issue_key = issue.key
    story_points = getattr(issue.fields, STORY_POINTS_FIELD, None)  # Story Points field

    # Extract original_estimate from timetracking if present
    original_estimate = None
    timetracking = getattr(issue.fields, TIME_TRACKING_FIELD, None)
    if timetracking is not None:
        original_estimate = getattr(timetracking, ORIGINAL_ESTIMATE_FIELD, None)

    # Check if issue has story points but no original estimate
    if story_points is not None and original_estimate is not None:
        estimate_value = original_estimate

        if args.perform_update:
            try:
                # Update the remaining estimates using timetracking structure
                # Since we're only finding open tickets, always set remaining to the estimate time
                safe_jira_update(issue, {TIME_TRACKING_FIELD: {
                    REMAINING_ESTIMATE_FIELD: estimate_value
                }})
                print(f"{Fore.GREEN}{issue_key}{Style.RESET_ALL}: {issue.fields.summary}")
                print(f"  ✓ Set remaining estimate to: {Fore.GREEN}{estimate_value}{Style.RESET_ALL} (completed ticket)")
                updated_count += 1
            except jira.exceptions.JIRAError as e:
                print(f"{Fore.RED}{issue_key}: Error updating issue - {e}{Style.RESET_ALL}")
                skipped_count += 1
        else:
            print(f"{Fore.CYAN}{issue_key}{Style.RESET_ALL}: {issue.fields.summary}")
            print(f"  Would set remaining estimate to: {Fore.GREEN}{estimate_value}{Style.RESET_ALL} (completed ticket)")
            updated_count += 1
    else:
        if story_points is None:
            reason = "no story points"
        elif original_estimate is not None:
            reason = f"already has original estimate ({original_estimate})"
        else:
            reason = "unknown reason"

        print(f"{Fore.YELLOW}{issue_key}{Style.RESET_ALL}: {issue.fields.summary}")
        print(f"  Skipped - {reason}")
        skipped_count += 1

# Print summary
print(f"\n{Style.BRIGHT}Summary:{Style.RESET_ALL}")
if args.perform_update:
    print(f"  Updated: {Fore.GREEN}{updated_count}{Style.RESET_ALL} issues")
    print(f"  Skipped: {Fore.YELLOW}{skipped_count}{Style.RESET_ALL} issues")
else:
    print(f"  Would update: {Fore.GREEN}{updated_count}{Style.RESET_ALL} issues")
    print(f"  Would skip: {Fore.YELLOW}{skipped_count}{Style.RESET_ALL} issues")
    print(f"\n{Fore.CYAN}Run with --perform-update to apply changes.{Style.RESET_ALL}")

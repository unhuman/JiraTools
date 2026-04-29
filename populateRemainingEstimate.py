# This script copies story points to original estimate for tickets that have story points but no original estimate.

# pip install colorama jira

import argparse
import time
from colorama import init, Fore, Style
import jira
from libraries.jiraToolsConfig import load_config, safe_jira_update, convert_story_points_to_estimate, MINUTES_PER_POINT
from libraries.jiraQueryTools import search_issues, build_remaining_estimate_query

# Configuration constants
STORY_POINTS_FIELD = 'customfield_10502'  # Story Points custom field ID
TIME_TRACKING_FIELD = 'timetracking'  # Time Tracking field
ORIGINAL_ESTIMATE_FIELD = 'originalEstimate'  # Original Estimate field (for updates)
REMAINING_ESTIMATE_FIELD = 'remainingEstimate' # Remaining Estimate field

if __name__ == '__main__':
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
        jql_query = build_remaining_estimate_query(args.type, args.name)
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
    issues = search_issues(jira_client, jql_query, max_results=False, fields=f"summary,{STORY_POINTS_FIELD},{TIME_TRACKING_FIELD}")

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

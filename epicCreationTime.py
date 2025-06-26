# This was created with Google Gemini, with prompting for various features and fixes.

import argparse
from datetime import datetime, timedelta
import jira
from jiraToolsConfig import load_config, statusIsDone # Import your configuration and utility functions
import sys
import re # For parsing sprint data if it's a string

def parse_jira_datetime(dt_str):
    """
    Parses a Jira datetime string into a datetime object, handling common Jira formats.
    Specifically handles:
    - ISO 8601 with 'Z' for UTC (e.g., '2021-08-12T17:46:44.000Z')
    - ISO 8601 with timezone offset without colon (e.g., '2021-08-12T17:46:44.000+0000')
    - ISO 8601 with timezone offset with colon (e.g., '2021-08-12T17:46:44.000+00:00')
    """
    if dt_str.endswith('Z'):
        # Convert 'Z' to '+00:00' for consistent parsing with fromisoformat
        dt_str = dt_str.replace('Z', '+00:00')
    elif '+' in dt_str or '-' in dt_str:
        # Check for timezone offset without a colon (e.g., '+0000' or '-0500')
        # Split the string by the last '+' or '-' to isolate the offset
        match = re.search(r'([+-])(\d{2})(\d{2})$', dt_str)
        if match:
            sign = match.group(1)
            hours = match.group(2)
            minutes = match.group(3)
            # Reconstruct the string with a colon in the offset
            dt_str = re.sub(r'([+-])(\d{2})(\d{2})$', f'{sign}{hours}:{minutes}', dt_str)

    return datetime.fromisoformat(dt_str)


def get_jira_client():
    """Initializes and returns a Jira client using the common configuration."""
    config = load_config()
    try:
        jira_server = config["jira_server"]
        personal_access_token = config["personal_access_token"]
    except KeyError:
        print("Error: Jira server URL or personal access token not found in ~/.jiraTools config.")
        print("Please ensure the configuration is set up correctly.")
        sys.exit(1)

    try:
        jira_client = jira.JIRA(jira_server, token_auth=(personal_access_token))
        print("Successfully connected to Jira.")
        return jira_client
    except jira.exceptions.JIRAError as e:
        print(f"Error connecting to Jira: {e}")
        sys.exit(1)

def get_open_epics(jira_client, sprint_team_name_filter=None, project_key=None):
    """
    Fetches all open epics, optionally filtered by project key and/or a "Sprint Team" field on the epic itself.
    An epic is considered 'open' if its status category is not 'Done'.

    Args:
        jira_client: An initialized Jira client object.
        sprint_team_name_filter: (Optional) A string that is the value of the "Sprint Team" custom field on the epic.
                                 If provided, epics will be filtered by this field directly.
        project_key: (Optional) The key of the Jira project (e.g., 'PROJ').
                     If provided, epics will be scoped to this project.

    Returns:
        A list of Jira Issue objects representing the open epics,
        or an empty list if none are found or an error occurs.
    """
    print(f"\nSearching for open epics...")
    jql_parts = [
        'issueType = Epic',
        'statusCategory != "Done"'
    ]

    if project_key:
        jql_parts.append(f'project = "{project_key}"')
        print(f"  (Scoped to project: '{project_key}')")

    # Assuming "Sprint Team" is a custom field on the Epic issue type itself
    if sprint_team_name_filter:
        # Use a case-insensitive 'LIKE' search if the exact match isn't always present
        # or if the field can contain more text. For exact match, use '='
        jql_parts.append(f'"Sprint Team" = "{sprint_team_name_filter}"')
        print(f"  (Filtered by Epic's 'Sprint Team' field: '{sprint_team_name_filter}')")

    jql_open_epics = " AND ".join(jql_parts) + ' ORDER BY created ASC'
    print(f"  JQL Query: {jql_open_epics}")

    epics = []
    try:
        # Request necessary fields: summary and created date
        epics = jira_client.search_issues(jql_open_epics, maxResults=False, fields="summary,created")
        if not epics:
            print(f"No open epics found for the specified criteria.")
        else:
            print(f"Found {len(epics)} open epics.")
    except jira.exceptions.JIRAError as e:
        print(f"Error searching for epics: {e}")
    return epics

def get_epic_development_data(jira_client, epic):
    """
    Analyzes a single epic to determine its development duration.
    Calculates the time difference between the epic's creation date and the
    latest creation date of any child ticket within that epic,
    considering only tickets created after the epic.

    Args:
        jira_client: An initialized Jira client object.
        epic: The Jira Issue object for the epic.

    Returns:
        A dictionary containing epic details and development duration, or None if no
        relevant data is found.
    """
    epic_key = epic.key
    epic_summary = epic.fields.summary
    epic_created_str = epic.fields.created
    epic_created = parse_jira_datetime(epic_created_str)

    print(f"\n  Analyzing Epic: {epic_key} - {epic_summary} (Created: {epic_created.strftime('%Y-%m-%d %H:%M:%S')})")

    jql_epic_children = f'"Epic Link" = "{epic_key}" OR parent = "{epic_key}"'
    # Removed: print(f"    JQL Query for all child tickets: {jql_epic_children}") # <--- Log the query

    epic_children = []
    try:
        epic_children = jira_client.search_issues(
            jql_epic_children,
            maxResults=False,
            fields="created"
        )
        if not epic_children:
            print(f"    No child tickets found for Epic {epic_key} using JQL: {jql_epic_children}.")
            return None
        # Removed: print(f"    Found {len(epic_children)} total child tickets for Epic {epic_key} using above JQL.")
    except jira.exceptions.JIRAError as e:
        print(f"    Error searching for children of epic {epic_key}: {e}")
        return None

    relevant_children_creation_times = []
    prior_children_creation_times = []

    for child_issue in epic_children:
        child_created_str = child_issue.fields.created
        child_created = parse_jira_datetime(child_created_str)

        if child_created < epic_created:
            prior_children_creation_times.append(child_created)
        else:
            relevant_children_creation_times.append(child_created)

    number_of_prior_tickets_count = len(prior_children_creation_times)
    relevant_child_issues_count = len(relevant_children_creation_times)


    if not relevant_children_creation_times:
        print(f"    No relevant child tickets (created after epic) found for Epic {epic_key}.")
        if number_of_prior_tickets_count > 0:
            first_prior = min(prior_children_creation_times).strftime('%Y-%m-%d %H:%M:%S')
            last_prior = max(prior_children_creation_times).strftime('%Y-%m-%d %H:%M:%S')
            print(f"    Number of Prior Tickets (Created Before Epic): {number_of_prior_tickets_count} (Range: {first_prior} to {last_prior})")
        else:
            print(f"    Number of Prior Tickets (Created Before Epic): {number_of_prior_tickets_count}")
        return None

    first_relevant_ticket_created = min(relevant_children_creation_times)
    last_relevant_ticket_created = max(relevant_children_creation_times)

    epic_development_span = last_relevant_ticket_created - epic_created
    ticket_creation_activity_span = last_relevant_ticket_created - first_relevant_ticket_created

    if number_of_prior_tickets_count > 0:
        first_prior = min(prior_children_creation_times).strftime('%Y-%m-%d %H:%M:%S')
        last_prior = max(prior_children_creation_times).strftime('%Y-%m-%d %H:%M:%S')
        print(f"    Number of Prior Tickets (Created Before Epic): {number_of_prior_tickets_count} (Range: {first_prior} to {last_prior})")
    else:
        print(f"    Number of Prior Tickets (Created Before Epic): {number_of_prior_tickets_count}")

    print(f"    Number of Relevant Tickets (Created After Epic): {relevant_child_issues_count} (Range: {first_relevant_ticket_created.strftime('%Y-%m-%d %H:%M:%S')} to {last_relevant_ticket_created.strftime('%Y-%m-%d %H:%M:%S')})")

    print(f"    Epic Development Span (Epic Creation to Last Relevant Ticket): {epic_development_span}")
    print(f"    Ticket Creation Activity Span (First Relevant Ticket to Last Relevant Ticket): {ticket_creation_activity_span}")


    return {
        "epic_key": epic_key,
        "epic_summary": epic_summary,
        "epic_created": epic_created,
        "first_relevant_ticket_created": first_relevant_ticket_created,
        "last_relevant_ticket_created": last_relevant_ticket_created,
        "epic_development_span": epic_development_span,
        "relevant_ticket_count": relevant_child_issues_count,
        "number_of_prior_tickets": number_of_prior_tickets_count,
        "first_prior_ticket_created": min(prior_children_creation_times) if prior_children_creation_times else None,
        "last_prior_ticket_created": max(prior_children_creation_times) if prior_children_creation_times else None,
        "ticket_creation_activity_span": ticket_creation_activity_span
    }

if __name__ == "__main__":
    # Parse arguments
    parser = argparse.ArgumentParser(
        description="Analyzes development time of open epics in Jira."
    )
    parser.add_argument(
        "--project_key",
        help="Optional: The key of the Jira project (e.g., 'PROJ'). If not provided, "
             "the search for epics will be broader, relying on the 'sprint_team_filter' argument."
    )
    parser.add_argument(
        "sprint_team_name",
        help="A unique string identifier for your sprint team (e.g., 'Team Alpha'). "
             "This will be used to filter epics if a 'Sprint Team' custom field exists on the epic itself (case-insensitive match)."
    )
    args = parser.parse_args()

    jira_client = get_jira_client()

    epics_to_analyze = get_open_epics(jira_client,
                                      sprint_team_name_filter=args.sprint_team_name,
                                      project_key=args.project_key)

    if not epics_to_analyze:
        print("No epics to analyze. Exiting.")
        sys.exit(0)

    analysis_results = []
    for epic in epics_to_analyze:
        result = get_epic_development_data(jira_client, epic)
        if result:
            analysis_results.append(result)

    if analysis_results:
        # Sort results by Epic Development Span in descending order
        sorted_results = sorted(analysis_results, key=lambda x: x["epic_development_span"], reverse=True)

        print("\n" + "="*60)
        print("      Epic Development Time Analysis Summary")
        print("="*60)

        for res in sorted_results:
            print(f"\nEpic Key: {res['epic_key']}")
            print(f"  Summary: {res['epic_summary']} (Created: {res['epic_created'].strftime('%Y-%m-%d %H:%M:%S')})")

            if res['number_of_prior_tickets'] > 0:
                print(f"  Number of Prior Tickets (Created Before Epic): {res['number_of_prior_tickets']} (Range: {res['first_prior_ticket_created'].strftime('%Y-%m-%d %H:%M:%S')} to {res['last_prior_ticket_created'].strftime('%Y-%m-%d %H:%M:%S')})")
            else:
                print(f"  Number of Prior Tickets (Created Before Epic): {res['number_of_prior_tickets']}")

            print(f"  Number of Relevant Tickets (Created After Epic): {res['relevant_ticket_count']} (Range: {res['first_relevant_ticket_created'].strftime('%Y-%m-%d %H:%M:%S')} to {res['last_relevant_ticket_created'].strftime('%Y-%m-%d %H:%M:%S')})")

            print(f"  Epic Development Span (Epic Creation to Last Relevant Ticket): {res['epic_development_span']}")
            print(f"  Ticket Creation Activity Span (First Relevant Ticket to Last Relevant Ticket): {res['ticket_creation_activity_span']}")
            print("-" * 50)


        if sorted_results:
            shortest_span_epic = sorted_results[-1] # Shortest is the last after sorting descending
            print("\n" + "="*60)
            print("      Epic with the SHORTEST Development Span")
            print("="*60)
            print(f"\nEpic Key: {shortest_span_epic['epic_key']}")
            print(f"  Summary: {shortest_span_epic['epic_summary']} (Created: {shortest_span_epic['epic_created'].strftime('%Y-%m-%d %H:%M:%S')})")

            if shortest_span_epic['number_of_prior_tickets'] > 0:
                print(f"  Number of Prior Tickets (Created Before Epic): {shortest_span_epic['number_of_prior_tickets']} (Range: {shortest_span_epic['first_prior_ticket_created'].strftime('%Y-%m-%d %H:%M:%S')} to {shortest_span_epic['last_prior_ticket_created'].strftime('%Y-%m-%d %H:%M:%S')})")
            else:
                print(f"  Number of Prior Tickets (Created Before Epic): {shortest_span_epic['number_of_prior_tickets']}")
            print(f"  Number of Relevant Tickets (Created After Epic): {shortest_span_epic['relevant_ticket_count']} (Range: {shortest_span_epic['first_relevant_ticket_created'].strftime('%Y-%m-%d %H:%M:%S')} to {shortest_span_epic['last_relevant_ticket_created'].strftime('%Y-%m-%d %H:%M:%S')})")
            print(f"  Development Span (Epic Creation to Last Relevant Ticket): {shortest_span_epic['epic_development_span']}")
            print(f"  Ticket Creation Activity Span (First Relevant Ticket to Last Relevant Ticket): {shortest_span_epic['ticket_creation_activity_span']}")
            print("="*60)


            greatest_diff_epic = sorted_results[0]
            print("\n" + "="*60)
            print("      Epic with the GREATEST Development Span")
            print("="*60)
            print(f"\nEpic Key: {greatest_diff_epic['epic_key']}")
            print(f"  Summary: {greatest_diff_epic['epic_summary']} (Created: {greatest_diff_epic['epic_created'].strftime('%Y-%m-%d %H:%M:%S')})")

            if greatest_diff_epic['number_of_prior_tickets'] > 0:
                print(f"  Number of Prior Tickets (Created Before Epic): {greatest_diff_epic['number_of_prior_tickets']} (Range: {greatest_diff_epic['first_prior_ticket_created'].strftime('%Y-%m-%d %H:%M:%S')} to {greatest_diff_epic['last_prior_ticket_created'].strftime('%Y-%m-%d %H:%M:%S')})")
            else:
                print(f"  Number of Prior Tickets (Created Before Epic): {greatest_diff_epic['number_of_prior_tickets']}")
            print(f"  Number of Relevant Tickets (Created After Epic): {greatest_diff_epic['relevant_ticket_count']} (Range: {greatest_diff_epic['first_relevant_ticket_created'].strftime('%Y-%m-%d %H:%M:%S')} to {greatest_diff_epic['last_relevant_ticket_created'].strftime('%Y-%m-%d %H:%M:%S')})")
            print(f"  Development Span (Epic Creation to Last Relevant Ticket): {greatest_diff_epic['epic_development_span']}")
            print(f"  Ticket Creation Activity Span (First Relevant Ticket to Last Relevant Ticket): {greatest_diff_epic['ticket_creation_activity_span']}")
            print("="*60)

        # Calculate Average Span
        total_span_seconds = sum(res['epic_development_span'].total_seconds() for res in analysis_results)
        average_span_seconds = total_span_seconds / len(analysis_results)
        average_span = timedelta(seconds=average_span_seconds)

        print("\n" + "="*60)
        print("      Overall Summary")
        print("="*60)
        print(f"Average Epic Development Span: {average_span}")
    else:
        print("\nNo epics with relevant development time data could be analyzed.")


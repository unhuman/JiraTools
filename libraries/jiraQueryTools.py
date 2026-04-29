# Shared Jira query utilities for JQL-based searches
# Used by scripts that query Jira for issues/epics/subtasks
# pip install colorama jira

from colorama import Fore, Style
import jira


def search_issues(jira_client, jql_query, max_results=False, fields=None):
    """
    Execute a JQL query with logging and error handling.

    Args:
        jira_client: The Jira client instance
        jql_query: The JQL query string
        max_results: Maximum results to return (False for all)
        fields: Comma-separated fields to return

    Returns:
        List of issues matching the query, or empty list on error
    """
    print(f"{Fore.CYAN}Executing JQL:{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{jql_query}{Style.RESET_ALL}\n")

    try:
        issues = jira_client.search_issues(jql_query, maxResults=max_results, fields=fields)
        print(f"{Fore.GREEN}Found {len(issues)} matching issues{Style.RESET_ALL}\n")
        return issues
    except jira.exceptions.JIRAError as e:
        print(f"{Fore.RED}Error executing JQL query: {e}{Style.RESET_ALL}")
        return []


def build_assignee_or_team_query(query_type, name, base_conditions):
    """
    Build a JQL query with assignee or team filtering prepended.

    Args:
        query_type: 'assignee' or 'team'
        name: Name of assignee or team
        base_conditions: List of base condition strings

    Returns:
        Complete JQL query string

    Raises:
        ValueError: If query_type is invalid
    """
    if query_type.lower() == "assignee":
        owner_condition = f'Assignee = "{name}"'
    elif query_type.lower() == "team":
        owner_condition = f'"Sprint Team" = "{name}"'
    else:
        raise ValueError(f"Invalid query type: {query_type}. Must be 'assignee' or 'team'")

    all_conditions = [owner_condition] + base_conditions
    return " AND ".join(all_conditions)


def build_epic_query(epic_key):
    """
    Build a JQL query to find all issues linked to an epic.

    Args:
        epic_key: The epic key (e.g., 'PROJ-123')

    Returns:
        JQL query string
    """
    return f'"Epic Link"={epic_key}'


def build_subtask_query(assignee, start_date, end_date):
    """
    Build a JQL query to find subtasks for a user within a date range.

    Args:
        assignee: Username/ID of the subtask assignee
        start_date: Start date (YYYY-MM-DD format)
        end_date: End date (YYYY-MM-DD format)

    Returns:
        JQL query string
    """
    return (
        f"issuetype in subTaskIssueTypes() AND assignee = '{assignee}' "
        f"AND updated >= '{start_date}' AND updated <= '{end_date}'"
    )


def build_open_epics_query(project_key=None, sprint_team=None):
    """
    Build a JQL query to find open (non-Done) epics.

    Args:
        project_key: Optional project key to filter by
        sprint_team: Optional "Sprint Team" field value to filter by

    Returns:
        JQL query string
    """
    conditions = [
        'issueType = Epic',
        'statusCategory != "Done"'
    ]

    if project_key:
        conditions.append(f'project = "{project_key}"')

    if sprint_team:
        conditions.append(f'"Sprint Team" = "{sprint_team}"')

    return " AND ".join(conditions) + " ORDER BY created ASC"


def build_points_estimate_query(query_type, name, exclude_done=True):
    """
    Build a JQL query for tickets needing story points to estimate conversion.
    Finds issues with story points but no original estimate.

    Args:
        query_type: 'assignee' or 'team'
        name: Name of assignee or team
        exclude_done: If True, exclude done/resolved statuses

    Returns:
        JQL query string
    """
    base_conditions = [
        '"Story Points" > 0',
        'originalEstimate is EMPTY',
        'issuetype not in (subTaskIssueTypes(), "Test Case Execution", "Test Execution", Test, DBCR)',
    ]

    if exclude_done:
        base_conditions.append(
            'status NOT IN ("Acceptance", "Approved to Deploy", Certified, Closed, '
            'Complete, Completed, Deployed, Done, "Ready for Deployment", "Ready For Release", '
            '"Ready to Deploy", "Ready to Release", Released, Resolved, Withdrawn)'
        )

    base_conditions.append('ORDER BY key ASC')

    return build_assignee_or_team_query(query_type, name, base_conditions)


def build_remaining_estimate_query(query_type, name):
    """
    Build a JQL query for tickets with story points but no remaining estimate.
    Finds issues that have been moved to done status this year.

    Args:
        query_type: 'assignee' or 'team'
        name: Name of assignee or team

    Returns:
        JQL query string
    """
    base_conditions = [
        '"Story Points" > 0',
        'originalEstimate > 0',
        'remainingEstimate = 0',
        'issuetype not in (subTaskIssueTypes(), "Test Case Execution", "Test Execution", Test, DBCR)',
        'status NOT IN ("Acceptance", "Approved to Deploy", Certified, Closed, Complete, Completed, '
        'Deployed, Done, "Ready for Deployment", "Ready For Release", "Ready to Deploy", '
        '"Ready to Release", Released, Resolved, Withdrawn)',
        'ORDER BY key ASC'
    ]

    return build_assignee_or_team_query(query_type, name, base_conditions)

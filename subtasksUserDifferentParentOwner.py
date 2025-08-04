"""
This script finds all subtasks assigned to a specific user within a given time period
where the parent ticket is owned by a different user. It is built to work with
the jira-python library using token authentication.

Usage:
    python find_mismatched_subtasks.py --user <subtask_assignee> \
        --start-date <YYYY-MM-DD> --end-date <YYYY-MM-DD>
"""
import argparse
from datetime import datetime
from jira import JIRA
from jiraToolsConfig import load_config, statusIsDone # This config needs to be updated

def main():
    """
    Main function to parse arguments, connect to Jira, and find the mismatched subtasks.
    """
    parser = argparse.ArgumentParser(description="Finds subtasks assigned to a user where the parent is owned by someone else.")
    
    # Required arguments for the query
    parser.add_argument("--user", required=True, help="The Jira username/ID of the subtask assignee to check")
    parser.add_argument("--start-date", required=True, help="The start date of the time period in YYYY-MM-DD format")
    parser.add_argument("--end-date", required=True, help="The end date of the time period in YYYY-MM-DD format")
    
    args = parser.parse_args()
    
    try:
        # Load the configuration from the jiraToolsConfig module
        config = load_config()

        # Validate date formats
        datetime.strptime(args.start_date, "%Y-%m-%d")
        datetime.strptime(args.end_date, "%Y-%m-%d")

        # Connect to Jira using a Personal Access Token
        # Note: 'jiraToolsConfig.py' must be updated with 'jira_server' and 'personal_access_token'
        jira_client = JIRA(config["jira_server"], token_auth=config["personal_access_token"])
        print("Connected to Jira successfully.")

        # Construct the initial JQL query to find all relevant subtasks
        jql_query = (
            f"issuetype in subTaskIssueTypes() AND assignee = '{args.user}' "
            f"AND updated >= '{args.start_date}' AND updated <= '{args.end_date}'"
        )
        print(f"Executing JQL: {jql_query}")
        
        # Fetch the subtasks using the corrected method for the jira-python library
        subtasks = jira_client.search_issues(jql_query)
        print(f"Found {len(subtasks)} subtasks matching the initial criteria.")

        mismatched_subtasks = []

        # Iterate through the subtasks to check the parent's assignee
        for subtask in subtasks:
            # Check if the subtask has a parent link
            if not hasattr(subtask.fields, 'parent'):
                print(f"Warning: Subtask {subtask.key} has no parent issue. Skipping.")
                continue

            parent_key = subtask.fields.parent.key
            
            # Fetch the full parent issue details using the corrected method
            parent_issue = jira_client.issue(parent_key)

            # Get the assignee and status of the subtask and the parent issue
            subtask_assignee = getattr(subtask.fields.assignee, 'name', 'Unassigned')
            subtask_status = getattr(subtask.fields.status, 'name', 'Unknown')
            
            parent_assignee = getattr(parent_issue.fields.assignee, 'name', 'Unassigned')
            parent_status = getattr(parent_issue.fields.status, 'name', 'Unknown')


            # Compare the assignees.
            if subtask_assignee and parent_assignee and subtask_assignee != parent_assignee:
                mismatched_subtasks.append({
                    'subtask_key': subtask.key,
                    'subtask_summary': subtask.fields.summary,
                    'subtask_assignee': subtask_assignee,
                    'subtask_status': subtask_status,
                    'parent_key': parent_key,
                    'parent_summary': parent_issue.fields.summary,
                    'parent_assignee': parent_assignee,
                    'parent_status': parent_status
                })

        # Print the final results
        if mismatched_subtasks:
            print("\n------------------------------------------------------------")
            print("Mismatched Subtasks Found:")
            print("------------------------------------------------------------")
            for item in mismatched_subtasks:
                # The status is now printed on the same line as the ticket key and summary.
                print(f"Subtask: {item['subtask_key']} ({item['subtask_summary']}) - {item['subtask_status']}")
                print(f"  Assigned to: {item['subtask_assignee']}")
                print(f"  Parent:    {item['parent_key']} ({item['parent_summary']}) - {item['parent_status']}")
                print(f"  Parent owner: {item['parent_assignee']}\n")
        else:
            print("\nNo subtasks found with a different parent owner in the specified time period.")

    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    main()

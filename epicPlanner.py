# pip install jira
# pip install networkx

import argparse
import jira
import networkx as nx
import os
import json

# Define the configuration file path
config_file = os.path.expanduser("~/.epicPlanner")

def load_config():
    """Loads the configuration from the file."""
    try:
        with open(config_file, "r") as f:
            config = json.load(f)
            return config
    except FileNotFoundError:
        return {}

def save_config(config):
    """Saves the configuration to the file."""
    with open(config_file, "w") as f:
        json.dump(config, f, indent=4)

# Parse command-line arguments
parser = argparse.ArgumentParser(description="Resolve ticket order based on dependencies.")
parser.add_argument("epic_key", help="The key of the epic")
parser.add_argument("--transitive", action="store_true", help="Include transitive dependencies")
args = parser.parse_args()

# Prompt for JIRA credentials if not stored in the config file
config = load_config()
if not "jira_server" in config or not "personal_access_token" in config:
    jira_server = input("Enter your JIRA server URL: ")
    # Ensure the JIRA server URL starts with "https://"
    if not jira_server.startswith("https://"):
        jira_server = "https://" + jira_server

    personal_access_token = input("Enter your JIRA personal access token: ")

    # Store the credentials in the configuration file
    config["jira_server"] = jira_server
    config["personal_access_token"] = personal_access_token
    save_config(config)

# Create the JIRA client using the stored credentials
jira_client = jira.JIRA(config["jira_server"], token_auth=(config["personal_access_token"]))

# Get Epic information
epic_key = args.epic_key
epic = jira_client.issue(epic_key)

# Search for all issues linked to the epic
jql = f"\"Epic Link\"={epic_key}"
issues = jira_client.search_issues(jql)

# Create a dependency graph
G = nx.DiGraph()
for issue in issues:
    issue_key = issue.key
    G.add_node(issue_key)
    for link in issue.fields.issuelinks:
        if hasattr(link, 'outwardIssue') and link.outwardIssue and link.outwardIssue.key != issue_key:
            G.add_edge(issue_key, link.outwardIssue.key)

# Topological sort
sorted_issues = list(nx.topological_sort(G))

# Get the transitive closure of the graph to include transitive dependencies
if args.transitive:
    G = nx.transitive_closure(G)


print("Ordered tickets with dependencies and summaries:")
for issue_key in sorted_issues:
    dependencies = [dep for dep in G.predecessors(issue_key)]
    issue = jira_client.issue(issue_key)  # Get the issue object
    summary = issue.fields.summary
    print(f"{issue_key}: {summary} - {dependencies}")


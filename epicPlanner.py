# This was created with Google Gemini, with prompting for various features and fixes.

# pip install colorama | jira | networkx

import argparse
from colorama import init,Fore,Back,Style
import jira
from jiraToolsConfig import load_config, statusIsDone
import networkx as nx

def createDependencyOutput(graph, listOfDependencies):
    """Creates a string representation of the dependencies."""
    if len (listOfDependencies) == 0:
        return "[]"
    else:
        data = "["
        for dep in listOfDependencies:
            if len(data) > 1:
                data = data + ", "
            data += Fore.GREEN if statusIsDone(graph.nodes[dep]['status']) else Fore.RED
            data += dep + Style.RESET_ALL
        data += "]"
        return data

def checkDependenciesResolved(strDependencies):
    return (strDependencies.find(Fore.RED) == -1)

# Parse command-line arguments
parser = argparse.ArgumentParser(description="Resolve ticket order based on dependencies.")
parser.add_argument("epic_key", help="The key of the epic")
parser.add_argument("-t", "--transitive", action="store_true", help="Include transitive dependencies")
args = parser.parse_args()

# Prompt for JIRA credentials if not stored in the config file
config = load_config()

# Create the JIRA client using the stored credentials
jira_client = jira.JIRA(config["jira_server"], token_auth=(config["personal_access_token"]))

# Get Epic information
epic_key = args.epic_key
epic = jira_client.issue(epic_key)

# Search for all issues linked to the epic
jql = f"\"Epic Link\"={epic_key}"
issues = jira_client.search_issues(jql)

# Create a dependency graph
graph = nx.DiGraph()
for issue in issues:
    issue_key = issue.key
    graph.add_node(issue_key, status=issue.fields.status.name)
    for link in issue.fields.issuelinks:
        if link.type.name.lower() == "blocks":
            if hasattr(link, 'outwardIssue') and link.outwardIssue and link.outwardIssue.key != issue_key:
                graph.add_edge(issue_key, link.outwardIssue.key)

# Topological sort
sorted_issues = list(nx.topological_sort(graph))

# Get the transitive closure of the graph to include transitive dependencies
transitive_graph = nx.transitive_closure(graph)

# Identify tickets that can be done in the same round
rounds = []
current_round = []
for issue_key in sorted_issues:
    if not current_round or all(any(dep in round_issues for round_issues in rounds) for dep in graph.predecessors(issue_key)):
        current_round.append(issue_key)
    else:
        rounds.append(current_round)
        current_round = [issue_key]
if current_round:
    rounds.append(current_round)

# Print ordered tickets with dependencies and summaries, grouped by round
print(f"{Style.BRIGHT}Ordered tickets with dependencies and summaries, grouped by round.{Style.RESET_ALL}")
print(f"Work that is done is {Style.BRIGHT}{Fore.GREEN}bright green{Style.RESET_ALL}, work that is ready is {Style.BRIGHT}{Fore.CYAN}bright cyan{Style.RESET_ALL}, and work that isn't ready is {Fore.CYAN}dim cyan{Style.RESET_ALL}.")
print(f"Dependencies that are in a completed state are {Fore.GREEN}green{Style.RESET_ALL}, while those that are not are {Fore.RED}red{Style.RESET_ALL}.")

for round_num, round_issues in enumerate(rounds, 1):
    print(Style.BRIGHT + f"Round {round_num}:" + Style.RESET_ALL)
    for issue_key in round_issues:
        dependencies = sorted([dep for dep in graph.predecessors(issue_key)])
        transitive_dependencies = "" if not args.transitive else sorted(set([dep for dep in transitive_graph.predecessors(issue_key)]) - set(dependencies))
        issue = jira_client.issue(issue_key)  # Get the issue object
        summary = issue.fields.summary

        outputDependencies = createDependencyOutput(graph, dependencies)
        outputTransitiveDependencies = f"transitive {createDependencyOutput(graph, transitive_dependencies)}" if len(transitive_dependencies) > 0 else ''
        
        colorIssue = Fore.GREEN if statusIsDone(issue.fields.status.name) else Fore.CYAN
        styleIssue = Style.BRIGHT if checkDependenciesResolved(outputDependencies) else ''
        print(f"{colorIssue}{styleIssue}{issue_key}{Style.RESET_ALL}: {summary} - {outputDependencies} {outputTransitiveDependencies}")

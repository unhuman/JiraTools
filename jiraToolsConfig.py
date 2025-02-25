import json
import os

# Configuration
config_file = os.path.expanduser("~/.jiraTools")

def load_config():
    try:
        with open(config_file, "r") as f:
            config = json.load(f)

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

            return config
    except FileNotFoundError:
        return {}

def save_config(config):
    with open(config_file, "w") as f:
        json.dump(config, f, indent=4)

def statusIsDone(check_status):
    doneStatuses = ["closed", "deployed", "done", "resolved"]
    return check_status.lower() in doneStatuses


import json
import os
import time

# Configuration
config_file = os.path.expanduser("~/.jiraTools")

# Constants for story point estimation
MINUTES_PER_POINT = 360  # Minutes per story point (1 point = 1 day = 6 hours = 360 minutes)

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
    doneStatuses = ["closed", "deployed", "done", "released", "resolved"]
    return check_status.lower() in doneStatuses

def get_backstage_url(config, cli_override=None):
    """Get Backstage URL from CLI override or config file.
    
    Args:
        config: Config dict from load_config()
        cli_override: Optional URL passed via CLI argument
        
    Returns:
        Backstage URL string (trailing slash stripped), or None if not configured
    """
    url = cli_override or config.get("backstageUrl")
    if url:
        return url.rstrip("/")
    return None

def safe_jira_update(issue, fields):
    """Safely update a JIRA issue with rate limiting awareness."""
    import jira as jira_module
    try:
        result = issue.update(fields=fields)
        # Add a small delay between updates to be respectful
        time.sleep(0.5)
        return result
    except jira_module.exceptions.JIRAError as e:
        if e.status_code == 429:  # Too Many Requests
            from colorama import Fore, Style
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


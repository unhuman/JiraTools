# Shared Backstage API utilities
# Used by teamApplicationAttribution.py, codeAudit.py, and other scripts that query Backstage
# pip install colorama requests

import requests
from colorama import Fore, Style
from typing import Dict, List, Optional


def matches_team_owner(owner: str, team_name: str) -> bool:
    """Check if an owner string matches the team name in various formats (case-insensitive)."""
    if not owner:
        return False
    owner_lower = owner.lower()
    team_lower = team_name.lower()
    return (owner_lower == f"group:default/{team_lower}" or 
            owner_lower == f"group:{team_lower}" or 
            owner_lower == team_lower or
            owner_lower.endswith(f"/{team_lower}") or
            owner_lower.endswith(f":{team_lower}"))


def get_all_teams(backstage_url: str, timeout: int = 30) -> List[Dict]:
    """
    Query Backstage catalog for all teams (groups).
    
    Args:
        backstage_url: Base URL for Backstage instance
        timeout: Request timeout in seconds
        
    Returns:
        List of team entities
    """
    print(f"{Fore.CYAN}Querying Backstage for all teams...{Style.RESET_ALL}")
    
    # Query the catalog for all groups (teams)
    catalog_url = f"{backstage_url}/api/catalog/entities"
    params = {
        "filter": "kind=group",
    }
    
    try:
        response = requests.get(catalog_url, params=params, timeout=timeout)
        response.raise_for_status()
        
        data = response.json()
        
        # Handle both list and dict responses
        if isinstance(data, list):
            teams = data
        elif isinstance(data, dict):
            teams = data.get('items', [])
        else:
            teams = []
        
        print(f"{Fore.GREEN}Found {len(teams)} teams in Backstage{Style.RESET_ALL}")
        return teams
        
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}Error querying Backstage catalog: {e}{Style.RESET_ALL}")
        return []


def get_all_components(backstage_url: str, timeout: int = 30) -> List[Dict]:
    """
    Fetch all components from Backstage catalog.
    Call this once and then use filter_components_for_team() to filter per team.
    
    Args:
        backstage_url: Base URL for Backstage instance
        timeout: Request timeout in seconds
        
    Returns:
        List of all component entities
    """
    catalog_url = f"{backstage_url}/api/catalog/entities"
    params = {
        "filter": "kind=component",
    }
    
    try:
        response = requests.get(catalog_url, params=params, timeout=timeout)
        response.raise_for_status()
        
        data = response.json()
        
        # Handle both list and dict responses
        if isinstance(data, list):
            return data
        elif isinstance(data, dict):
            return data.get('items', [])
        else:
            return []
        
    except requests.exceptions.RequestException as e:
        print(f"{Fore.RED}Error querying Backstage catalog for components: {e}{Style.RESET_ALL}")
        return []


def filter_components_for_team(all_components: List[Dict], team_name: str, comp_type: Optional[str] = 'application') -> List[Dict]:
    """
    Filter a pre-fetched list of components to those owned by a specific team.
    
    Args:
        all_components: List of all component entities (from get_all_components)
        team_name: Name of the team to filter for
        comp_type: Component type to filter (e.g., 'application'). None to include all types.
        
    Returns:
        List of components owned by the team
    """
    team_components = []
    
    for comp in all_components:
        owner = comp.get('spec', {}).get('owner', '')
        
        if matches_team_owner(owner, team_name):
            if comp_type is None:
                team_components.append(comp)
            else:
                component_type = comp.get('spec', {}).get('type', 'NO_TYPE')
                if component_type.lower() == comp_type.lower():
                    team_components.append(comp)
    
    return team_components


def get_team_components(backstage_url: str, team_name: str, timeout: int = 30) -> List[Dict]:
    """
    Query Backstage for all application components owned by a specific team.
    Convenience wrapper that fetches all components and filters for a single team.
    For processing multiple teams, use get_all_components() + filter_components_for_team() instead.
    
    Args:
        backstage_url: Base URL for Backstage instance
        team_name: Name of the team
        timeout: Request timeout in seconds
        
    Returns:
        List of application components owned by the team
    """
    all_components = get_all_components(backstage_url, timeout)
    return filter_components_for_team(all_components, team_name)

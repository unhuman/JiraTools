#!/usr/bin/env python3
"""
Team Application Attribution Script

This script queries Backstage for all sprint teams and the applications they own.
It creates a JSON file mapping each team to their owned applications.
Only includes components of type 'application' (excludes libraries, tests, cookbooks, repositories, infrastructure).

Usage:
    python teamApplicationAttribution.py <backstage_url> [options]

Examples:
    # Query all teams (outputs to allTeamApplications.json)
    python teamApplicationAttribution.py https://backstage.example.com
    
    # Query all teams with custom output file
    python teamApplicationAttribution.py https://backstage.example.com --output teams_apps.json
    
    # Query a single team (outputs to <team>Applications.json by default)
    python teamApplicationAttribution.py https://backstage.example.com --team knightriders
    
    # Query a single team with custom output file
    python teamApplicationAttribution.py https://backstage.example.com --team knightriders --output my_team.json
"""

import argparse
import json
import requests
from collections import Counter
from colorama import Fore, Style, init
from typing import Dict, List, Set


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Query Backstage for team application attribution and save to JSON file."
    )
    parser.add_argument(
        "backstage_url",
        help="Base URL for Backstage instance (e.g., https://backstage.example.com)"
    )
    parser.add_argument(
        "-t", "--team",
        help="Optional: Query only a specific team (e.g., knightriders)"
    )
    parser.add_argument(
        "-o", "--output",
        help="Output JSON file path (default: allTeamApplications.json or <team>Applications.json if --team is specified)"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="Request timeout in seconds (default: 30)"
    )
    
    args = parser.parse_args()
    
    # Prepend https:// if not present
    if not args.backstage_url.startswith(('http://', 'https://')):
        args.backstage_url = f'https://{args.backstage_url}'
    
    return args


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


def get_team_components(backstage_url: str, team_name: str, timeout: int = 30) -> List[Dict]:
    """
    Query Backstage for all application components owned by a specific team.
    Note: The UI may show more components than API reports as owner.
    The UI includes components where team is in relations, annotations, or other fields.
    This function only includes components where spec.owner matches the team.
    
    Args:
        backstage_url: Base URL for Backstage instance
        team_name: Name of the team
        timeout: Request timeout in seconds
        
    Returns:
        List of application components owned by the team
    """
    catalog_url = f"{backstage_url}/api/catalog/entities"
    
    # Get all components and filter manually
    # This is more reliable than using API filters which may not work correctly
    try:
        params = {
            "filter": "kind=component",
        }
        
        response = requests.get(catalog_url, params=params, timeout=timeout)
        response.raise_for_status()
        
        data = response.json()
        
        # Handle both list and dict responses
        if isinstance(data, list):
            all_components = data
        elif isinstance(data, dict):
            all_components = data.get('items', [])
        else:
            all_components = []
        
        # Filter components by owner and type
        # Only include components of type 'application' 
        # Exclude: libraries, tests, cookbooks, repositories, infrastructure, tools, external-api-keys, etc.
        team_components = []
        
        for comp in all_components:
            owner = comp.get('spec', {}).get('owner', '')
            comp_type = comp.get('spec', {}).get('type', 'NO_TYPE')
            
            if matches_team_owner(owner, team_name):
                # Only include type='application'
                if comp_type.lower() == 'application':
                    team_components.append(comp)
        
        return team_components
        
    except requests.exceptions.RequestException as e:
        print(f"{Fore.YELLOW}Warning: Error querying applications for team {team_name}: {e}{Style.RESET_ALL}")
        return []


def extract_component_info(component: Dict) -> Dict:
    """
    Extract relevant information from an application component entity.
    
    Args:
        component: Application component entity from Backstage
        
    Returns:
        Dictionary with application name, type, and lifecycle
    """
    metadata = component.get('metadata', {})
    spec = component.get('spec', {})
    labels = metadata.get('labels', {})
    
    # Get system from spec, use platform from labels as fallback
    system = spec.get('system', None)
    platform = labels.get('platform', None)
    product = labels.get('product', None)
    business_unit = labels.get('business-unit', None)
    
    return {
        'name': metadata.get('name', 'Unknown'),
        'title': metadata.get('title', metadata.get('name', 'Unknown')),
        'type': spec.get('type', 'Unknown'),
        'lifecycle': spec.get('lifecycle', 'Unknown'),
        'system': system or platform,  # Use platform as fallback if system is not defined
        'platform': platform,  # Also include platform explicitly
        'product': product,  # Include product from labels
        'business_unit': business_unit,  # Include business_unit from labels
        'description': metadata.get('description', '')
    }


def get_domain_info(backstage_url: str, domain_ref: str, timeout: int = 30) -> Dict:
    """
    Query Backstage for domain information.
    
    Args:
        backstage_url: Base URL for Backstage instance
        domain_ref: Domain reference (e.g., "domain:default/iam")
        timeout: Request timeout in seconds
        debug: Whether to print debug information
        
    Returns:
        Dictionary with domain information
    """
    # Parse the domain reference to get the domain name
    if ':' in domain_ref and '/' in domain_ref:
        # Format: "domain:default/iam"
        domain_name = domain_ref.split('/')[-1]
        namespace = domain_ref.split('/')[0].split(':')[-1] if '/' in domain_ref else 'default'
    else:
        return {}
    
    # Query the catalog for the domain entity
    catalog_url = f"{backstage_url}/api/catalog/entities/by-name/domain/{namespace}/{domain_name}"
    
    try:
        response = requests.get(catalog_url, timeout=timeout)
        response.raise_for_status()
        
        domain_entity = response.json()
        
        # LOG FULL DOMAIN RESPONSE FOR DEBUGGING (commented out for normal use)
        # print(f"{Fore.YELLOW}[DEBUG] === FULL DOMAIN RESPONSE FOR {domain_name} ==={Style.RESET_ALL}")
        # print(f"{Fore.YELLOW}{json.dumps(domain_entity, indent=2)}{Style.RESET_ALL}")
        # print(f"{Fore.YELLOW}[DEBUG] === END DOMAIN RESPONSE ==={Style.RESET_ALL}\n")
        
        metadata = domain_entity.get('metadata', {})
        spec = domain_entity.get('spec', {})
        annotations = metadata.get('annotations', {})
        
        # Get domain title
        domain_title = metadata.get('title', metadata.get('name', ''))
        
        # Get business unit from parent domain (if this is a subdomain)
        business_unit = None
        subdomain_of = spec.get('subdomainOf')
        parent_domain = spec.get('owner')
        
        if subdomain_of or parent_domain:
            # Try to get the parent domain's title as the business unit
            parent_ref = parent_domain if parent_domain else f"domain:{namespace}/{subdomain_of}"
            if parent_ref and parent_ref.startswith('domain:'):
                parent_domain_name = parent_ref.split('/')[-1]
                parent_catalog_url = f"{backstage_url}/api/catalog/entities/by-name/domain/{namespace}/{parent_domain_name}"
                
                try:
                    parent_response = requests.get(parent_catalog_url, timeout=timeout)
                    parent_response.raise_for_status()
                    parent_entity = parent_response.json()
                    parent_metadata = parent_entity.get('metadata', {})
                    business_unit = parent_metadata.get('title', parent_metadata.get('name', ''))
                except requests.exceptions.RequestException:
                    pass
        
        # Fallback to annotations
        if not business_unit:
            business_unit = (annotations.get('backstage.io/business-unit') or
                            annotations.get('business-unit') or
                            spec.get('businessUnit') or
                            spec.get('business_unit'))
        
        # Get product from annotations or spec
        product = (annotations.get('backstage.io/product') or
                  annotations.get('product') or
                  spec.get('product'))
        
        return {
            'domain_name': metadata.get('name', ''),
            'domain_title': domain_title,
            'business_unit': business_unit,
            'product': product,
        }
        
    except requests.exceptions.RequestException:
        return {}


def extract_team_info(team: Dict, debug: bool = False, backstage_url: str = None, timeout: int = 30) -> Dict:
    """
    Extract team information including Domain, Business Unit, and Platform.
    
    Args:
        team: Team entity from Backstage
        debug: Whether to print debug information
        backstage_url: Base URL for Backstage (to fetch domain info)
        timeout: Request timeout
        
    Returns:
        Dictionary with team metadata
    """
    metadata = team.get('metadata', {})
    spec = team.get('spec', {})
    annotations = metadata.get('annotations', {})
    labels = metadata.get('labels', {})
    
    team_name = metadata.get('name', 'unknown')
    
    # LOG FULL TEAM ENTITY FOR DEBUGGING (commented out for normal use)
    # print(f"{Fore.YELLOW}[DEBUG] === FULL TEAM ENTITY FOR {team_name} ==={Style.RESET_ALL}")
    # print(f"{Fore.YELLOW}{json.dumps(team, indent=2)}{Style.RESET_ALL}")
    # print(f"{Fore.YELLOW}[DEBUG] === END TEAM ENTITY ==={Style.RESET_ALL}\n")
    
    # Debug: print all available annotations and spec fields
    if debug:
        team_name = metadata.get('name', 'unknown')
        print(f"{Fore.YELLOW}Debug: Team {team_name} annotations: {annotations}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}Debug: Team {team_name} spec: {spec}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}Debug: Team {team_name} labels: {labels}{Style.RESET_ALL}")
    
    # Extract parent domain reference
    parent = spec.get('parent', None)
    domain_name = None
    business_unit = None
    product = None
    platform = None
    
    # First, try to get values from team labels (highest priority)
    business_unit = (labels.get('business-unit') or
                    labels.get('businessUnit'))
    
    product = labels.get('product')
    
    platform = labels.get('platform')
    
    # If parent is a domain reference, fetch domain information for fallback
    if parent and parent.startswith('domain:') and backstage_url:
        domain_info = get_domain_info(backstage_url, parent, timeout)
        if domain_info:
            domain_name = domain_info.get('domain_title') or domain_info.get('domain_name')
            # Use domain info as fallback only if not in labels
            if not business_unit:
                business_unit = domain_info.get('business_unit')
            if not product:
                product = domain_info.get('product')
    
    # Fallback: parse domain from parent if domain info not available
    if not domain_name and parent:
        if parent.startswith('domain:'):
            domain_part = parent.split('/', 1)[-1] if '/' in parent else parent.split(':', 1)[-1]
            if domain_part and domain_part != 'default':
                domain_name = domain_part.upper()  # Convert to uppercase (e.g., iam -> IAM)
    
    # Try annotations as final fallback
    if not business_unit:
        business_unit = (annotations.get('backstage.io/business-unit') or
                        annotations.get('business-unit') or
                        annotations.get('businessUnit'))
    
    if not product:
        product = (annotations.get('backstage.io/product') or
                  annotations.get('product'))
    
    if not platform:
        platform = (annotations.get('backstage.io/platform') or 
                   annotations.get('platform'))
    
    # Capitalize product if it exists (e.g., "essentials" -> "Essentials")
    if product:
        product = product.capitalize()
    
    # Format business_unit: convert "event-cloud" to "Event Cloud"
    if business_unit:
        business_unit = business_unit.replace('-', ' ').title()
    
    # Format platform: convert "simple-solutions" to "Simple Solutions"
    if platform:
        platform = platform.replace('-', ' ').title()
    
    return {
        'team_name': metadata.get('name', ''),
        'team_title': metadata.get('title', metadata.get('name', '')),
        'description': metadata.get('description', ''),
        'domain': domain_name,
        'business_unit': business_unit,
        'product': product,
        'platform': platform,
        'parent': parent,
        'type': spec.get('type', None)
    }


def build_service_attribution(backstage_url: str, timeout: int = 30, single_team: str = None) -> Dict[str, List[Dict]]:
    """
    Build a mapping of teams to their owned applications.
    
    Args:
        backstage_url: Base URL for Backstage instance
        timeout: Request timeout in seconds
        single_team: Optional team name to query only that team
        
    Returns:
        Dictionary mapping team names to lists of their owned applications
    """
    attribution = {}
    
    # If a single team is specified, query only that team
    if single_team:
        print(f"{Fore.CYAN}Querying applications for team: {single_team}...{Style.RESET_ALL}")
        
        # Try to get team info from Backstage
        teams = get_all_teams(backstage_url, timeout)
        team_info = None
        for team in teams:
            if team.get('metadata', {}).get('name', '').lower() == single_team.lower():
                team_info = extract_team_info(team, backstage_url=backstage_url, timeout=timeout)
                break
        
        # If we didn't find team info, create a basic one
        if not team_info:
            team_info = {
                'team_name': single_team,
                'team_title': single_team,
                'description': '',
                'domain': None,
                'business_unit': None,
                'product': None,
                'platform': None,
                'parent': None
            }
        
        components = get_team_components(backstage_url, single_team, timeout)
        
        if components:
            component_info = [extract_component_info(comp) for comp in components]
            
            # If team product is null, try to infer from applications
            if team_info.get('product') is None and component_info:
                products = [app.get('product') for app in component_info if app.get('product')]
                if products:
                    # Use the most common product, or first one if all unique
                    from collections import Counter
                    product_counts = Counter(products)
                    team_info['product'] = product_counts.most_common(1)[0][0]
            
            attribution[single_team] = {
                **team_info,  # Include all team metadata
                'application_count': len(component_info),
                'applications': component_info
            }
            print(f"{Fore.GREEN}Found {len(component_info)} applications for {single_team}{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}No applications found for {single_team}{Style.RESET_ALL}")
        
        return attribution
    
    # Otherwise, get all teams
    teams = get_all_teams(backstage_url, timeout)
    
    if not teams:
        print(f"{Fore.RED}No teams found in Backstage{Style.RESET_ALL}")
        return attribution
    
    total_teams = len(teams)
    print(f"{Fore.CYAN}Querying applications for each team...{Style.RESET_ALL}")
    
    # For each team, get their owned components
    for team_index, team in enumerate(teams, start=1):
        team_info = extract_team_info(team, debug=False, backstage_url=backstage_url, timeout=timeout)
        team_name = team_info['team_name']
        team_title = team_info['team_title']
        
        if not team_name:
            continue
        
        print(f"{Fore.CYAN}  Querying team: {team_title} ({team_name}) ({team_index}/{total_teams}){Style.RESET_ALL}")
        
        components = get_team_components(backstage_url, team_name, timeout)
        
        if components:
            component_info = [extract_component_info(comp) for comp in components]
            
            # If team product is null, try to infer from applications
            if team_info.get('product') is None and component_info:
                products = [app.get('product') for app in component_info if app.get('product')]
                if products:
                    # Use the most common product, or first one if all unique
                    from collections import Counter
                    product_counts = Counter(products)
                    team_info['product'] = product_counts.most_common(1)[0][0]
            
            attribution[team_name] = {
                **team_info,  # Include all team metadata (name, title, domain, business_unit, platform, etc.)
                'application_count': len(component_info),
                'applications': component_info
            }
            print(f"{Fore.GREEN}    Found {len(component_info)} applications{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}    No applications found{Style.RESET_ALL}")
    
    return attribution


def save_to_json(data: Dict, output_file: str):
    """
    Save the application attribution data to a JSON file.
    Sorts teams alphabetically and applications within each team alphabetically.
    
    Args:
        data: Application attribution dictionary
        output_file: Path to output JSON file
    """
    try:
        # Sort teams alphabetically by team name
        sorted_data = {}
        for team_name in sorted(data.keys()):
            team_data = data[team_name].copy()
            
            # Sort applications alphabetically by name within each team
            if 'applications' in team_data and team_data['applications']:
                team_data['applications'] = sorted(
                    team_data['applications'],
                    key=lambda app: app.get('name', '').lower()
                )
            
            sorted_data[team_name] = team_data
        
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(sorted_data, f, indent=2, ensure_ascii=False)
        
        print(f"{Fore.GREEN}Successfully saved application attribution to {output_file}{Style.RESET_ALL}")
        
    except Exception as e:
        print(f"{Fore.RED}Error saving to file: {e}{Style.RESET_ALL}")


def print_summary(attribution: Dict):
    """
    Print a summary of the application attribution data.
    
    Args:
        attribution: Application attribution dictionary
    """
    print(f"\n{Fore.CYAN}=== Application Attribution Summary ==={Style.RESET_ALL}")
    
    total_teams = len(attribution)
    total_applications = sum(team_data['application_count'] for team_data in attribution.values())
    teams_with_applications = sum(1 for team_data in attribution.values() if team_data['application_count'] > 0)
    
    print(f"{Fore.CYAN}Total teams: {total_teams}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Teams with applications: {teams_with_applications}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Total applications: {total_applications}{Style.RESET_ALL}")
    
    if total_teams > 0:
        avg_applications = total_applications / total_teams
        print(f"{Fore.CYAN}Average applications per team: {avg_applications:.1f}{Style.RESET_ALL}")
    
    # Show top teams by application count
    if attribution:
        print(f"\n{Fore.CYAN}Top teams by application count:{Style.RESET_ALL}")
        sorted_teams = sorted(
            attribution.items(),
            key=lambda x: x[1]['application_count'],
            reverse=True
        )[:10]  # Top 10
        
        for team_name, team_data in sorted_teams:
            count = team_data['application_count']
            title = team_data['team_title']
            if count > 0:
                print(f"{Fore.GREEN}  {title}: {count} applications{Style.RESET_ALL}")


def main():
    """Main function."""
    # Initialize colorama
    init()
    
    # Parse arguments
    args = parse_arguments()
    
    # Remove trailing slash from backstage_url if present
    backstage_url = args.backstage_url.rstrip('/')
    
    # Determine output file name
    if args.output:
        output_file = args.output
    elif args.team:
        output_file = f"{args.team}Applications.json"
    else:
        output_file = "allTeamApplications.json"
    
    print(f"{Fore.CYAN}Starting application attribution query...{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Backstage URL: {backstage_url}{Style.RESET_ALL}")
    if args.team:
        print(f"{Fore.CYAN}Team: {args.team}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Output file: {output_file}{Style.RESET_ALL}")
    
    # Build application attribution
    attribution = build_service_attribution(backstage_url, args.timeout, args.team)
    
    if not attribution:
        print(f"{Fore.RED}No application attribution data collected{Style.RESET_ALL}")
        return
    
    # Save to JSON file
    save_to_json(attribution, output_file)
    
    # Print summary
    print_summary(attribution)
    
    print(f"\n{Fore.GREEN}Application attribution complete!{Style.RESET_ALL}")


if __name__ == "__main__":
    main()

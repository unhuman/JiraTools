# This script creates standard Jira tickets based on data from an Excel file
# Runs in dry-run mode by default - use -c/--create to actually create tickets
# pip install colorama jira pandas openpyxl
#
# Teams Sheet Fields:
# - Sprint Team: Team identifier (required)
# - Assignee: Person to assign tickets to (optional)
# - Project: Jira project key (required)
# - Epic Link: Parent epic key to link tickets to (optional)
# - Issue Type: Jira issue type override (optional)
# - Sprint: Sprint to assign tickets to (optional)
#
# Custom Fields Configuration:
# - Define custom fields in the "CustomFields" sheet with columns:
#   1. Field Name: Name of the field in Excel (e.g., "Sprint Team", "Epic Link")
#   2. Field ID: Jira custom field ID (e.g., "customfield_10001")
#   3. Data Wrapper: (Optional) How to format the field value in the API request
#      - If set to "value" -> {"value": field_value}
#      - If set to "none" or left empty -> field_value (directly)
#      - Any other value will be used as the wrapper key -> {wrapper: field_value}

import argparse
import os
import pandas as pd
from colorama import init, Fore, Style
import jira
import sys
import json
import requests
import traceback
from collections import defaultdict
from jiraToolsConfig import load_config

# Class to represent a ticket with ID and summary
class TicketInfo:
    def __init__(self, ticket_id, summary):
        self.ticket_id = ticket_id
        self.summary = summary
    
    def __str__(self):
        return self.ticket_id

# Class to track simulated ticket counters in dry-run mode
class SimulatedTicketCounter:
    def __init__(self):
        self.counters = defaultdict(int)
    
    def get_next_ticket_id(self, project_key):
        """Generate a simulated ticket ID with format: simulated-PROJECT-COUNTER"""
        self.counters[project_key] += 1
        counter = self.counters[project_key]
        return f"simulated-{project_key}-{counter}"

# Global counter for simulated tickets
simulated_ticket_counter = SimulatedTicketCounter()

# static array of Sheets to process in the excel file.
SHEET_NAMES = ["Ownership", "Quality", "Security", "Reliability"]

# Constants for column names
ASSIGNEE_FIELD = "Assignee"
PROJECT_FIELD = "Project"
EPIC_LINK_FIELD = "Epic Link"
SPRINT_FIELD = "Sprint"
EPIC_LINK_TYPE = "Epic-Story Link"  # The link type used to connect stories to epics
CONFIG_SHEET = "Config"
CUSTOM_FIELDS_SHEET = "CustomFields"  # Sheet for custom field mappings
ISSUE_TYPE_KEY = "Issue Type"
PRIORITY_KEY = "Priority"  # The key for the Priority field in the Config sheet

def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="Create standard Jira tickets from Excel data.")
    parser.add_argument("excel_file", help="Path to the Excel file containing team data", default="teams.xlsx")
    parser.add_argument("-c", "--create", action="store_true", help="Actually create tickets in Jira (default is dry-run mode)")
    parser.add_argument("--csv", "--export-csv", dest="csv_file", metavar="FILE", help="Export tickets to CSV file for Jira import instead of creating them")
    
    # Team filtering parameters (mutually exclusive)
    team_group = parser.add_mutually_exclusive_group()
    team_group.add_argument("--processTeams", help="Comma-separated list of teams to process (if provided, only these teams will be processed)")
    team_group.add_argument("--excludeTeams", help="Comma-separated list of teams to exclude from processing")
    
    # Note in help text about using Project field for project key
    parser.epilog = ("Note: Project key is determined by the 'Project' field in the Teams sheet.\n"
                   "Issue type is determined by the 'Issue Type' field in the Teams sheet.\n"
                   "Sprint is determined by the 'Sprint' field in the Teams sheet (optional).\n"
                   "Component is determined by the 'Component' field in the Teams sheet (optional).\n"
                   "Priority is read from the 'Config' sheet with key 'Priority'.\n"
                   "Each ticket will be linked to the 'Epic Link' specified in the Teams sheet.\n"
                   "Use --processTeams to specify which teams to process or --excludeTeams to exclude specific teams.\n"
                   "Use --csv to export tickets to a CSV file for Jira import instead of creating them directly.")
    
    return parser.parse_args()

def read_config_sheet(file_path):
    """Read the Config sheet from the Excel file to get configuration values."""
    try:
        # Read the Config sheet
        df = pd.read_excel(file_path, sheet_name=CONFIG_SHEET)
        
        # Convert to a key-value dictionary
        config = {}
        for _, row in df.iterrows():
            if len(row) >= 2:  # Ensure the row has at least 2 columns
                key = str(row.iloc[0]).strip()
                value = str(row.iloc[1]).strip()
                if key and value != 'nan':
                    config[key] = value
        
        return config
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Could not read Config sheet: {str(e)}. Using default values.{Style.RESET_ALL}")
        return {}

def get_categories_from_config(config):
    """Extract and parse categories from config, with fallback to default categories."""
    import re
    
    # Get categories from config or use defaults
    categories_config = config.get('Categories', config.get('categories', ''))
    
    if categories_config:
        # Split by comma and clean up whitespace
        categories = re.split(r'\s*,\s*', categories_config.strip())
        # Filter out empty strings
        categories = [cat for cat in categories if cat]
        print(f"Using categories from Config sheet: {', '.join(categories)}")
        return categories
    else:
        # Default categories
        default_categories = ['Ownership', 'Quality', 'Security', 'Reliability']
        print(f"No Categories found in Config sheet, using defaults: {', '.join(default_categories)}")
        return default_categories
        
def read_custom_fields_mapping(file_path):
    """Read the CustomFields sheet from the Excel file to get custom field mappings.
    
    The CustomFields sheet should have the following columns:
    1. Field Name: The name of the field as it appears in Excel data
    2. Field ID: The Jira custom field ID (e.g., customfield_10001)
    3. Data Wrapper: (Optional) How to format the value in the API request:
       - If "value" -> the field will be formatted as {"value": field_value}
       - If empty or "none" -> the field value will be used directly
       - Any other string -> the field will be formatted as {wrapper: field_value}
    """
    try:
        # Read the CustomFields sheet
        df = pd.read_excel(file_path, sheet_name=CUSTOM_FIELDS_SHEET)
        
        # Convert to a key-value dictionary with data wrapper information
        custom_fields_mapping = {}
        for _, row in df.iterrows():
            if len(row) >= 2:  # Ensure the row has at least 2 columns
                field_name = str(row.iloc[0]).strip()
                custom_field_id = str(row.iloc[1]).strip()
                
                # Check if Data Wrapper column exists (should be the 3rd column)
                data_wrapper = None
                if len(row) >= 3 and pd.notna(row.iloc[2]):
                    data_wrapper = str(row.iloc[2]).strip()
                    # If data_wrapper is "none" (case insensitive), set to None
                    if data_wrapper.lower() == "none":
                        data_wrapper = None
                
                if field_name and custom_field_id != 'nan':
                    # Store both the field ID and data wrapper information
                    custom_fields_mapping[field_name] = {
                        "id": custom_field_id,
                        "wrapper": data_wrapper
                    }
        
        print(f"{Fore.CYAN}Loaded {len(custom_fields_mapping)} custom field mappings{Style.RESET_ALL}")
        for field_name, mapping in custom_fields_mapping.items():
            wrapper_info = f"wrapper: '{mapping['wrapper']}'" if mapping['wrapper'] else "no wrapper"
            print(f"{Fore.CYAN}  Field '{field_name}' → {mapping['id']} ({wrapper_info}){Style.RESET_ALL}")
        return custom_fields_mapping
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Could not read CustomFields sheet: {str(e)}. No custom field mappings will be used.{Style.RESET_ALL}")
        return {}

def get_backstage_url_from_config(config):
    """Extract Backstage URL from config dictionary."""
    backstage_url = config.get('Backstage')
    if backstage_url:
        return backstage_url.rstrip('/')  # Remove trailing slash
    return None

def query_backstage_entity(backstage_url, team_name):
    """Query Backstage API for entity scorecard data with enhanced health extraction."""
    try:
        # Use enhanced team health extraction
        health_data = get_team_health_enhanced(backstage_url, team_name)
        
        if health_data:
            # Convert to legacy format for compatibility with existing code
            return {
                'metadata': {
                    'name': team_name,
                    'annotations': {
                        'enhanced-scorecard-data': json.dumps(health_data)
                    }
                }
            }
        else:
            print(f"{Fore.YELLOW}Warning: Team '{team_name}' not found in Backstage{Style.RESET_ALL}")
            return None
            
    except Exception as e:
        print(f"{Fore.RED}Error connecting to Backstage for {team_name}: {e}{Style.RESET_ALL}")
        return None

def get_team_health_enhanced(backstage_url, team_name, categories=None):
    """Enhanced team health data extraction from multiple Backstage sources."""
    print(f"{Fore.CYAN}Extracting health data for team: {team_name}{Style.RESET_ALL}")
    
    # Use default categories if none provided
    if categories is None:
        categories = ['Ownership', 'Quality', 'Security', 'Reliability']
    
    # Try multiple API endpoints for comprehensive data extraction
    health_data = None
    
    # Method 1: Try scorecards API endpoints
    health_data = try_scorecards_api(backstage_url, team_name, categories)
    
    # Method 2: Fall back to catalog entity API
    if not health_data:
        health_data = try_catalog_api(backstage_url, team_name, categories)
        
    # Method 3: Try alternative endpoints
    if not health_data:
        health_data = try_alternative_apis(backstage_url, team_name, categories)
        
    if health_data:
        print(f"{Fore.GREEN}  ✓ Successfully extracted health data for {team_name}{Style.RESET_ALL}")
        print_health_summary(health_data)
    else:
        print(f"{Fore.YELLOW}  ⚠ No health data found for {team_name}{Style.RESET_ALL}")
        
    return health_data

def try_scorecards_api(backstage_url, team_name, categories):
    """Try dedicated scorecards API endpoints."""
    try:
        # First try GraphQL API that the UI actually uses - this contains complete track data
        graphql_url = f"{backstage_url}/api/soundcheck/graphql"
        graphql_query = {
            "query": """query getAllCertifications($entityRef: String!) {
                certifications(entityRef: $entityRef, includeFilteredChecks: false) {
                    ...CertificationSummary
                }
            }
            fragment CertificationSummary on Certification {
                entityRef
                track {
                    id
                    name
                    description
                    type
                    draft
                }
                highestLevel {
                    entityRef
                    ordinal
                    name
                    checks {
                        id
                        name
                    }
                    badge {
                        ... on BadgeVariantMedal {
                            variant
                            options {
                                level
                                color
                            }
                        }
                    }
                }
                levels {
                    ordinal
                    name
                    badge {
                        ... on BadgeVariantMedal {
                            variant
                            options {
                                level
                                color
                            }
                        }
                    }
                    checks {
                        id
                        name
                        entityRef
                        result
                        description
                        timestamp
                        details
                    }
                }
            }""",
            "variables": {
                "entityRef": f"group:default/{team_name}"
            }
        }
        
        print(f"    Trying GraphQL API: /api/soundcheck/graphql")
        response = requests.post(graphql_url, json=graphql_query, timeout=30)
        print(f"    GraphQL Status: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and 'certifications' in data['data']:
                print(f"    ✓ Successfully found GraphQL certification data")
                health_data = parse_graphql_certifications(data, categories, team_name)
                if health_data:
                    return health_data
            else:
                print(f"    GraphQL response structure: {list(data.keys()) if isinstance(data, dict) else 'not dict'}")
        
        # Fall back to REST API if GraphQL fails
        url = f"{backstage_url}/api/soundcheck/results?entityRef=group:default/{team_name}"
        print(f"    Falling back to REST API: /api/soundcheck/results?entityRef=group:default/{team_name}")
        
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            data = response.json()
            print(f"    REST API fallback successful")
            health_data = parse_soundcheck_results(data, categories, team_name)
            if health_data:
                return health_data
        
        # Fall back to other API endpoints if needed
        api_endpoints = [
            f"/api/scorecards/entities/group:default/{team_name}",
            f"/api/scorecards/group/default/{team_name}",
            f"/api/soundcheck/entities/group:default/{team_name}",
            f"/api/tech-insights/entities/group:default/{team_name}"
        ]
        
        for endpoint in api_endpoints:
            url = f"{backstage_url}{endpoint}"
            print(f"    Trying scorecards API: {endpoint}")
            
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                data = response.json()
                health_data = parse_scorecards_response(data, categories)
                if health_data:
                    return health_data
                    
    except Exception as e:
        print(f"    Scorecards API error: {e}")
        
    return None

def try_catalog_api(backstage_url, team_name, categories):
    """Try catalog entity API for health data."""
    try:
        url = f"{backstage_url}/api/catalog/entities/by-name/group/default/{team_name.lower()}"
        print(f"    Trying catalog API: /api/catalog/entities/by-name/group/default/{team_name.lower()}")
        
        response = requests.get(url, timeout=30)
        if response.status_code == 200:
            entity_data = response.json()
            return parse_entity_health_data(entity_data, categories)
            
    except Exception as e:
        print(f"    Catalog API error: {e}")
        
    return None

def try_alternative_apis(backstage_url, team_name, categories):
    """Try alternative API endpoints for health data."""
    try:
        # Try different API patterns that might contain scorecard data
        alternative_endpoints = [
            f"/api/catalog/entities?filter=kind=group,metadata.name={team_name}",
            f"/api/catalog/entities/by-name/component/default/{team_name}",
            f"/api/catalog/locations/by-entity/group:default/{team_name}"
        ]
        
        for endpoint in alternative_endpoints:
            url = f"{backstage_url}{endpoint}"
            print(f"    Trying alternative API: {endpoint}")
            
            response = requests.get(url, timeout=30)
            if response.status_code == 200:
                data = response.json()
                health_data = parse_alternative_response(data, team_name, categories)
                if health_data:
                    return health_data
                    
    except Exception as e:
        print(f"    Alternative API error: {e}")
        
    return None

def parse_graphql_certifications(data, categories, team_name):
    """Parse GraphQL certifications response to extract compliance data."""
    try:
        if 'errors' in data:
            print(f"    GraphQL errors: {data['errors']}")
            return None
            
        certifications = data.get('data', {}).get('certifications', [])
        if not certifications:
            print(f"    No certifications found for {team_name}")
            return None
            
        print(f"    Found {len(certifications)} certifications")
        
        # Initialize health categories
        health_categories = {}
        for category in categories:
            health_categories[category] = {}
            
        # Process all certifications (tracks)
        all_checks = []
        for cert in certifications:
            track_name = cert.get('track', {}).get('name', 'Unknown')
            print(f"    Processing track: {track_name}")
            
            # Extract checks from all levels
            levels = cert.get('levels', [])
            for level in levels:
                level_name = level.get('name', '')
                checks = level.get('checks', [])
                print(f"      Level {level_name}: {len(checks)} checks")
                
                for check in checks:
                    check_id = check.get('id', '')
                    result = check.get('result', '')
                    details = check.get('details', {})
                    
                    # Convert GraphQL format to our expected format and preserve level info
                    converted_check = {
                        'checkId': check_id,
                        'state': result.lower() if result else 'unknown',
                        'details': details,
                        'backstage_level': level_name,  # Preserve actual Backstage level
                        'track_name': track_name  # Preserve track name
                    }
                    all_checks.append(converted_check)
        
        print(f"    Total checks extracted from GraphQL: {len(all_checks)}")
        
        # Process checks using existing analysis logic
        category_data = {}
        unmapped_checks = []
        for check in all_checks:
            check_id = check['checkId']
            state = check['state']
            
            # For GraphQL data, allow non-rollup checks since they're track-level aggregates
            category = map_check_to_category(check_id, allow_non_rollups=True)
            if category and category in categories:
                if category not in category_data:
                    category_data[category] = {
                        'checks': [],
                        'current_level': None,
                        'improvement_needed': False
                    }
                
                category_data[category]['checks'].append(check)
            else:
                unmapped_checks.append(f"{check_id} -> {category or 'NO_CATEGORY'}")
        
        # DEBUG: Show unmapped checks to identify missing patterns
        if unmapped_checks:
            print(f"    DEBUG GraphQL: {len(unmapped_checks)} unmapped checks:")
            for check in unmapped_checks[:10]:  # Show first 10
                print(f"      {check}")
            if len(unmapped_checks) > 10:
                print(f"      ... and {len(unmapped_checks)-10} more")
                
        # Analyze compliance levels for each category using existing logic
        all_checks_for_analysis = []
        for data in category_data.values():
            all_checks_for_analysis.extend(data['checks'])
            
        for category, data in category_data.items():
            if category in ['Ownership', 'Quality', 'Security', 'Reliability']:
                # These categories need to see all checks for comprehensive analysis
                # (checks may be categorized under different tracks in Backstage)
                compliance_analysis = analyze_compliance_levels(all_checks_for_analysis, category)
            else:
                compliance_analysis = analyze_compliance_levels(data['checks'], category)
                
            if compliance_analysis and compliance_analysis['improvement_needed']:
                print(f"    {category}: Currently at {compliance_analysis['current_level']}, improvement opportunities available")
                health_categories[category] = compliance_analysis
            else:
                print(f"    {category}: At maximum compliance level - no improvement needed")
        
        # Remove categories with no failures
        health_categories = {k: v for k, v in health_categories.items() if v}
        
        if health_categories:
            print(f"    Will create tickets for {len(health_categories)} categories with compliance gaps")
            return health_categories
        else:
            print(f"    No compliance gaps found for {team_name}")
            return None
            
    except Exception as e:
        print(f"    Error parsing GraphQL certifications: {e}")
        return None

def parse_soundcheck_results(data, categories, team_name):
    """Parse actual soundcheck results to determine real compliance levels."""
    try:
        print(f"    Analyzing actual scorecard compliance for {team_name}...")
        
        # Initialize categories with no failed checks
        health_categories = {}
        for category in categories:
            health_categories[category] = {}
        
        results = data.get('results', [])
        if not results:
            print(f"    No check results found for {team_name}")
            return None
            
        print(f"    Found {len(results)} checks to analyze")
        
        # Analyze each check result to build comprehensive compliance picture
        category_data = {}
        unmapped_checks = []
        for result in results:
            check_id = result.get('checkId', '')
            state = result.get('state', '')
            
            category = map_check_to_category(check_id)
            if category and category in categories:
                if category not in category_data:
                    category_data[category] = {
                        'checks': [],
                        'current_level': None,
                        'improvement_needed': False
                    }
                
                category_data[category]['checks'].append({
                    'checkId': check_id,
                    'state': state,
                    'details': result.get('details', {})
                })
            else:
                unmapped_checks.append(f"{check_id} -> {category or 'NO_CATEGORY'}")
        
        # DEBUG: Show unmapped checks to identify missing patterns
        if unmapped_checks:
            print(f"    DEBUG: {len(unmapped_checks)} unmapped checks:")
            for check in unmapped_checks[:10]:  # Show first 10
                print(f"      {check}")
            if len(unmapped_checks) > 10:
                print(f"      ... and {len(unmapped_checks)-10} more")
        
        # Analyze compliance levels for each category
        # Some categories need access to all checks since their requirements reference checks from other categories
        all_checks = []
        for data in category_data.values():
            all_checks.extend(data['checks'])
            
        for category, data in category_data.items():
            if category in ['Ownership', 'Quality', 'Security', 'Reliability']:
                # These categories need to see all checks for comprehensive analysis
                # (checks may be categorized under different tracks in Backstage)
                compliance_analysis = analyze_compliance_levels(all_checks, category)
            else:
                compliance_analysis = analyze_compliance_levels(data['checks'], category)
                
            if compliance_analysis and compliance_analysis['improvement_needed']:
                print(f"    {category}: Currently at {compliance_analysis['current_level']}, improvement opportunities available")
                health_categories[category] = compliance_analysis
            else:
                print(f"    {category}: At maximum compliance level - no improvement needed")
        
        # Remove categories with no failures
        health_categories = {k: v for k, v in health_categories.items() if v}
        
        if health_categories:
            print(f"    Will create tickets for {len(health_categories)} categories with compliance gaps")
        else:
            print(f"    No compliance gaps found - no tickets will be created for {team_name}")
            
        return health_categories if health_categories else None
        
    except Exception as e:
        print(f"    Error parsing soundcheck results: {e}")
        return None

def map_check_to_category(check_id, allow_non_rollups=False):
    """Map a Backstage check ID to a scorecard category using configurable patterns."""
    check_id_lower = check_id.lower()
    
    # For REST API, only consider rollup checks for scorecard categories as these represent
    # the team's overall performance, not individual entity-level issues
    # For GraphQL API, allow track-level checks without .rollups suffix
    if not allow_non_rollups and not check_id_lower.endswith('.rollups'):
        return None  # Skip individual entity checks - they're not scorecard-level
    
    # Define category mapping patterns - easily extensible for new categories
    # Order matters: Quality patterns checked before Security to catch production bug SLA
    category_patterns = {
        'Ownership': ['ownership'],
        'Quality': [
            # Code quality
            'sonar', 'coverage', 'test', 'quality', 'code',
            # Test types (case-insensitive matching)
            'bluecumber', 'cucumber', 'e2e', 'wdio', 'integration', 
            # Production bugs and SLA
            'prodbug', 'prod', 'bug', 'sev1', 'sev2', 'sev3', 'sev4', 'a11y',
            # Test pass rates
            'pass', 'rate', 'passing', 'itpass', 'percentage'
        ],
        'Security': ['security', 'vuln', 'cve', 'auth', 'api-key', 'mend', 'challenge'],
        'Reliability': ['deployment', 'monitor', 'uptime', 'reliability', 'pager', 'datadog', 'sla']
    }
    
    # Check for specific Quality check patterns we discovered in GraphQL
    quality_specific_checks = [
        'zerosev1sev2prodbugs', 'zerosev1prodbugs', 'prodbuginslaover80percentage',
        'itpassrateover95percent', 'e2ebluecumberpassrateover', 'e2ewdiopassrateover',
        'bluecumberpassrate', 'wdiopassrate', 'itpassrate'
    ]
    
    if any(specific in check_id_lower for specific in quality_specific_checks):
        return 'Quality'
    
    # Check for specific Security check patterns
    security_specific_checks = [
        'challengetimelessthanthandoubleplus', 'challengetime'
    ]
    
    if any(specific in check_id_lower for specific in security_specific_checks):
        return 'Security'
    
    # Check each category's patterns
    for category, patterns in category_patterns.items():
        if any(term in check_id_lower for term in patterns):
            return category
    
    # For rollup checks without clear category mapping, try to infer from name
    if 'check' in check_id_lower:
        # Default rollup checks to Quality category if no specific mapping found
        return 'Quality'
    
    return None  # Skip checks that don't fit scorecard categories

def analyze_compliance_levels(checks, category):
    """Analyze compliance levels from rollup checks to determine current status and improvement opportunities."""
    try:
        # Determine current compliance level and opportunities based on category-specific logic
        return analyze_category_compliance(checks, category)
        
    except Exception as e:
        print(f"    Error analyzing compliance levels: {e}")
        return None

def analyze_category_compliance(checks, category):
    """Analyze compliance levels for specific categories based on business rules."""
    
    if category == 'Ownership':
        return analyze_ownership_compliance(checks)
    elif category == 'Quality':
        return analyze_quality_compliance(checks)
    elif category == 'Security':
        return analyze_security_compliance(checks)
    elif category == 'Reliability':
        return analyze_reliability_compliance(checks)
    else:
        # Generic analysis for unknown categories
        return analyze_generic_compliance(checks, category)

def analyze_ownership_compliance(checks):
    """Analyze Ownership compliance levels based on actual check results."""
    
    # Find all failing ownership-related checks
    failing_checks = []
    for check in checks:
        # Use actual track_name from GraphQL data to determine category
        track_name = check.get('track_name', '')
        
        # Only include checks that are actually from the Ownership track
        if track_name == 'Ownership':
            if check['state'] in ['failed', 'warning']:
                failing_checks.append(check)
    
    # Analyze failing checks using their actual Backstage level information  
    improvement_details = []
    highest_passing_level = 'L3'  # Default to highest for Ownership (L0-L3)
    
    for check in failing_checks:
        check_details = parse_check_details(check.get('details', {}))
        
        # Use actual Backstage level from GraphQL data (not hardcoded logic!)
        backstage_level = check.get('backstage_level', 'Level 1')  # Default fallback
        track_name = check.get('track_name', 'Unknown')
        
        # Convert Backstage level format to our format
        if backstage_level.startswith('Level '):
            level_number = backstage_level.replace('Level ', '')
            if level_number == '0':
                check_level = 'L1'  # Level 0 becomes L1 (basic)
            else:
                check_level = f'L{level_number}'
        else:
            check_level = 'L1'  # Fallback for ownership issues
        
        # Track the lowest level that has issues (determines current compliance level)
        if check_level < highest_passing_level:
            highest_passing_level = check_level
        
        improvement_details.append({
            'level': f'{check_level}-Issue',
            'level_name': convert_check_id_to_readable_name(check['checkId']),
            'threshold': f"{check_details.get('current_count', 0)}/{check_details.get('total_count', 1)} ({check_details.get('percentage', 0):.0f}%)",
            'check_id': check['checkId'],
            'state': check['state'],
            'current_count': check_details.get('current_count', 0),
            'total_count': check_details.get('total_count', 1),
            'needed_count': check_details.get('total_count', 1) - check_details.get('current_count', 0),
            'percentage': check_details.get('percentage', 0),
            'target': check_details.get('target', {}),
            'level_category': check_level,
            'backstage_level': backstage_level,  # Include original level info
            'track_name': track_name
        })
    
    # Determine current level based on failing checks
    if failing_checks:
        # If there are failing checks, current level is one below the lowest failing level
        if highest_passing_level == 'L1':
            current_level = 'NL'  # Not even at L1
        else:
            # Convert back to determine current level
            level_num = int(highest_passing_level[1:]) - 1
            current_level = f'L{level_num}' if level_num > 0 else 'NL'
    else:
        # Check if there are any Ownership checks at all
        has_any_ownership_checks = any(
            check.get('track_name') == 'Ownership'
            for check in checks
        )
        
        if has_any_ownership_checks:
            current_level = 'L3'  # Passing all available ownership checks
        else:
            current_level = 'L1'  # Default ownership level if no checks found
    
    return {
        'current_level': current_level,
        'max_available_level': 'L3',
        'improvement_needed': len(failing_checks) > 0,
        'improvement_details': improvement_details,
        'category': 'Ownership',
        'analysis_type': 'level_based'
    }

def analyze_quality_compliance(checks):
    """Analyze Quality compliance levels based on actual check results."""
    
    # Separate Quality checks into categories for comprehensive analysis
    sonar_coverage_checks = []
    production_bug_checks = []
    test_pass_rate_checks = []
    other_quality_checks = []
    
    for check in checks:
        check_id = check['checkId']
        check_id_lower = check_id.lower()
        track_name = check.get('track_name', '')
        
        # Only process checks from the Quality track
        if track_name != 'Quality':
            continue
        
        # Categorize Quality checks by check ID patterns
        if 'sonarcoverage' in check_id_lower or 'coverage' in check_id_lower:
            sonar_coverage_checks.append(check)
        elif 'prodbug' in check_id_lower or 'prodbugin' in check_id_lower:
            production_bug_checks.append(check)
        elif any(term in check_id_lower for term in ['passrate', 'bluecumber', 'e2e', 'wdio', 'itpass']):
            test_pass_rate_checks.append(check)
        else:
            other_quality_checks.append(check)
    
    # Collect ALL failing/warning Quality checks for improvement opportunities
    failing_checks = []
    all_quality_checks = sonar_coverage_checks + production_bug_checks + test_pass_rate_checks + other_quality_checks
    
    for check in all_quality_checks:
        if check['state'] in ['failed', 'warning']:
            failing_checks.append(check)
    
    # Determine current compliance level (use SonarQube as primary indicator, supplemented by others)
    current_level = 'L1'  # Default level
    if sonar_coverage_checks:
        # Use existing SonarQube level logic as baseline
        sonar_30_passed = any(check['checkId'] == 'sonarCoverageCheckComponent30.rollups' and check['state'] == 'passed' for check in sonar_coverage_checks)
        sonar_50_passed = any(check['checkId'] == 'sonarCoverageCheckComponent50.rollups' and check['state'] == 'passed' for check in sonar_coverage_checks)
        sonar_70_passed = any(check['checkId'] == 'sonarCoverageCheckComponent70.rollups' and check['state'] == 'passed' for check in sonar_coverage_checks)
        sonar_90_passed = any(check['checkId'] == 'sonarCoverageCheckComponent90.rollups' and check['state'] == 'passed' for check in sonar_coverage_checks)
        
        if sonar_90_passed:
            current_level = 'L4'
        elif sonar_70_passed:
            current_level = 'L3'
        elif sonar_50_passed:
            current_level = 'L2'
        elif sonar_30_passed:
            current_level = 'L1'
    
    # Build comprehensive improvement details for ALL failing Quality checks
    improvement_details = []
    
    # Add SonarQube coverage issues (existing working logic)
    for check in sonar_coverage_checks:
        if check['state'] in ['failed', 'warning']:
            check_details = parse_check_details(check.get('details', {}))
            
            # Determine level based on SonarQube check type
            check_level = 'L1'
            if '50' in check['checkId']:
                check_level = 'L2'
            elif '70' in check['checkId']:
                check_level = 'L3'
            elif '90' in check['checkId']:
                check_level = 'L4'
            
            improvement_details.append({
                'level': f'{check_level}-Issue',
                'level_name': convert_check_id_to_readable_name(check['checkId']),
                'threshold': f"{check_details.get('current_count', 0)}/{check_details.get('total_count', 1)} ({check_details.get('percentage', 0):.0f}%)",
                'check_id': check['checkId'],
                'state': check['state'],
                'current_count': check_details.get('current_count', 0),
                'total_count': check_details.get('total_count', 1),
                'needed_count': check_details.get('total_count', 1) - check_details.get('current_count', 0),
                'percentage': check_details.get('percentage', 0),
                'target': check_details.get('target', {}),
                'level_category': check_level
            })
    
    # Add ALL failing Quality checks using their actual Backstage level information
    all_failing_quality_checks = production_bug_checks + test_pass_rate_checks + other_quality_checks
    
    for check in all_failing_quality_checks:
        if check['state'] in ['failed', 'warning']:
            check_details = parse_check_details(check.get('details', {}))
            percentage = check_details.get('percentage', 0)
            
            # Use actual Backstage level from GraphQL data (not hardcoded logic!)
            backstage_level = check.get('backstage_level', 'Level 1')  # Default fallback
            track_name = check.get('track_name', 'Unknown')
            
            # Convert Backstage level format to our format
            if backstage_level.startswith('Level '):
                level_number = backstage_level.replace('Level ', '')
                if level_number == '0':
                    check_level = 'L1'  # Level 0 becomes L1 (basic)
                else:
                    check_level = f'L{level_number}'
            else:
                check_level = 'L2'  # Fallback
            
            # Determine appropriate target percentage based on check type
            target_percentage = 80  # Default
            if 'over85' in check['checkId'].lower():
                target_percentage = 85
            elif 'over95' in check['checkId'].lower():
                target_percentage = 95
            elif 'over99' in check['checkId'].lower():
                target_percentage = 99
            elif 'over100' in check['checkId'].lower() or '100percentage' in check['checkId'].lower():
                target_percentage = 100
            
            improvement_details.append({
                'level': f'{check_level}-Issue',
                'level_name': convert_check_id_to_readable_name(check['checkId']),
                'threshold': f"{percentage:.0f}% (target: {target_percentage}%+)" if target_percentage != 80 else f"{percentage:.0f}%",
                'check_id': check['checkId'],
                'state': check['state'],
                'current_count': check_details.get('current_count', 0),
                'total_count': check_details.get('total_count', 1),
                'needed_count': check_details.get('total_count', 1) - check_details.get('current_count', 0),
                'percentage': percentage,
                'target': {'lower': target_percentage, 'upper': 100},
                'level_category': check_level,
                'backstage_level': backstage_level,  # Include original level info
                'track_name': track_name
            })
    
    # Determine max available level and improvement needed
    max_available_level = 'L4'
    improvement_needed = len(improvement_details) > 0
    
    return {
        'current_level': current_level,
        'max_available_level': max_available_level,
        'improvement_needed': improvement_needed,
        'improvement_details': improvement_details,
        'category': 'Quality',
        'analysis_type': 'level_based'
    }

def analyze_security_compliance(checks):
    """Analyze Security compliance levels based on actual check results."""
    
    # Note: Security SLA requirements are not yet fully implemented in Backstage
    # Expected requirements (for future implementation):
    # L1: 80% of the Security Issue backlog is within SLA end date
    # L2: 80% of the Security Issue backlog is within original SLA end date  
    # L3: Challenge Time < 2x original SLA + No outstanding Low+ Issues + 100% within original SLA
    
    # Look for any actual security-related checks that are failing
    failing_checks = []
    
    # Find any Security checks that are actually failing
    for check in checks:
        track_name = check.get('track_name', '')
        
        # Only include checks from the Security track
        if track_name == 'Security':
            if check['state'] in ['failed', 'warning']:
                failing_checks.append(check)
    
    # Analyze failing checks using their actual Backstage level information  
    improvement_details = []
    highest_passing_level = 'L3'  # Default to highest for Security (L0-L3)
    
    for check in failing_checks:
        check_details = parse_check_details(check.get('details', {}))
        
        # Use actual Backstage level from GraphQL data (not hardcoded logic!)
        backstage_level = check.get('backstage_level', 'Level 1')  # Default fallback
        track_name = check.get('track_name', 'Unknown')
        
        # Convert Backstage level format to our format
        if backstage_level.startswith('Level '):
            level_number = backstage_level.replace('Level ', '')
            if level_number == '0':
                check_level = 'L1'  # Level 0 becomes L1 (basic)
            else:
                check_level = f'L{level_number}'
        else:
            check_level = 'L1'  # Fallback for security issues
        
        # Track the lowest level that has issues (determines current compliance level)
        if check_level < highest_passing_level:
            highest_passing_level = check_level
        
        improvement_details.append({
            'level': f'{check_level}-Issue',
            'level_name': convert_check_id_to_readable_name(check['checkId']),
            'threshold': f"{check_details.get('current_count', 0)}/{check_details.get('total_count', 1)} ({check_details.get('percentage', 0):.0f}%)",
            'check_id': check['checkId'],
            'state': check['state'],
            'current_count': check_details.get('current_count', 0),
            'total_count': check_details.get('total_count', 1),
            'needed_count': check_details.get('total_count', 1) - check_details.get('current_count', 0),
            'percentage': check_details.get('percentage', 0),
            'target': check_details.get('target', {}),
            'level_category': check_level,
            'backstage_level': backstage_level,  # Include original level info
            'track_name': track_name
        })
    
    # Determine current level based on failing checks
    if failing_checks:
        # If there are failing checks, current level is one below the lowest failing level
        if highest_passing_level == 'L1':
            current_level = 'NL'  # Not even at L1
        else:
            # Convert back to determine current level
            level_num = int(highest_passing_level[1:]) - 1
            current_level = f'L{level_num}' if level_num > 0 else 'NL'
    else:
        # Check if there are any Security checks at all
        has_any_security_checks = any(
            check.get('track_name') == 'Security'
            for check in checks
        )
        
        if has_any_security_checks:
            current_level = 'L3'  # Passing all available security checks
        else:
            current_level = 'L3'  # No security checks found - assume compliant
    
    return {
        'current_level': current_level,
        'max_available_level': 'L3',
        'improvement_needed': len(failing_checks) > 0,
        'improvement_details': improvement_details,
        'category': 'Security',
        'analysis_type': 'level_based'
    }

def analyze_reliability_compliance(checks):
    """Analyze Reliability compliance levels based on actual check results."""
    
    # Find all failing reliability-related checks
    failing_checks = []
    for check in checks:
        track_name = check.get('track_name', '')
        
        # Only include checks from the Reliability track
        if track_name == 'Reliability':
            if check['state'] in ['failed', 'warning']:
                failing_checks.append(check)
    
    # Analyze failing checks using their actual Backstage level information  
    improvement_details = []
    highest_passing_level = 'L3'  # Default to highest for Reliability (L1-L3)
    
    for check in failing_checks:
        check_details = parse_check_details(check.get('details', {}))
        
        # Use actual Backstage level from GraphQL data (not hardcoded logic!)
        backstage_level = check.get('backstage_level', 'Level 1')  # Default fallback
        track_name = check.get('track_name', 'Unknown')
        
        # Convert Backstage level format to our format
        if backstage_level.startswith('Level '):
            level_number = backstage_level.replace('Level ', '')
            if level_number == '0':
                check_level = 'L1'  # Level 0 becomes L1 (basic)
            else:
                check_level = f'L{level_number}'
        else:
            check_level = 'L1'  # Fallback for reliability issues
        
        # Track the lowest level that has issues (determines current compliance level)
        if check_level < highest_passing_level:
            highest_passing_level = check_level
        
        improvement_details.append({
            'level': f'{check_level}-Issue',
            'level_name': convert_check_id_to_readable_name(check['checkId']),
            'threshold': f"{check_details.get('current_count', 0)}/{check_details.get('total_count', 1)} ({check_details.get('percentage', 0):.0f}%)",
            'check_id': check['checkId'],
            'state': check['state'],
            'current_count': check_details.get('current_count', 0),
            'total_count': check_details.get('total_count', 1),
            'needed_count': check_details.get('total_count', 1) - check_details.get('current_count', 0),
            'percentage': check_details.get('percentage', 0),
            'target': check_details.get('target', {}),
            'level_category': check_level,
            'backstage_level': backstage_level,  # Include original level info
            'track_name': track_name
        })
    
    # Determine current level based on failing checks
    if failing_checks:
        # If there are failing checks, current level is one below the lowest failing level
        if highest_passing_level == 'L1':
            current_level = 'NL'  # Not even at L1
        else:
            # Convert back to determine current level
            level_num = int(highest_passing_level[1:]) - 1
            current_level = f'L{level_num}' if level_num > 0 else 'NL'
    else:
        # Check if there are any Reliability checks at all
        has_any_reliability_checks = any(
            check.get('track_name') == 'Reliability'
            for check in checks
        )
        
        if has_any_reliability_checks:
            current_level = 'L3'  # Passing all available reliability checks
        else:
            current_level = 'L3'  # No reliability checks found - assume compliant
    
    return {
        'current_level': current_level,
        'max_available_level': 'L3',
        'improvement_needed': len(failing_checks) > 0,
        'improvement_details': improvement_details,
        'category': 'Reliability',
        'analysis_type': 'level_based'
    }

def create_level_opportunities(category, current_level, opportunity_levels):
    """Create improvement opportunities for specific levels."""
    improvement_details = []
    
    for level in opportunity_levels:
        improvement_details.append({
            'level': level,
            'level_name': f'{category} {level}',
            'threshold': level,
            'current_count': 0 if current_level == 'NL' else int(level[1:]) - 1,
            'total_count': int(level[1:]),
            'needed_count': int(level[1:]) if current_level == 'NL' else 1,
            'percentage': 0 if current_level == 'NL' else ((int(level[1:]) - 1) / int(level[1:])) * 100,
            'description': f'Achieve {category} {level} compliance requirements'
        })
    
    return {
        'current_level': current_level,
        'max_available_level': opportunity_levels[-1] if opportunity_levels else 'L4',
        'improvement_needed': True,
        'improvement_details': improvement_details,
        'category': category,
        'analysis_type': 'level_based'
    }

def analyze_generic_compliance(checks, category):
    """Generic compliance analysis for unknown categories."""
    failing_checks = [c for c in checks if c['state'] in ['failed', 'warning']]
    
    if failing_checks:
        improvement_details = []
        for i, check in enumerate(failing_checks):
            check_details = parse_check_details(check.get('details', {}))
            improvement_details.append({
                'level': f'Issue-{i+1}',
                'level_name': convert_check_id_to_readable_name(check['checkId']),
                'threshold': f"{check_details.get('current_count', 0)}/{check_details.get('total_count', 1)} ({check_details.get('percentage', 0):.0f}%)",
                'check_id': check['checkId'],
                'state': check['state'],
                'current_count': check_details.get('current_count', 0),
                'total_count': check_details.get('total_count', 1),
                'needed_count': check_details.get('total_count', 1) - check_details.get('current_count', 0),
                'percentage': check_details.get('percentage', 0),
                'target': check_details.get('target', {})
            })
        
        return {
            'current_level': 'Has Issues',
            'max_available_level': f'{len(failing_checks)} Issues Found',
            'improvement_needed': True,
            'improvement_details': improvement_details,
            'category': category,
            'analysis_type': 'specific_failures'
        }
    
    return None

def convert_check_id_to_readable_name(check_id):
    """Convert a Backstage check ID to a human-friendly name."""
    # Remove .rollups suffix if present
    name = check_id.replace('.rollups', '')
    
    # Handle specific known check patterns
    check_patterns = {
        'defaultMonitorPagerdutyEnabledCheck': 'PagerDuty Default Monitors Enabled',
        'defaultMonitorPagingPriorityCheck': 'PagerDuty Default Monitor Paging Priority',
        'datadogIntegrationCheck': 'Datadog Integration',
        'datadogAPMInstrumentationCheck': 'Datadog APM Instrumentation',
        'deploymentDriftCheck': 'Deployment Drift Check',
        'lastDeploymentCheck': 'Recent Deployment Check',
        'pagerDutyIntegrationCheck': 'PagerDuty Integration',
        'outOfDateDeploymentsCheck': 'Out of Date Deployments',
        'deploymentAZResilientCheck': 'Deployment AZ Resilience',
        'sonarCoverageCheckComponent30': 'SonarQube Code Coverage (30%)',
        'sonarCoverageCheckComponent50': 'SonarQube Code Coverage (50%)',
        'sonarCoverageCheckComponent70': 'SonarQube Code Coverage (70%)',
        'sonarCoverageCheckComponent90': 'SonarQube Code Coverage (90%)',
        'prodBugInSlaOver80Percentage': 'Production Bug SLA > 80%',
        'prodBugInSlaOver90Percentage': 'Production Bug SLA > 90%',
        'prodBugInSlaOver100Percentage': 'Production Bug SLA = 100%',
        'eightyPercentWithinOriginalSlaCheck': '80% Within Original SLA',
        'oneHundredPercentWithinOriginalSlaCheck': '100% Within Original SLA',
        'moreThan80WithinSlaCheck': 'More Than 80% Within SLA',
        'noSlaMissLowPlusCheck': 'No SLA Miss (Low+ Priority)',
        'noSlaMissMediumPlusCheck': 'No SLA Miss (Medium+ Priority)',
        'noSlaMissUrgentPlusCheck': 'No SLA Miss (Urgent+ Priority)',
        'challengeTimeLessThanDoubleSlaExternalCheck': 'Challenge Time < 2x SLA (External)',
        'challengeTimeLessThanDoubleSlaHighPlusCheck': 'Challenge Time < 2x SLA (High+)',
        'challengeTimeLessThanDoubleSlaLowPlusCheck': 'Challenge Time < 2x SLA (Low+)',
        'preventableRcaUnder30': 'Preventable RCA Under 30%'
    }
    
    # Check for exact match first
    if name in check_patterns:
        return check_patterns[name]
    
    # Try partial matches for dynamic check names
    for pattern, readable in check_patterns.items():
        if pattern in name:
            return readable
    
    # Generic transformation if no specific pattern found
    # Convert camelCase to Title Case and add spaces
    import re
    
    # Handle camelCase to Title Case
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    
    # Handle consecutive capitals
    name = re.sub(r'([A-Z]+)([A-Z][a-z])', r'\1 \2', name)
    
    # Remove common suffixes
    name = re.sub(r'\s*Check$', '', name, flags=re.IGNORECASE)
    
    # Title case
    name = name.title()
    
    # Fix common abbreviations
    name = re.sub(r'\bSla\b', 'SLA', name)
    name = re.sub(r'\bApi\b', 'API', name)
    name = re.sub(r'\bRca\b', 'RCA', name)
    name = re.sub(r'\bAz\b', 'AZ', name)
    name = re.sub(r'\bApm\b', 'APM', name)
    
    return name

def extract_level_from_check_id(check_id):
    """Extract compliance level information from check ID."""
    import re
    
    # Map coverage percentages to levels
    coverage_to_level = {
        '30': {'level': 'L1', 'name': 'Basic Coverage', 'threshold': '30%'},
        '50': {'level': 'L2', 'name': 'Moderate Coverage', 'threshold': '50%'},
        '70': {'level': 'L3', 'name': 'Good Coverage', 'threshold': '70%'},
        '90': {'level': 'L4', 'name': 'Excellent Coverage', 'threshold': '90%'}
    }
    
    # Look for coverage percentage patterns (Quality checks)
    coverage_match = re.search(r'coverage.*?(\d{2})', check_id)
    if coverage_match:
        percentage = coverage_match.group(1)
        return coverage_to_level.get(percentage)
    
    # Handle rollup checks that don't have coverage patterns - map to L1 basic level
    if check_id.lower().endswith('.rollups'):
        # Define basic level patterns for different categories
        basic_level_patterns = {
            'ownership': 'Ownership',
            'security': 'Security', 
            'vuln': 'Security',
            'cve': 'Security',
            'auth': 'Security',
            'api-key': 'Security',
            'mend': 'Security',
            'deployment': 'Reliability',
            'monitor': 'Reliability',
            'sla': 'Reliability',
            'uptime': 'Reliability',
            'reliability': 'Reliability',
            'pager': 'Reliability',
            'datadog': 'Reliability'
        }
        
        # Check for any basic level pattern matches
        for term, category in basic_level_patterns.items():
            if term in check_id.lower():
                return {'level': 'L1', 'name': f'Basic {category}', 'threshold': 'L1'}
    
    # Add other level mapping patterns as needed
    return None

def parse_check_details(details):
    """Parse check details to extract metrics."""
    try:
        notes = details.get('notes', {})
        data_str = notes.get('data', '{}')
        
        if isinstance(data_str, str):
            import json
            data = json.loads(data_str)
        else:
            data = data_str
            
        value = data.get('value', {})
        target = data.get('target', {})
        
        return {
            'current_count': value.get('count', 0),
            'total_count': value.get('total', 0),
            'percentage': value.get('percentage', 0),
            'target': target
        }
    except Exception:
        return {'current_count': 0, 'total_count': 0, 'percentage': 0, 'target': {}}

def parse_scorecards_response(data, categories):
    """Parse response from dedicated scorecards API."""
    try:
        health_categories = {
            'Ownership': {},
            'Quality': {},
            'Security': {},
            'Reliability': {}
        }
        
        # Handle different response structures
        if 'scorecards' in data:
            scorecards = data['scorecards']
        elif 'results' in data:
            scorecards = data['results']
        elif isinstance(data, list):
            scorecards = data
        else:
            scorecards = [data]
        
        found_data = False
        for scorecard in scorecards:
            if 'checks' in scorecard:
                for check in scorecard['checks']:
                    category = detect_category_from_name(check.get('name', ''))
                    if category and check.get('passed', False):
                        found_data = True
                        levels = extract_levels_from_name(check.get('name', ''))
                        health_categories[category].update(levels)
        
        return health_categories if found_data else None
        
    except Exception as e:
        print(f"    Error parsing scorecards response: {e}")
        return None

def parse_entity_health_data(entity_data, categories):
    """Parse health data from catalog entity response for specified categories."""
    try:
        metadata = entity_data.get('metadata', {})
        annotations = metadata.get('annotations', {})
        spec = entity_data.get('spec', {})
        relations = entity_data.get('relations', [])
        
        # Initialize health categories based on config
        health_categories = {}
        for category in categories:
            health_categories[category] = {}
        
        found_data = False
        
        # Look for scorecard annotations
        for key, value in annotations.items():
            if any(term in key.lower() for term in ['scorecard', 'compliance', 'health', 'soundcheck']):
                found_data = True
                category_data = parse_annotation_health_value(key, value)
                
                # Map to health categories
                for category in health_categories.keys():
                    if category.lower() in key.lower():
                        health_categories[category].update(category_data)
                        break
                else:
                    # If no specific category match, infer from scorecard domain
                    if 'scorecard-domain' in key.lower() and 'Ownership' in health_categories:
                        # Infer health status based on having a scorecard domain
                        health_categories['Ownership']['L1'] = 'X'  # Has team structure
                        found_data = True
        
        # Infer health data from entity structure for all configured categories
        # This ensures we create tickets for all specified categories
        
        # Check for ownership indicators
        if 'Ownership' in health_categories and (spec.get('members') and len(spec.get('members', [])) > 0):
            health_categories['Ownership']['L1'] = 'X'  # Has team members
            found_data = True
        
        # Check for component ownership (quality indicator)
        owned_components = [r for r in relations if r.get('type') == 'ownerOf' and 'component:' in r.get('targetRef', '')]
        if 'Quality' in health_categories and owned_components:
            health_categories['Quality']['L1'] = 'X'  # Owns components
            found_data = True
        
        # Check for contact information (reliability indicator)
        if 'Reliability' in health_categories and (spec.get('contacts') and len(spec.get('contacts', [])) > 0):
            health_categories['Reliability']['L1'] = 'X'  # Has contact info
            found_data = True
        
        # For Security, infer from team structure or add default level
        if 'Security' in health_categories and spec.get('members'):
            health_categories['Security']['L1'] = 'X'  # Has team for security responsibilities
            found_data = True
        
        # If no data found but categories are configured, create default L1 entries for all categories
        # This ensures tickets are created for all requested categories
        if not found_data and categories:
            for category in categories:
                health_categories[category]['L1'] = 'X'  # Default compliance level
            found_data = True
            print("    No specific health indicators found, creating default L1 entries for all categories")
        
        return health_categories if found_data else None
        
    except Exception as e:
        print(f"    Error parsing entity health data: {e}")
        return None

def parse_alternative_response(data, team_name, categories):
    """Parse response from alternative scorecard APIs."""
    try:
        if 'entities' in data:
            for entity in data['entities']:
                if entity.get('metadata', {}).get('name', '').lower() == team_name.lower():
                    return parse_entity_health_data(entity, categories)
        
        return None
        
    except Exception as e:
        print(f"    Error parsing alternative response: {e}")
        return None

def detect_category_from_name(name):
    """Detect health category from check/annotation name."""
    name_lower = name.lower()
    
    if any(term in name_lower for term in ['owner', 'ownership', 'responsible', 'maintainer']):
        return 'Ownership'
    elif any(term in name_lower for term in ['quality', 'code', 'test', 'coverage', 'sonar']):
        return 'Quality'
    elif any(term in name_lower for term in ['security', 'vuln', 'cve', 'scan', 'auth']):
        return 'Security'
    elif any(term in name_lower for term in ['reliability', 'uptime', 'sla', 'monitor', 'alert']):
        return 'Reliability'
    
    return None

def extract_levels_from_name(name):
    """Extract compliance levels from check name."""
    levels = {}
    
    # Look for level indicators like L1, L2, L3, etc.
    import re
    level_matches = re.findall(r'[lL](\d+)', name)
    if level_matches:
        for level_num in level_matches:
            levels[f'L{level_num}'] = 'X'
    else:
        # Default to L1 if no specific level found
        levels['L1'] = 'X'
    
    return levels

def parse_annotation_health_value(key, value):
    """Parse scorecard annotation value to extract compliance levels."""
    try:
        levels = {}
        
        # Try to parse as JSON first
        if isinstance(value, str) and (value.startswith('{') or value.startswith('[')):
            try:
                parsed_value = json.loads(value)
                if isinstance(parsed_value, dict):
                    for level_key, level_value in parsed_value.items():
                        if level_value and str(level_value).lower() not in ['false', 'no', '0', 'none']:
                            levels[level_key] = 'X'
                elif isinstance(parsed_value, list):
                    for i, level in enumerate(parsed_value):
                        if level:
                            levels[f'L{i+1}'] = 'X'
            except json.JSONDecodeError:
                pass
        
        # Handle simple string values
        if not levels:
            if str(value).lower() in ['true', 'yes', 'x', '1', 'pass', 'passing']:
                levels.update(extract_levels_from_name(key))
        
        return levels
        
    except Exception as e:
        print(f"    Error parsing annotation value: {e}")
        return {}

def print_health_summary(health_data):
    """Print a summary of extracted health data."""
    for category, levels in health_data.items():
        if levels:
            level_str = ', '.join([f"{k}: {v}" for k, v in levels.items()])
            print(f"    {category}: {level_str}")

def extract_scorecard_data_from_entity(entity_data, team_name):
    """Extract scorecard categories from Backstage entity data (enhanced version)."""
    if not entity_data:
        return {}
        
    try:
        # Check if this is enhanced format first
        metadata = entity_data.get('metadata', {})
        annotations = metadata.get('annotations', {})
        
        # Handle enhanced format
        if 'enhanced-scorecard-data' in annotations:
            try:
                enhanced_data = json.loads(annotations['enhanced-scorecard-data'])
                print(f"{Fore.CYAN}  Using enhanced scorecard data for {team_name}...{Style.RESET_ALL}")
                return enhanced_data
            except json.JSONDecodeError:
                print("    Warning: Could not parse enhanced scorecard data")
        
        # Fall back to original parsing logic
        print(f"{Fore.CYAN}  Processing scorecard data for {team_name}...{Style.RESET_ALL}")
        
        # Initialize category structure
        categories = {
            'Ownership': {},
            'Quality': {},
            'Security': {},
            'Reliability': {}
        }
        
        # Look for scorecard-related annotations
        scorecard_found = False
        for key, value in annotations.items():
            if 'scorecard' in key.lower():
                scorecard_found = True
                print(f"    Found scorecard annotation: {key}")
                
                # Parse scorecard data based on key name
                if 'ownership' in key.lower():
                    categories['Ownership'] = parse_scorecard_levels(value)
                elif 'quality' in key.lower():
                    categories['Quality'] = parse_scorecard_levels(value)
                elif 'security' in key.lower():
                    categories['Security'] = parse_scorecard_levels(value)
                elif 'reliability' in key.lower():
                    categories['Reliability'] = parse_scorecard_levels(value)
        
        if not scorecard_found:
            print(f"    No scorecard annotations found for {team_name}")
        
        return categories
        
    except Exception as e:
        print(f"{Fore.RED}Error extracting scorecard data for {team_name}: {e}{Style.RESET_ALL}")
        return {}

def parse_scorecard_levels(scorecard_value):
    """Parse scorecard level data from Backstage annotation value."""
    try:
        # If it's a JSON string, parse it
        if isinstance(scorecard_value, str):
            if scorecard_value.startswith('{') or scorecard_value.startswith('['):
                scorecard_data = json.loads(scorecard_value)
            else:
                # Simple string value - treat as a single level
                return {'L1': 'X'} if scorecard_value.lower() in ['true', 'yes', 'x', '1'] else {}
        else:
            scorecard_data = scorecard_value
        
        # Convert scorecard data to category format expected by ticket creation
        levels = {}
        
        if isinstance(scorecard_data, dict):
            # Handle different scorecard data structures
            for level_key, level_value in scorecard_data.items():
                if level_value and str(level_value).lower() not in ['false', 'no', '0', 'none']:
                    levels[level_key] = 'X'
        elif isinstance(scorecard_data, list):
            # Handle list of levels
            for i, level in enumerate(scorecard_data):
                if level:
                    levels[f'L{i+1}'] = 'X'
        
        return levels
        
    except Exception as e:
        print(f"    Warning: Could not parse scorecard value: {e}")
        return {}

def get_team_categories_from_backstage(backstage_url, team_name, categories=None):
    """Get all category data for a team from Backstage using enhanced extraction."""
    # Use the enhanced health extraction directly
    health_data = get_team_health_enhanced(backstage_url, team_name, categories)
    
    if not health_data:
        return None
        
    # Filter out empty categories
    filtered_categories = {}
    for category_name, category_data in health_data.items():
        if category_data:  # Only keep categories with data
            filtered_categories[category_name] = category_data
    
    return filtered_categories if filtered_categories else None

def validate_file(file_path):
    """Validate that the file exists and is an Excel file."""
    if not os.path.exists(file_path):
        print(f"{Fore.RED}Error: File not found: {file_path}{Style.RESET_ALL}")
        return False
    
    if not file_path.lower().endswith(('.xlsx', '.xls', '.xlsm')):
        print(f"{Fore.RED}Error: File is not an Excel file: {file_path}{Style.RESET_ALL}")
        return False
    
    return True

def filter_excel_columns(df):
    """Filter out columns labeled 'ColumnX'."""
    columns_to_keep = [col for col in df.columns if not col.startswith('Column')]
    return df[columns_to_keep]

def transform_to_key_value_format(df):
    """Transform dataframe to use first column as keys with subsequent columns as values."""
    if len(df.columns) < 2:
        print(f"{Fore.YELLOW}Warning: Excel file does not have enough columns for transformation. Returning None.{Style.RESET_ALL}")
        return None
    
    first_col_name = df.columns[0]
    result_df = pd.DataFrame()
    
    # Process each unique key value
    for key_value in df[first_col_name].unique():
        key_rows = df[df[first_col_name] == key_value]
        result_df = process_key_rows(result_df, key_rows, key_value, df.columns[1:])
    
    # print(f"Transformed data from {len(df)} rows to {len(result_df)} key-value pairs")
    
    # Check if the transformation resulted in an empty DataFrame or missing required columns
    if result_df.empty:
        print(f"{Fore.YELLOW}Warning: No usable data found in sheet after transformation.{Style.RESET_ALL}")
        return None
    
    # Ensure required columns exist
    if not all(col in result_df.columns for col in ["Key", "Field", "Value"]):
        print(f"{Fore.YELLOW}Warning: Transformed data does not have the required column structure.{Style.RESET_ALL}")
        return None
        
    return result_df

def process_key_rows(result_df, key_rows, key_value, columns):
    """Process rows for a specific key value and columns."""
    new_rows = []
    
    for col in columns:
        for _, row in key_rows.iterrows():
            # Skip empty values
            if pd.notna(row[col]) and str(row[col]).strip() != '':
                new_row = {
                    'Key': key_value,
                    'Field': col,
                    'Value': row[col]
                }
                new_rows.append(new_row)
    
    if new_rows:
        return pd.concat([result_df, pd.DataFrame(new_rows)], ignore_index=True)
    return result_df

def read_excel_file(file_path, sheet_name):
    """Read the Excel file into a pandas DataFrame.
    
    Args:
        file_path (str): Path to the Excel file
        sheet_name (str): Name of the sheet to read
        
    Returns:
        DataFrame: Transformed dataframe with key-value pairs
    """
    try:
        # Read the Excel file
        df = pd.read_excel(file_path, sheet_name=sheet_name)
        
        # Check if the sheet is empty
        if df.empty:
            print(f"{Fore.YELLOW}Warning: Sheet '{sheet_name}' is empty.{Style.RESET_ALL}")
            return None
            
        # Filter and transform the data
        df = filter_excel_columns(df)
        df = transform_to_key_value_format(df)
        return df
        
    except Exception as e:
        print(f"{Fore.RED}Error reading Excel file '{sheet_name}': {str(e)}{Style.RESET_ALL}")
        return None

def validate_data(df):
    """Validate that the dataframe has the required columns for the transformed data."""
    required_columns = ["Key", "Field", "Value"]
    
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        print(f"{Fore.RED}Error: Missing required columns: {', '.join(missing_columns)}{Style.RESET_ALL}")
        print(f"Available columns: {', '.join(df.columns)}")
        return False
    
    return True

def assign_ticket(jira_client, issue_key, assignee_name):
    """Set the assignee for a ticket using a separate API request.
    
    Args:
        jira_client: The Jira client instance
        issue_key: The key of the issue to update (e.g., PRJ-123)
        assignee_name: The username of the assignee to set
        
    Returns:
        bool: True if successful, False if failed
    """
    if not assignee_name or str(assignee_name).lower() == 'nan':
        print(f"{Fore.YELLOW}Skipping assignee update - no assignee specified{Style.RESET_ALL}")
        return False
        
    try:
        print(f"{Fore.CYAN}Setting assignee for {issue_key} to '{assignee_name}' with separate API request{Style.RESET_ALL}")
        
        # Use the standard Jira API method to assign the issue
        jira_client.assign_issue(issue_key, assignee_name)
        print(f"{Fore.GREEN}Successfully set assignee for {issue_key} to '{assignee_name}'{Style.RESET_ALL}")
        return True
            
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Could not set assignee for ticket {issue_key}: {str(e)}{Style.RESET_ALL}")
        return False

def link_to_epic(jira_client, issue_key, epic_key):
    """Link an issue to an epic using various methods."""
    if not epic_key or str(epic_key) == 'nan':
        return False
        
    try:
        # Try multiple approaches to link to epic
        # Method 1: Try to update the Epic Link custom field (common in many Jira instances)
        try:
            jira_client.update_issue_field(issue_key, {'customfield_10000': epic_key})
            return True
        except Exception:
            pass
            
        # Method 2: Try using the Epic-Story Link issue link type
        try:
            jira_client.create_issue_link(EPIC_LINK_TYPE, epic_key, issue_key)
            return True
        except Exception:
            pass
            
        # Method 3: Fallback to "Relates to" if other methods fail
        jira_client.create_issue_link('Relates to', epic_key, issue_key)
        return True
            
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Could not link ticket {issue_key} to epic {epic_key}: {str(e)}{Style.RESET_ALL}")
        return False

def validate_required_fields(project_key, issue_type, summary):
    """Validate required fields before creating a ticket."""
    errors = []
    
    if not project_key:
        errors.append("Missing required field: project key")
    
    if not issue_type:
        errors.append("Missing required field: issue type")
    
    if not summary:
        errors.append("Missing required field: summary")
    
    return errors

def prepare_issue_dict(project_key, issue_type, summary, description, fields, custom_fields_mapping=None):
    """Prepare the issue dictionary for Jira API."""
    # Validate required fields
    validation_errors = validate_required_fields(project_key, issue_type, summary)
    if validation_errors:
        print(f"{Fore.YELLOW}Warning: Field validation issues detected:{Style.RESET_ALL}")
        for error in validation_errors:
            print(f"{Fore.YELLOW}- {error}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}This may cause a 400 Bad Request error when submitting to Jira.{Style.RESET_ALL}")
    
    # Create base issue dictionary
    issue_dict = {
        'project': {'key': project_key},
        'summary': summary,
        'description': description,
        'issuetype': {'name': issue_type},
    }
    
    # Extract Epic Link field if present for traditional linking method
    epic_field_name = EPIC_LINK_FIELD
    epic_value = None
    
    # Make a copy of fields to preserve the original
    fields_copy = fields.copy()
    
    # Check if Epic Link field is present - only extract value for possible link creation
    if epic_field_name in fields_copy:
        epic_value = fields_copy[epic_field_name]
        # Do not remove from fields_copy since we want all fields to be processed uniformly
        # including Epic Link through the normal custom fields process
    
    # Process all fields to add to the issue dictionary with uniform handling
    process_fields_for_jira(fields_copy, issue_dict, custom_fields_mapping)
    
    # Return the prepared data
    return (issue_dict, epic_value)

def process_fields_for_jira(fields, issue_dict, custom_fields_mapping=None):
    """Process and filter fields before sending to Jira API.
    
    This function handles three types of fields:
    1. Standard Jira fields (with specific formatting requirements)
    2. Direct custom fields (already have customfield_XXXXX format)
    3. Mapped custom fields (using the custom_fields_mapping parameter)
    
    For mapped custom fields, the formatting depends on the Data Wrapper setting:
    - With wrapper "value": {"value": field_value}
    - With no wrapper (None): field_value directly
    - With custom wrapper: {wrapper: field_value}
    """
    # Define known Jira standard fields that require special handling
    # Note: 'assignee' is deliberately excluded as it's handled separately after ticket creation
    standard_fields = {
        'reporter': 'name',
        'priority': 'name',
        'component': 'name',  # Single component name -> components field
        'components': 'name',  # List of component names
        'labels': None,  # Simple list
        'duedate': None,  # String in format 'YYYY-MM-DD'
        'fixVersions': 'name',  # List of version names
        'versions': 'name',  # List of version names
    }
    
    # Add any additional fields provided
    for field, value in fields.items():
        # Skip empty, NaN values, or special fields not meant for Jira API
        if not value or str(value).lower() == 'nan' or field == 'Project':
            continue

        # Skip assignee field as it's handled separately after ticket creation
        if field.lower() == 'assignee':
            print(f"{Fore.YELLOW}Skipping 'assignee' field during initial ticket creation - will be set afterwards{Style.RESET_ALL}")
            continue        # Handle standard fields with special formatting requirements
        field_lower = field.lower()
        if field_lower in standard_fields:
            format_type = standard_fields[field_lower]
            add_standard_field(issue_dict, field, value, format_type)
        
        # Handle custom fields and other fields (always prefixed with 'customfield_' or contain a dot)
        elif field.startswith('customfield_') or '.' in field:
            issue_dict[field] = value

        # Check if field is in the custom fields mapping, use the mapping to get the actual field ID
        elif custom_fields_mapping and field in custom_fields_mapping:
            mapping = custom_fields_mapping[field]
            custom_field_id = mapping["id"]
            wrapper = mapping.get("wrapper")
            
            # Format based on wrapper setting
            if wrapper is None:
                # No wrapper - use value directly
                issue_dict[custom_field_id] = value
                print(f"{Fore.GREEN}Mapped field '{field}' to custom field '{custom_field_id}' with direct value: {value}{Style.RESET_ALL}")
            else:
                # Use specified wrapper
                wrapped_value = {wrapper: value}
                issue_dict[custom_field_id] = wrapped_value
                print(f"{Fore.GREEN}Mapped field '{field}' to custom field '{custom_field_id}' with wrapper '{wrapper}': {wrapped_value}{Style.RESET_ALL}")

        else:
            # Skip unknown fields to avoid API errors
            print(f"{Fore.YELLOW}Skipping unknown field '{field}' to avoid Jira API errors{Style.RESET_ALL}")

def add_standard_field(issue_dict, field, value, format_type):
    """Format and add a standard field to the issue dictionary."""
    # Map singular 'component' to plural 'components' for Jira API
    api_field = 'components' if field.lower() == 'component' else field
    
    if format_type == 'name':
        if isinstance(value, list):
            # Handle list of values (components, fixVersions, etc.)
            issue_dict[api_field] = [{'name': item} for item in value]
        else:
            # Handle single value - for component, wrap in a list since Jira expects components as array
            if field.lower() == 'component':
                issue_dict[api_field] = [{'name': value}]
            else:
                issue_dict[api_field] = {'name': value}
    else:
        # Fields that don't need special formatting (labels, duedate, etc.)
        issue_dict[api_field] = value

def log_issue_fields(issue_dict):
    """Log the fields being sent to Jira API."""
    import json
    
    print(f"{Fore.CYAN}Sending to Jira API:{Style.RESET_ALL}")
    
    # Log individual fields in a readable format
    for field, value in issue_dict.items():
        if field == 'project':
            print(f"{Fore.CYAN}  project: {value['key']}{Style.RESET_ALL}")
        elif field == 'issuetype':
            print(f"{Fore.CYAN}  issuetype: {value['name']}{Style.RESET_ALL}")
        elif field == 'description' and value:
            # Show more description for better visibility in dry-run mode
            desc_preview = value[:500] + ('...' if len(value) > 500 else '')
            print(f"{Fore.CYAN}  description: {desc_preview}{Style.RESET_ALL}")
        else:
            print(f"{Fore.CYAN}  {field}: {value}{Style.RESET_ALL}")
    
    # Also provide a JSON representation for easy copy-paste
    # Replace complex objects with strings for JSON serialization
    json_dict = {}
    for k, v in issue_dict.items():
        if k == 'project':
            json_dict[k] = {'key': v['key']}
        elif k == 'issuetype':
            json_dict[k] = {'name': v['name']}
        else:
            json_dict[k] = v
    
    print(f"\n{Fore.CYAN}JSON Payload:{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{json.dumps(json_dict, indent=2)}{Style.RESET_ALL}")

def log_request_details(jira_client, issue_dict):
    """Log the Jira API request details including headers and payload."""
    # Get Jira server URL from client for logging
    jira_url = jira_client._options['server']
    print(f"{Fore.CYAN}Jira server URL: {jira_url}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}API endpoint: {jira_url}/rest/api/2/issue{Style.RESET_ALL}")
    
    # Log authentication method
    auth_method = "Personal Access Token" if hasattr(jira_client, "_session") and jira_client._session.auth else "Unknown"
    print(f"{Fore.CYAN}Authentication method: {auth_method}{Style.RESET_ALL}")
    
    # Log request headers (as much as we can access)
    print(f"{Fore.CYAN}Request Headers:{Style.RESET_ALL}")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    
    # Try to extract actual headers if possible
    if hasattr(jira_client, "_session") and hasattr(jira_client._session, "headers"):
        for key, value in jira_client._session.headers.items():
            # Mask token values for security
            if "token" in key.lower() or "auth" in key.lower():
                headers[key] = "********" 
            else:
                headers[key] = value
    
    for header, value in headers.items():
        print(f"{Fore.CYAN}  {header}: {value}{Style.RESET_ALL}")
    
    # Log the complete request payload
    print(f"{Fore.CYAN}Request Payload:{Style.RESET_ALL}")
    request_payload = {
        "fields": issue_dict
    }
    print(f"{Fore.CYAN}{json.dumps(request_payload, indent=2)}{Style.RESET_ALL}")
    
    # Show common field formatting examples for reference
    print(f"{Fore.CYAN}Common Field Formats (for reference):{Style.RESET_ALL}")
    print(f"{Fore.CYAN}  Standard fields: project, summary, description, issuetype{Style.RESET_ALL}")
    print(f"{Fore.CYAN}  Object fields: assignee:{{'name': 'username'}}, priority:{{'name': 'High'}}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}  Custom fields: Use field ID (customfield_XXXXX) for custom fields{Style.RESET_ALL}")
    
    # Identify potential field format issues
    for field, value in issue_dict.items():
        if field.lower() in ['assignee', 'reporter', 'priority'] and not isinstance(value, dict):
            print(f"{Fore.YELLOW}Warning: Field '{field}' should typically be an object with a 'name' property{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}  Current: '{field}': {value}{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}  Expected: '{field}': {{'name': '{value}'}}{Style.RESET_ALL}")

def log_response_headers(response):
    """Log HTTP response headers."""
    if response and hasattr(response, 'headers'):
        print(f"{Fore.RED}Response Headers:{Style.RESET_ALL}")
        for header, value in response.headers.items():
            print(f"{Fore.RED}  {header}: {value}{Style.RESET_ALL}")

def log_request_headers(req):
    """Log HTTP request headers."""
    if hasattr(req, 'headers'):
        print(f"{Fore.RED}Request Headers:{Style.RESET_ALL}")
        for header, value in req.headers.items():
            # Mask sensitive values
            if header.lower() in ('authorization', 'cookie'):
                print(f"{Fore.RED}  {header}: ********{Style.RESET_ALL}")
            else:
                print(f"{Fore.RED}  {header}: {value}{Style.RESET_ALL}")

def log_request_body(req):
    """Log HTTP request body."""
    if hasattr(req, 'body') and req.body:
        print(f"{Fore.RED}Request Body:{Style.RESET_ALL}")
        try:
            # Try to parse as JSON for pretty printing
            body = json.loads(req.body.decode('utf-8'))
            print(f"{Fore.RED}{json.dumps(body, indent=2)}{Style.RESET_ALL}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            # If not JSON or can't decode, just print the body
            print(f"{Fore.RED}Raw body (not JSON): {req.body}{Style.RESET_ALL}")

def log_request_details_from_response(response):
    """Log HTTP request details extracted from the response object."""
    if response and hasattr(response, 'request'):
        req = response.request
        print(f"{Fore.RED}Request Method: {req.method}{Style.RESET_ALL}")
        print(f"{Fore.RED}Request URL: {req.url}{Style.RESET_ALL}")
        
        # Log request headers
        log_request_headers(req)
        
        # Log request body
        log_request_body(req)

def handle_http_error(e):
    """Handle and log HTTP errors from Jira API."""
    print(f"{Fore.RED}HTTP Error during API request: {str(e)}{Style.RESET_ALL}")
    
    # Extract status code and response details
    response = e.response if hasattr(e, 'response') else None
    status_code = response.status_code if response else "Unknown"
    
    print(f"{Fore.RED}Status code: {status_code}{Style.RESET_ALL}")
    
    # Log response headers
    log_response_headers(response)
    
    # Log request details
    log_request_details_from_response(response)
    
    # Handle specific status codes
    if response and status_code == 400:
        handle_bad_request(response)

def handle_bad_request(response):
    """Handle 400 Bad Request errors with detailed logging."""
    print(f"{Fore.RED}Bad Request (400) - Invalid input data{Style.RESET_ALL}")
    try:
        error_data = response.json()
        print(f"{Fore.RED}Error details:{Style.RESET_ALL}")
        print(f"{Fore.RED}{json.dumps(error_data, indent=2)}{Style.RESET_ALL}")
        
        # Check for common error patterns
        if 'errors' in error_data:
            for field, error in error_data['errors'].items():
                print(f"{Fore.RED}Field '{field}': {error}{Style.RESET_ALL}")
                
                # Provide guidance for common error fields
                if field == 'project':
                    print(f"{Fore.YELLOW}TIP: Check that project key '{field}' exists in Jira and is accessible by your user{Style.RESET_ALL}")
                elif field == 'issuetype':
                    print(f"{Fore.YELLOW}TIP: Check that issue type is valid for this project. Valid types may include: Task, Bug, Story, etc.{Style.RESET_ALL}")
                elif 'customfield' in field:
                    print(f"{Fore.YELLOW}TIP: Custom field '{field}' may not be properly configured or may require specific format{Style.RESET_ALL}")
                elif field.lower() in ['assignee', 'reporter', 'priority']:
                    print(f"{Fore.YELLOW}TIP: '{field}' should be formatted as an object: {{'name': 'value'}}{Style.RESET_ALL}")
        
        if 'errorMessages' in error_data:
            for msg in error_data['errorMessages']:
                print(f"{Fore.RED}Error message: {msg}{Style.RESET_ALL}")
                # Check for common field format issues in error messages
                if "Field" in msg and "is not available" in msg:
                    field = msg.split("'")[1] if "'" in msg else "unknown"
                    print(f"{Fore.YELLOW}TIP: Field '{field}' is not recognized. Custom fields may need to use ID (customfield_XXXXX){Style.RESET_ALL}")
                elif "could not be set" in msg.lower():
                    print(f"{Fore.YELLOW}TIP: A field value has incorrect format. Check object fields like assignee, priority, etc.{Style.RESET_ALL}")
        
        # Check if it might be an authentication problem        
        if any("authentication" in str(msg).lower() for msg in error_data.get('errorMessages', [])):
            print(f"{Fore.YELLOW}TIP: This may be an authentication issue. Check your Jira token is valid and has not expired.{Style.RESET_ALL}")
                
    except Exception:
        # If can't parse as JSON, show raw response
        print(f"{Fore.RED}Raw response: {response.text}{Style.RESET_ALL}")
        
    # General guidance for 400 errors
    print(f"{Fore.YELLOW}Common causes for 400 errors:{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}1. Required fields missing (check project, issuetype, summary){Style.RESET_ALL}")
    print(f"{Fore.YELLOW}2. Invalid project key or issue type{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}3. Custom field format incorrect - custom fields may need IDs like customfield_10001{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}4. Standard fields like 'assignee' need object format: {{'name': 'username'}}{Style.RESET_ALL}")
    print(f"{Fore.YELLOW}5. Authentication or permissions issues{Style.RESET_ALL}")

def create_jira_ticket(jira_client, project_key, issue_type, summary, description, excel_file=None, **fields):

    """Create a Jira ticket with the given fields."""
    # Add a separator for better log readability
    print(f"\n{Fore.CYAN}{'=' * 80}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Creating Jira ticket for {project_key} - {summary}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}{'-' * 80}{Style.RESET_ALL}")
    
    # Load custom fields mapping if excel file is provided
    custom_fields_mapping = None
    if excel_file:
        try:
            custom_fields_mapping = read_custom_fields_mapping(excel_file)
        except Exception as e:
            print(f"{Fore.YELLOW}Warning: Could not load custom fields mapping: {e}{Style.RESET_ALL}")
    
    # Extract assignee before preparing the issue dictionary, if present
    fields = fields.copy()  # Make a copy to avoid modifying the original
    assignee_name = None
    if ASSIGNEE_FIELD in fields:
        print(f"{Fore.CYAN}Found assignee{Style.RESET_ALL}")
        assignee_name = fields[ASSIGNEE_FIELD]
        del fields[ASSIGNEE_FIELD]  # Remove from fields to avoid issues during creation

    # Prepare the issue dictionary
    issue_data = prepare_issue_dict(project_key, issue_type, summary, description, fields, custom_fields_mapping)
    issue_dict, epic_link = issue_data
    
    # Log the content being sent to Jira API in a readable format
    log_issue_fields(issue_dict)
    
    print(f"{Fore.CYAN}{'-' * 80}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Submitting to Jira...{Style.RESET_ALL}")
    
    try:
        # Log detailed request information including headers and payload
        log_request_details(jira_client, issue_dict)
        
        # Create the issue with extended error handling
        new_issue = jira_client.create_issue(fields=issue_dict)
        print(f"{Fore.GREEN}Successfully created ticket: {new_issue.key}{Style.RESET_ALL}")

        # Set assignee with a separate API request if specified
        if assignee_name:
            assign_ticket(jira_client, new_issue.key, assignee_name)
    
        # Link to parent epic if specified and if we don't have a custom field mapping
        if epic_link and str(epic_link) != 'nan':
            # Only use the traditional linking method if we don't have a custom field mapping
            # or if the Epic Link field isn't in the mapping
            if not custom_fields_mapping or EPIC_LINK_FIELD not in custom_fields_mapping:
                if link_to_epic(jira_client, new_issue.key, epic_link):
                    print(f"{Fore.CYAN}Linked ticket {new_issue.key} to parent epic {epic_link} using traditional link method{Style.RESET_ALL}")
        
        return new_issue
        
    except requests.exceptions.HTTPError as e:
        handle_http_error(e)
        raise
        
    except Exception as e:
        print(f"{Fore.RED}Error creating ticket: {str(e)}{Style.RESET_ALL}")

        print(f"{Fore.RED}Error type: {type(e).__name__}{Style.RESET_ALL}")
        raise
        
    finally:
        print(f"{Fore.CYAN}{'=' * 80}{Style.RESET_ALL}\n")

def display_data_info(df, excel_file):
    """Display basic information about the imported data."""
    print(f"\n{Fore.CYAN}Excel File: {excel_file}{Style.RESET_ALL}")
    print(f"Number of tickets to create: {len(df)}")
    print(f"Columns: {', '.join(df.columns)}")

def confirm_operation(args, sheet_count=None, issue_type=None):
    """Confirm operation mode with the user."""
    if issue_type is None:
        issue_type = "Task"  # Default if not specified
    
    # Display mode message
    if args.create:
        print(f"{Fore.YELLOW}Running in CREATE mode - tickets will be created in Jira as default issue type '{issue_type}' (unless team-specific){Style.RESET_ALL}")
    else:
        print(f"{Fore.CYAN}Running in DRY-RUN mode - tickets would be created as default issue type '{issue_type}' (unless team-specific){Style.RESET_ALL}")
    
    # Confirm before proceeding with actual ticket creation
    if args.create:
        if sheet_count is not None and sheet_count > 0:
            confirm = input(f"\n{Fore.YELLOW}WARNING: This will create actual tickets in Jira using Backstage data for {sheet_count} categories. Continue? (y/n): {Style.RESET_ALL}")
        else:
            confirm = input(f"\n{Fore.YELLOW}WARNING: This will create actual tickets in Jira using Backstage data. Continue? (y/n): {Style.RESET_ALL}")
        
        if confirm.lower() != 'y':
            print(f"{Fore.RED}Operation cancelled by user.{Style.RESET_ALL}")
            return None
        
    return issue_type

def group_rows_by_key(df):
    """Group dataframe rows by key into ticket data structure."""
    ticket_data = {}
    
    for _, row in df.iterrows():
        key = row['Key']
        field = row['Field']
        value = row['Value']
        
        if key not in ticket_data:
            ticket_data[key] = {}
        
        # Some fields might appear multiple times, so collect in lists
        ticket_data[key] = add_to_team_field(ticket_data[key], field, value)
    
    return ticket_data

def format_summary(summary, sheet_name):
    """Format summary with sheet name according to the specified format."""
    if not sheet_name:
        return summary
    
    # Extract the team name from summary (usually it's just the team name)
    team_name = summary
    if isinstance(summary, list):
        team_name = ', '.join(map(str, summary))
    
    # Format as "<Team Name> Scorecards Improvement: <Sheet Name>"
    return f"{team_name} Scorecards Improvement: {sheet_name}"

def add_team_fields(additional_fields, team_info):
    """Add team fields to additional fields without overriding existing values."""
    result = additional_fields.copy()
    
    for team_field, team_value in team_info.items():
        if team_field not in additional_fields and team_field not in ['Summary', 'Description', ISSUE_TYPE_KEY]:
            # Only add non-empty fields
            if isinstance(team_value, str) and team_value.strip() or not isinstance(team_value, str):
                result[team_field] = team_value
    
    return result

def enhance_description_with_grouped_fields(description, grouped_fields, sheet_name=None):
    """Enhance description with the grouped fields information and sheet name."""
    if not description:
        description = ""
    
    # Add sheet name at the beginning of the description if provided
    if sheet_name:
        if description:
            description = f"*Backstage Scorecards Category:* {sheet_name}\n\n{description}"
        else:
            description = f"*Backstage Scorecards Category:* {sheet_name}"
    
    # Add an empty line if the description is not empty
    if description and not description.endswith('\n\n'):
        description += '\n\n'
    
    # Add grouped fields to the description
    for field_prefix, value in grouped_fields.items():
        # Only process grouped fields (with prefixes like 'L') which typically contain commas
        # Don't include fields that are sent to the API directly
        if (isinstance(value, str) and ',' in value and 
            len(field_prefix) <= 3):  # Short prefixes like 'L' are likely patterns, not regular fields
            # Convert comma-separated values to an unordered list
            items = value.split(", ")
            list_items = "\n* ".join([""] + items)
            description += f"*Address the Following Compliance Level(s):*{list_items}\n\n"
    
    return description.rstrip()

def prepare_ticket_fields(fields, key, team_mapping, sheet_name):
    """Prepare ticket fields with team mapping data."""
    # Extract basic fields
    summary = fields.get('Summary', key)
    description = fields.get('Description', '')
    
    # Remove summary and description from additional fields
    additional_fields = {k: v for k, v in fields.items() if k not in ['Summary', 'Description']}
    
    # Initialize variables to be extracted from team_info if available
    project = None
    issue_type = None

    # Add team fields without overriding
    additional_fields['Sprint Team'] = key  # Always set Sprint Team to the team key

    # Check if this team is in the filtered team mapping
    if team_mapping is not None and key not in team_mapping:
        # Set a flag to indicate this team was filtered out
        additional_fields['is_filtered_out'] = True
    
    # Add team information if available
    if team_mapping and key in team_mapping:
        team_info = team_mapping[key]
        
        # Extract project from team info if available
        if PROJECT_FIELD in team_info:
            project = team_info[PROJECT_FIELD]
            
        # Extract issue type from team info if available
        if ISSUE_TYPE_KEY in team_info:
            issue_type = team_info[ISSUE_TYPE_KEY]
        
        # Format summary with sheet name
        summary = format_summary(summary, sheet_name)
        
        additional_fields = add_team_fields(additional_fields, team_info)
        
        # Use team description if none provided
        if not description and 'Description' in team_info:
            description = team_info['Description']
    elif sheet_name:
        # If no team mapping but we have a sheet name, still format the summary
        summary = format_summary(summary, sheet_name)
    
    # Group related fields - this returns fields for API and grouped fields for display
    fields_for_api, grouped_display_fields = group_related_fields(additional_fields)

    # Enhance description with grouped fields information and sheet name
    description = enhance_description_with_grouped_fields(description, grouped_display_fields, sheet_name)

    return summary, description, fields_for_api, project, issue_type

def get_display_mode_info(is_dry_run, ticket_key):
    """Get the mode prefix and color for ticket display."""
    if is_dry_run:
        if ticket_key and ticket_key.startswith("simulated-"):
            return f"[DRY RUN] Would create (simulated ID: {ticket_key})", Fore.BLUE
        return "[DRY RUN] Would create", Fore.BLUE
    else:
        # If we have a ticket_key, it means the ticket was created
        # Otherwise, we're about to create it
        mode_prefix = "Created" if ticket_key else "Creating"
        return mode_prefix, Fore.GREEN

def display_epic_info(additional_fields, is_dry_run):
    """Display epic linking information if available."""
    epic_field = EPIC_LINK_FIELD
    if epic_field in additional_fields and additional_fields[epic_field]:
        epic = additional_fields[epic_field]
        epic_action = "Would link" if is_dry_run else "Linked"
        print(f"{Fore.CYAN}  {epic_action} to parent epic: {epic}{Style.RESET_ALL}")

def display_categories(additional_fields):
    """Display category fields and return whether any were found."""
    has_categories = False
    for field, value in additional_fields.items():
        if isinstance(value, str) and ',' in value:
            print(f"{Fore.BLUE}  {field} Categories: {value}{Style.RESET_ALL}")
            has_categories = True
    return has_categories

def display_ticket_details(key, summary, description, project_key, additional_fields, issue_type, is_dry_run=True, ticket_key=None):
    """Display ticket details, for both dry run and actual creation."""
    # Get mode prefix and color based on context
    mode_prefix, color = get_display_mode_info(is_dry_run, ticket_key)
    
    # Format ticket ID if available
    ticket_id = f"{ticket_key} - " if ticket_key else ""
    
    # Display main ticket information
    print(f"{color}{mode_prefix} ticket: '{ticket_id}{summary}' for key '{key}' in project {project_key} as issue type '{issue_type}'{Style.RESET_ALL}")
    
    # Show description preview if available
    if description:
        # Show full description in dry-run mode for better visibility
        if is_dry_run:
            print(f"{Fore.BLUE}  Description: {description}{Style.RESET_ALL}")
        else:
            # For actual creation, show truncated version to keep logs clean
            desc_preview = description[:200] + ('...' if len(description) > 200 else '')
            print(f"{Fore.BLUE}  Description: {desc_preview}{Style.RESET_ALL}")
    
    # Show epic information
    display_epic_info(additional_fields, is_dry_run)
    
    # Show categories and check if any were found
    has_categories = display_categories(additional_fields)
    
    # Note if no categories were found
    if not has_categories:
        print(f"{Fore.YELLOW}  Note: No category selections found for this ticket{Style.RESET_ALL}")

def create_single_ticket(jira_client, project_key, issue_type, key, summary, description, additional_fields, create_mode, excel_file=None, custom_fields_mapping=None):
    """Create a single Jira ticket or simulate in dry run."""
    # If no project key and key wasn't in the team mapping (due to filtering),
    # this team was likely excluded by processTeams/excludeTeams filters
    if not project_key:
        # Check if this is likely due to a team filter rather than missing Project field
        if 'is_filtered_out' in additional_fields and additional_fields['is_filtered_out']:
            print(f"{Fore.CYAN}Skipping ticket for '{key}' - team excluded by filter{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}Skipping ticket for '{key}' - no Project field specified{Style.RESET_ALL}")
        return None, key
    
    # Generate a simulated ticket ID if in dry run mode
    simulated_ticket_key = None
    if not create_mode:
        simulated_ticket_key = simulated_ticket_counter.get_next_ticket_id(project_key)
    
    # Always display the ticket details for both dry run and creation modes
    display_ticket_details(key, summary, description, project_key, additional_fields, issue_type, 
                          is_dry_run=not create_mode, ticket_key=simulated_ticket_key)
    
    # If we're in dry run mode, return the simulated ticket key and summary
    if not create_mode:
        # Create a TicketInfo object with the simulated ticket ID and summary
        ticket_info = TicketInfo(simulated_ticket_key, summary)
        return ticket_info, None
    
    # Otherwise, create the ticket
    try:
        new_issue = create_jira_ticket(jira_client, project_key, issue_type, summary, description, excel_file=excel_file, **additional_fields)
        # Display the creation result with the actual ticket key
        display_ticket_details(key, summary, description, project_key, additional_fields, issue_type, 
                              is_dry_run=False, ticket_key=new_issue.key)
        # Return a TicketInfo object with the real ticket ID and summary
        ticket_info = TicketInfo(new_issue.key, summary)
        return ticket_info, None
    except requests.exceptions.HTTPError as e:
        # HTTP errors are already handled in create_jira_ticket with detailed logging
        # Just return the failure result here
        return None, key
    except Exception as e:
        print(f"{Fore.RED}Unexpected error creating ticket for key '{key}': {str(e)}{Style.RESET_ALL}")
        import traceback
        print(f"{Fore.RED}Stack trace: {traceback.format_exc()}{Style.RESET_ALL}")
        return None, key

def extract_field_prefix(field):
    """Extract the prefix from a field name like 'L1', 'L2', etc."""
    import re
    match = re.match(r'^([A-Za-z]+)(\d+)$', field)
    if match:
        return match.group(1)
    return None

def sort_fields_numerically(fields):
    """Sort fields like L1, L2, L3 numerically by their number component."""
    import re
    
    def get_number(field):
        match = re.match(r'^[A-Za-z]+(\d+)$', field)
        return int(match.group(1)) if match else 0
        
    return sorted(fields, key=get_number)

def collect_field_values(fields, related_fields):
    """Collect values from related fields."""
    values = []
    for field in related_fields:
        # Check if field exists and has a non-empty value
        if field in fields and fields[field]:
            # Convert to string and check if it's not just whitespace
            field_value = str(fields[field]).strip()
            if field_value and field_value.lower() not in ['none', 'nan', 'null']:
                values.append(f"{field}: {field_value}")
    return values

def group_related_fields(fields):
    """Group related fields based on common prefixes or patterns.
    
    For example, if there are fields like L1, L2, L3, they will be grouped together.
    Returns a tuple containing:
    - fields_for_api: Fields that should be sent to the Jira API (no pattern-based fields)
    - grouped_display_fields: Fields grouped by prefixes for display purposes only
    """
    fields_for_api = {}
    grouped_display_fields = {}
    field_patterns = {}
    
    # List of valid Jira fields (add more as needed)
    valid_jira_fields = [
        'assignee', 'component', 'components', 'description', 'duedate', 'environment', 'epic link',
        'fixVersions', 'issuetype', 'labels', 'priority', 'project', 'reporter',
        'security', 'sprint team', 'sprint', 'summary', 'timetracking', 'versions'
        # Add specific custom fields your Jira instance supports here
    ]
    
    # Process regular fields first
    for field, value in fields.items():
        if field in ['Summary', 'Description']:
            continue
            
        prefix = extract_field_prefix(field)
        if prefix:
            # Add to pattern collection for later processing
            if prefix not in field_patterns:
                field_patterns[prefix] = []
            field_patterns[prefix].append(field)
            # DO NOT add pattern fields like L1, L2 to API fields
        else:
            # For regular fields, only include known Jira fields in API request
            # This avoids sending invalid fields to Jira
            if field.lower() in [f.lower() for f in valid_jira_fields] or field.startswith('customfield_'):
                fields_for_api[field] = value
            
            # Always keep for display purposes
            grouped_display_fields[field] = value
    
    # Process pattern-based fields for display only
    for prefix, related_fields in field_patterns.items():
        sorted_fields = sort_fields_numerically(related_fields)
        values = collect_field_values(fields, sorted_fields)
        
        if values:
            # Add grouped field to display fields only, not to API fields
            grouped_display_fields[prefix] = ", ".join(values)
    
    return fields_for_api, grouped_display_fields

def is_field_empty(value):
    """Check if a field value is effectively empty."""
    import pandas as pd
    
    # None values are empty
    if value is None:
        return True
        
    # Empty strings or whitespace
    if isinstance(value, str) and not value.strip():
        return True
        
    # NaN values
    if pd.isna(value):
        return True
        
    # Common string representations of empty values
    if str(value).lower() in ['nan', 'none', 'null']:
        return True
        
    return False

def has_category_selections(fields):
    """Check if any category fields (pattern-based fields) have values."""
    import re
    
    for field in fields:
        # Skip non-category fields
        if field in ['Summary', 'Description']:
            continue
            
        # Check if this is a category field (matches pattern like L1, L2, etc.)
        if re.match(r'^[A-Za-z]+\d+$', field):
            # Check if the field has a non-empty value
            if not is_field_empty(fields[field]):
                return True
    
    return False

def create_tickets_from_key_value(jira_client, df, default_issue_type, create_mode, team_mapping=None, sheet_name=None, priority=None, custom_fields_mapping=None, excel_file=None):
    """Process transformed key-value data and create tickets."""
    created_tickets = []
    skipped_tickets = []
    dry_run_ticket_count = 0
    
    # Group by 'Key' to process each unique key
    ticket_data = group_rows_by_key(df)
    
    # Create a ticket for each unique key
    for key, fields in ticket_data.items():
        # Check if team was filtered out by processTeams or excludeTeams
        if team_mapping is not None and key not in team_mapping:
            # Just silently skip this team as it was already mentioned in the filtered teams list
            continue
            
        # Check if this team has any category selections
        if not has_category_selections(fields):
            print(f"{Fore.YELLOW}Skipping ticket for '{key}' - no categories selected{Style.RESET_ALL}")
            continue
        
        # Prepare ticket fields with standard processing
        # This already includes grouping related fields and separating display fields from API fields
        summary, description, additional_fields, team_project, team_issue_type = prepare_ticket_fields(fields, key, team_mapping, sheet_name)
        
        # Use team-specific issue type if available, otherwise use the default
        issue_type = team_issue_type if team_issue_type else default_issue_type
        
        # Get project key from team-specific Project field
        project_key = team_project
        
        # Add priority if specified in config sheet
        if priority:
            additional_fields['priority'] = priority
        
        # Create or simulate ticket creation
        ticket_info, skipped_key = create_single_ticket(
            jira_client, project_key, issue_type, key, summary, description, additional_fields, create_mode,
            excel_file, custom_fields_mapping
        )
        
        if ticket_info:
            created_tickets.append(ticket_info)
            
        if not create_mode:
            # In dry-run mode, count tickets that would be created (with valid project key)
            if project_key:  # Only count tickets that have a valid project key
                dry_run_ticket_count += 1
            
        if skipped_key:
            skipped_tickets.append(skipped_key)
    
    # For dry run mode, store the count on the dataframe for later use in summary
    if not create_mode:
        df._dry_run_ticket_count = dry_run_ticket_count
    
    return created_tickets, skipped_tickets

def assign_ticket(jira_client, issue_key, assignee_name):
    """Execute the actual assignment API call with error handling.
    
    Args:
        jira_client: The Jira client instance
        issue_key: The key of the issue to update
        assignee_name: The username of the assignee to set
        
    Returns:
        bool: True if successful, False if failed
    """
    try:
        print(f"{Fore.CYAN}Setting assignee for {issue_key} to '{assignee_name}'{Style.RESET_ALL}")
        
        # Use the standard Jira API method to assign the issue
        jira_client.assign_issue(issue_key, assignee_name)
        print(f"{Fore.GREEN}Successfully set assignee for {issue_key} to '{assignee_name}'{Style.RESET_ALL}")
        return True
            
    except requests.exceptions.HTTPError as e:
        # Get error details from response
        response = e.response if hasattr(e, 'response') else None
        status_code = response.status_code if response else "Unknown"
        
        print(f"{Fore.RED}HTTP Error ({status_code}) setting assignee for {issue_key}: {str(e)}{Style.RESET_ALL}")
        
        if response:
            try:
                error_data = response.json()
                print(f"{Fore.RED}Error details: {error_data}{Style.RESET_ALL}")
            except ValueError:
                print(f"{Fore.RED}Raw response: {response.text}{Style.RESET_ALL}")
        
        return False
    except Exception as e:
        print(f"{Fore.RED}Error setting assignee for {issue_key}: {str(e)}{Style.RESET_ALL}")
        print(f"{Fore.RED}Error type: {type(e).__name__}{Style.RESET_ALL}")
        print(f"{Fore.RED}Stack trace: {traceback.format_exc()}{Style.RESET_ALL}")
        return False

def display_ticket_count_message(create_mode, created_tickets, sheet_name=None):
    """Display a message about the number of tickets created or to be created."""
    sheet_info = f"for {sheet_name}" if sheet_name else ""
    if not create_mode:
        # In dry run mode, check if any tickets would be created
        ticket_count = len(created_tickets) if created_tickets else 0
        if ticket_count > 0:
            print(f"\n{Fore.YELLOW}[DRY RUN] Would have created {ticket_count} tickets {sheet_info}.{Style.RESET_ALL}")
        else:
            print(f"\n{Fore.YELLOW}[DRY RUN] No tickets would be created {sheet_info}.{Style.RESET_ALL}")
    else:
        # In create mode, check if any tickets were actually created
        if created_tickets:
            # Extract ticket IDs from TicketInfo objects for display
            ticket_ids = [ticket.ticket_id for ticket in created_tickets]
            print(f"\n{Fore.GREEN}Created {len(created_tickets)} tickets {sheet_info}: {', '.join(ticket_ids)}{Style.RESET_ALL}")
        else:
            print(f"\n{Fore.YELLOW}No tickets were created {sheet_info}.{Style.RESET_ALL}")

def display_skipped_messages(create_mode, skipped_count, skipped_tickets):
    """Display messages about skipped tickets."""
    if skipped_count > 0:
        prefix = "[DRY RUN] " if not create_mode else ""
        print(f"{Fore.YELLOW}{prefix}{skipped_count} teams skipped (no categories selected).{Style.RESET_ALL}")
    
    if skipped_tickets and create_mode:
        print(f"{Fore.RED}Skipped {len(skipped_tickets)} tickets due to errors.{Style.RESET_ALL}")

def display_summary(create_mode, df, created_tickets, skipped_tickets, sheet_name=None):
    """Display summary of the operation."""
    # Fix for dry run mode - add placeholder ticket IDs if we have a count but no IDs
    if not create_mode and hasattr(df, '_dry_run_ticket_count') and df._dry_run_ticket_count > 0:
        # Create placeholder TicketInfo objects with generic summaries
        created_tickets = [TicketInfo(f"Ticket-{i+1}", "Generated placeholder ticket") for i in range(df._dry_run_ticket_count)]
    elif not create_mode and len(created_tickets) == 0 and not hasattr(df, '_dry_run_ticket_count'):
        # Ensure we show 0 tickets for sheets with no valid tickets
        created_tickets = []
    
    # Count unique keys in the dataframe
    unique_keys_count = len(df['Key'].unique()) if 'Key' in df.columns else len(df)
    
    # Calculate how many were skipped due to missing categories or other issues
    # Note: This calculation is modified to handle the case where tickets are skipped due to missing Project field
    if create_mode:
        skipped_count = unique_keys_count - len(created_tickets) - len(skipped_tickets)
    else:
        # In dry run mode, use the tracked count for accuracy
        dry_run_count = getattr(df, '_dry_run_ticket_count', 0)
        skipped_count = unique_keys_count - dry_run_count - len(skipped_tickets)
    
    # Display ticket count message
    display_ticket_count_message(create_mode, created_tickets, sheet_name)
    
    # Display messages about skipped tickets
    display_skipped_messages(create_mode, skipped_count, skipped_tickets)

def get_excel_sheets(file_path):
    """Get list of all sheets in the Excel file."""
    try:
        xls = pd.ExcelFile(file_path)
        return xls.sheet_names
    except Exception as e:
        print(f"{Fore.RED}Error reading Excel sheets: {str(e)}{Style.RESET_ALL}")
        return None

def process_sheet(args, file_path, sheet_name, jira_client, default_issue_type, team_mapping=None, priority=None, custom_fields_mapping=None):
    """Process a single sheet from the Excel file."""
    print(f"\n{Fore.CYAN}Processing sheet: {sheet_name}{Style.RESET_ALL}")
    
    # Read the Excel sheet
    df = read_excel_file(file_path, sheet_name)
    if df is None:
        return [], [], 0
    
    # Validate the data has required columns
    if not validate_data(df):
        return [], [], 0
    
    # Display data information
    # display_data_info(df, f"{file_path} - {sheet_name}")
    
    # Create tickets, optionally using team mapping data
    created_tickets, skipped_tickets = create_tickets_from_key_value(
        jira_client, df, default_issue_type, args.create, team_mapping, sheet_name, priority, 
        custom_fields_mapping, file_path
    )
    
    # Display summary for this sheet
    display_summary(args.create, df, created_tickets, skipped_tickets, sheet_name)
    
    # Return dry run count for overall summary
    dry_run_count = getattr(df, '_dry_run_ticket_count', 0) if not args.create else 0
    
    return created_tickets, skipped_tickets, dry_run_count

def format_sprint_value(value):
    """Format Sprint value to ensure it's an integer, not a float.
    
    Args:
        value: The Sprint value (could be int, float, or string)
        
    Returns:
        Integer sprint value or original value if not numeric
    """
    import pandas as pd
    
    # Check if value is a float that represents an integer
    if isinstance(value, float) and value.is_integer():
        return int(value)
    # Check if value is NaN
    elif pd.isna(value):
        return None
    # Check if it's a string that looks like a float
    elif isinstance(value, str):
        try:
            float_val = float(value)
            if float_val.is_integer():
                return int(float_val)
        except (ValueError, AttributeError):
            pass
    
    return value

def add_to_team_field(team_data, field, value):
    """Add a value to a team's field, handling lists for multiple values."""
    # Special handling for Sprint field to ensure it's an integer
    if field == SPRINT_FIELD:
        value = format_sprint_value(value)
    
    if field in team_data:
        if isinstance(team_data[field], list):
            team_data[field].append(value)
        else:
            team_data[field] = [team_data[field], value]
    else:
        team_data[field] = value
    
    return team_data

def create_team_mapping(teams_df):
    """Create a mapping of team information from the Teams sheet."""
    team_mapping = {}
    
    # Validate required columns exist
    if not all(col in teams_df.columns for col in ['Key', 'Field', 'Value']):
        print(f"{Fore.YELLOW}Warning: Teams sheet does not have required columns (Key, Field, Value){Style.RESET_ALL}")
        return team_mapping
    
    # Group data by key (team name)
    for _, row in teams_df.iterrows():
        team_name = row['Key']
        field = row['Field']
        value = row['Value']
        
        # Initialize team entry if needed
        if team_name not in team_mapping:
            team_mapping[team_name] = {}
        
        # Update team data with the new field value
        team_mapping[team_name] = add_to_team_field(team_mapping[team_name], field, value)
    
    return team_mapping

def process_teams_sheet(excel_file, available_sheets):
    """Process the Teams sheet from the Excel file."""
    # Check if Teams sheet exists
    if "Teams" not in available_sheets:
        print(f"{Fore.RED}Error: 'Teams' sheet not found in the Excel file. Available sheets: {', '.join(available_sheets)}{Style.RESET_ALL}")
        return None
    
    # Read the Teams sheet
    teams_df = read_excel_file(excel_file, "Teams")
    if teams_df is None:
        return None
    
    # Validate the data has required columns
    if not validate_data(teams_df):
        return None
    
    # Create team mapping from Teams sheet
    team_mapping = create_team_mapping(teams_df)
    print(f"\n{Fore.CYAN}Created team mapping with {len(team_mapping)} teams{Style.RESET_ALL}")
    
    # Display data information for Teams (just for information)
    print(f"\n{Fore.CYAN}Teams information (for reference only - no tickets will be created from this sheet):{Style.RESET_ALL}")
    # display_data_info(teams_df, excel_file)
    
    return team_mapping

def display_team_projects(team_mapping):
    """Display team-specific Project values (project keys) if available."""
    projects = {}
    
    # Collect Project values by team
    for team_name, team_data in team_mapping.items():
        if PROJECT_FIELD in team_data:
            project = team_data[PROJECT_FIELD]
            if project not in projects:
                projects[project] = []
            projects[project].append(team_name)
    
def display_team_issue_types(team_mapping):
    """Display team-specific Issue Type values if available."""
    issue_types = {}
    
    # Collect Issue Type values by team
    for team_name, team_data in team_mapping.items():
        if ISSUE_TYPE_KEY in team_data:
            issue_type = team_data[ISSUE_TYPE_KEY]
            if issue_type not in issue_types:
                issue_types[issue_type] = []
            issue_types[issue_type].append(team_name)
    
def filter_team_mapping(team_mapping, args):
    """Filter team mapping based on processTeams or excludeTeams arguments."""
    filtered_mapping = team_mapping.copy()
    
    # If processTeams is provided, only include those teams (case insensitive)
    if args.processTeams:
        teams_to_process = [team.strip() for team in args.processTeams.split(',')]
        # Create a case-insensitive lookup dictionary
        teams_lookup = {key.lower(): key for key in team_mapping.keys()}
        
        # Calculate which teams will be excluded
        all_teams = set(team_mapping.keys())
        included_teams = set()
        
        # Filter the mapping using case-insensitive comparison
        filtered_mapping = {}
        for team in teams_to_process:
            if team.lower() in teams_lookup:
                original_key = teams_lookup[team.lower()]
                filtered_mapping[original_key] = team_mapping[original_key]
                included_teams.add(original_key)
        
        # Calculate excluded teams
        excluded_teams = all_teams - included_teams
        excluded_teams_list = sorted(list(excluded_teams))
        
        print(f"{Fore.CYAN}Processing only specified teams: {', '.join(teams_to_process)}{Style.RESET_ALL}")
        if excluded_teams:
            print(f"{Fore.CYAN}Filtering out {len(excluded_teams)} teams: {', '.join(excluded_teams_list[:5])}" + 
                  (f", and {len(excluded_teams) - 5} more..." if len(excluded_teams) > 5 else "") + 
                  f"{Style.RESET_ALL}")
        
    # If excludeTeams is provided, exclude those teams (case insensitive)
    elif args.excludeTeams:
        teams_to_exclude = [team.strip().lower() for team in args.excludeTeams.split(',')]
        
        # Keep track of which teams are actually excluded (for better messaging)
        excluded_teams = []
        
        # Filter the mapping using case-insensitive comparison
        filtered_mapping = {}
        for key, value in team_mapping.items():
            if key.lower() not in teams_to_exclude:
                filtered_mapping[key] = value
            else:
                excluded_teams.append(key)
                
        print(f"{Fore.CYAN}Excluding specified teams: {', '.join(args.excludeTeams.split(','))}{Style.RESET_ALL}")
        if excluded_teams:
            print(f"{Fore.CYAN}Actually excluded {len(excluded_teams)} teams: {', '.join(excluded_teams)}{Style.RESET_ALL}")
    
    # If filtered mapping is empty but original wasn't, provide a warning
    if not filtered_mapping and team_mapping:
        print(f"{Fore.YELLOW}Warning: No teams match the filter criteria. No tickets will be created.{Style.RESET_ALL}")
    
    return filtered_mapping

def process_all_sheets(args, file_path, available_sheets, jira_client, default_issue_type, team_mapping, priority=None):
    """Process teams using Backstage API instead of Excel category sheets."""
    all_created_tickets = []
    all_skipped_tickets = []
    total_dry_run_count = 0
    
    # Load custom fields mapping if the CustomFields sheet exists
    custom_fields_mapping = None
    if CUSTOM_FIELDS_SHEET in available_sheets:
        custom_fields_mapping = read_custom_fields_mapping(file_path)
    
    # Read Excel config to get Backstage URL and categories
    excel_config = read_config_sheet(file_path)
    backstage_url = get_backstage_url_from_config(excel_config)
    categories = get_categories_from_config(excel_config)
    
    if not backstage_url:
        print(f"{Fore.RED}Error: Backstage URL not found in Config sheet.{Style.RESET_ALL}")
        print(f"{Fore.RED}Please add a 'Backstage' key with the base URL (e.g., https://backstage.core.cvent.org) to the Config sheet.{Style.RESET_ALL}")
        return all_created_tickets, all_skipped_tickets, total_dry_run_count
    
    print(f"{Fore.CYAN}Using Backstage URL: {backstage_url}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Will process categories: {', '.join(categories)}{Style.RESET_ALL}")
    
    # Filter teams based on arguments
    filtered_team_mapping = filter_team_mapping(team_mapping, args)
    
    # Display team-specific projects and issue types for user information
    display_team_projects(filtered_team_mapping)
    display_team_issue_types(filtered_team_mapping)
    
    print(f"\n{Fore.CYAN}Processing teams with Backstage integration...{Style.RESET_ALL}")
    
    # Process each team using Backstage data
    for team_name, team_config in filtered_team_mapping.items():
        print(f"\n{Fore.CYAN}=== Processing team: {team_name} ==={Style.RESET_ALL}")
        
        # Get team's scorecard data from Backstage using configured categories
        team_categories = get_team_categories_from_backstage(backstage_url, team_name, categories)
        
        if team_categories:
            # Process each category that has data
            for category_name, category_data in team_categories.items():
                print(f"\n{Fore.CYAN}Processing category: {category_name} for team {team_name}{Style.RESET_ALL}")
                
                # Create tickets for this team/category combination
                created, skipped, dry_run_count = process_team_category_from_backstage(
                    args, team_name, category_name, category_data, team_config, 
                    jira_client, default_issue_type, priority, custom_fields_mapping
                )
                
                all_created_tickets.extend(created)
                all_skipped_tickets.extend(skipped)
                total_dry_run_count += dry_run_count
        else:
            print(f"{Fore.YELLOW}No scorecard data found for team: {team_name}{Style.RESET_ALL}")
            # Count as skipped
            all_skipped_tickets.append(f"{team_name} (No Backstage data)")
    
    return all_created_tickets, all_skipped_tickets, total_dry_run_count

def process_team_category_from_backstage(args, team_name, category_name, category_data, team_config, 
                                        jira_client, default_issue_type, priority, custom_fields_mapping):
    """Process a single team/category combination from Backstage data."""
    created_tickets = []
    skipped_tickets = []
    dry_run_count = 0
    
    try:
        # Prepare ticket data similar to existing logic
        summary = f"{team_name} Scorecards Improvement: {category_name}"
        
        # Build detailed description from compliance analysis
        description = f"*Backstage Scorecards Category:* {category_name}\\n\\n"
        
        if category_data and isinstance(category_data, dict):
            # Handle new detailed compliance analysis format
            if 'current_level' in category_data:
                current_level = category_data.get('current_level', 'L0')
                improvement_details = category_data.get('improvement_details', [])
                
                description += f"*Current Compliance Level:* {current_level}\\n\\n"
                
                if improvement_details:
                    total_opportunities = len(improvement_details)
                    description += f"*Improvement Opportunities:* ({total_opportunities} total)\\n\\n"
                    
                    # Group improvement details by level
                    grouped_by_level = {}
                    for detail in improvement_details:
                        level_category = detail.get('level_category', 'L1')
                        if level_category not in grouped_by_level:
                            grouped_by_level[level_category] = []
                        grouped_by_level[level_category].append(detail)
                    
                    # Sort levels (L1, L2, L3, L4, etc.)
                    sorted_levels = sorted(grouped_by_level.keys(), key=lambda x: (len(x), x))
                    
                    # Generate description grouped by level
                    for level_category in sorted_levels:
                        level_details = grouped_by_level[level_category]
                        description += f"*{level_category} Issues:*\\n"
                        
                        for detail in level_details:
                            level = detail['level']
                            level_name = detail['level_name']
                            threshold = detail['threshold']
                            current_count = detail['current_count']
                            total_count = detail['total_count']
                            needed_count = detail['needed_count']
                            percentage = detail['percentage']
                            
                            # Enhanced description based on analysis type
                            if category_data.get('analysis_type') == 'specific_failures':
                                check_id = detail.get('check_id', '')
                                state = detail.get('state', 'failed')
                                target = detail.get('target', {})
                                
                                description += f"* *{level_name}*:\\n"
                                description += f"** Check: {{monospace}}{check_id}{{monospace}}\\n"
                                description += f"** Status: {state.upper()}\\n"
                                description += f"** Current: {current_count}/{total_count} components ({percentage:.0f}%)\\n"
                                
                                if target:
                                    target_range = f"{target.get('lower', '??')}-{target.get('upper', '??')}%"
                                    description += f"** Target: {target_range}\\n"
                                
                                if needed_count > 0:
                                    description += f"** *Action Required*: Fix {needed_count} additional component{'s' if needed_count != 1 else ''}\\n"
                                description += "\\n"
                            else:
                                # Traditional level-based or synthetic description
                                description += f"* *{level_name}* - {threshold}:\\n"
                                description += f"** Current: {current_count}/{total_count} components ({percentage}%)\\n"
                                description += f"** Need to improve: {needed_count} additional components\\n\\n"
                        
                        description += "\\n"  # Extra spacing between level groups
                else:
                    description += "Team is at maximum compliance level for this category.\\n"
            else:
                # Handle legacy format for backward compatibility
                description += "*Address the Following Compliance Level(s):*\\n"
                for level, value in category_data.items():
                    if value:
                        description += f"* {level}: {value}\\n"
        else:
            description += "No specific compliance levels identified.\\n"
        
        # Get team configuration
        project_key = team_config.get('Project')
        if not project_key:
            print(f"{Fore.YELLOW}Skipping {team_name} - no Project specified{Style.RESET_ALL}")
            skipped_tickets.append(f"{team_name}_{category_name}")
            return created_tickets, skipped_tickets, dry_run_count
        
        # Determine issue type
        issue_type = team_config.get('Issue Type', default_issue_type)
        
        # Prepare additional fields from team config
        additional_fields = {}
        for field_name, field_value in team_config.items():
            if field_name not in ['Project', 'Issue Type'] and field_value:
                additional_fields[field_name] = field_value
        
        # Add priority if specified
        if priority:
            additional_fields['Priority'] = priority
        
        # Create the ticket
        if args.create:
            ticket = create_single_ticket(
                jira_client, project_key, issue_type, team_name, summary, description, 
                additional_fields, args.create, excel_file=None, custom_fields_mapping=custom_fields_mapping
            )
            if ticket:
                created_tickets.append(ticket)
                print(f"{Fore.GREEN}Created ticket: '{ticket.ticket_id} - {summary}' for key '{team_name}' in project {project_key} as issue type '{issue_type}'{Style.RESET_ALL}")
            else:
                skipped_tickets.append(f"{team_name}_{category_name}")
        else:
            # Dry run mode
            dry_run_count += 1
            simulated_ticket_id = f"simulated-{project_key}-{dry_run_count}"
            ticket = TicketInfo(simulated_ticket_id, summary)
            created_tickets.append(ticket)
            
            print(f"{Fore.YELLOW}[DRY RUN] Would create ticket: '{summary}' for key '{team_name}' in project {project_key} as issue type '{issue_type}'{Style.RESET_ALL}")
            print(f"  Description: {description}")
            if 'Epic Link' in additional_fields:
                print(f"  Would link to parent epic: {additional_fields['Epic Link']}")
    
    except Exception as e:
        print(f"{Fore.RED}Error processing {team_name} - {category_name}: {e}{Style.RESET_ALL}")
        skipped_tickets.append(f"{team_name}_{category_name}")
    
    return created_tickets, skipped_tickets, dry_run_count

def display_filter_info(args):
    """Generate filter info string based on arguments."""
    if args and args.processTeams:
        return " (filtered to include only specified teams)"
    elif args and args.excludeTeams:
        return " (with excluded teams filtered out)"
    return ""

def display_dry_run_summary(total_dry_run_count, all_created_tickets, issue_type, filter_info):
    """Display summary for dry run mode."""
    ticket_count = total_dry_run_count if total_dry_run_count > 0 else len(all_created_tickets)
    print(f"{Fore.YELLOW}[DRY RUN] Would have created a total of {ticket_count} tickets in Jira across all sheets as issue type '{issue_type}'{filter_info}.{Style.RESET_ALL}")
    
    # If we have simulated tickets, display them in alphabetical sorted order
    if all_created_tickets:
        # Sort tickets alphabetically by ticket ID
        sorted_tickets = sorted(all_created_tickets, key=lambda t: t.ticket_id)
        
        print(f"\n{Fore.CYAN}=== SIMULATED TICKETS (Alphabetical) ==={Style.RESET_ALL}")
        for i, ticket in enumerate(sorted_tickets, 1):
            print(f"{Fore.BLUE}{i}. {ticket.ticket_id}: {ticket.summary}{Style.RESET_ALL}")
        
        print(f"\n{Fore.CYAN}Simulated tickets for copy-paste:{Style.RESET_ALL}")
        ticket_ids = [t.ticket_id for t in sorted_tickets]
        print(f"{Fore.BLUE}{','.join(ticket_ids)}{Style.RESET_ALL}")

def display_created_tickets(all_created_tickets):
    """Display detailed list of created tickets in alphabetical order."""
    # Sort tickets alphabetically by ticket ID
    sorted_tickets = sorted(all_created_tickets, key=lambda t: t.ticket_id)
    
    print(f"\n{Fore.CYAN}=== CREATED TICKETS (Alphabetical) ==={Style.RESET_ALL}")
    for i, ticket in enumerate(sorted_tickets, 1):
        print(f"{Fore.GREEN}{i}. {ticket.ticket_id}: {ticket.summary}{Style.RESET_ALL}")
    
    # Print tickets in comma-separated format for easy copy-pasting
    # print(f"\n{Fore.CYAN}Tickets for copy-paste:{Style.RESET_ALL}")
    # ticket_ids = [t.ticket_id for t in sorted_tickets]
    # print(f"{Fore.GREEN}{','.join(ticket_ids)}{Style.RESET_ALL}")
    
    # Print suggestion for using the tickets in other tools
    if sorted_tickets:
        first_ticket_id = sorted_tickets[0].ticket_id
        print(f"\n{Fore.CYAN}Tip: You can use these tickets in other JiraTools scripts:{Style.RESET_ALL}")
        print(f"{Fore.CYAN}  e.g., python epicStatus.py {first_ticket_id}{Style.RESET_ALL}")
        if len(sorted_tickets) > 1:
            print(f"{Fore.CYAN}  or python jira_assign.py {first_ticket_id} username{Style.RESET_ALL}")

def display_skipped_tickets(all_skipped_tickets):
    """Display list of skipped tickets in alphabetical order."""
    if not all_skipped_tickets:
        return
    
    # Sort skipped tickets alphabetically    
    sorted_skipped = sorted(all_skipped_tickets)
    
    print(f"{Fore.RED}Skipped a total of {len(sorted_skipped)} tickets due to errors.{Style.RESET_ALL}")
    print(f"\n{Fore.RED}=== SKIPPED TICKETS (Alphabetical) ==={Style.RESET_ALL}")
    for i, team in enumerate(sorted_skipped, 1):
        print(f"{Fore.RED}{i}. {team}{Style.RESET_ALL}")

def display_overall_summary(create_mode, all_created_tickets, all_skipped_tickets, total_dry_run_count, issue_type, args=None):
    """Display the overall summary of the operation."""
    print(f"\n{Fore.CYAN}=== OVERALL SUMMARY ==={Style.RESET_ALL}")
    
    # Generate filter info string
    filter_info = display_filter_info(args)
    
    if not create_mode:
        display_dry_run_summary(total_dry_run_count, all_created_tickets, issue_type, filter_info)
    else:
        if all_created_tickets:
            print(f"{Fore.GREEN}Created a total of {len(all_created_tickets)} tickets across all sheets as issue type '{issue_type}'{filter_info}.{Style.RESET_ALL}")
            display_created_tickets(all_created_tickets)
        else:
            print(f"{Fore.YELLOW}No tickets were created across all sheets{filter_info}.{Style.RESET_ALL}")
            
        display_skipped_tickets(all_skipped_tickets)

def export_tickets_to_csv(csv_file, tickets_data, custom_fields_mapping=None):
    """Export ticket data to CSV files, creating one file per Sprint Team.
    
    Args:
        csv_file: Base path for output CSV files (will be modified to include team name)
        tickets_data: List of ticket dictionaries with all the data
        custom_fields_mapping: Dictionary mapping field names to custom field IDs
    """
    import csv
    import os
    
    if not tickets_data:
        print(f"{Fore.YELLOW}No tickets to export to CSV{Style.RESET_ALL}")
        return
    
    # Group tickets by Sprint Team
    tickets_by_team = {}
    for ticket in tickets_data:
        team = ticket.get('Sprint Team', 'NoTeam')
        if team not in tickets_by_team:
            tickets_by_team[team] = []
        tickets_by_team[team].append(ticket)
    
    # Parse the base filename to insert team name
    csv_path = os.path.splitext(csv_file)
    base_name = csv_path[0]
    extension = csv_path[1] if csv_path[1] else '.csv'
    
    # Jira CSV import required and optional fields
    # Required: Summary, Issue Type, Project Key
    # Common optional: Description, Assignee, Reporter, Priority, Labels, Epic Link, Sprint, Component
    
    # Collect all unique field names from all tickets
    all_fields = set()
    for ticket in tickets_data:
        all_fields.update(ticket.keys())
    
    # Define standard Jira CSV column names and their order
    standard_columns = [
        'Summary',
        'Issue Type',
        'Project Key',
        'Priority',
        'Assignee',
        'Epic Link',
        'Sprint',
        'Component',
        'Labels',
        'Sprint Team',
        'Description'
    ]
    
    # Start with standard columns that exist in the data
    csv_columns = [col for col in standard_columns if col in all_fields]
    
    # Add custom fields and any other fields not in standard list
    custom_columns = sorted([f for f in all_fields if f not in standard_columns])
    csv_columns.extend(custom_columns)
    
    # Write separate CSV file for each team
    total_exported = 0
    exported_files = []
    
    for team, team_tickets in sorted(tickets_by_team.items()):
        # Create team-specific filename
        team_csv_file = f"{base_name}-{team}{extension}"
        
        try:
            with open(team_csv_file, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=csv_columns, extrasaction='ignore', quoting=csv.QUOTE_NONNUMERIC)
                writer.writeheader()
                
                for ticket in team_tickets:
                    # Prepare row with proper formatting
                    row = {}
                    for col in csv_columns:
                        value = ticket.get(col, '')
                        
                        # Handle None values
                        if value is None:
                            row[col] = ''
                        # Handle list values (like labels, components)
                        elif isinstance(value, list):
                            row[col] = ', '.join(str(v) for v in value)
                        # Handle dict values (extract 'value' or 'name' key if present)
                        elif isinstance(value, dict):
                            if 'value' in value:
                                row[col] = value['value']
                            elif 'name' in value:
                                row[col] = value['name']
                            else:
                                row[col] = str(value)
                        # Special handling for Sprint field - ensure integer format
                        elif col == 'Sprint' and isinstance(value, float) and value.is_integer():
                            row[col] = str(int(value))
                        # Special handling for numeric values that should be integers
                        elif isinstance(value, float) and value.is_integer():
                            row[col] = str(int(value))
                        else:
                            # Convert escaped newlines to real newlines for CSV
                            str_value = str(value)
                            row[col] = str_value.replace('\\n', '\n')
                    
                    writer.writerow(row)
            
            print(f"{Fore.GREEN}Successfully exported {len(team_tickets)} tickets for team '{team}' to {team_csv_file}{Style.RESET_ALL}")
            total_exported += len(team_tickets)
            exported_files.append(team_csv_file)
            
        except Exception as e:
            print(f"{Fore.RED}Error writing CSV file for team '{team}': {str(e)}{Style.RESET_ALL}")
            import traceback
            print(f"{Fore.RED}Stack trace: {traceback.format_exc()}{Style.RESET_ALL}")
    
    # Summary
    print(f"\n{Fore.CYAN}=== CSV Export Summary ==={Style.RESET_ALL}")
    print(f"{Fore.GREEN}Total tickets exported: {total_exported}{Style.RESET_ALL}")
    print(f"{Fore.GREEN}Files created: {len(exported_files)}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}CSV columns: {', '.join(csv_columns)}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}You can now import these files into Jira using: Project Settings > Import > CSV{Style.RESET_ALL}")


def collect_ticket_data_for_csv(df, default_issue_type, team_mapping, sheet_name, priority, custom_fields_mapping, excel_file, args):
    """Collect ticket data for CSV export without creating tickets.
    
    Args:
        df: DataFrame with ticket data
        default_issue_type: Default issue type
        team_mapping: Team information mapping
        sheet_name: Name of the sheet being processed
        priority: Priority value from config
        custom_fields_mapping: Custom field mappings
        excel_file: Path to Excel file
        args: Command-line arguments
        
    Returns:
        List of ticket dictionaries ready for CSV export
    """
    tickets_data = []
    
    # Group by 'Key' to process each unique key
    ticket_data = group_rows_by_key(df)
    
    # Collect data for each unique key
    for key, fields in ticket_data.items():
        # Check if team was filtered out
        if team_mapping is not None and key not in team_mapping:
            continue
        
        # Check if this team has any category selections
        if not has_category_selections(fields):
            print(f"{Fore.YELLOW}Skipping ticket for '{key}' - no categories selected{Style.RESET_ALL}")
            continue
        
        # Prepare ticket fields
        summary, description, additional_fields, team_project, team_issue_type = prepare_ticket_fields(
            fields, key, team_mapping, sheet_name
        )
        
        # Skip if no project key
        if not team_project:
            print(f"{Fore.YELLOW}Skipping ticket for '{key}' - no Project field specified{Style.RESET_ALL}")
            continue
        
        # Use team-specific issue type if available
        issue_type = team_issue_type if team_issue_type else default_issue_type
        
        # Build ticket data dictionary for CSV
        ticket_dict = {
            'Summary': summary,
            'Issue Type': issue_type,
            'Project Key': team_project,
            'Description': description
        }
        
        # Add priority if specified
        if priority:
            ticket_dict['Priority'] = priority
        
        # Add additional fields
        for field_name, field_value in additional_fields.items():
            # Skip internal flags
            if field_name == 'is_filtered_out':
                continue
            
            # Map field names to Jira-friendly names
            if field_name == ASSIGNEE_FIELD:
                ticket_dict['Assignee'] = field_value
            elif field_name == EPIC_LINK_FIELD:
                ticket_dict['Epic Link'] = field_value
            elif field_name == SPRINT_FIELD:
                ticket_dict['Sprint'] = format_sprint_value(field_value)
            elif field_name.lower() == 'component':
                ticket_dict['Component'] = field_value
            elif field_name.lower() == 'labels':
                ticket_dict['Labels'] = field_value
            else:
                # For custom fields or other fields, use the field name as-is
                ticket_dict[field_name] = field_value
        
        tickets_data.append(ticket_dict)
        
        # Display what would be exported
        print(f"{Fore.BLUE}Collecting ticket: '{summary}' for key '{key}' in project {team_project}{Style.RESET_ALL}")
    
    return tickets_data

def process_all_sheets_for_csv(args, file_path, available_sheets, default_issue_type, team_mapping, priority, custom_fields_mapping):
    """Process teams using Backstage API and collect ticket data for CSV export.
    
    Args:
        args: Command-line arguments
        file_path: Path to Excel file
        available_sheets: Available sheets in the Excel file
        default_issue_type: Default issue type
        team_mapping: Team information mapping
        priority: Priority value from config
        custom_fields_mapping: Custom field mappings
        
    Returns:
        List of all ticket dictionaries ready for CSV export
    """
    all_tickets_data = []
    
    # Read Excel config to get Backstage URL and categories
    excel_config = read_config_sheet(file_path)
    backstage_url = get_backstage_url_from_config(excel_config)
    categories = get_categories_from_config(excel_config)
    
    if not backstage_url:
        print(f"{Fore.RED}Error: Backstage URL not found in Config sheet.{Style.RESET_ALL}")
        print(f"{Fore.RED}Please add a 'Backstage' key with the base URL (e.g., https://backstage.core.cvent.org) to the Config sheet.{Style.RESET_ALL}")
        return all_tickets_data
    
    print(f"{Fore.CYAN}Using Backstage URL: {backstage_url}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Will process categories: {', '.join(categories)}{Style.RESET_ALL}")
    
    # Filter teams based on arguments
    filtered_team_mapping = filter_team_mapping(team_mapping, args)
    
    # Display team-specific projects and issue types for user information
    display_team_projects(filtered_team_mapping)
    display_team_issue_types(filtered_team_mapping)
    
    print(f"\n{Fore.CYAN}Collecting ticket data from Backstage for CSV export...{Style.RESET_ALL}")
    
    # Process each team using Backstage data
    for team_name, team_config in filtered_team_mapping.items():
        print(f"\n{Fore.CYAN}=== Processing team: {team_name} ==={Style.RESET_ALL}")
        
        # Get team's scorecard data from Backstage using configured categories
        team_categories = get_team_categories_from_backstage(backstage_url, team_name, categories)
        
        if team_categories:
            # Process each category that has data
            for category_name, category_data in team_categories.items():
                print(f"{Fore.CYAN}Processing category: {category_name} for team {team_name}{Style.RESET_ALL}")
                
                # Collect ticket data for this team/category combination
                ticket_data = collect_ticket_data_from_backstage(
                    team_name, category_name, category_data, team_config,
                    default_issue_type, priority
                )
                
                if ticket_data:
                    all_tickets_data.append(ticket_data)
        else:
            print(f"{Fore.YELLOW}No scorecard data found for team: {team_name}{Style.RESET_ALL}")
    
    print(f"\n{Fore.GREEN}Collected {len(all_tickets_data)} tickets for CSV export{Style.RESET_ALL}")
    return all_tickets_data

def collect_ticket_data_from_backstage(team_name, category_name, category_data, team_config, default_issue_type, priority):
    """Collect ticket data from Backstage for CSV export.
    
    Args:
        team_name: Name of the team
        category_name: Name of the category
        category_data: Category compliance data
        team_config: Team configuration from Teams sheet
        default_issue_type: Default issue type
        priority: Priority value
        
    Returns:
        Dictionary with ticket data for CSV export
    """
    # Prepare ticket data similar to existing logic
    summary = f"{team_name} Scorecards Improvement: {category_name}"
    
    # Build detailed description from compliance analysis
    description = f"*Backstage Scorecards Category:* {category_name}\\n\\n"
    
    if category_data and isinstance(category_data, dict):
        # Handle new detailed compliance analysis format
        if 'current_level' in category_data:
            current_level = category_data.get('current_level', 'L0')
            improvement_details = category_data.get('improvement_details', [])
            
            description += f"*Current Compliance Level:* {current_level}\\n\\n"
            
            if improvement_details:
                total_opportunities = len(improvement_details)
                description += f"*Improvement Opportunities:* ({total_opportunities} total)\\n\\n"
                
                # Group improvement details by level
                grouped_by_level = {}
                for detail in improvement_details:
                    level_category = detail.get('level_category', 'L1')
                    if level_category not in grouped_by_level:
                        grouped_by_level[level_category] = []
                    grouped_by_level[level_category].append(detail)
                
                # Sort levels
                sorted_levels = sorted(grouped_by_level.keys(), key=lambda x: (len(x), x))
                
                # Generate description grouped by level
                for level_category in sorted_levels:
                    level_details = grouped_by_level[level_category]
                    description += f"*{level_category} Issues:*\\n"
                    
                    for detail in level_details:
                        level_name = detail['level_name']
                        threshold = detail['threshold']
                        current_count = detail['current_count']
                        total_count = detail['total_count']
                        needed_count = detail['needed_count']
                        percentage = detail['percentage']
                        
                        description += f"* *{level_name}* - {threshold}:\\n"
                        description += f"** Current: {current_count}/{total_count} components ({percentage}%)\\n"
                        description += f"** Need to improve: {needed_count} additional components\\n\\n"
                    
                    description += "\\n"
            else:
                description += "Team is at maximum compliance level for this category.\\n"
        else:
            # Handle legacy format
            description += "*Address the Following Compliance Level(s):*\\n"
            for level, value in category_data.items():
                if value:
                    description += f"* {level}: {value}\\n"
    else:
        description += "No specific compliance levels identified.\\n"
    
    # Get project key and issue type from team config
    project_key = team_config.get(PROJECT_FIELD, '')
    team_issue_type = team_config.get(ISSUE_TYPE_KEY, default_issue_type)
    
    if not project_key:
        print(f"{Fore.YELLOW}Skipping ticket for '{team_name}' - no Project field specified{Style.RESET_ALL}")
        return None
    
    # Build ticket data dictionary
    ticket_dict = {
        'Summary': summary,
        'Issue Type': team_issue_type,
        'Project Key': project_key,
        'Description': description,
        'Sprint Team': team_name
    }
    
    # Add priority if specified
    if priority:
        ticket_dict['Priority'] = priority
    
    # Add optional fields from team config
    if ASSIGNEE_FIELD in team_config:
        ticket_dict['Assignee'] = team_config[ASSIGNEE_FIELD]
    
    if EPIC_LINK_FIELD in team_config:
        ticket_dict['Epic Link'] = team_config[EPIC_LINK_FIELD]
    
    if SPRINT_FIELD in team_config:
        ticket_dict['Sprint'] = format_sprint_value(team_config[SPRINT_FIELD])
    
    if 'Component' in team_config:
        ticket_dict['Component'] = team_config['Component']
    
    print(f"{Fore.BLUE}Collected ticket: '{summary}' for project {project_key}{Style.RESET_ALL}")
    
    return ticket_dict

def main():
    # Initialize colorama
    init()
    
    # Parse arguments
    args = parse_arguments()
    
    # Validate the Excel file
    if not validate_file(args.excel_file):
        return
    
    # Load JIRA config for later use
    config = load_config()
    
    # Get available sheets in the Excel file
    available_sheets = get_excel_sheets(args.excel_file)
    if not available_sheets:
        return
    
    print(f"{Fore.CYAN}Available sheets in {args.excel_file}: {', '.join(available_sheets)}{Style.RESET_ALL}")
    
    # Read configuration from Config sheet if it exists
    excel_config = {}
    if CONFIG_SHEET in available_sheets:
        excel_config = read_config_sheet(args.excel_file)
        print(f"{Fore.CYAN}Read configuration from Config sheet{Style.RESET_ALL}")
    
    # Use "Task" as the default issue type, individual teams can override
    issue_type = "Task"  # Default
    print(f"{Fore.CYAN}Using default issue type: {issue_type} (teams can override with '{ISSUE_TYPE_KEY}' column){Style.RESET_ALL}")
    
    # Get priority from Config sheet if available
    priority = None
    if PRIORITY_KEY in excel_config:
        priority = excel_config[PRIORITY_KEY]
        print(f"{Fore.CYAN}Using priority from Config sheet: {priority}{Style.RESET_ALL}")
    else:
        print(f"{Fore.CYAN}No priority specified in Config sheet, using Jira default{Style.RESET_ALL}")
    
    # Process Teams sheet
    team_mapping = process_teams_sheet(args.excel_file, available_sheets)
    if team_mapping is None:
        return
    
    # Report on team filtering if parameters are provided
    if args.processTeams:
        teams_to_process = [team.strip() for team in args.processTeams.split(',')]
        print(f"{Fore.CYAN}Team filter active: Will only process these teams: {', '.join(teams_to_process)}{Style.RESET_ALL}")
        # Create a case-insensitive lookup for team mapping
        team_mapping_lower = {key.lower(): key for key in team_mapping.keys()}
        # Warn about any teams that don't exist in the mapping (case-insensitive)
        missing_teams = [team for team in teams_to_process if team.lower() not in team_mapping_lower]
        if missing_teams:
            print(f"{Fore.YELLOW}Warning: Some specified teams not found in Teams sheet: {', '.join(missing_teams)}{Style.RESET_ALL}")
    elif args.excludeTeams:
        teams_to_exclude = [team.strip() for team in args.excludeTeams.split(',')]
        print(f"{Fore.CYAN}Team filter active: Will exclude these teams: {', '.join(teams_to_exclude)}{Style.RESET_ALL}")
        # Create a case-insensitive lookup for team mapping
        team_mapping_lower = {key.lower(): key for key in team_mapping.keys()}
        # Warn about any teams that don't exist in the mapping (case-insensitive)
        nonexistent_teams = [team for team in teams_to_exclude if team.lower() not in team_mapping_lower]
        if nonexistent_teams:
            print(f"{Fore.YELLOW}Warning: Some excluded teams not found in Teams sheet: {', '.join(nonexistent_teams)}{Style.RESET_ALL}")
    
    # Since we're using Backstage, we'll process all teams for all categories
    # Count teams for processing information
    team_count = len(team_mapping)
    category_count = len(SHEET_NAMES)  # Ownership, Quality, Security, Reliability
    
    print(f"{Fore.CYAN}Will query Backstage for {team_count} teams across {category_count} categories{Style.RESET_ALL}")
    
    # Check if CSV export mode is enabled
    if args.csv_file:
        if args.create:
            print(f"{Fore.YELLOW}Warning: --create flag is ignored when using CSV export mode{Style.RESET_ALL}")
        
        print(f"{Fore.CYAN}CSV Export Mode: Will export tickets to {args.csv_file}{Style.RESET_ALL}")
        
        # Load custom fields mapping if available
        custom_fields_mapping = None
        if CUSTOM_FIELDS_SHEET in available_sheets:
            custom_fields_mapping = read_custom_fields_mapping(args.excel_file)
        
        # Process all sheets and collect ticket data for CSV export
        all_tickets_data = process_all_sheets_for_csv(
            args, args.excel_file, available_sheets, issue_type, team_mapping, priority, custom_fields_mapping
        )
        
        # Export to CSV
        export_tickets_to_csv(args.csv_file, all_tickets_data, custom_fields_mapping)
        return
    
    # Confirm operation with user
    issue_type = confirm_operation(args, category_count, issue_type)
    if not issue_type:
        return
    
    # Create JIRA client
    try:
        jira_client = jira.JIRA(config["jira_server"], token_auth=(config["personal_access_token"]))
        # Add specific auth header if needed (some Jira functions require Bearer token format - for ticket creation)
        jira_client._session.headers.update({'Authorization': "BEARER {config['personal_access_token']}"})
    except Exception as e:
        print(f"{Fore.RED}Error connecting to Jira: {str(e)}{Style.RESET_ALL}")
        return
    
    # Process all sheets
    all_created_tickets, all_skipped_tickets, total_dry_run_count = process_all_sheets(
        args, args.excel_file, available_sheets, jira_client, issue_type, team_mapping, priority
    )
    
    # Display overall summary
    display_overall_summary(args.create, all_created_tickets, all_skipped_tickets, total_dry_run_count, issue_type, args)


if __name__ == "__main__":
    main()
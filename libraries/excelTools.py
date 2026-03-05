# Shared Excel reading utilities and team management functions
# Used by standardTicketCreator.py, codeAudit.py, and other scripts that read Excel team data
# pip install colorama pandas openpyxl

import os
import pandas as pd
from colorama import Fore, Style

# Constants
SPRINT_FIELD = "Sprint"


def validate_file(file_path):
    """Validate that the file exists and is an Excel file."""
    if not os.path.exists(file_path):
        print(f"{Fore.RED}Error: File not found: {file_path}{Style.RESET_ALL}")
        return False
    
    if not file_path.lower().endswith(('.xlsx', '.xls', '.xlsm')):
        print(f"{Fore.RED}Error: File is not an Excel file: {file_path}{Style.RESET_ALL}")
        return False
    
    return True


def get_excel_sheets(file_path):
    """Get list of all sheets in the Excel file."""
    try:
        xls = pd.ExcelFile(file_path)
        return xls.sheet_names
    except Exception as e:
        print(f"{Fore.RED}Error reading Excel sheets: {str(e)}{Style.RESET_ALL}")
        return None


def read_config_sheet(file_path, config_sheet_name="Config"):
    """Read a Config sheet from the Excel file to get configuration values.
    
    Args:
        file_path: Path to the Excel file
        config_sheet_name: Name of the config sheet (default: "Config")
        
    Returns:
        Dictionary of key-value configuration pairs
    """
    try:
        # Read the Config sheet
        df = pd.read_excel(file_path, sheet_name=config_sheet_name)
        
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
        print(f"{Fore.YELLOW}Warning: Could not read {config_sheet_name} sheet: {str(e)}. Using default values.{Style.RESET_ALL}")
        return {}


def get_backstage_url_from_config(config):
    """Extract Backstage URL from config dictionary."""
    backstage_url = config.get('Backstage')
    if backstage_url:
        return backstage_url.rstrip('/')  # Remove trailing slash
    return None


def filter_excel_columns(df):
    """Filter out columns labeled 'ColumnX'."""
    columns_to_keep = [col for col in df.columns if not col.startswith('Column')]
    return df[columns_to_keep]


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
    
    # Check if the transformation resulted in an empty DataFrame or missing required columns
    if result_df.empty:
        print(f"{Fore.YELLOW}Warning: No usable data found in sheet after transformation.{Style.RESET_ALL}")
        return None
    
    # Ensure required columns exist
    if not all(col in result_df.columns for col in ["Key", "Field", "Value"]):
        print(f"{Fore.YELLOW}Warning: Transformed data does not have the required column structure.{Style.RESET_ALL}")
        return None
        
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


def format_sprint_value(value):
    """Format Sprint value to ensure it's an integer, not a float.
    
    Args:
        value: The Sprint value (could be int, float, or string)
        
    Returns:
        Integer sprint value or original value if not numeric
    """
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
    
    return team_mapping


def filter_team_mapping(team_mapping, process_teams=None, exclude_teams=None):
    """Filter team mapping based on processTeams or excludeTeams.
    
    Args:
        team_mapping: Dictionary mapping team names to team data
        process_teams: Comma-separated string of teams to include (or None)
        exclude_teams: Comma-separated string of teams to exclude (or None)
        
    Returns:
        Filtered team mapping dictionary
    """
    filtered_mapping = team_mapping.copy()
    
    # If processTeams is provided, only include those teams (case insensitive)
    if process_teams:
        teams_to_process = [team.strip() for team in process_teams.split(',')]
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
    elif exclude_teams:
        teams_to_exclude = [team.strip().lower() for team in exclude_teams.split(',')]
        
        # Keep track of which teams are actually excluded (for better messaging)
        excluded_team_names = []
        
        # Filter the mapping using case-insensitive comparison
        filtered_mapping = {}
        for key, value in team_mapping.items():
            if key.lower() not in teams_to_exclude:
                filtered_mapping[key] = value
            else:
                excluded_team_names.append(key)
                
        print(f"{Fore.CYAN}Excluding specified teams: {', '.join(exclude_teams.split(','))}{Style.RESET_ALL}")
        if excluded_team_names:
            print(f"{Fore.CYAN}Actually excluded {len(excluded_team_names)} teams: {', '.join(excluded_team_names)}{Style.RESET_ALL}")
    
    # If filtered mapping is empty but original wasn't, provide a warning
    if not filtered_mapping and team_mapping:
        print(f"{Fore.YELLOW}Warning: No teams match the filter criteria.{Style.RESET_ALL}")
    
    return filtered_mapping

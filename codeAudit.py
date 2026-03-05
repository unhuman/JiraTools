#!/usr/bin/env python3
"""
Code Audit Script

This script audits code across repositories owned by teams listed in an Excel file.
For each team, it queries Backstage for owned application components, finds their
git repositories, fetches a specific file from each repo, and applies a regex to
find and report matches.

Usage:
    python codeAudit.py <excel_file> --checkFilename <file> --searchRegex <regex> [options]

Examples:
    # Audit build.gradle files for a specific dependency version
    python codeAudit.py --teams TeamA --checkFilename build.gradle --searchRegex 'spring-boot:(.+?)'

    # Audit only specific teams, export to CSV
    python codeAudit.py --teams "TeamA,TeamB" --checkFilename Dockerfile --searchRegex 'FROM (.+)' -o results.csv

Requirements:
    pip install colorama pandas openpyxl requests
    git must be available on PATH
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile

import requests
from colorama import init, Fore, Style

from libraries.jiraToolsConfig import load_config, get_backstage_url
from libraries.backstageTools import get_all_components, filter_components_for_team


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Audit code across team repositories using Backstage and git."
    )
    parser.add_argument(
        "--teams",
        required=True,
        help="Comma-separated list of team names to audit"
    )
    parser.add_argument(
        "--backstageUrl",
        help="Backstage base URL (overrides backstageUrl in ~/.jiraTools config)"
    )
    parser.add_argument(
        "--checkFilename",
        required=True,
        help="File path to look for in each repository (e.g., build.gradle, Dockerfile)"
    )
    parser.add_argument(
        "--searchRegex",
        required=True,
        help="Regex pattern with exactly one capture group to apply to each file"
    )
    parser.add_argument(
        "-o", "--output",
        metavar="FILE",
        help="Export results to a CSV file"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed git operation logging for debugging"
    )

    return parser.parse_args()


def validate_regex(pattern_str):
    """Validate that the regex compiles and has at least one capture group.
    
    Args:
        pattern_str: Raw regex string
        
    Returns:
        Compiled regex pattern, or None if invalid
    """
    try:
        compiled = re.compile(pattern_str, re.DOTALL)
    except re.error as e:
        print(f"{Fore.RED}Error: Invalid regex '{pattern_str}': {e}{Style.RESET_ALL}")
        return None

    if compiled.groups < 1:
        print(f"{Fore.RED}Error: searchRegex must have at least one capture group, "
              f"but found {compiled.groups}.{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}Example: 'spring-boot:(.+?)' has one capture group.{Style.RESET_ALL}")
        return None

    return compiled


def get_component_repo_url(backstage_url, component_name, timeout=30):
    """Get the git repository URL for a Backstage component.
    
    Queries the Backstage catalog API for the component entity and extracts
    the git-repository-url annotation.
    
    Args:
        backstage_url: Base URL for Backstage instance
        component_name: Name of the component
        timeout: Request timeout in seconds
        
    Returns:
        Tuple of (git_url, repo_display_name) or (None, None) if not found
    """
    url = f"{backstage_url}/api/catalog/entities/by-name/component/default/{component_name}"

    try:
        response = requests.get(url, timeout=timeout)
        response.raise_for_status()

        entity = response.json()
        annotations = entity.get('metadata', {}).get('annotations', {})
        git_url = annotations.get('git-repository-url')

        if not git_url:
            return None, None

        # Convert HTTPS URLs to SSH for authentication with private repos
        git_url = normalize_git_url_to_ssh(git_url)

        # Extract display name from git URL
        # Format: git@github.com:org/repo.git -> org/repo
        repo_display = extract_repo_name(git_url)
        return git_url, repo_display

    except requests.exceptions.RequestException as e:
        print(f"{Fore.YELLOW}  Warning: Could not fetch component '{component_name}': {e}{Style.RESET_ALL}")
        return None, None


def extract_repo_name(git_url):
    """Extract a human-readable repo name from a git URL.
    
    Handles formats:
        git@github.com:org/repo.git -> org/repo
        https://github.com/org/repo.git -> org/repo
        https://github.com/org/repo -> org/repo
    """
    # SSH format: git@github.com:org/repo.git
    ssh_match = re.match(r'git@[^:]+:(.+?)(?:\.git)?$', git_url)
    if ssh_match:
        return ssh_match.group(1)

    # HTTPS format: https://github.com/org/repo.git
    https_match = re.match(r'https?://[^/]+/(.+?)(?:\.git)?$', git_url)
    if https_match:
        return https_match.group(1)

    return git_url


def normalize_git_url_to_ssh(git_url):
    """Convert HTTPS git URLs to SSH format for authentication.
    
    HTTPS URLs to private/internal repos fail without credentials,
    but SSH URLs use local SSH keys which typically have access.
    
    Converts: https://github.com/org/repo[.git] -> git@github.com:org/repo.git
    Already-SSH URLs are returned unchanged.
    """
    https_match = re.match(r'https?://([^/]+)/(.+?)(?:\.git)?/?$', git_url)
    if https_match:
        host = https_match.group(1)
        path = https_match.group(2)
        return f"git@{host}:{path}.git"
    return git_url


def _run_git(cmd, cwd, timeout, verbose, step_label):
    """Run a git command, optionally logging details.
    
    Returns:
        subprocess.CompletedProcess result
    """
    if verbose:
        print(f"\n      {Fore.MAGENTA}[git] {step_label}: {' '.join(cmd)}{Style.RESET_ALL}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if verbose:
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                print(f"        stdout: {line}")
        if result.stderr.strip():
            for line in result.stderr.strip().splitlines():
                print(f"        stderr: {line}")
        if result.returncode != 0:
            print(f"        {Fore.RED}exit code: {result.returncode}{Style.RESET_ALL}")
    return result


def fetch_file_from_repo(git_url, file_path, verbose=False):
    """Fetch a specific file from a git repository using sparse checkout.
    
    Initializes a fresh git repo, configures sparse checkout via the classic
    .git/info/sparse-checkout file, and pulls from the remote. Tries 'main'
    branch first, then falls back to 'master'.
    
    Args:
        git_url: Git clone URL (SSH or HTTPS)
        file_path: Path to the file within the repository
        verbose: If True, log all git commands and their output
        
    Returns:
        File contents as a string, or None if the file doesn't exist or pull fails
    """
    temp_dir = tempfile.mkdtemp(prefix="codeAudit_")
    if verbose:
        print(f"\n      {Fore.MAGENTA}[git] temp dir: {temp_dir}{Style.RESET_ALL}")

    try:
        # Initialize a new repo
        result = _run_git(["git", "init"], temp_dir, 30, verbose, "init")
        if result.returncode != 0:
            return None

        # Add the remote
        result = _run_git(
            ["git", "remote", "add", "origin", git_url],
            temp_dir, 30, verbose, "remote add"
        )
        if result.returncode != 0:
            return None

        # Enable sparse checkout
        result = _run_git(
            ["git", "config", "core.sparseCheckout", "true"],
            temp_dir, 30, verbose, "config sparseCheckout"
        )
        if result.returncode != 0:
            return None

        # Write the target file path into sparse-checkout config
        sparse_checkout_file = os.path.join(temp_dir, ".git", "info", "sparse-checkout")
        os.makedirs(os.path.dirname(sparse_checkout_file), exist_ok=True)
        with open(sparse_checkout_file, 'w') as f:
            f.write(file_path + "\n")
        if verbose:
            print(f"      {Fore.MAGENTA}[git] sparse-checkout file contents: '{file_path}'{Style.RESET_ALL}")

        # Pull the remote's default branch (HEAD)
        result = _run_git(
            ["git", "pull", "--depth", "1", "origin", "HEAD"],
            temp_dir, 60, verbose, "pull origin HEAD"
        )
        if result.returncode != 0:
            return None

        # Check what files exist
        target = os.path.join(temp_dir, file_path)
        if verbose:
            print(f"      {Fore.MAGENTA}[git] Checking for file: {target}{Style.RESET_ALL}")
            print(f"      {Fore.MAGENTA}[git] File exists: {os.path.isfile(target)}{Style.RESET_ALL}")
            # List what was actually checked out
            all_files = []
            for root, dirs, files in os.walk(temp_dir):
                # Skip .git directory
                if '.git' in root:
                    continue
                for fname in files:
                    rel = os.path.relpath(os.path.join(root, fname), temp_dir)
                    all_files.append(rel)
            if all_files:
                print(f"      {Fore.MAGENTA}[git] Files checked out: {', '.join(all_files)}{Style.RESET_ALL}")
            else:
                print(f"      {Fore.MAGENTA}[git] No files were checked out{Style.RESET_ALL}")

        if os.path.isfile(target):
            with open(target, 'r', errors='replace') as f:
                return f.read()
        return None

    except subprocess.TimeoutExpired:
        print(f"{Fore.YELLOW}  Warning: Git operation timed out for {git_url}{Style.RESET_ALL}")
        return None
    except Exception as e:
        print(f"{Fore.YELLOW}  Warning: Error fetching file from {git_url}: {e}{Style.RESET_ALL}")
        return None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def build_match_display(regex_str, captured_value):
    """Build a display string by replacing the capture group in the regex with the captured value.
    
    Finds the first top-level (...) group in the regex string and replaces it
    with the actual captured value.
    
    Args:
        regex_str: Original regex string (e.g., 'FROM (.+)')
        captured_value: Value captured by the group
        
    Returns:
        Display string with the group replaced (e.g., 'FROM ubuntu:22.04')
    """
    # Find the first top-level capture group by tracking parenthesis depth
    depth = 0
    group_start = None

    i = 0
    while i < len(regex_str):
        ch = regex_str[i]

        # Skip escaped characters
        if ch == '\\':
            i += 2
            continue

        # Skip character classes [...]
        if ch == '[':
            i += 1
            while i < len(regex_str) and regex_str[i] != ']':
                if regex_str[i] == '\\':
                    i += 1
                i += 1
            i += 1
            continue

        if ch == '(':
            # Skip non-capturing groups (?...) but capture regular (...)
            if (i + 1 < len(regex_str) and regex_str[i + 1] == '?'):
                depth += 1
            else:
                if depth == 0 and group_start is None:
                    group_start = i
                depth += 1

            i += 1
            continue

        if ch == ')':
            depth -= 1
            if depth == 0 and group_start is not None:
                # Found the matching close of our capture group
                return regex_str[:group_start] + captured_value + regex_str[i + 1:]
            i += 1
            continue

        i += 1

    # Fallback: just return the captured value
    return captured_value


def extract_capture_groups(regex_str):
    """Extract the text of each capture group from a regex string.
    
    Parses the regex string to find top-level capturing groups (skipping
    non-capturing (?:...) groups) and returns the raw text of each.
    
    Args:
        regex_str: Original regex string
        
    Returns:
        List of group text strings, e.g. ['.*?', '.+'] for '(.*?)foo(.+)'
    """
    groups = []
    depth = 0
    group_start = None
    capture_depth = None

    i = 0
    while i < len(regex_str):
        ch = regex_str[i]

        # Skip escaped characters
        if ch == '\\':
            i += 2
            continue

        # Skip character classes [...]
        if ch == '[':
            i += 1
            while i < len(regex_str) and regex_str[i] != ']':
                if regex_str[i] == '\\':
                    i += 1
                i += 1
            i += 1
            continue

        if ch == '(':
            if i + 1 < len(regex_str) and regex_str[i + 1] == '?':
                # Non-capturing group
                depth += 1
            else:
                # Capturing group
                depth += 1
                if capture_depth is None:
                    group_start = i
                    capture_depth = depth
            i += 1
            continue

        if ch == ')':
            if capture_depth is not None and depth == capture_depth:
                # End of a top-level capture group
                groups.append(regex_str[group_start:i + 1])
                group_start = None
                capture_depth = None
            depth -= 1
            i += 1
            continue

        i += 1

    return groups


def main():
    init()

    args = parse_arguments()

    # Validate the regex
    compiled_regex = validate_regex(args.searchRegex)
    if compiled_regex is None:
        sys.exit(1)

    # Get Backstage URL from CLI or config file
    config = load_config()
    backstage_url = get_backstage_url(config, args.backstageUrl)
    if not backstage_url:
        print(f"{Fore.RED}Error: No Backstage URL configured.{Style.RESET_ALL}")
        print(f"{Fore.RED}Set 'backstageUrl' in ~/.jiraTools or pass --backstageUrl.{Style.RESET_ALL}")
        sys.exit(1)

    # Parse team names from CLI
    teams = [t.strip() for t in args.teams.split(",") if t.strip()]
    if not teams:
        print(f"{Fore.RED}Error: No valid team names provided.{Style.RESET_ALL}")
        sys.exit(1)

    # Fetch all Backstage components once
    print(f"\n{Fore.CYAN}Fetching all components from Backstage...{Style.RESET_ALL}")
    all_components = get_all_components(backstage_url)
    if not all_components:
        print(f"{Fore.RED}Error: No components found in Backstage.{Style.RESET_ALL}")
        sys.exit(1)
    print(f"{Fore.GREEN}Fetched {len(all_components)} components from Backstage{Style.RESET_ALL}")

    # Process each team
    results = []
    teams_not_found = []
    teams_processed = 0
    repos_checked = 0
    total_matches = 0

    for team_name in sorted(teams):
        print(f"\n{Fore.CYAN}Processing team: {team_name}{Style.RESET_ALL}")

        # Get application components owned by this team
        team_components = filter_components_for_team(all_components, team_name)
        if not team_components:
            print(f"  No application components found for team {team_name}")
            teams_not_found.append(team_name)
            continue

        component_names = [comp.get('metadata', {}).get('name', '') for comp in team_components]
        print(f"  Found {len(component_names)} application(s): {', '.join(component_names)}")

        # Collect unique repo URLs to avoid cloning the same repo twice
        repos = {}  # git_url -> (repo_display, [component_names])
        for comp_name in component_names:
            if not comp_name:
                continue
            git_url, repo_display = get_component_repo_url(backstage_url, comp_name)
            if git_url:
                if git_url not in repos:
                    repos[git_url] = (repo_display, [])
                repos[git_url][1].append(comp_name)
            else:
                print(f"{Fore.YELLOW}  No git repository found for component: {comp_name}{Style.RESET_ALL}")

        if not repos:
            print(f"  No repositories found for team {team_name}")
            continue

        deduped = len(component_names) - len(repos)
        dedup_msg = f" ({deduped} duplicate(s) removed)" if deduped > 0 else ""
        print(f"  Checking {len(repos)} unique repository(ies) for '{args.checkFilename}'{dedup_msg}")
        teams_processed += 1

        # Fetch and analyze files from each repo
        for git_url, (repo_display, comp_names) in repos.items():
            repos_checked += 1
            print(f"  [{repos_checked}] Cloning {Fore.BLUE}{repo_display}{Style.RESET_ALL} ...", end=" " if not args.verbose else "\n", flush=True)
            content = fetch_file_from_repo(git_url, args.checkFilename, verbose=args.verbose)

            prefix = "  Result: " if args.verbose else ""
            if content is None:
                print(f"{prefix}{Fore.YELLOW}'{args.checkFilename}' not found{Style.RESET_ALL}")
                continue

            # Apply regex
            match_list = list(compiled_regex.finditer(content))
            if not match_list:
                print(f"{prefix}found file, {Fore.YELLOW}no regex matches{Style.RESET_ALL}")
                continue

            print(f"{prefix}found file, {Fore.GREEN}{len(match_list)} match(es){Style.RESET_ALL}")
            for match in match_list:
                groups = match.groups()
                captured = ", ".join(groups)
                results.append((team_name, repo_display) + groups)
                total_matches += 1
                print(f"    {Fore.GREEN}{team_name}{Style.RESET_ALL} | "
                      f"{Fore.BLUE}{repo_display}{Style.RESET_ALL} | "
                      f"{captured}")

    # Consolidated results
    if results:
        print(f"\n{Fore.CYAN}{'=' * 60}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Results:{Style.RESET_ALL}")
        for row in results:
            team_name_r = row[0]
            repo_display_r = row[1]
            captured_r = ", ".join(row[2:])
            print(f"  {Fore.GREEN}{team_name_r}{Style.RESET_ALL} | "
                  f"{Fore.BLUE}{repo_display_r}{Style.RESET_ALL} | "
                  f"{captured_r}")

    # Summary
    print(f"\n{Fore.CYAN}{'=' * 60}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Summary:{Style.RESET_ALL}")
    print(f"  Teams processed: {teams_processed}")
    print(f"  Repositories checked: {repos_checked}")
    print(f"  Total matches: {total_matches}")

    if teams_not_found:
        print(f"\n{Fore.RED}Error: Teams not found in Backstage: {', '.join(teams_not_found)}{Style.RESET_ALL}")

    # CSV export
    if args.output and results:
        num_groups = compiled_regex.groups
        group_texts = extract_capture_groups(args.searchRegex)
        if num_groups == 1:
            match_headers = [group_texts[0]] if group_texts else ["Match"]
        else:
            match_headers = group_texts if len(group_texts) == num_groups else [f"Match{i+1}" for i in range(num_groups)]
        with open(args.output, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Team", "Repository"] + match_headers)
            writer.writerows(results)
        print(f"\n{Fore.GREEN}Results exported to {args.output}{Style.RESET_ALL}")
    elif args.output and not results:
        print(f"\n{Fore.YELLOW}No results to export to CSV.{Style.RESET_ALL}")


if __name__ == "__main__":
    main()

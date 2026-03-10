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
    pip install aiohttp colorama pandas openpyxl requests
    git must be available on PATH
"""

import argparse
import asyncio
import csv
from datetime import datetime, timedelta
import gc
import os
import re
import shutil
import subprocess
import sys
import tempfile

import aiohttp
import requests
from colorama import init, Fore, Style

from libraries.jiraToolsConfig import load_config, get_backstage_url
from libraries.backstageTools import get_all_components, get_all_teams, filter_components_for_team
from libraries.excelTools import process_teams_sheet, get_excel_sheets
from libraries.jiraTicketTools import (
    ASSIGNEE_FIELD, EPIC_LINK_FIELD,
    create_jira_ticket, link_to_epic,
)


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Audit code across team repositories using Backstage and git."
    )
    parser.add_argument(
        "--teams",
        required=True,
        help="Comma-separated list of team names to audit, or 'all' / '*' to audit all teams in Backstage"
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
    parser.add_argument(
        "--compare-repo",
        help="Git repo URL to fetch tags from for version date comparison"
    )
    parser.add_argument(
        "--dateTolerance",
        help="Max age for compliance (e.g., 2d=2 days, 3m=3 months, 1y=1 year). Requires --compare-repo"
    )
    parser.add_argument(
        "--createTickets",
        metavar="EXCEL_FILE",
        help="Excel file with Teams sheet for Jira ticket creation (requires --compare-repo, --dateTolerance, --dependencyName)"
    )
    parser.add_argument(
        "--dependencyName",
        help="Dependency name for ticket summaries (e.g., 'Spring Boot'). Requires --createTickets"
    )
    parser.add_argument(
        "-c", "--create",
        action="store_true",
        help="Actually create tickets in Jira (default is dry-run mode)"
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=5,
        help="Number of parallel workers for async operations (default: 5, max: 15)"
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


def parse_date_tolerance(tolerance_str):
    """Parse a date tolerance string into a timedelta.

    Supported formats: Nd (days), Nm (months, approx 30 days), Ny (years, approx 365 days).

    Args:
        tolerance_str: String like '2d', '3m', '1y'

    Returns:
        timedelta, or None if invalid
    """
    match = re.match(r'^(\d+)([dmy])$', tolerance_str)
    if not match:
        print(f"{Fore.RED}Error: Invalid dateTolerance '{tolerance_str}'. "
              f"Use format like 2d, 3m, 1y.{Style.RESET_ALL}")
        return None
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == 'd':
        return timedelta(days=amount)
    elif unit == 'm':
        return timedelta(days=amount * 30)
    else:  # 'y'
        return timedelta(days=amount * 365)


def extract_semver(tag_name):
    """Extract a semantic version (Major.Minor.Patch) from a tag name.

    Ignores any prefix or suffix text around the version number.

    Args:
        tag_name: Tag string (e.g., 'v1.2.3', 'release-1.2.3-rc1')

    Returns:
        Version string like '1.2.3', or None if no semver found
    """
    match = re.search(r'(\d+\.\d+\.\d+)', tag_name)
    return match.group(1) if match else None


def fetch_repo_tags(repo_url, verbose=False):
    """Fetch all tags and their creation dates from a git repository.

    Uses a temporary directory to init a repo, fetch tags, and parse
    the output of git for-each-ref.

    Args:
        repo_url: Git clone URL (SSH or HTTPS)
        verbose: If True, log git commands and output

    Returns:
        Dict mapping semantic version string to date string (e.g., {'1.2.3': '2025-06-15'})
    """
    repo_url = normalize_git_url_to_ssh(repo_url)
    temp_dir = tempfile.mkdtemp(prefix="codeAudit_tags_")
    if verbose:
        print(f"\n  {Fore.MAGENTA}[git] tag temp dir: {temp_dir}{Style.RESET_ALL}")

    try:
        result = _run_git(["git", "init"], temp_dir, 30, verbose, "init")
        if result.returncode != 0:
            return {}

        result = _run_git(
            ["git", "fetch", "--tags", repo_url],
            temp_dir, 120, verbose, "fetch --tags"
        )
        if result.returncode != 0:
            if _is_permission_error(result):
                print(f"{Fore.YELLOW}Warning: Permission denied fetching tags from {repo_url}{Style.RESET_ALL}")
            return {}

        result = _run_git(
            ["git", "for-each-ref", "--sort=-creatordate",
             "--format", "%(refname:short) %(creatordate:short)", "refs/tags"],
            temp_dir, 30, verbose, "for-each-ref tags"
        )
        if result.returncode != 0:
            return {}

        version_dates = {}
        for line in result.stdout.strip().splitlines():
            if not line.strip():
                continue
            # Format: "tag_name YYYY-MM-DD"
            parts = line.rsplit(' ', 1)
            if len(parts) != 2:
                continue
            tag_name, date_str = parts
            version = extract_semver(tag_name)
            if version and version not in version_dates:
                version_dates[version] = date_str

        if verbose:
            print(f"  {Fore.MAGENTA}[git] Found {len(version_dates)} semver tag(s){Style.RESET_ALL}")
            for ver, date in list(version_dates.items())[:10]:
                print(f"    {ver} -> {date}")
            if len(version_dates) > 10:
                print(f"    ... and {len(version_dates) - 10} more")

        return version_dates

    except subprocess.TimeoutExpired:
        print(f"{Fore.YELLOW}Warning: Git operation timed out fetching tags from {repo_url}{Style.RESET_ALL}")
        return {}
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Error fetching tags from {repo_url}: {e}{Style.RESET_ALL}")
        return {}
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


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
    
    Sets GIT_TERMINAL_PROMPT=0 to prevent interactive password prompts.
    
    Returns:
        subprocess.CompletedProcess result
    """
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    if verbose:
        print(f"\n      {Fore.MAGENTA}[git] {step_label}: {' '.join(cmd)}{Style.RESET_ALL}")
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, env=env)
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


def _is_permission_error(result):
    """Check if a git command result indicates a permission/authentication error."""
    if result.returncode == 0:
        return False
    stderr = result.stderr.lower()
    permission_indicators = [
        "permission denied",
        "could not read from remote",
        "authentication failed",
        "host key verification failed",
        "fatal: could not read username",
        "terminal prompts disabled",
    ]
    return any(indicator in stderr for indicator in permission_indicators)


def _is_async_permission_error(returncode, stderr):
    """Check if an async git command result indicates a permission/authentication error."""
    if returncode == 0:
        return False
    stderr_lower = stderr.lower()
    permission_indicators = [
        "permission denied",
        "could not read from remote",
        "authentication failed",
        "host key verification failed",
        "fatal: could not read username",
        "terminal prompts disabled",
    ]
    return any(indicator in stderr_lower for indicator in permission_indicators)


async def _async_run_git(cmd, cwd, timeout, verbose, step_label, log_lines=None):
    """Run a git command asynchronously, optionally logging details.

    Sets GIT_TERMINAL_PROMPT=0 to prevent interactive password prompts.
    When log_lines is provided, appends log messages there instead of printing.

    Returns:
        Tuple of (returncode, stdout, stderr)
    """
    def _log(msg):
        if log_lines is not None:
            log_lines.append(msg)
        else:
            print(msg)

    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_SSH_COMMAND"] = "ssh -o BatchMode=yes"
    if verbose:
        _log(f"\n      {Fore.MAGENTA}[git] {step_label}: {' '.join(cmd)}{Style.RESET_ALL}")
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=cwd, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, env=env,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        stdout = stdout_bytes.decode(errors='replace')
        stderr = stderr_bytes.decode(errors='replace')
        returncode = proc.returncode
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise subprocess.TimeoutExpired(cmd, timeout)

    if verbose:
        if stdout.strip():
            for line in stdout.strip().splitlines():
                _log(f"        stdout: {line}")
        if stderr.strip():
            for line in stderr.strip().splitlines():
                _log(f"        stderr: {line}")
        if returncode != 0:
            _log(f"        {Fore.RED}exit code: {returncode}{Style.RESET_ALL}")
    return returncode, stdout, stderr


async def async_get_component_repo_url(session, backstage_url, component_name, timeout=30, log_lines=None):
    """Async version of get_component_repo_url using an aiohttp session.

    Args:
        session: aiohttp.ClientSession instance
        backstage_url: Base URL for Backstage instance
        component_name: Name of the component
        timeout: Request timeout in seconds
        log_lines: If provided, append log messages here instead of printing

    Returns:
        Tuple of (git_url, repo_display_name) or (None, None) if not found
    """
    def _log(msg):
        if log_lines is not None:
            log_lines.append(msg)
        else:
            print(msg)

    url = f"{backstage_url}/api/catalog/entities/by-name/component/default/{component_name}"
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as response:
            response.raise_for_status()
            entity = await response.json()
            annotations = entity.get('metadata', {}).get('annotations', {})
            git_url = annotations.get('git-repository-url')
            if not git_url:
                return None, None
            git_url = normalize_git_url_to_ssh(git_url)
            repo_display = extract_repo_name(git_url)
            return git_url, repo_display
    except Exception as e:
        _log(f"{Fore.YELLOW}  Warning: Could not fetch component '{component_name}': {e}{Style.RESET_ALL}")
        return None, None


async def async_fetch_file_from_repo(git_url, file_path, verbose=False, log_lines=None):
    """Async version of fetch_file_from_repo using asyncio subprocesses.

    Uses sparse checkout to fetch a single file from a git repository.

    Args:
        git_url: Git clone URL (SSH or HTTPS)
        file_path: Path to the file within the repository
        verbose: If True, log all git commands and their output
        log_lines: If provided, append log messages here instead of printing

    Returns:
        File contents as a string, None if not found, or 'PERMISSION_DENIED'
    """
    def _log(msg):
        if log_lines is not None:
            log_lines.append(msg)
        else:
            print(msg)

    temp_dir = tempfile.mkdtemp(prefix="codeAudit_")
    if verbose:
        _log(f"\n      {Fore.MAGENTA}[git] temp dir: {temp_dir}{Style.RESET_ALL}")

    try:
        # Initialize a new repo
        rc, _, _ = await _async_run_git(["git", "init"], temp_dir, 30, verbose, "init", log_lines)
        if rc != 0:
            return None

        # Add the remote
        rc, _, _ = await _async_run_git(
            ["git", "remote", "add", "origin", git_url],
            temp_dir, 30, verbose, "remote add", log_lines
        )
        if rc != 0:
            return None

        # Enable sparse checkout
        rc, _, _ = await _async_run_git(
            ["git", "config", "core.sparseCheckout", "true"],
            temp_dir, 30, verbose, "config sparseCheckout", log_lines
        )
        if rc != 0:
            return None

        # Write the target file path into sparse-checkout config
        sparse_checkout_file = os.path.join(temp_dir, ".git", "info", "sparse-checkout")
        os.makedirs(os.path.dirname(sparse_checkout_file), exist_ok=True)
        with open(sparse_checkout_file, 'w') as f:
            f.write(file_path + "\n")
        if verbose:
            _log(f"      {Fore.MAGENTA}[git] sparse-checkout file contents: '{file_path}'{Style.RESET_ALL}")

        # Pull the remote's default branch (HEAD)
        rc, stdout, stderr = await _async_run_git(
            ["git", "pull", "--depth", "1", "origin", "HEAD"],
            temp_dir, 60, verbose, "pull origin HEAD", log_lines
        )
        if rc != 0:
            if _is_async_permission_error(rc, stderr):
                return "PERMISSION_DENIED"
            return None

        # Check what files exist
        target = os.path.join(temp_dir, file_path)
        if verbose:
            _log(f"      {Fore.MAGENTA}[git] Checking for file: {target}{Style.RESET_ALL}")
            _log(f"      {Fore.MAGENTA}[git] File exists: {os.path.isfile(target)}{Style.RESET_ALL}")
            all_files = []
            for root, dirs, files in os.walk(temp_dir):
                if '.git' in root:
                    continue
                for fname in files:
                    rel = os.path.relpath(os.path.join(root, fname), temp_dir)
                    all_files.append(rel)
            if all_files:
                _log(f"      {Fore.MAGENTA}[git] Files checked out: {', '.join(all_files)}{Style.RESET_ALL}")
            else:
                _log(f"      {Fore.MAGENTA}[git] No files were checked out{Style.RESET_ALL}")

        if os.path.isfile(target):
            with open(target, 'r', errors='replace') as f:
                return f.read()
        return None

    except subprocess.TimeoutExpired:
        _log(f"{Fore.YELLOW}  Warning: Git operation timed out for {git_url}{Style.RESET_ALL}")
        return None
    except Exception as e:
        _log(f"{Fore.YELLOW}  Warning: Error fetching file from {git_url}: {e}{Style.RESET_ALL}")
        return None
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


async def async_gather_repo_urls(session, backstage_url, component_names, semaphore, team_log_lines=None):
    """Fetch repo URLs for multiple components concurrently.

    Args:
        session: aiohttp.ClientSession instance
        backstage_url: Base URL for Backstage instance
        component_names: List of component names to query
        semaphore: asyncio.Semaphore for concurrency limiting
        team_log_lines: If provided, append log messages here instead of printing

    Returns:
        Dict mapping git_url -> (repo_display, [component_names]), with deduplication
    """
    def _log(msg):
        if team_log_lines is not None:
            team_log_lines.append(msg)
        else:
            print(msg)

    async def _fetch_one(comp_name):
        log_lines = []
        async with semaphore:
            result = await async_get_component_repo_url(session, backstage_url, comp_name, log_lines=log_lines)
            return comp_name, result, log_lines

    tasks = [_fetch_one(name) for name in component_names if name]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    repos = {}
    for result in results:
        if isinstance(result, Exception):
            _log(f"{Fore.YELLOW}  Warning: Unexpected error during repo URL fetch: {result}{Style.RESET_ALL}")
            continue
        comp_name, (git_url, repo_display), log_lines = result
        for line in log_lines:
            _log(line)
        if git_url:
            if git_url not in repos:
                repos[git_url] = (repo_display, [])
            repos[git_url][1].append(comp_name)
        else:
            _log(f"{Fore.YELLOW}  No git repository found for component: {comp_name}{Style.RESET_ALL}")
    return repos


async def async_process_repos(repos, check_filename, compiled_regex, team_name, team_idx,
                              total_teams, semaphore, verbose, team_log_lines=None):
    """Fetch files from multiple repos concurrently and apply regex.

    Each task buffers its log output; after all tasks complete, logs are
    printed sequentially in repo order to avoid interleaved output.

    Args:
        repos: Dict mapping git_url -> (repo_display, [component_names])
        check_filename: File path to look for in each repo
        compiled_regex: Compiled regex pattern to apply
        team_name: Team name for display
        team_idx: Current team index for progress display
        total_teams: Total number of teams for progress display
        semaphore: asyncio.Semaphore for concurrency limiting
        verbose: If True, log git operation details
        team_log_lines: If provided, append log messages here instead of printing

    Returns:
        Tuple of (results_list, repos_permission_denied_list, repos_checked_count)
    """
    def _log(msg):
        if team_log_lines is not None:
            team_log_lines.append(msg)
        else:
            print(msg)

    results = []
    repos_permission_denied = []
    repos_checked = 0
    total_repos_in_team = len(repos)

    async def _process_one(repo_idx, git_url, repo_display, comp_names):
        log_lines = []
        async with semaphore:
            progress = f"Team: {team_idx}/{total_teams} ({team_name}), App {repo_idx}/{total_repos_in_team}"
            log_lines.append(
                f"  [{progress}] Cloning {Fore.BLUE}{repo_display}{Style.RESET_ALL} ..."
            )
            content = await async_fetch_file_from_repo(git_url, check_filename, verbose=verbose, log_lines=log_lines)
            return repo_idx, git_url, repo_display, comp_names, content, log_lines

    tasks = [
        _process_one(idx, git_url, repo_display, comp_names)
        for idx, (git_url, (repo_display, comp_names)) in enumerate(repos.items(), 1)
    ]
    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    for result in task_results:
        if isinstance(result, Exception):
            _log(f"{Fore.YELLOW}  Warning: Unexpected error during repo processing: {result}{Style.RESET_ALL}")
            repos_checked += 1
            continue
        repo_idx, git_url, repo_display, comp_names, content, log_lines = result

        # Emit all buffered log lines for this repo
        for line in log_lines:
            _log(line)

        repos_checked += 1
        prefix = "  Result: " if verbose else ""

        if content == "PERMISSION_DENIED":
            _log(f"{prefix}{Fore.YELLOW}permission denied — {repo_display}{Style.RESET_ALL}")
            repos_permission_denied.append(repo_display)
            continue
        if content is None:
            _log(f"{prefix}{Fore.YELLOW}'{check_filename}' not found — {repo_display}{Style.RESET_ALL}")
            continue

        match_list = list(compiled_regex.finditer(content))
        if not match_list:
            _log(f"{prefix}found file, {Fore.YELLOW}no regex matches — {repo_display}{Style.RESET_ALL}")
            continue

        _log(f"{prefix}found file, {Fore.GREEN}{len(match_list)} match(es) — {repo_display}{Style.RESET_ALL}")
        for match in match_list:
            groups = match.groups()
            captured = ", ".join(groups)
            results.append((team_name, repo_display) + groups)
            _log(f"    {Fore.GREEN}{team_name}{Style.RESET_ALL} | "
                 f"{Fore.BLUE}{repo_display}{Style.RESET_ALL} | "
                 f"{captured}")

    return results, repos_permission_denied, repos_checked


async def async_process_team(session, backstage_url, all_components, team_name, team_idx,
                             total_teams, check_filename, compiled_regex, semaphore, verbose):
    """Process one team end-to-end: URL gathering + repo processing.

    All output is buffered into log_lines so that cross-team async execution
    does not produce interleaved console output.

    Args:
        session: aiohttp.ClientSession instance
        backstage_url: Base URL for Backstage instance
        all_components: Pre-fetched list of all Backstage components
        team_name: Team name to process
        team_idx: Current team index for progress display
        total_teams: Total number of teams for progress display
        check_filename: File path to look for in each repo
        compiled_regex: Compiled regex pattern to apply
        semaphore: asyncio.Semaphore for concurrency limiting
        verbose: If True, log git operation details

    Returns:
        Dict with keys: team_name, log_lines, results, perm_denied,
        repos_checked, team_not_found, team_processed
    """
    log_lines = []
    log_lines.append(f"\n{Fore.CYAN}Processing team: {team_name}{Style.RESET_ALL}")

    # Get components owned by this team (local filter, no I/O)
    team_components = filter_components_for_team(all_components, team_name, comp_type=None)
    if not team_components:
        log_lines.append(f"  No components found for team {team_name}")
        return {
            'team_name': team_name,
            'log_lines': log_lines,
            'results': [],
            'perm_denied': [],
            'repos_checked': 0,
            'team_not_found': True,
            'team_processed': False,
        }

    component_names = [comp.get('metadata', {}).get('name', '') for comp in team_components]
    log_lines.append(f"  Found {len(component_names)} application(s): {', '.join(component_names)}")

    # Async: fetch all repo URLs concurrently
    repos = await async_gather_repo_urls(session, backstage_url, component_names, semaphore,
                                         team_log_lines=log_lines)

    if not repos:
        log_lines.append(f"  No repositories found for team {team_name}")
        return {
            'team_name': team_name,
            'log_lines': log_lines,
            'results': [],
            'perm_denied': [],
            'repos_checked': 0,
            'team_not_found': False,
            'team_processed': False,
        }

    deduped = len(component_names) - len(repos)
    dedup_msg = f" ({deduped} duplicate(s) removed)" if deduped > 0 else ""
    log_lines.append(f"  Checking {len(repos)} unique repository(ies) for '{check_filename}'{dedup_msg}")

    # Async: fetch and analyze files from all repos concurrently
    team_results, team_perm_denied, team_repos_checked = await async_process_repos(
        repos, check_filename, compiled_regex, team_name, team_idx,
        total_teams, semaphore, verbose, team_log_lines=log_lines,
    )

    return {
        'team_name': team_name,
        'log_lines': log_lines,
        'results': team_results,
        'perm_denied': team_perm_denied,
        'repos_checked': team_repos_checked,
        'team_not_found': False,
        'team_processed': True,
    }


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
            if _is_permission_error(result):
                return "PERMISSION_DENIED"
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


def _create_compliance_tickets(args, config, results, version_dates):
    """Create Jira tickets for out-of-compliance results.

    Reads team configuration from the Excel Teams sheet, then creates one
    ticket per out-of-compliance repo using the shared jiraTicketTools library.
    Dry-run by default; use -c/--create to actually create tickets.
    """
    import jira as jira_mod

    excel_file = args.createTickets
    dependency_name = args.dependencyName
    create_mode = args.create

    # Determine latest available version from compare-repo tags
    latest_version = None
    latest_date = None
    if version_dates:
        # version_dates is ordered by -creatordate; first entry is newest
        latest_version = next(iter(version_dates))
        latest_date = version_dates[latest_version]

    # Load team mapping from Excel Teams sheet
    try:
        available_sheets = get_excel_sheets(excel_file)
        team_mapping = process_teams_sheet(excel_file, available_sheets)
    except Exception as e:
        print(f"{Fore.RED}Error reading Excel file '{excel_file}': {e}{Style.RESET_ALL}")
        return

    if not team_mapping:
        print(f"{Fore.RED}Error: No team configuration found in Teams sheet of '{excel_file}'{Style.RESET_ALL}")
        return

    # Connect to Jira if in create mode
    jira_client = None
    if create_mode:
        try:
            jira_client = jira_mod.JIRA(
                config["jira_server"],
                token_auth=config["personal_access_token"],
            )
        except Exception as e:
            print(f"{Fore.RED}Error connecting to Jira: {e}{Style.RESET_ALL}")
            return
    else:
        print(f"\n{Fore.CYAN}Running in DRY-RUN mode — use -c/--create to actually create tickets{Style.RESET_ALL}")

    # Build tickets: one per out-of-compliance repo
    print(f"\n{Fore.CYAN}{'=' * 60}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Ticket Creation ({len(results)} out-of-compliance result(s)):{Style.RESET_ALL}")

    created = 0
    skipped_no_mapping = []
    failed = []
    simulated_counter = 0

    for row in results:
        team_name = row[0]
        repo_display = row[1]
        # Capture groups are between team/repo and last_updated/age
        current_version = ", ".join(row[2:-2])
        last_updated = row[-2]
        age_days = row[-1]

        # Look up team in Excel mapping
        if team_name not in team_mapping:
            if team_name not in skipped_no_mapping:
                skipped_no_mapping.append(team_name)
                print(f"{Fore.YELLOW}Skipping team '{team_name}' — not found in Teams sheet{Style.RESET_ALL}")
            continue

        team_config = team_mapping[team_name]
        project_key = team_config.get("Project")
        if not project_key:
            print(f"{Fore.YELLOW}Skipping team '{team_name}' — no Project field in Teams sheet{Style.RESET_ALL}")
            continue

        issue_type = team_config.get("Issue Type", "Task")
        epic_link = team_config.get(EPIC_LINK_FIELD)
        assignee = team_config.get(ASSIGNEE_FIELD)

        summary = f"Update {repo_display} {dependency_name} from {current_version} to latest version"

        desc_lines = [
            f"*Repository:* {repo_display}",
            f"*Current Version:* {current_version}",
            f"*Current Version Date:* {last_updated}",
            f"*Current Version Age:* {age_days} days",
        ]
        if latest_version:
            desc_lines.append(f"*Latest Available Version:* {latest_version}")
        if latest_date:
            desc_lines.append(f"*Latest Available Date:* {latest_date}")
        desc_lines.append("")
        desc_lines.append("_Note: Please verify the latest version at the time of work._")
        description = "\n".join(desc_lines)

        additional_fields = {}
        if epic_link and str(epic_link) != 'nan':
            additional_fields[EPIC_LINK_FIELD] = epic_link
        if assignee and str(assignee) != 'nan':
            additional_fields[ASSIGNEE_FIELD] = assignee

        if create_mode:
            try:
                new_issue = create_jira_ticket(
                    jira_client, project_key, issue_type, summary, description,
                    excel_file=excel_file, **additional_fields,
                )
                if new_issue:
                    created += 1
                    print(f"{Fore.GREEN}Created {new_issue.key}: {summary}{Style.RESET_ALL}")
                else:
                    failed.append(repo_display)
            except Exception as e:
                print(f"{Fore.RED}Failed to create ticket for {repo_display}: {e}{Style.RESET_ALL}")
                failed.append(repo_display)
        else:
            simulated_counter += 1
            print(f"  {Fore.BLUE}[DRY RUN] (simulated-{project_key}-{simulated_counter}) "
                  f"{summary}{Style.RESET_ALL}")
            if description:
                for line in description.splitlines():
                    print(f"    {Fore.BLUE}{line}{Style.RESET_ALL}")
            created += 1

    # Ticket creation summary
    print(f"\n{Fore.CYAN}{'=' * 60}{Style.RESET_ALL}")
    mode_label = "Created" if create_mode else "Would create"
    print(f"{Fore.CYAN}Ticket Summary: {mode_label} {created} ticket(s){Style.RESET_ALL}")
    if skipped_no_mapping:
        print(f"{Fore.YELLOW}Teams not in Excel mapping (skipped): {', '.join(skipped_no_mapping)}{Style.RESET_ALL}")
    if failed:
        print(f"{Fore.RED}Failed: {', '.join(failed)}{Style.RESET_ALL}")


def main():
    init()

    args = parse_arguments()

    # Clamp --parallel to [1, 15]
    args.parallel = max(1, min(15, args.parallel))

    # Validate --compare-repo and --dateTolerance are used together
    compare_repo = getattr(args, 'compare_repo', None)
    date_tolerance_str = args.dateTolerance
    if bool(compare_repo) != bool(date_tolerance_str):
        print(f"{Fore.RED}Error: --compare-repo and --dateTolerance must be used together.{Style.RESET_ALL}")
        sys.exit(1)

    # Validate --createTickets requires --compare-repo, --dateTolerance, and --dependencyName
    if args.createTickets:
        missing = []
        if not compare_repo:
            missing.append("--compare-repo")
        if not date_tolerance_str:
            missing.append("--dateTolerance")
        if not args.dependencyName:
            missing.append("--dependencyName")
        if missing:
            print(f"{Fore.RED}Error: --createTickets requires {', '.join(missing)}.{Style.RESET_ALL}")
            sys.exit(1)
    if args.dependencyName and not args.createTickets:
        print(f"{Fore.RED}Error: --dependencyName requires --createTickets.{Style.RESET_ALL}")
        sys.exit(1)

    # Parse date tolerance if provided
    tolerance = None
    if date_tolerance_str:
        tolerance = parse_date_tolerance(date_tolerance_str)
        if tolerance is None:
            sys.exit(1)

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

    asyncio.run(async_main(args, config, backstage_url, compiled_regex, compare_repo, tolerance))


async def async_main(args, config, backstage_url, compiled_regex, compare_repo, tolerance):
    """Async entry point that parallelizes Backstage API calls and git operations."""
    # Fetch compare-repo tags if requested (sync — single operation)
    version_dates = {}
    if compare_repo:
        print(f"\n{Fore.CYAN}Fetching tags from compare repo: {compare_repo}{Style.RESET_ALL}")
        version_dates = fetch_repo_tags(compare_repo, verbose=args.verbose)
        if not version_dates:
            print(f"{Fore.YELLOW}Warning: No semantic version tags found in compare repo.{Style.RESET_ALL}")
        else:
            print(f"{Fore.GREEN}Found {len(version_dates)} semantic version tag(s) in compare repo{Style.RESET_ALL}")

    # Parse team names from CLI (sync — single bulk fetch)
    if args.teams.strip() in ('*', 'all'):
        print(f"\n{Fore.CYAN}Fetching all teams from Backstage...{Style.RESET_ALL}")
        all_teams = get_all_teams(backstage_url)
        teams = [t.get('metadata', {}).get('name', '') for t in all_teams]
        teams = [t for t in teams if t]
        if not teams:
            print(f"{Fore.RED}Error: No teams found in Backstage.{Style.RESET_ALL}")
            sys.exit(1)
        print(f"{Fore.GREEN}Found {len(teams)} team(s) in Backstage{Style.RESET_ALL}")
    else:
        teams = [t.strip() for t in args.teams.split(",") if t.strip()]
    if not teams:
        print(f"{Fore.RED}Error: No valid team names provided.{Style.RESET_ALL}")
        sys.exit(1)

    # Fetch all Backstage components once (sync — single bulk fetch)
    print(f"\n{Fore.CYAN}Fetching all components from Backstage...{Style.RESET_ALL}")
    all_components = get_all_components(backstage_url)
    if not all_components:
        print(f"{Fore.RED}Error: No components found in Backstage.{Style.RESET_ALL}")
        sys.exit(1)
    print(f"{Fore.GREEN}Fetched {len(all_components)} components from Backstage{Style.RESET_ALL}")

    # Create semaphore for concurrency control
    semaphore = asyncio.Semaphore(args.parallel)
    print(f"{Fore.CYAN}Using {args.parallel} parallel worker(s){Style.RESET_ALL}")

    # Process all teams concurrently (semaphore limits total in-flight I/O)
    results = []
    teams_not_found = []
    repos_permission_denied = []
    teams_processed = 0
    repos_checked = 0
    total_matches = 0

    sorted_teams = sorted(teams)
    total_teams = len(sorted_teams)

    async with aiohttp.ClientSession() as session:
        team_tasks = [
            async_process_team(
                session, backstage_url, all_components, team_name, team_idx,
                total_teams, args.checkFilename, compiled_regex, semaphore, args.verbose,
            )
            for team_idx, team_name in enumerate(sorted_teams, 1)
        ]

        # Use as_completed to show live progress as teams finish
        completed = 0
        with_repos = 0
        team_outputs = []
        for future in asyncio.as_completed(team_tasks):
            try:
                output = await future
                team_outputs.append(output)
                if isinstance(output, dict) and output.get('team_processed'):
                    with_repos += 1
            except Exception as e:
                team_outputs.append(e)
            completed += 1
            print(f"\r{Fore.CYAN}  Progress: {completed}/{total_teams} teams completed "
                  f"({with_repos} with repos){Style.RESET_ALL}    ", end="", flush=True)
        print()  # newline after progress line

    # Print buffered output sorted alphabetically by team name
    valid_outputs = []
    for output in team_outputs:
        if isinstance(output, Exception):
            print(f"{Fore.YELLOW}Warning: Unexpected error processing a team: {output}{Style.RESET_ALL}")
            continue
        valid_outputs.append(output)

    for output in sorted(valid_outputs, key=lambda x: x['team_name']):
        # Print buffered log lines for this team
        for line in output['log_lines']:
            print(line)

        if output['team_not_found']:
            teams_not_found.append(output['team_name'])
        if output['team_processed']:
            teams_processed += 1
        results.extend(output['results'])
        repos_permission_denied.extend(output['perm_denied'])
        repos_checked += output['repos_checked']
        total_matches += len(output['results'])

    # Apply compliance filtering if compare-repo was provided
    if version_dates and tolerance is not None:
        today = datetime.now()
        filtered_results = []
        compliant_count = 0
        for row in results:
            groups = row[2:]
            # Auto-detect which capture group matches a version in the tag map
            matched_version = None
            for group_val in groups:
                version = extract_semver(group_val)
                if version and version in version_dates:
                    matched_version = version
                    break

            if matched_version:
                date_str = version_dates[matched_version]
                try:
                    tag_date = datetime.strptime(date_str, "%Y-%m-%d")
                except ValueError:
                    # Include with unknown date if parsing fails
                    filtered_results.append(row + ("Unknown", "Unknown"))
                    continue
                age = today - tag_date
                age_days = age.days
                if age > tolerance:
                    filtered_results.append(row + (date_str, str(age_days)))
                else:
                    compliant_count += 1
            else:
                # Version not found in tags — include with warning
                filtered_results.append(row + ("Unknown", "Unknown"))

        print(f"\n{Fore.CYAN}Compliance filtering: {compliant_count} result(s) within tolerance, "
              f"{len(filtered_results)} out of compliance or unknown{Style.RESET_ALL}")
        results = filtered_results
        total_matches = len(results)

    # Consolidated results
    if results:
        print(f"\n{Fore.CYAN}{'=' * 60}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Results:{Style.RESET_ALL}")
        for row in results:
            team_name_r = row[0]
            repo_display_r = row[1]
            # With compliance filtering, last two elements are date and age
            if version_dates and tolerance is not None:
                captured_r = ", ".join(row[2:-2])
                last_updated = row[-2]
                age_days = row[-1]
                date_color = Fore.YELLOW if last_updated == "Unknown" else ""
                print(f"  {Fore.GREEN}{team_name_r}{Style.RESET_ALL} | "
                      f"{Fore.BLUE}{repo_display_r}{Style.RESET_ALL} | "
                      f"{captured_r} | "
                      f"{date_color}Last Updated: {last_updated} | Age: {age_days} days{Style.RESET_ALL}")
            else:
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

    if repos_permission_denied:
        print(f"\n{Fore.YELLOW}Warning: Permission denied for {len(repos_permission_denied)} repository(ies):{Style.RESET_ALL}")
        for repo_name in repos_permission_denied:
            print(f"  {Fore.YELLOW}- {repo_name}{Style.RESET_ALL}")

    # CSV export
    if args.output and results:
        num_groups = compiled_regex.groups
        group_texts = extract_capture_groups(args.searchRegex)
        if num_groups == 1:
            match_headers = [group_texts[0]] if group_texts else ["Match"]
        else:
            match_headers = group_texts if len(group_texts) == num_groups else [f"Match{i+1}" for i in range(num_groups)]
        if version_dates and tolerance is not None:
            match_headers.extend(["Last Updated", "Age (in days)"])
        with open(args.output, 'w', newline='') as f:
            writer = csv.writer(f)
            writer.writerow(["Team", "Repository"] + match_headers)
            writer.writerows(results)
        print(f"\n{Fore.GREEN}Results exported to {args.output}{Style.RESET_ALL}")
    elif args.output and not results:
        print(f"\n{Fore.YELLOW}No results to export to CSV.{Style.RESET_ALL}")

    # Ticket creation
    if args.createTickets and results:
        _create_compliance_tickets(args, config, results, version_dates)

    # Force garbage collection while the event loop is still running
    # to prevent "Event loop is closed" errors from subprocess transports
    gc.collect()


if __name__ == "__main__":
    main()

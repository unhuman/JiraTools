#!/usr/bin/env python3
"""
Developer Metrics Script

Audits developer productivity across teams by fetching completed work from Jira,
aggregating by week, and generating CSV reports + PNG visualizations.

Usage:
    python developerMetrics.py --teams TeamA --period ytd --output results.csv --report charts

Requirements:
    pip install aiohttp colorama jira matplotlib pandas openpyxl requests
"""

import argparse
import csv
from datetime import datetime, timedelta
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys

import jira
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.ticker import MaxNLocator
import requests
from colorama import init, Fore, Style

from libraries.jiraToolsConfig import load_config, get_backstage_url
from libraries.backstageTools import get_all_teams, get_team_members
from libraries.jiraQueryTools import search_issues
from libraries.githubTools import (
    derive_github_username,
    get_github_session,
    get_github_metrics_for_user,
    aggregate_github_weekly,
    print_github_summary
)

# Distinct marker shapes and colors for multi-series plots to prevent overlap
PLOT_MARKERS = ['o', 's', '^', 'D', 'v', 'P', '*', 'X', 'h', '+']
PLOT_COLORS = [
    '#1f77b4', '#ff7f0e', '#2ca02c', '#d62728', '#9467bd',  # blue, orange, green, red, purple
    '#8c564b', '#e377c2', '#7f7f7f', '#bcbd22', '#17becf',  # brown, pink, gray, olive, cyan
    '#1a9850', '#d73027', '#fee090', '#4575b4', '#f46d43',  # forest green, dark red, light yellow, steel blue, coral
    '#e6194b', '#3cb44b', '#ffe119', '#0082c8', '#f58231',  # crimson, kelly green, yellow, bright blue, orange
]

# Sprint Drag custom field
SPRINT_DRAG_IMPACTED_TIME_FIELD = 'customfield_12106'  # Impacted Time (Hrs)


def parse_arguments():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Audit developer productivity across teams using Backstage and Jira."
    )
    parser.add_argument(
        "--teams",
        required=True,
        help="Comma-separated list of team names, 'org' / '*' / 'all' to use teams from config, or 'all' to audit all teams in Backstage"
    )
    parser.add_argument(
        "--backstageUrl",
        help="Backstage base URL (overrides backstageUrl in ~/.jiraTools config)"
    )
    parser.add_argument(
        "--period",
        required=True,
        help="Time period: 'ytd', 'month', 'Nm' (e.g., '3m', '6m'), or 'YYYY-MM-DD:YYYY-MM-DD' for an explicit range"
    )
    parser.add_argument(
        "-o", "--output",
        metavar="PREFIX",
        help="CSV output prefix (generates {prefix}_raw.csv and {prefix}_aggregated.csv)"
    )
    parser.add_argument(
        "--filePrefix",
        required=True,
        metavar="PREFIX",
        help="File prefix for PNG output (generates {prefix}_{team}_overall.png)"
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Show detailed logging for debugging"
    )
    parser.add_argument(
        "--parallel",
        type=int,
        default=5,
        help="Number of parallel workers for Jira queries (default: 5, max: 15)"
    )
    parser.add_argument(
        "--githubOrg",
        help="GitHub organization name (overrides github_org in ~/.jiraTools config)"
    )
    return parser.parse_args()


def parse_period(period_str):
    """Parse period string into (lower_clause, start_date, upper_clause, end_date).

    Args:
        period_str: 'ytd', 'month', 'Nm' (e.g., '3m', '6m'), or 'YYYY-MM-DD:YYYY-MM-DD'

    Returns:
        Tuple of (lower_clause, start_date, upper_clause, end_date)
        For open-ended modes, upper_clause and end_date are None
    """
    today = datetime.now()

    if period_str == 'ytd':
        return ">= startOfYear()", datetime(today.year, 1, 1), None, None
    elif period_str == 'month':
        return ">= startOfMonth()", datetime(today.year, today.month, 1), None, None
    elif period_str.endswith('m'):
        try:
            months = int(period_str[:-1])
            # Calculate date N months ago (approximate: N * 30 days)
            days_back = months * 30
            start_date = today - timedelta(days=days_back)
            jql_date = start_date.strftime("%Y-%m-%d")
            return f'>= {jql_date}', start_date, None, None
        except ValueError:
            print(f"{Fore.RED}Error: Invalid period '{period_str}'. Use 'ytd', 'month', 'Nm' (e.g., '3m'), or 'YYYY-MM-DD:YYYY-MM-DD'{Style.RESET_ALL}")
            return None, None, None, None
    elif ':' in period_str:
        try:
            parts = period_str.split(':')
            if len(parts) != 2:
                raise ValueError("Date range must have exactly one colon")
            start_date = datetime.strptime(parts[0].strip(), "%Y-%m-%d")
            end_date = datetime.strptime(parts[1].strip(), "%Y-%m-%d")
            start_str = parts[0].strip()
            end_str = parts[1].strip()
            return f'>= {start_str}', start_date, f'<= {end_str}', end_date
        except (ValueError, IndexError) as e:
            print(f"{Fore.RED}Error: Invalid date range '{period_str}'. Use 'YYYY-MM-DD:YYYY-MM-DD' format. {e}{Style.RESET_ALL}")
            return None, None, None, None
    else:
        print(f"{Fore.RED}Error: Invalid period '{period_str}'. Use 'ytd', 'month', 'Nm' (e.g., '3m'), or 'YYYY-MM-DD:YYYY-MM-DD'{Style.RESET_ALL}")
        return None, None, None, None


def build_jql_for_user(username, date_clause, upper_date_clause=None):
    """Build JQL query for a user's completed work.

    Args:
        username: Jira assignee username
        date_clause: Lower date filter clause (e.g., ">= startOfYear()" or ">= 2026-01-01")
        upper_date_clause: Optional upper date filter clause (e.g., "<= 2026-03-31")

    Returns:
        JQL query string
    """
    statuses = [
        "Acceptance", "Approved to Deploy", "Certified", "Closed",
        "Complete", "Completed", "Deployed", "Done", "Released",
        "Ready for Deployment", "Ready For Release", "Ready to Deploy",
        "Ready to Release", "Resolved"
    ]
    status_list = ", ".join(f'"{s}"' for s in statuses)

    updated_filter = f'updated {date_clause}'
    if upper_date_clause:
        updated_filter += f' AND updated {upper_date_clause}'

    if upper_date_clause:
        res_filter = (
            f'(resolutiondate is EMPTY OR '
            f'(resolutiondate {date_clause} AND resolutiondate {upper_date_clause}))'
        )
    else:
        res_filter = f'(resolutiondate is EMPTY OR resolutiondate {date_clause})'

    jql = (
        f'Assignee = "{username}" '
        'AND issuetype not in (subTaskIssueTypes(), Epic, "Test Case Execution", "Test Execution", Test, DBCR) '
        f'AND status IN ({status_list}) '
        f'AND {updated_filter} '
        f'AND {res_filter} '
        'ORDER BY resolved DESC'
    )
    return jql


def build_jql_drag_for_user(username, date_clause, upper_date_clause=None):
    """Build JQL query for a user's Sprint Drag issues.

    Args:
        username: Jira reporter username
        date_clause: Lower date filter clause (e.g., ">= startOfYear()" or ">= 2026-01-01")
        upper_date_clause: Optional upper date filter clause

    Returns:
        JQL query string
    """
    created_filter = f'created {date_clause}'
    if upper_date_clause:
        created_filter += f' AND created {upper_date_clause}'

    jql = (
        f'reporter = "{username}" '
        'AND issuetype = "Sprint Drag" '
        f'AND {created_filter} '
        'ORDER BY created DESC'
    )
    return jql


def build_jql_tickets_created_for_user(username, date_clause, upper_date_clause=None):
    """Build JQL query for Development Issues created by a user.

    Args:
        username: Jira creator username
        date_clause: Lower date filter clause
        upper_date_clause: Optional upper date filter clause

    Returns:
        JQL query string
    """
    created_filter = f'created {date_clause}'
    if upper_date_clause:
        created_filter += f' AND created {upper_date_clause}'

    jql = (
        f'creator = "{username}" '
        'AND (issuetype not in subTaskIssueTypes() OR issuetype = "Development Issue") '
        f'AND {created_filter} '
        'ORDER BY created ASC'
    )
    return jql


def query_user_issues(jira_client, username, display_name, job_title, team_name, jql):
    """Query issues for a single user.

    Args:
        jira_client: Jira client
        username: Jira assignee username
        display_name: Display name for reporting
        job_title: Job title for reporting
        team_name: Team name for grouping
        jql: JQL query string

    Returns:
        List of issue dicts
    """
    issues = []
    try:
        fields = ['summary', 'resolutiondate', 'updated', 'timeoriginalestimate', 'issuetype', 'assignee']
        results = search_issues(jira_client, jql, max_results=False, fields=fields)

        for issue in results:
            resolved_date_str = getattr(issue.fields, 'resolutiondate', None)
            resolved_date = None
            if resolved_date_str:
                try:
                    resolved_date = datetime.strptime(resolved_date_str[:10], "%Y-%m-%d").date()
                except (ValueError, AttributeError, TypeError):
                    pass

            if not resolved_date:
                updated_str = getattr(issue.fields, 'updated', None)
                if updated_str:
                    try:
                        resolved_date = datetime.strptime(updated_str[:10], "%Y-%m-%d").date()
                    except (ValueError, AttributeError, TypeError):
                        pass
                if not resolved_date:
                    continue

            original_estimate_seconds = getattr(issue.fields, 'timeoriginalestimate', None) or 0

            issue_type_obj = getattr(issue.fields, 'issuetype', None)
            issue_type_name = issue_type_obj.name if issue_type_obj else ''

            issues.append({
                'team': team_name,
                'user': username,
                'display_name': display_name,
                'job_title': job_title,
                'issue_key': issue.key,
                'summary': getattr(issue.fields, 'summary', ''),
                'resolved_date': resolved_date,
                'original_estimate_seconds': original_estimate_seconds,
                'issue_type': issue_type_name,
            })
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Error querying issues for {username}: {e}{Style.RESET_ALL}")

    return issues


def query_user_drag_issues(jira_client, username, display_name, job_title, team_name, jql):
    """Query Sprint Drag issues for a single user.

    Args:
        jira_client: Jira client
        username: Jira reporter username
        display_name: Display name for reporting
        job_title: Job title for reporting
        team_name: Team name for grouping
        jql: JQL query string

    Returns:
        List of drag issue dicts
    """
    issues = []
    try:
        fields = ['summary', 'created', SPRINT_DRAG_IMPACTED_TIME_FIELD]
        results = search_issues(jira_client, jql, max_results=False, fields=fields)

        for issue in results:
            created_str = getattr(issue.fields, 'created', None)
            created_date = None
            if created_str:
                try:
                    created_date = datetime.strptime(created_str[:10], "%Y-%m-%d").date()
                except (ValueError, AttributeError, TypeError):
                    pass

            if not created_date:
                continue

            impacted_hours = getattr(issue.fields, SPRINT_DRAG_IMPACTED_TIME_FIELD, None) or 0
            impacted_hours = float(impacted_hours) if impacted_hours else 0.0

            issues.append({
                'team': team_name,
                'user': username,
                'display_name': display_name,
                'job_title': job_title,
                'issue_key': issue.key,
                'summary': getattr(issue.fields, 'summary', ''),
                'created_date': created_date,
                'impacted_hours': impacted_hours,
            })
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Error querying drag issues for {username}: {e}{Style.RESET_ALL}")

    return issues


def query_user_tickets_created(jira_client, username, display_name, job_title, team_name, jql):
    """Query Development Issues created by a single user.

    Args:
        jira_client: Jira client
        username: Jira creator username
        display_name: Display name for reporting
        job_title: Job title for reporting
        team_name: Team name for grouping
        jql: JQL query string

    Returns:
        List of ticket dicts
    """
    issues = []
    try:
        fields = ['summary', 'created', 'issuetype']
        results = search_issues(jira_client, jql, max_results=False, fields=fields)

        for issue in results:
            created_str = getattr(issue.fields, 'created', None)
            created_date = None
            if created_str:
                try:
                    created_date = datetime.strptime(created_str[:10], "%Y-%m-%d").date()
                except (ValueError, AttributeError, TypeError):
                    pass

            if not created_date:
                continue

            issues.append({
                'team': team_name,
                'user': username,
                'display_name': display_name,
                'job_title': job_title,
                'issue_key': issue.key,
                'summary': getattr(issue.fields, 'summary', ''),
                'created_date': created_date,
            })
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Error querying created tickets for {username}: {e}{Style.RESET_ALL}")

    return issues


def aggregate_to_weekly(df, day_size=6):
    """Aggregate issue data to weekly buckets.

    Args:
        df: DataFrame with resolved_date and original_estimate_seconds columns
        day_size: Work hours per day (default: 6)

    Returns:
        Aggregated DataFrame
    """
    if df.empty:
        return pd.DataFrame()

    df['week_start'] = df['resolved_date'] - pd.to_timedelta(df['resolved_date'].dt.weekday, unit='D')

    agg = df.groupby(['team', 'user', 'display_name', 'week_start']).agg(
        issue_count=('issue_key', 'count'),
        total_estimate_seconds=('original_estimate_seconds', 'sum'),
    ).reset_index()

    # Preserve job_title if present in original df
    if 'job_title' in df.columns:
        job_title_map = df.groupby('user')['job_title'].first()
        agg['job_title'] = agg['user'].map(job_title_map).fillna('')

    agg['total_estimate_days'] = (agg['total_estimate_seconds'] / (day_size * 3600)).round(2)
    agg['total_estimate_weeks'] = (agg['total_estimate_days'] / 5).round(2)
    agg = agg.drop(columns=['total_estimate_seconds'])

    return agg.sort_values(['team', 'user', 'week_start'])


def make_cumulative(agg_df):
    """Convert weekly aggregated data to cumulative values.

    Args:
        agg_df: Aggregated DataFrame with weekly data

    Returns:
        DataFrame with cumulative issue_count and total_estimate_days
    """
    if agg_df.empty:
        return pd.DataFrame()

    cum_df = agg_df.copy()

    for team in cum_df['team'].unique():
        team_mask = cum_df['team'] == team
        for user in cum_df.loc[team_mask, 'user'].unique():
            user_mask = team_mask & (cum_df['user'] == user)
            cum_df.loc[user_mask, 'issue_count'] = cum_df.loc[user_mask, 'issue_count'].cumsum()
            cum_df.loc[user_mask, 'total_estimate_days'] = cum_df.loc[user_mask, 'total_estimate_days'].cumsum()
            cum_df.loc[user_mask, 'total_estimate_weeks'] = cum_df.loc[user_mask, 'total_estimate_weeks'].cumsum()

    return cum_df


def aggregate_drag_to_weekly(drag_issues_list):
    """Aggregate Sprint Drag issues to weekly buckets by created date.

    Args:
        drag_issues_list: List of drag issue dicts with created_date and impacted_hours

    Returns:
        Aggregated DataFrame with team, user, display_name, week_start, drag_hours
    """
    if not drag_issues_list:
        return pd.DataFrame()

    df = pd.DataFrame(drag_issues_list)
    df['created_date'] = pd.to_datetime(df['created_date'])
    df['week_start'] = df['created_date'] - pd.to_timedelta(df['created_date'].dt.weekday, unit='D')

    agg = df.groupby(['team', 'user', 'display_name', 'week_start']).agg(
        drag_issue_count=('issue_key', 'count'),
        drag_hours=('impacted_hours', 'sum'),
    ).reset_index()

    agg['drag_hours'] = agg['drag_hours'].round(2)
    return agg.sort_values(['team', 'user', 'week_start'])


def make_drag_cumulative(drag_agg_df):
    """Convert weekly aggregated drag data to cumulative values.

    Args:
        drag_agg_df: Aggregated drag DataFrame with weekly data

    Returns:
        DataFrame with cumulative drag_hours
    """
    if drag_agg_df.empty:
        return pd.DataFrame()

    cum_df = drag_agg_df.copy()

    for team in cum_df['team'].unique():
        team_mask = cum_df['team'] == team
        for user in cum_df.loc[team_mask, 'user'].unique():
            user_mask = team_mask & (cum_df['user'] == user)
            cum_df.loc[user_mask, 'drag_hours'] = cum_df.loc[user_mask, 'drag_hours'].cumsum()

    return cum_df


def aggregate_tickets_to_weekly(tickets_list):
    """Aggregate created tickets to weekly buckets by created date.

    Args:
        tickets_list: List of ticket dicts with created_date

    Returns:
        Aggregated DataFrame with team, user, display_name, week_start, ticket_count
    """
    if not tickets_list:
        return pd.DataFrame()

    df = pd.DataFrame(tickets_list)
    df['created_date'] = pd.to_datetime(df['created_date'])
    df['week_start'] = df['created_date'] - pd.to_timedelta(df['created_date'].dt.weekday, unit='D')

    agg = df.groupby(['team', 'user', 'display_name', 'week_start']).agg(
        ticket_count=('issue_key', 'count'),
    ).reset_index()

    return agg.sort_values(['team', 'user', 'week_start'])


def make_tickets_cumulative(tickets_agg_df):
    """Convert weekly aggregated tickets data to cumulative values.

    Args:
        tickets_agg_df: Aggregated tickets DataFrame with weekly data

    Returns:
        DataFrame with cumulative ticket_count
    """
    if tickets_agg_df.empty:
        return pd.DataFrame()

    cum_df = tickets_agg_df.copy()

    for team in cum_df['team'].unique():
        team_mask = cum_df['team'] == team
        for user in cum_df.loc[team_mask, 'user'].unique():
            user_mask = team_mask & (cum_df['user'] == user)
            cum_df.loc[user_mask, 'ticket_count'] = cum_df.loc[user_mask, 'ticket_count'].cumsum()

    return cum_df


def export_csv(raw_issues, agg_df, output_prefix, day_size=6, github_df=None, drag_agg_df=None, tickets_agg_df=None):
    """Export raw and aggregated data to CSV files.

    Args:
        raw_issues: List of raw issue dicts
        agg_df: Aggregated DataFrame
        output_prefix: Output file prefix
        day_size: Work hours per day (default: 6)
        github_df: Optional GitHub metrics DataFrame
    """
    raw_file = f"{output_prefix}_raw.csv"
    agg_file = f"{output_prefix}_aggregated.csv"

    # Raw CSV
    with open(raw_file, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'Team', 'User', 'Display Name', 'Issue Key', 'Summary',
            'Resolved Date', 'Original Estimate (weeks)', 'Issue Type'
        ])
        for issue in raw_issues:
            estimate_days = round(issue['original_estimate_seconds'] / (day_size * 3600), 2)
            estimate_weeks = round(estimate_days / 5, 2)
            writer.writerow([
                issue['team'],
                issue['user'],
                issue['display_name'],
                issue['issue_key'],
                issue['summary'],
                issue['resolved_date'],
                estimate_weeks,
                issue['issue_type'],
            ])

    print(f"{Fore.GREEN}Raw results exported to {raw_file}{Style.RESET_ALL}")

    # Aggregated CSV (Phase 3: join GitHub and Drag data if available)
    if not agg_df.empty:
        agg_df_sorted = agg_df.sort_values(['team', 'user', 'week_start'])
        agg_df_sorted['Week Start'] = agg_df_sorted['week_start'].dt.strftime('%Y-%m-%d')

        # Join Drag data if available
        if drag_agg_df is not None and not drag_agg_df.empty:
            drag_agg_df_join = drag_agg_df.copy()
            drag_agg_df_join['week_start'] = pd.to_datetime(drag_agg_df_join['week_start']).dt.date
            agg_df_sorted['week_start'] = agg_df_sorted['week_start'].dt.date

            agg_df_sorted = agg_df_sorted.merge(
                drag_agg_df_join[['team', 'user', 'week_start', 'drag_hours']],
                on=['team', 'user', 'week_start'],
                how='left'
            )
            agg_df_sorted['drag_hours'] = agg_df_sorted['drag_hours'].fillna(0)
            agg_df_sorted['week_start'] = pd.to_datetime(agg_df_sorted['week_start'])
        else:
            agg_df_sorted['week_start'] = pd.to_datetime(agg_df_sorted['week_start'])

        # Join Tickets Created data if available
        if tickets_agg_df is not None and not tickets_agg_df.empty:
            tickets_agg_df_join = tickets_agg_df.copy()
            tickets_agg_df_join['week_start'] = pd.to_datetime(tickets_agg_df_join['week_start']).dt.date
            agg_df_sorted['week_start'] = agg_df_sorted['week_start'].dt.date

            agg_df_sorted = agg_df_sorted.merge(
                tickets_agg_df_join[['team', 'user', 'week_start', 'ticket_count']],
                on=['team', 'user', 'week_start'],
                how='left'
            )
            agg_df_sorted['ticket_count'] = agg_df_sorted['ticket_count'].fillna(0).astype(int)
            agg_df_sorted['week_start'] = pd.to_datetime(agg_df_sorted['week_start'])
        else:
            agg_df_sorted['week_start'] = pd.to_datetime(agg_df_sorted['week_start'])

        # Join GitHub data if available
        if github_df is not None and not github_df.empty:
            # Ensure week_start is datetime in github_df for consistent joining
            github_df_join = github_df.copy()
            github_df_join['week_start'] = pd.to_datetime(github_df_join['week_start']).dt.date

            # Join on (team, user, week_start)
            agg_df_sorted_with_gh = agg_df_sorted.merge(
                github_df_join[['team', 'user', 'week_start', 'prs_opened', 'commits', 'reviews_given', 'comments_received']],
                on=['team', 'user', 'week_start'],
                how='left'
            )
            # Fill NaN with 0 for GitHub metrics
            for col in ['prs_opened', 'commits', 'reviews_given', 'comments_received']:
                if col in agg_df_sorted_with_gh.columns:
                    agg_df_sorted_with_gh[col] = agg_df_sorted_with_gh[col].fillna(0).astype(int)

            cols_to_export = ['team', 'user', 'display_name', 'Week Start', 'issue_count', 'total_estimate_weeks']
            headers = ['Team', 'User', 'Display Name', 'Week Start', 'Issue Count', 'Total Estimate (weeks)']
            if 'ticket_count' in agg_df_sorted_with_gh.columns:
                cols_to_export.extend(['ticket_count'])
                headers.extend(['Tickets Created'])
            if 'drag_hours' in agg_df_sorted_with_gh.columns:
                cols_to_export.extend(['drag_hours'])
                headers.extend(['Drag Hours'])
            cols_to_export.extend(['prs_opened', 'commits', 'reviews_given', 'comments_received'])
            headers.extend(['GitHub PRs Opened', 'GitHub Commits', 'GitHub Reviews Given', 'GitHub Comments Received'])

            agg_df_sorted_with_gh[cols_to_export].to_csv(agg_file, index=False, header=headers)
        else:
            cols_to_export = ['team', 'user', 'display_name', 'Week Start', 'issue_count', 'total_estimate_weeks']
            headers = ['Team', 'User', 'Display Name', 'Week Start', 'Issue Count', 'Total Estimate (weeks)']
            if 'ticket_count' in agg_df_sorted.columns:
                cols_to_export.extend(['ticket_count'])
                headers.extend(['Tickets Created'])
            if 'drag_hours' in agg_df_sorted.columns:
                cols_to_export.extend(['drag_hours'])
                headers.extend(['Drag Hours'])

            agg_df_sorted[cols_to_export].to_csv(agg_file, index=False, header=headers)

        print(f"{Fore.GREEN}Aggregated results exported to {agg_file}{Style.RESET_ALL}")


def generate_team_chart(team_name, team_df, report_prefix, start_date, end_date):
    """Generate two PNG files for a team: individuals and total.

    Args:
        team_name: Team name
        team_df: Aggregated DataFrame filtered to this team
        report_prefix: PNG prefix
        start_date: Report start date
        end_date: Report end date
    """
    if team_df.empty:
        return

    team_display_name = team_name[:1].upper() + team_name[1:]

    # Convert week_start to datetime for plotting
    team_df = team_df.copy()
    team_df['week_start'] = pd.to_datetime(team_df['week_start'])

    # Individual chart
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

    max_issue_count = 0
    max_estimate = 0

    for user in team_df['user'].unique():
        user_df = team_df[team_df['user'] == user].sort_values('week_start')
        display_name = user_df['display_name'].iloc[0]
        ax1.plot(user_df['week_start'], user_df['issue_count'], marker='o', label=display_name, linewidth=2)
        ax2.plot(user_df['week_start'], user_df['total_estimate_weeks'], marker='o', label=display_name, linewidth=2)
        max_issue_count = max(max_issue_count, user_df['issue_count'].max())
        max_estimate = max(max_estimate, user_df['total_estimate_weeks'].max())

    ax1.set_title(f"{team_display_name} — Cumulative Issue Count", fontsize=12, fontweight='bold')
    ax1.set_ylabel('Issue Count', fontsize=10)
    ax1.set_ylim(0, max_issue_count * 1.05)
    ax1.legend(loc='best', fontsize=9)
    ax1.grid(True, alpha=0.3)
    ax1.yaxis.set_major_locator(MaxNLocator(integer=True))

    ax2.set_title(f"{team_display_name} — Cumulative Original Estimate (weeks)", fontsize=12, fontweight='bold')
    ax2.set_xlabel('Week of', fontsize=10)
    ax2.set_ylabel('Estimate (weeks)', fontsize=10)
    ax2.set_ylim(0, max_estimate * 1.05)
    ax2.legend(loc='best', fontsize=9)
    ax2.grid(True, alpha=0.3)

    for ax in [ax1, ax2]:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

    weeks = (end_date.date() - start_date.date()).days // 7 + 1
    fig.text(0.5, 0.98, f"{team_display_name} — {start_date.date()} to {end_date.date()} ({weeks} weeks)",
             ha='center', va='top', fontsize=13, fontweight='bold')

    plt.tight_layout(rect=[0, 0, 1, 0.94])
    individuals_file = f"{report_prefix}_{team_name}_individuals.png"
    plt.savefig(individuals_file, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"{Fore.GREEN}Generated {individuals_file}{Style.RESET_ALL}")

    # Team total chart
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

    # Convert cumulative per-user data back to incremental, then sum and re-cumulate
    incremental = []
    for user in team_df['user'].unique():
        user_df = team_df[team_df['user'] == user].sort_values('week_start').copy()
        user_df['issue_count_inc'] = user_df['issue_count'].diff().fillna(user_df['issue_count'])
        user_df['total_estimate_weeks_inc'] = user_df['total_estimate_weeks'].diff().fillna(user_df['total_estimate_weeks'])
        incremental.append(user_df[['week_start', 'issue_count_inc', 'total_estimate_weeks_inc']])

    inc_df = pd.concat(incremental, ignore_index=True)
    team_total = inc_df.groupby('week_start').agg({
        'issue_count_inc': 'sum',
        'total_estimate_weeks_inc': 'sum'
    }).reset_index().sort_values('week_start')

    # Re-cumulate to get team total cumulative
    team_total['issue_count'] = team_total['issue_count_inc'].cumsum()
    team_total['total_estimate_weeks'] = team_total['total_estimate_weeks_inc'].cumsum()

    ax1.plot(team_total['week_start'], team_total['issue_count'], marker='o', color='#2E86AB', linewidth=2.5, markersize=6)
    ax1.fill_between(team_total['week_start'], team_total['issue_count'], alpha=0.3, color='#2E86AB')
    ax1.set_title(f"{team_display_name} — Cumulative Issue Count (Team Total)", fontsize=12, fontweight='bold')
    ax1.set_ylabel('Issue Count', fontsize=10)
    ax1.grid(True, alpha=0.3)
    ax1.yaxis.set_major_locator(MaxNLocator(integer=True))

    ax2.plot(team_total['week_start'], team_total['total_estimate_weeks'], marker='o', color='#A23B72', linewidth=2.5, markersize=6)
    ax2.fill_between(team_total['week_start'], team_total['total_estimate_weeks'], alpha=0.3, color='#A23B72')
    ax2.set_title(f"{team_display_name} — Cumulative Original Estimate (weeks) (Team Total)", fontsize=12, fontweight='bold')
    ax2.set_xlabel('Week of', fontsize=10)
    ax2.set_ylabel('Estimate (weeks)', fontsize=10)
    ax2.grid(True, alpha=0.3)

    for ax in [ax1, ax2]:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

    weeks = (end_date.date() - start_date.date()).days // 7 + 1
    fig.text(0.5, 0.98, f"{team_display_name} — {start_date.date()} to {end_date.date()} ({weeks} weeks)",
             ha='center', va='top', fontsize=13, fontweight='bold')

    plt.tight_layout(rect=[0, 0, 1, 0.94])
    total_file = f"{report_prefix}_{team_name}_total.png"
    plt.savefig(total_file, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"{Fore.GREEN}Generated {total_file}{Style.RESET_ALL}")


def generate_team_overall_report(team_name, team_df, report_prefix, start_date, end_date, github_df=None, drag_df=None, tickets_df=None):
    """Generate a comprehensive single-page team report with team totals, individuals combined, then individual breakdowns.

    Args:
        team_name: Team name
        team_df: Cumulative DataFrame filtered to this team
        report_prefix: PNG prefix
        start_date: Report start date
        end_date: Report end date
        github_df: Optional GitHub metrics DataFrame with columns: user, team, week_start, prs_opened, commits, reviews_given, comments_received
        drag_df: Optional Sprint Drag DataFrame filtered to this team with columns: user, display_name, week_start, drag_hours
        tickets_df: Optional Tickets Created DataFrame filtered to this team with columns: user, display_name, week_start, ticket_count
    """
    if team_df.empty:
        return

    team_display_name = team_name[:1].upper() + team_name[1:]

    team_df = team_df.copy()
    team_df['week_start'] = pd.to_datetime(team_df['week_start'])

    # Get sorted list of developers
    developers = sorted(team_df['user'].unique())
    num_developers = len(developers)

    # Check if GitHub data is available for this team
    github_enabled = github_df is not None and not github_df.empty and (github_df['team'] == team_name).any()

    # Check if drag data is available for this team
    drag_enabled = drag_df is not None and not drag_df.empty and (drag_df['team'] == team_name).any()

    # Check if tickets created data is available for this team
    tickets_enabled = tickets_df is not None and not tickets_df.empty and (tickets_df['team'] == team_name).any()

    # Grid layout: 1 row per section (team, combined, + one or two per dev), now 4 columns (Issues | Estimate | Drag | Tickets Created)
    # When GitHub enabled: Jira row + GitHub row per dev
    if github_enabled:
        # Filter GitHub data to this team for reference
        team_github_df = github_df[github_df['team'] == team_name]
        total_rows = 2 + (num_developers * 2)
        height_ratios = [2.5, 2.5] + ([1.5, 1.0] * num_developers)
        fig_height = 3.5 + 3.5 + (num_developers * 2.5) + 2.0
    else:
        total_rows = 2 + num_developers
        height_ratios = [2.5, 2.5] + [1.5] * num_developers
        fig_height = 3.5 + 3.5 + (num_developers * 2.5) + 1.5

    fig = plt.figure(figsize=(24, fig_height), constrained_layout=True)

    # Create grid with 4 columns (Issues | Estimate | Drag | Tickets Created)
    gs = fig.add_gridspec(total_rows, 4, height_ratios=height_ratios, wspace=0.3)

    # Calculate team total
    incremental = []
    for user in team_df['user'].unique():
        user_df = team_df[team_df['user'] == user].sort_values('week_start').copy()
        user_df['issue_count_inc'] = user_df['issue_count'].diff().fillna(user_df['issue_count'])
        user_df['total_estimate_weeks_inc'] = user_df['total_estimate_weeks'].diff().fillna(user_df['total_estimate_weeks'])
        incremental.append(user_df[['week_start', 'issue_count_inc', 'total_estimate_weeks_inc']])

    inc_df = pd.concat(incremental, ignore_index=True)
    team_total = inc_df.groupby('week_start').agg({
        'issue_count_inc': 'sum',
        'total_estimate_weeks_inc': 'sum'
    }).reset_index().sort_values('week_start')

    team_total['issue_count'] = team_total['issue_count_inc'].cumsum()
    team_total['total_estimate_weeks'] = team_total['total_estimate_weeks_inc'].cumsum()

    # Pre-calculate max drag values if drag is enabled
    max_team_drag = 0
    max_dev_drag_pre = 0
    if drag_enabled:
        team_drag_total_pre = drag_df.groupby('week_start').agg({'drag_hours': 'sum'}).reset_index().sort_values('week_start')
        team_drag_total_pre['drag_hours'] = pd.to_numeric(team_drag_total_pre['drag_hours'], errors='coerce').fillna(0)
        team_drag_total_pre['drag_hours_cumsum'] = team_drag_total_pre['drag_hours'].cumsum()
        max_team_drag = team_drag_total_pre['drag_hours_cumsum'].max()

        for user in developers:
            user_drag_df_pre = drag_df[drag_df['user'] == user].sort_values('week_start').copy()
            if not user_drag_df_pre.empty:
                user_drag_df_pre['drag_hours'] = pd.to_numeric(user_drag_df_pre['drag_hours'], errors='coerce').fillna(0)
                user_drag_cumsum_pre = user_drag_df_pre['drag_hours'].cumsum()
                max_dev_drag_pre = max(max_dev_drag_pre, user_drag_cumsum_pre.max())

    # Initialize max_dev_drag early so it's available in combined individuals section
    max_dev_drag = max_dev_drag_pre

    # Pre-calculate max tickets values if tickets are enabled
    max_team_tickets = 0
    max_dev_tickets_pre = 0
    if tickets_enabled:
        team_tickets_total_pre = tickets_df.groupby('week_start').agg({'ticket_count': 'sum'}).reset_index().sort_values('week_start')
        team_tickets_total_pre['ticket_count'] = pd.to_numeric(team_tickets_total_pre['ticket_count'], errors='coerce').fillna(0)
        team_tickets_total_pre['ticket_count_cumsum'] = team_tickets_total_pre['ticket_count'].cumsum()
        max_team_tickets = team_tickets_total_pre['ticket_count_cumsum'].max()

        for user in developers:
            user_tickets_df_pre = tickets_df[tickets_df['user'] == user].sort_values('week_start').copy()
            if not user_tickets_df_pre.empty:
                user_tickets_df_pre['ticket_count'] = pd.to_numeric(user_tickets_df_pre['ticket_count'], errors='coerce').fillna(0)
                user_tickets_cumsum_pre = user_tickets_df_pre['ticket_count'].cumsum()
                max_dev_tickets_pre = max(max_dev_tickets_pre, user_tickets_cumsum_pre.max())

    # Initialize max_dev_tickets early so it's available in combined individuals section
    max_dev_tickets = max_dev_tickets_pre

    # Team totals section (row 0)
    ax_team_issues = fig.add_subplot(gs[0, 0])
    ax_team_estimate = fig.add_subplot(gs[0, 1])
    ax_team_tickets = fig.add_subplot(gs[0, 2])
    ax_team_drag = fig.add_subplot(gs[0, 3])

    ax_team_issues.plot(team_total['week_start'], team_total['issue_count'], marker='o', color='#2E86AB', linewidth=2, markersize=5)
    ax_team_issues.fill_between(team_total['week_start'], team_total['issue_count'], alpha=0.2, color='#2E86AB')
    ax_team_issues.set_title(f"{team_display_name} — Team Total Issue Count", fontsize=10, fontweight='bold')
    ax_team_issues.set_ylabel('Issues', fontsize=9)
    ax_team_issues.grid(True, alpha=0.3)
    ax_team_issues.yaxis.set_major_locator(MaxNLocator(integer=True))

    ax_team_estimate.plot(team_total['week_start'], team_total['total_estimate_weeks'], marker='o', color='#A23B72', linewidth=2, markersize=5)
    ax_team_estimate.fill_between(team_total['week_start'], team_total['total_estimate_weeks'], alpha=0.2, color='#A23B72')
    ax_team_estimate.set_title(f"{team_display_name} — Team Total Estimate (weeks)", fontsize=10, fontweight='bold')
    ax_team_estimate.set_ylabel('Weeks', fontsize=9)
    ax_team_estimate.grid(True, alpha=0.3)

    # Team drag total (if available)
    if drag_enabled:
        team_drag_total = team_drag_total_pre.copy()
        team_drag_total['week_start'] = pd.to_datetime(team_drag_total['week_start'])
        ax_team_drag.plot(team_drag_total['week_start'], team_drag_total['drag_hours_cumsum'], marker='o', color='#E63946', linewidth=2, markersize=5)
        ax_team_drag.fill_between(team_drag_total['week_start'], team_drag_total['drag_hours_cumsum'], alpha=0.2, color='#E63946')
        ax_team_drag.set_title(f"{team_display_name} — Team Total Drag (hrs)", fontsize=10, fontweight='bold')
        ax_team_drag.set_ylabel('Hours', fontsize=9)
        ax_team_drag.set_ylim(0, max_team_drag * 1.05)
        ax_team_drag.grid(True, alpha=0.3)
    else:
        ax_team_drag.text(0.5, 0.5, 'No drag data', ha='center', va='center', transform=ax_team_drag.transAxes, fontsize=9, color='gray')
        ax_team_drag.set_title(f"{team_display_name} — Team Total Drag (hrs)", fontsize=10, fontweight='bold')

    # Team tickets total (if available)
    if tickets_enabled:
        team_tickets_total = team_tickets_total_pre.copy()
        team_tickets_total['week_start'] = pd.to_datetime(team_tickets_total['week_start'])
        ax_team_tickets.plot(team_tickets_total['week_start'], team_tickets_total['ticket_count_cumsum'], marker='o', color='#06A77D', linewidth=2, markersize=5)
        ax_team_tickets.fill_between(team_tickets_total['week_start'], team_tickets_total['ticket_count_cumsum'], alpha=0.2, color='#06A77D')
        ax_team_tickets.set_title(f"{team_display_name} — Team Total Tickets Created", fontsize=10, fontweight='bold')
        ax_team_tickets.set_ylabel('Count', fontsize=9)
        ax_team_tickets.set_ylim(0, max_team_tickets * 1.05)
        ax_team_tickets.grid(True, alpha=0.3)
        ax_team_tickets.yaxis.set_major_locator(MaxNLocator(integer=True))
    else:
        ax_team_tickets.text(0.5, 0.5, 'No tickets data', ha='center', va='center', transform=ax_team_tickets.transAxes, fontsize=9, color='gray')
        ax_team_tickets.set_title(f"{team_display_name} — Team Total Tickets Created", fontsize=10, fontweight='bold')

    # Format team total x-axes
    for ax in [ax_team_issues, ax_team_estimate, ax_team_drag, ax_team_tickets]:
        ax.set_xlim(start_date, end_date + pd.Timedelta(days=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right', fontsize=8)
        ax.tick_params(axis='y', labelsize=8)

    # Combined individuals section (row 1)
    ax_ind_issues = fig.add_subplot(gs[1, 0])
    ax_ind_estimate = fig.add_subplot(gs[1, 1])
    ax_ind_tickets = fig.add_subplot(gs[1, 2])
    ax_ind_drag = fig.add_subplot(gs[1, 3])

    max_ind_issues = 0
    max_ind_estimate = 0

    for idx, user in enumerate(developers):
        user_df = team_df[team_df['user'] == user].sort_values('week_start')
        display_name = user_df['display_name'].iloc[0]
        marker = PLOT_MARKERS[idx % len(PLOT_MARKERS)]
        color = PLOT_COLORS[idx % len(PLOT_COLORS)]
        ax_ind_issues.plot(user_df['week_start'], user_df['issue_count'], marker=marker, color=color, label=display_name, linewidth=1.5)
        ax_ind_estimate.plot(user_df['week_start'], user_df['total_estimate_weeks'], marker=marker, color=color, label=display_name, linewidth=1.5)
        max_ind_issues = max(max_ind_issues, user_df['issue_count'].max())
        max_ind_estimate = max(max_ind_estimate, user_df['total_estimate_weeks'].max())

        # Add drag data for combined view
        if drag_enabled:
            user_drag_df = drag_df[drag_df['user'] == user].sort_values('week_start')
            if not user_drag_df.empty:
                user_drag_df = user_drag_df.copy()
                user_drag_df['week_start'] = pd.to_datetime(user_drag_df['week_start'])
                user_drag_df['drag_hours'] = pd.to_numeric(user_drag_df['drag_hours'], errors='coerce').fillna(0)
                user_drag_df['drag_cumsum'] = user_drag_df['drag_hours'].cumsum()
                ax_ind_drag.plot(user_drag_df['week_start'], user_drag_df['drag_cumsum'], marker=marker, color=color, label=display_name, linewidth=1.5)

        # Add tickets data for combined view
        if tickets_enabled:
            user_tickets_df = tickets_df[tickets_df['user'] == user].sort_values('week_start')
            if not user_tickets_df.empty:
                user_tickets_df = user_tickets_df.copy()
                user_tickets_df['week_start'] = pd.to_datetime(user_tickets_df['week_start'])
                user_tickets_df['ticket_count'] = pd.to_numeric(user_tickets_df['ticket_count'], errors='coerce').fillna(0)
                user_tickets_df['ticket_cumsum'] = user_tickets_df['ticket_count'].cumsum()
                ax_ind_tickets.plot(user_tickets_df['week_start'], user_tickets_df['ticket_cumsum'], marker=marker, color=color, label=display_name, linewidth=1.5)

    ax_ind_issues.set_title(f"{team_display_name} — All Developers - Cumulative Issues", fontsize=10, fontweight='bold')
    ax_ind_issues.set_ylabel('Issues', fontsize=9)
    ax_ind_issues.set_ylim(0, max_ind_issues * 1.05)
    ax_ind_issues.legend(loc='best', fontsize=8)
    ax_ind_issues.grid(True, alpha=0.3)
    ax_ind_issues.yaxis.set_major_locator(MaxNLocator(integer=True))

    ax_ind_estimate.set_title(f"{team_display_name} — All Developers - Cumulative Estimate", fontsize=10, fontweight='bold')
    ax_ind_estimate.set_ylabel('Estimate (weeks)', fontsize=9)
    ax_ind_estimate.set_ylim(0, max_ind_estimate * 1.05)
    ax_ind_estimate.legend(loc='best', fontsize=8)
    ax_ind_estimate.grid(True, alpha=0.3)

    if drag_enabled:
        ax_ind_drag.set_title(f"{team_display_name} — All Developers - Cumulative Drag", fontsize=10, fontweight='bold')
        ax_ind_drag.set_ylabel('Hours', fontsize=9)
        ax_ind_drag.set_ylim(0, max_dev_drag * 1.05)
        ax_ind_drag.legend(loc='best', fontsize=8)
        ax_ind_drag.grid(True, alpha=0.3)
    else:
        ax_ind_drag.text(0.5, 0.5, 'No drag data', ha='center', va='center', transform=ax_ind_drag.transAxes, fontsize=9, color='gray')
        ax_ind_drag.set_title(f"{team_display_name} — All Developers - Cumulative Drag", fontsize=10, fontweight='bold')

    if tickets_enabled:
        ax_ind_tickets.set_title(f"{team_display_name} — All Developers - Cumulative Tickets Created", fontsize=10, fontweight='bold')
        ax_ind_tickets.set_ylabel('Count', fontsize=9)
        ax_ind_tickets.set_ylim(0, max_dev_tickets * 1.05)
        ax_ind_tickets.legend(loc='best', fontsize=8)
        ax_ind_tickets.grid(True, alpha=0.3)
        ax_ind_tickets.yaxis.set_major_locator(MaxNLocator(integer=True))
    else:
        ax_ind_tickets.text(0.5, 0.5, 'No tickets data', ha='center', va='center', transform=ax_ind_tickets.transAxes, fontsize=9, color='gray')
        ax_ind_tickets.set_title(f"{team_display_name} — All Developers - Cumulative Tickets Created", fontsize=10, fontweight='bold')

    # Format combined individuals x-axes
    for ax in [ax_ind_issues, ax_ind_estimate, ax_ind_drag, ax_ind_tickets]:
        ax.set_xlim(start_date, end_date + pd.Timedelta(days=2))
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right', fontsize=8)
        ax.tick_params(axis='y', labelsize=8)

    # Calculate max values across all developers for consistent y-axis scaling
    max_dev_issues = 0
    max_dev_estimate = 0
    max_gh_prs_commits = 0
    max_gh_reviews_comments = 0

    for user in developers:
        user_df = team_df[team_df['user'] == user].sort_values('week_start')
        max_dev_issues = max(max_dev_issues, user_df['issue_count'].max())
        max_dev_estimate = max(max_dev_estimate, user_df['total_estimate_weeks'].max())

        if github_enabled:
            user_gh_df = team_github_df[team_github_df['user'] == user]
            if not user_gh_df.empty:
                max_gh_prs_commits = max(max_gh_prs_commits, user_gh_df['prs_opened'].max(), user_gh_df['commits'].max())
                max_gh_reviews_comments = max(max_gh_reviews_comments, user_gh_df['reviews_given'].max(), user_gh_df['comments_received'].max())

    # Plot individual developer breakdowns (rows 2+)
    dev_start_row = 2
    for idx, user in enumerate(developers):
        if github_enabled:
            jira_row = dev_start_row + (idx * 2)
            gh_row = jira_row + 1
        else:
            jira_row = dev_start_row + idx

        # Jira rows (issues, estimate, tickets, and drag)
        ax_issues = fig.add_subplot(gs[jira_row, 0])
        ax_estimate = fig.add_subplot(gs[jira_row, 1])
        ax_tickets = fig.add_subplot(gs[jira_row, 2])
        ax_drag = fig.add_subplot(gs[jira_row, 3])

        user_df = team_df[team_df['user'] == user].sort_values('week_start')
        display_name = user_df['display_name'].iloc[0]
        job_title = user_df['job_title'].iloc[0] if 'job_title' in user_df.columns else ''

        ax_issues.plot(user_df['week_start'], user_df['issue_count'], marker='o', color='#06A77D', linewidth=1.5, markersize=4)
        ax_issues.fill_between(user_df['week_start'], user_df['issue_count'], alpha=0.15, color='#06A77D')
        title_prefix = f"{display_name} ({job_title})" if job_title else display_name
        ax_issues.set_title(f"{title_prefix} — Issues", fontsize=9, fontweight='bold')
        ax_issues.set_ylabel('Count', fontsize=8)
        ax_issues.set_ylim(0, max_dev_issues * 1.05)
        ax_issues.grid(True, alpha=0.2)
        ax_issues.yaxis.set_major_locator(MaxNLocator(integer=True))

        ax_estimate.plot(user_df['week_start'], user_df['total_estimate_weeks'], marker='o', color='#F18F01', linewidth=1.5, markersize=4)
        ax_estimate.fill_between(user_df['week_start'], user_df['total_estimate_weeks'], alpha=0.15, color='#F18F01')
        ax_estimate.set_title(f"{title_prefix} — Estimate (weeks)", fontsize=9, fontweight='bold')
        ax_estimate.set_ylabel('Weeks', fontsize=8)
        ax_estimate.set_ylim(0, max_dev_estimate * 1.05)
        ax_estimate.grid(True, alpha=0.2)

        # Drag data for individual developer
        if drag_enabled:
            user_drag_df = drag_df[drag_df['user'] == user].sort_values('week_start')
            if not user_drag_df.empty:
                user_drag_df = user_drag_df.copy()
                user_drag_df['week_start'] = pd.to_datetime(user_drag_df['week_start'])
                user_drag_df['drag_cumsum'] = user_drag_df['drag_hours'].cumsum()
                ax_drag.plot(user_drag_df['week_start'], user_drag_df['drag_cumsum'], marker='o', color='#E63946', linewidth=1.5, markersize=4)
                ax_drag.fill_between(user_drag_df['week_start'], user_drag_df['drag_cumsum'], alpha=0.15, color='#E63946')
                ax_drag.set_title(f"{title_prefix} — Drag (hrs)", fontsize=9, fontweight='bold')
                ax_drag.set_ylabel('Hours', fontsize=8)
                ax_drag.set_ylim(0, max_dev_drag * 1.05)
                ax_drag.grid(True, alpha=0.2)
            else:
                ax_drag.text(0.5, 0.5, 'No drag', ha='center', va='center', transform=ax_drag.transAxes, fontsize=8, color='gray')
                ax_drag.set_title(f"{title_prefix} — Drag (hrs)", fontsize=9, fontweight='bold')
        else:
            ax_drag.text(0.5, 0.5, 'No drag data', ha='center', va='center', transform=ax_drag.transAxes, fontsize=8, color='gray')
            ax_drag.set_title(f"{title_prefix} — Drag (hrs)", fontsize=9, fontweight='bold')

        # Tickets data for individual developer
        if tickets_enabled:
            user_tickets_df = tickets_df[tickets_df['user'] == user].sort_values('week_start')
            if not user_tickets_df.empty:
                user_tickets_df = user_tickets_df.copy()
                user_tickets_df['week_start'] = pd.to_datetime(user_tickets_df['week_start'])
                user_tickets_df['ticket_cumsum'] = user_tickets_df['ticket_count'].cumsum()
                ax_tickets.plot(user_tickets_df['week_start'], user_tickets_df['ticket_cumsum'], marker='o', color='#06A77D', linewidth=1.5, markersize=4)
                ax_tickets.fill_between(user_tickets_df['week_start'], user_tickets_df['ticket_cumsum'], alpha=0.15, color='#06A77D')
                ax_tickets.set_title(f"{title_prefix} — Tickets Created", fontsize=9, fontweight='bold')
                ax_tickets.set_ylabel('Count', fontsize=8)
                ax_tickets.set_ylim(0, max_dev_tickets * 1.05)
                ax_tickets.grid(True, alpha=0.2)
                ax_tickets.yaxis.set_major_locator(MaxNLocator(integer=True))
            else:
                ax_tickets.text(0.5, 0.5, 'No tickets', ha='center', va='center', transform=ax_tickets.transAxes, fontsize=8, color='gray')
                ax_tickets.set_title(f"{title_prefix} — Tickets Created", fontsize=9, fontweight='bold')
        else:
            ax_tickets.text(0.5, 0.5, 'No tickets data', ha='center', va='center', transform=ax_tickets.transAxes, fontsize=8, color='gray')
            ax_tickets.set_title(f"{title_prefix} — Tickets Created", fontsize=9, fontweight='bold')

        # Format x-axis for individual subplots — fix range so MonthLocator is consistent
        for ax in [ax_issues, ax_estimate, ax_drag, ax_tickets]:
            ax.set_xlim(start_date, end_date + pd.Timedelta(days=2))
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
            ax.xaxis.set_major_locator(mdates.MonthLocator())
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right', fontsize=8)
            ax.tick_params(axis='y', labelsize=8)

        # GitHub rows (if available)
        if github_enabled:
            ax_gh_left = fig.add_subplot(gs[gh_row, 0])
            ax_gh_right = fig.add_subplot(gs[gh_row, 1])

            user_gh_df = team_github_df[team_github_df['user'] == user].sort_values('week_start')

            if not user_gh_df.empty:
                user_gh_df['week_start'] = pd.to_datetime(user_gh_df['week_start'])
                # Left: PRs + commits
                ax_gh_left.bar(user_gh_df['week_start'] - pd.Timedelta(days=1.5), user_gh_df['prs_opened'], width=1.5, label='PRs', alpha=0.8, color='#C1121F')
                ax_gh_left.bar(user_gh_df['week_start'] + pd.Timedelta(days=0), user_gh_df['commits'], width=1.5, label='Commits', alpha=0.8, color='#E94B3C')
                ax_gh_left.set_title(f"{title_prefix} — PRs & Commits", fontsize=8, fontweight='bold', style='italic')
                ax_gh_left.set_ylabel('Count', fontsize=7)
                ax_gh_left.set_ylim(0, max(max_gh_prs_commits * 1.1, 1))
                ax_gh_left.legend(loc='upper left', fontsize=7)
                ax_gh_left.grid(True, alpha=0.2, axis='y')
                ax_gh_left.yaxis.set_major_locator(MaxNLocator(integer=True))

                # Right: reviews + comments
                ax_gh_right.bar(user_gh_df['week_start'] - pd.Timedelta(days=1.5), user_gh_df['reviews_given'], width=1.5, label='Reviews', alpha=0.8, color='#457B9D')
                ax_gh_right.bar(user_gh_df['week_start'] + pd.Timedelta(days=0), user_gh_df['comments_received'], width=1.5, label='Comments', alpha=0.8, color='#A8DADC')
                ax_gh_right.set_title(f"{title_prefix} — Reviews & Comments", fontsize=8, fontweight='bold', style='italic')
                ax_gh_right.set_ylabel('Count', fontsize=7)
                ax_gh_right.set_ylim(0, max(max_gh_reviews_comments * 1.1, 1))
                ax_gh_right.legend(loc='upper left', fontsize=7)
                ax_gh_right.grid(True, alpha=0.2, axis='y')
                ax_gh_right.yaxis.set_major_locator(MaxNLocator(integer=True))
            else:
                ax_gh_left.text(0.5, 0.5, 'No GitHub data', ha='center', va='center', transform=ax_gh_left.transAxes, fontsize=8, color='gray')
                ax_gh_right.text(0.5, 0.5, 'No GitHub data', ha='center', va='center', transform=ax_gh_right.transAxes, fontsize=8, color='gray')
                ax_gh_left.set_title(f"{title_prefix} — GitHub", fontsize=8, fontweight='bold', style='italic')
                ax_gh_right.set_title(f"{title_prefix} — GitHub", fontsize=8, fontweight='bold', style='italic')

            # Format GitHub x-axes
            for ax in [ax_gh_left, ax_gh_right]:
                ax.set_xlim(start_date, end_date + pd.Timedelta(days=2))
                ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
                ax.xaxis.set_major_locator(mdates.MonthLocator())
                plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right', fontsize=7)
                ax.tick_params(axis='y', labelsize=7)

    weeks = (end_date.date() - start_date.date()).days // 7 + 1
    fig.suptitle(f"{team_display_name} — {start_date.date()} to {end_date.date()} ({weeks} weeks)",
                 fontsize=20, fontweight='bold')

    overall_file = f"{report_prefix}_{team_name}_overall.png"
    plt.savefig(overall_file, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"{Fore.GREEN}Generated {overall_file}{Style.RESET_ALL}")


def generate_overlay_chart(agg_df, report_prefix, start_date, end_date):
    """Generate overlay chart showing all teams.

    Args:
        agg_df: Full cumulative DataFrame (already cumulative per user)
        report_prefix: PNG prefix
        start_date: Report start date
        end_date: Report end date
    """
    if agg_df.empty:
        return

    agg_df = agg_df.copy()
    agg_df['week_start'] = pd.to_datetime(agg_df['week_start'])

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

    for idx, team in enumerate(agg_df['team'].unique()):
        team_df = agg_df[agg_df['team'] == team].copy()
        # Forward-fill missing weeks per user so cumulative values carry forward
        all_weeks = pd.date_range(team_df['week_start'].min(), team_df['week_start'].max(), freq='W-MON')

        filled_rows = []
        for user in team_df['user'].unique():
            user_df = team_df[team_df['user'] == user].set_index('week_start').sort_index()
            user_df = user_df.reindex(all_weeks)
            user_df = user_df.ffill()  # Forward fill to carry last cumulative values forward
            user_df['user'] = user
            user_df['team'] = team
            filled_rows.append(user_df.reset_index().rename(columns={'index': 'week_start'}))

        team_df = pd.concat(filled_rows, ignore_index=True)
        team_df = team_df.dropna(subset=['issue_count'])  # Remove rows that had no data

        # Now sum cumulative values per week across all users
        team_total = team_df.groupby('week_start').agg({
            'issue_count': 'sum',
            'total_estimate_weeks': 'sum'
        }).reset_index().sort_values('week_start')

        marker = PLOT_MARKERS[idx % len(PLOT_MARKERS)]
        color = PLOT_COLORS[idx % len(PLOT_COLORS)]
        ax1.plot(team_total['week_start'], team_total['issue_count'], marker=marker, color=color, label=team, linewidth=2)
        ax2.plot(team_total['week_start'], team_total['total_estimate_weeks'], marker=marker, color=color, label=team, linewidth=2)

    ax1.set_title("All Teams — Cumulative Issue Count", fontsize=12, fontweight='bold')
    ax1.set_ylabel('Issue Count', fontsize=10)
    ax1.legend(loc='best', fontsize=9)
    ax1.grid(True, alpha=0.3)
    ax1.yaxis.set_major_locator(MaxNLocator(integer=True))

    ax2.set_title("All Teams — Cumulative Original Estimate (weeks)", fontsize=12, fontweight='bold')
    ax2.set_xlabel('Week of', fontsize=10)
    ax2.set_ylabel('Estimate (weeks)', fontsize=10)
    ax2.legend(loc='best', fontsize=9)
    ax2.grid(True, alpha=0.3)

    for ax in [ax1, ax2]:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

    weeks = (end_date.date() - start_date.date()).days // 7 + 1
    fig.text(0.5, 0.98, f"All Teams — {start_date.date()} to {end_date.date()} ({weeks} weeks)",
             ha='center', va='top', fontsize=13, fontweight='bold')

    plt.tight_layout(rect=[0, 0, 1, 0.94])
    overlay_file = f"{report_prefix}_overlay.png"
    plt.savefig(overlay_file, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"{Fore.GREEN}Generated {overlay_file}{Style.RESET_ALL}")


def print_summary(agg_df, raw_issues):
    """Print summary statistics.

    Args:
        agg_df: Aggregated DataFrame
        raw_issues: List of raw issues
    """
    if agg_df.empty or raw_issues is None or not raw_issues:
        print(f"\n{Fore.CYAN}No issues found.{Style.RESET_ALL}")
        return

    print(f"\n{Fore.CYAN}{'=' * 60}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Summary:{Style.RESET_ALL}")
    print(f"  Total issues: {len(raw_issues)}")

    if not agg_df.empty:
        total_estimate = agg_df['total_estimate_weeks'].sum()
        print(f"  Total estimate (weeks): {total_estimate:.1f}")
        print(f"\n{Fore.CYAN}Per User:{Style.RESET_ALL}")

        user_summary = agg_df.groupby(['team', 'user', 'display_name']).agg({
            'issue_count': 'sum',
            'total_estimate_weeks': 'sum'
        }).reset_index().sort_values('total_estimate_weeks', ascending=False)

        for _, row in user_summary.iterrows():
            print(f"  {Fore.GREEN}{row['display_name']}{Style.RESET_ALL} ({row['user']}) "
                  f"— {int(row['issue_count'])} issues, "
                  f"{row['total_estimate_weeks']:.1f} weeks [{row['team']}]")


def main():
    init()

    args = parse_arguments()
    args.parallel = max(1, min(15, args.parallel))

    date_clause, start_date, upper_date_clause, explicit_end_date = parse_period(args.period)
    if date_clause is None:
        sys.exit(1)

    end_date = explicit_end_date or datetime.now()

    config = load_config()
    backstage_url = get_backstage_url(config, args.backstageUrl)
    if not backstage_url:
        print(f"{Fore.RED}Error: No Backstage URL configured.{Style.RESET_ALL}")
        sys.exit(1)

    # Load day_size from config (default: 6 hours)
    day_size = config.get('day_size', 6)

    # Get GitHub config (optional)
    github_org = args.githubOrg or config.get('github_org')
    github_token = config.get('github_token')
    github_username_transform = config.get('github_username_transform')

    # Jira client
    try:
        jira_client = jira.JIRA(
            config["jira_server"],
            token_auth=config["personal_access_token"],
        )
    except Exception as e:
        print(f"{Fore.RED}Error connecting to Jira: {e}{Style.RESET_ALL}")
        sys.exit(1)

    # Get teams
    if args.teams.strip() in ('org', '*'):
        org_teams = config.get('orgTeams', [])
        if org_teams:
            teams = org_teams
            print(f"{Fore.GREEN}Loaded {len(teams)} team(s) from config{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}Warning: 'orgTeams' not found in config, fetching all teams from Backstage...{Style.RESET_ALL}")
            all_teams = get_all_teams(backstage_url)
            teams = [t.get('metadata', {}).get('name', '') for t in all_teams]
            teams = [t for t in teams if t]
            print(f"{Fore.GREEN}Found {len(teams)} team(s){Style.RESET_ALL}")
    elif args.teams.strip() == 'all':
        print(f"{Fore.CYAN}Fetching all teams from Backstage...{Style.RESET_ALL}")
        all_teams = get_all_teams(backstage_url)
        teams = [t.get('metadata', {}).get('name', '') for t in all_teams]
        teams = [t for t in teams if t]
        print(f"{Fore.GREEN}Found {len(teams)} team(s){Style.RESET_ALL}")
    else:
        teams = [t.strip() for t in args.teams.split(",") if t.strip()]

    if not teams:
        print(f"{Fore.RED}Error: No valid team names.{Style.RESET_ALL}")
        sys.exit(1)

    # Get all teams to map names to entities (case-insensitive matching)
    all_team_entities = get_all_teams(backstage_url)
    team_map = {t.get('metadata', {}).get('name', ''): t for t in all_team_entities}
    team_map_lower = {name.lower(): (name, entity) for name, entity in team_map.items()}

    # Excluded job title keywords (alphabetized)
    excluded_keywords = ["Analyst", "Architect", "Director", "Manager", "Product", "Program", "Project", "Scrum"]

    # Collect user queries
    user_queries = []
    for team_name in teams:
        team_name_lower = team_name.lower()
        if team_name_lower not in team_map_lower:
            print(f"{Fore.YELLOW}Warning: Team '{team_name}' not found in Backstage{Style.RESET_ALL}")
            continue

        actual_team_name, team_entity = team_map_lower[team_name_lower]
        members = get_team_members(backstage_url, team_entity)

        if not members:
            print(f"{Fore.YELLOW}Warning: No members found for team '{actual_team_name}'{Style.RESET_ALL}")
            continue

        print(f"{Fore.CYAN}Team: {actual_team_name} ({len(members)} member(s)){Style.RESET_ALL}")

        for member in members:
            job_title = member.get('job_title', '')
            # Skip if job title contains any excluded keywords (case-insensitive)
            if any(keyword.lower() in job_title.lower() for keyword in excluded_keywords):
                continue

            jql = build_jql_for_user(member['username'], date_clause, upper_date_clause)
            github_username = derive_github_username(member['username'], github_username_transform) if github_org else ""
            user_queries.append((member['username'], member['display_name'], job_title, team_name, jql, github_username))

    if not user_queries:
        print(f"{Fore.RED}No users to query.{Style.RESET_ALL}")
        sys.exit(1)

    # Print GitHub username mapping if GitHub is enabled (Phase 1 verification)
    if github_org:
        print(f"\n{Fore.CYAN}GitHub Username Mapping (org: {github_org}){Style.RESET_ALL}")
        for username, display_name, job_title, team_name, jql, github_username in user_queries:
            print(f"  {username} ({display_name}) → {github_username}")

    # Query in parallel
    print(f"\n{Fore.CYAN}Querying Jira for {len(user_queries)} user(s) ({args.parallel} workers)...{Style.RESET_ALL}")

    raw_issues = []
    completed = 0

    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {
            executor.submit(query_user_issues, jira_client, username, display_name, job_title, team_name, jql): (username, display_name, team_name, github_username)
            for username, display_name, job_title, team_name, jql, github_username in user_queries
        }

        for future in as_completed(futures):
            completed += 1
            print(f"\r{Fore.CYAN}  Progress: {completed}/{len(user_queries)} users queried{Style.RESET_ALL}    ", end="", flush=True)
            try:
                issues = future.result()
                raw_issues.extend(issues)
            except Exception as e:
                print(f"\n{Fore.YELLOW}Warning: Error querying user: {e}{Style.RESET_ALL}")

    print()  # newline after progress

    if not raw_issues:
        print(f"{Fore.YELLOW}No issues found for the given time period.{Style.RESET_ALL}")
        sys.exit(0)

    # Create DataFrame and aggregate
    df = pd.DataFrame(raw_issues)
    df['resolved_date'] = pd.to_datetime(df['resolved_date'])

    agg_df = aggregate_to_weekly(df, day_size=day_size)

    # Fetch GitHub metrics (Phase 2c)
    github_df = pd.DataFrame()
    if github_token and github_org:
        print(f"\n{Fore.CYAN}Fetching GitHub metrics...{Style.RESET_ALL}")
        try:
            session = get_github_session(github_token)
            github_rows = []

            with ThreadPoolExecutor(max_workers=args.parallel) as executor:
                futures = {
                    executor.submit(
                        get_github_metrics_for_user, session,
                        github_username, github_org, start_date, end_date
                    ): (username, display_name, team_name)
                    for username, display_name, job_title, team_name, jql, github_username in user_queries
                    if github_username  # skip users with no GitHub username
                }

                completed_gh = 0
                for future in as_completed(futures):
                    completed_gh += 1
                    username, display_name, team_name = futures[future]
                    try:
                        metrics = future.result()
                        for row in metrics:
                            row['user'] = username
                            row['team'] = team_name
                            github_rows.append(row)
                    except Exception as e:
                        print(f"{Fore.YELLOW}Warning: Error fetching GitHub metrics for {username}: {e}{Style.RESET_ALL}")

            if github_rows:
                github_df = pd.DataFrame(github_rows)
                print(f"{Fore.GREEN}✓ Fetched GitHub metrics for {len(set(github_rows[i]['user'] for i in range(len(github_rows))))} user(s){Style.RESET_ALL}")
            else:
                print(f"{Fore.YELLOW}No GitHub activity found for the given time period.{Style.RESET_ALL}")

        except Exception as e:
            print(f"{Fore.YELLOW}Warning: GitHub fetch failed: {e}{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}Continuing with Jira-only metrics.{Style.RESET_ALL}")
            github_df = pd.DataFrame()

    # Query Sprint Drag in parallel (same users, new JQL)
    print(f"\n{Fore.CYAN}Querying Jira for Sprint Drag...{Style.RESET_ALL}")
    drag_jql_queries = [
        (username, display_name, job_title, team_name,
         build_jql_drag_for_user(username, date_clause, upper_date_clause))
        for username, display_name, job_title, team_name, jql, github_username in user_queries
    ]

    raw_drag_issues = []
    completed_drag = 0

    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {
            executor.submit(query_user_drag_issues, jira_client, *q): q
            for q in drag_jql_queries
        }

        for future in as_completed(futures):
            completed_drag += 1
            print(f"\r{Fore.CYAN}  Progress: {completed_drag}/{len(drag_jql_queries)} users queried for drag{Style.RESET_ALL}    ", end="", flush=True)
            try:
                issues = future.result()
                raw_drag_issues.extend(issues)
            except Exception as e:
                print(f"\n{Fore.YELLOW}Warning: Error querying drag issues: {e}{Style.RESET_ALL}")

    print()  # newline after progress

    # Aggregate drag data
    drag_agg_df = pd.DataFrame()
    if raw_drag_issues:
        drag_agg_df = aggregate_drag_to_weekly(raw_drag_issues)
        cum_drag_df = make_drag_cumulative(drag_agg_df)
        print(f"{Fore.GREEN}✓ Found {len(raw_drag_issues)} Sprint Drag issue(s){Style.RESET_ALL}")
    else:
        print(f"{Fore.YELLOW}No Sprint Drag issues found for the given time period.{Style.RESET_ALL}")
        cum_drag_df = pd.DataFrame()

    # Query Tickets Created in parallel (same users, new JQL)
    print(f"\n{Fore.CYAN}Querying Jira for Tickets Created...{Style.RESET_ALL}")
    tickets_jql_queries = [
        (username, display_name, job_title, team_name,
         build_jql_tickets_created_for_user(username, date_clause, upper_date_clause))
        for username, display_name, job_title, team_name, jql, github_username in user_queries
    ]

    raw_tickets_issues = []
    completed_tickets = 0

    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {
            executor.submit(query_user_tickets_created, jira_client, *q): q
            for q in tickets_jql_queries
        }

        for future in as_completed(futures):
            completed_tickets += 1
            print(f"\r{Fore.CYAN}  Progress: {completed_tickets}/{len(tickets_jql_queries)} users queried for tickets{Style.RESET_ALL}    ", end="", flush=True)
            try:
                issues = future.result()
                raw_tickets_issues.extend(issues)
            except Exception as e:
                print(f"\n{Fore.YELLOW}Warning: Error querying created tickets: {e}{Style.RESET_ALL}")

    print()  # newline after progress

    # Aggregate tickets data
    tickets_agg_df = pd.DataFrame()
    if raw_tickets_issues:
        tickets_agg_df = aggregate_tickets_to_weekly(raw_tickets_issues)
        cum_tickets_df = make_tickets_cumulative(tickets_agg_df)
        print(f"{Fore.GREEN}✓ Found {len(raw_tickets_issues)} Tickets Created{Style.RESET_ALL}")
    else:
        print(f"{Fore.YELLOW}No Tickets Created found for the given time period.{Style.RESET_ALL}")
        cum_tickets_df = pd.DataFrame()

    # Print summary
    print_summary(agg_df, raw_issues)

    # Print GitHub summary if available
    if not github_df.empty:
        print_github_summary(github_df)

    # Export CSV
    if args.output:
        print(f"\n{Fore.CYAN}Exporting CSV...{Style.RESET_ALL}")
        export_csv(raw_issues, agg_df, args.output, day_size=day_size, github_df=github_df, drag_agg_df=drag_agg_df, tickets_agg_df=tickets_agg_df)

    # Generate charts with cumulative data
    if args.filePrefix:
        print(f"\n{Fore.CYAN}Generating charts...{Style.RESET_ALL}")
        cum_df = make_cumulative(agg_df)
        unique_teams = cum_df['team'].unique()
        for team_name in unique_teams:
            team_df = cum_df[cum_df['team'] == team_name]
            team_drag_df = cum_drag_df[cum_drag_df['team'] == team_name] if not cum_drag_df.empty else pd.DataFrame()
            team_tickets_df = cum_tickets_df[cum_tickets_df['team'] == team_name] if not cum_tickets_df.empty else pd.DataFrame()
            # generate_team_chart(team_name, team_df, args.filePrefix, start_date, end_date)
            generate_team_overall_report(team_name, team_df, args.filePrefix, start_date, end_date, github_df=github_df, drag_df=team_drag_df, tickets_df=team_tickets_df)
        # Only generate overlay if multiple teams
        if len(unique_teams) > 1:
            generate_overlay_chart(cum_df, args.filePrefix, start_date, end_date)


if __name__ == "__main__":
    main()

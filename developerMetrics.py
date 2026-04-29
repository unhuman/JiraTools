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


def export_csv(raw_issues, agg_df, output_prefix, day_size=6):
    """Export raw and aggregated data to CSV files.

    Args:
        raw_issues: List of raw issue dicts
        agg_df: Aggregated DataFrame
        output_prefix: Output file prefix
        day_size: Work hours per day (default: 6)
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

    # Aggregated CSV
    if not agg_df.empty:
        agg_df_sorted = agg_df.sort_values(['team', 'user', 'week_start'])
        agg_df_sorted['Week Start'] = agg_df_sorted['week_start'].dt.strftime('%Y-%m-%d')
        agg_df_sorted[['team', 'user', 'display_name', 'Week Start', 'issue_count', 'total_estimate_weeks']].to_csv(
            agg_file, index=False,
            header=['Team', 'User', 'Display Name', 'Week Start', 'Issue Count', 'Total Estimate (weeks)']
        )
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

    # Convert week_start to datetime for plotting
    team_df = team_df.copy()
    team_df['week_start'] = pd.to_datetime(team_df['week_start'])

    # Individual chart
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(12, 8))

    for user in team_df['user'].unique():
        user_df = team_df[team_df['user'] == user].sort_values('week_start')
        display_name = user_df['display_name'].iloc[0]
        ax1.plot(user_df['week_start'], user_df['issue_count'], marker='o', label=display_name, linewidth=2)
        ax2.plot(user_df['week_start'], user_df['total_estimate_weeks'], marker='o', label=display_name, linewidth=2)

    ax1.set_title(f"{team_name} — Cumulative Issue Count", fontsize=12, fontweight='bold')
    ax1.set_ylabel('Issue Count', fontsize=10)
    ax1.legend(loc='best', fontsize=9)
    ax1.grid(True, alpha=0.3)
    ax1.yaxis.set_major_locator(MaxNLocator(integer=True))

    ax2.set_title(f"{team_name} — Cumulative Original Estimate (weeks)", fontsize=12, fontweight='bold')
    ax2.set_xlabel('Week of', fontsize=10)
    ax2.set_ylabel('Estimate (weeks)', fontsize=10)
    ax2.legend(loc='best', fontsize=9)
    ax2.grid(True, alpha=0.3)

    for ax in [ax1, ax2]:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

    weeks = (end_date.date() - start_date.date()).days // 7 + 1
    fig.text(0.99, 0.01, f"Period: {start_date.date()} to {end_date.date()} | {weeks} weeks",
             ha='right', va='bottom', fontsize=9, style='italic')

    plt.tight_layout(rect=[0, 0.02, 1, 1])
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
    ax1.set_title(f"{team_name} — Cumulative Issue Count (Team Total)", fontsize=12, fontweight='bold')
    ax1.set_ylabel('Issue Count', fontsize=10)
    ax1.grid(True, alpha=0.3)
    ax1.yaxis.set_major_locator(MaxNLocator(integer=True))

    ax2.plot(team_total['week_start'], team_total['total_estimate_weeks'], marker='o', color='#A23B72', linewidth=2.5, markersize=6)
    ax2.fill_between(team_total['week_start'], team_total['total_estimate_weeks'], alpha=0.3, color='#A23B72')
    ax2.set_title(f"{team_name} — Cumulative Original Estimate (weeks) (Team Total)", fontsize=12, fontweight='bold')
    ax2.set_xlabel('Week of', fontsize=10)
    ax2.set_ylabel('Estimate (weeks)', fontsize=10)
    ax2.grid(True, alpha=0.3)

    for ax in [ax1, ax2]:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right')

    weeks = (end_date.date() - start_date.date()).days // 7 + 1
    fig.text(0.99, 0.01, f"Period: {start_date.date()} to {end_date.date()} | {weeks} weeks",
             ha='right', va='bottom', fontsize=9, style='italic')

    plt.tight_layout(rect=[0, 0.02, 1, 1])
    total_file = f"{report_prefix}_{team_name}_total.png"
    plt.savefig(total_file, dpi=100, bbox_inches='tight')
    plt.close()
    print(f"{Fore.GREEN}Generated {total_file}{Style.RESET_ALL}")


def generate_team_overall_report(team_name, team_df, report_prefix, start_date, end_date):
    """Generate a comprehensive single-page team report with team totals, individuals combined, then individual breakdowns.

    Args:
        team_name: Team name
        team_df: Cumulative DataFrame filtered to this team
        report_prefix: PNG prefix
        start_date: Report start date
        end_date: Report end date
    """
    if team_df.empty:
        return

    team_df = team_df.copy()
    team_df['week_start'] = pd.to_datetime(team_df['week_start'])

    # Get sorted list of developers
    developers = sorted(team_df['user'].unique())
    num_developers = len(developers)

    # Grid layout: 1 row per section (team, combined, + one per dev)
    # Use height_ratios to make team/combined proportionally taller
    total_rows = 2 + num_developers
    height_ratios = [2.5, 2.5] + [1] * num_developers

    # Physical height: team + combined at 3.5 inches each, devs at 2 inches each
    fig_height = 3.5 + 3.5 + (num_developers * 2.0)
    fig = plt.figure(figsize=(14, fig_height), constrained_layout=True)

    # Create grid with height ratios (no manual hspace/wspace needed)
    gs = fig.add_gridspec(total_rows, 2, height_ratios=height_ratios, wspace=0.3)

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

    # Team totals section (row 0)
    ax_team_issues = fig.add_subplot(gs[0, 0])
    ax_team_estimate = fig.add_subplot(gs[0, 1])

    ax_team_issues.plot(team_total['week_start'], team_total['issue_count'], marker='o', color='#2E86AB', linewidth=2, markersize=5)
    ax_team_issues.fill_between(team_total['week_start'], team_total['issue_count'], alpha=0.2, color='#2E86AB')
    ax_team_issues.set_title(f"{team_name} — Team Total Issue Count", fontsize=10, fontweight='bold')
    ax_team_issues.set_ylabel('Issues', fontsize=9)
    ax_team_issues.grid(True, alpha=0.3)
    ax_team_issues.yaxis.set_major_locator(MaxNLocator(integer=True))

    ax_team_estimate.plot(team_total['week_start'], team_total['total_estimate_weeks'], marker='o', color='#A23B72', linewidth=2, markersize=5)
    ax_team_estimate.fill_between(team_total['week_start'], team_total['total_estimate_weeks'], alpha=0.2, color='#A23B72')
    ax_team_estimate.set_title(f"{team_name} — Team Total Estimate (weeks)", fontsize=10, fontweight='bold')
    ax_team_estimate.set_ylabel('Weeks', fontsize=9)
    ax_team_estimate.grid(True, alpha=0.3)

    # Format team total x-axes
    for ax in [ax_team_issues, ax_team_estimate]:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right', fontsize=8)
        ax.tick_params(axis='y', labelsize=8)

    # Combined individuals section (row 1)
    ax_ind_issues = fig.add_subplot(gs[1, 0])
    ax_ind_estimate = fig.add_subplot(gs[1, 1])

    for user in developers:
        user_df = team_df[team_df['user'] == user].sort_values('week_start')
        display_name = user_df['display_name'].iloc[0]
        ax_ind_issues.plot(user_df['week_start'], user_df['issue_count'], marker='o', label=display_name, linewidth=1.5)
        ax_ind_estimate.plot(user_df['week_start'], user_df['total_estimate_weeks'], marker='o', label=display_name, linewidth=1.5)

    ax_ind_issues.set_title(f"{team_name} — All Developers - Cumulative Issues", fontsize=10, fontweight='bold')
    ax_ind_issues.set_ylabel('Issues', fontsize=9)
    ax_ind_issues.legend(loc='best', fontsize=8)
    ax_ind_issues.grid(True, alpha=0.3)
    ax_ind_issues.yaxis.set_major_locator(MaxNLocator(integer=True))

    ax_ind_estimate.set_title(f"{team_name} — All Developers - Cumulative Estimate", fontsize=10, fontweight='bold')
    ax_ind_estimate.set_ylabel('Estimate (weeks)', fontsize=9)
    ax_ind_estimate.legend(loc='best', fontsize=8)
    ax_ind_estimate.grid(True, alpha=0.3)

    # Format combined individuals x-axes
    for ax in [ax_ind_issues, ax_ind_estimate]:
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        ax.xaxis.set_major_locator(mdates.MonthLocator())
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right', fontsize=8)
        ax.tick_params(axis='y', labelsize=8)

    # Plot individual developer breakdowns (rows 2+)
    dev_start_row = 2
    for idx, user in enumerate(developers):
        row = dev_start_row + idx
        ax_issues = fig.add_subplot(gs[row, 0])
        ax_estimate = fig.add_subplot(gs[row, 1])

        user_df = team_df[team_df['user'] == user].sort_values('week_start')
        display_name = user_df['display_name'].iloc[0]
        job_title = user_df['job_title'].iloc[0] if 'job_title' in user_df.columns else ''

        ax_issues.plot(user_df['week_start'], user_df['issue_count'], marker='o', color='#06A77D', linewidth=1.5, markersize=4)
        ax_issues.fill_between(user_df['week_start'], user_df['issue_count'], alpha=0.15, color='#06A77D')
        title_prefix = f"{display_name} ({job_title})" if job_title else display_name
        ax_issues.set_title(f"{title_prefix} — Issues", fontsize=9, fontweight='bold')
        ax_issues.set_ylabel('Count', fontsize=8)
        ax_issues.grid(True, alpha=0.2)
        ax_issues.yaxis.set_major_locator(MaxNLocator(integer=True))

        ax_estimate.plot(user_df['week_start'], user_df['total_estimate_weeks'], marker='o', color='#F18F01', linewidth=1.5, markersize=4)
        ax_estimate.fill_between(user_df['week_start'], user_df['total_estimate_weeks'], alpha=0.15, color='#F18F01')
        ax_estimate.set_title(f"{title_prefix} — Estimate (weeks)", fontsize=9, fontweight='bold')
        ax_estimate.set_ylabel('Weeks', fontsize=8)
        ax_estimate.grid(True, alpha=0.2)

        # Format x-axis for individual subplots
        for ax in [ax_issues, ax_estimate]:
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
            ax.xaxis.set_major_locator(mdates.MonthLocator())
            plt.setp(ax.xaxis.get_majorticklabels(), rotation=45, ha='right', fontsize=8)
            ax.tick_params(axis='y', labelsize=8)

    weeks = (end_date.date() - start_date.date()).days // 7 + 1
    fig.text(0.99, 0.01, f"Period: {start_date.date()} to {end_date.date()} | {weeks} weeks",
             ha='right', va='bottom', fontsize=8, style='italic')

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

    for team in agg_df['team'].unique():
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

        ax1.plot(team_total['week_start'], team_total['issue_count'], marker='o', label=team, linewidth=2)
        ax2.plot(team_total['week_start'], team_total['total_estimate_weeks'], marker='o', label=team, linewidth=2)

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
    fig.text(0.99, 0.01, f"Period: {start_date.date()} to {end_date.date()} | {weeks} weeks",
             ha='right', va='bottom', fontsize=9, style='italic')

    plt.tight_layout(rect=[0, 0.02, 1, 1])
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
            user_queries.append((member['username'], member['display_name'], job_title, actual_team_name, jql))

    if not user_queries:
        print(f"{Fore.RED}No users to query.{Style.RESET_ALL}")
        sys.exit(1)

    # Query in parallel
    print(f"\n{Fore.CYAN}Querying Jira for {len(user_queries)} user(s) ({args.parallel} workers)...{Style.RESET_ALL}")

    raw_issues = []
    completed = 0

    with ThreadPoolExecutor(max_workers=args.parallel) as executor:
        futures = {
            executor.submit(query_user_issues, jira_client, username, display_name, job_title, team_name, jql): (username, display_name, team_name)
            for username, display_name, job_title, team_name, jql in user_queries
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

    # Print summary
    print_summary(agg_df, raw_issues)

    # Export CSV
    if args.output:
        print(f"\n{Fore.CYAN}Exporting CSV...{Style.RESET_ALL}")
        export_csv(raw_issues, agg_df, args.output, day_size=day_size)

    # Generate charts with cumulative data
    if args.filePrefix:
        print(f"\n{Fore.CYAN}Generating charts...{Style.RESET_ALL}")
        cum_df = make_cumulative(agg_df)
        unique_teams = cum_df['team'].unique()
        for team_name in unique_teams:
            team_df = cum_df[cum_df['team'] == team_name]
            # generate_team_chart(team_name, team_df, args.filePrefix, start_date, end_date)
            generate_team_overall_report(team_name, team_df, args.filePrefix, start_date, end_date)
        # Only generate overlay if multiple teams
        if len(unique_teams) > 1:
            generate_overlay_chart(cum_df, args.filePrefix, start_date, end_date)


if __name__ == "__main__":
    main()

"""GitHub integration utilities for developerMetrics."""

import requests
import time
import pandas as pd
from datetime import datetime, timedelta


def derive_github_username(backstage_username, transform_rules=None):
    """
    Transform a Backstage username to a GitHub username using configurable rules.

    Args:
        backstage_username: Backstage username (e.g., "john.doe")
        transform_rules: List of transformation rules from config (e.g., from github_username_transform).
                        If None, applies default Cvent convention.

    Returns:
        Transformed GitHub username, or empty string if backstage_username is None/empty.

    Example config:
        "github_username_transform": [
            {"op": "replace", "from": ".", "to": "-"},
            {"op": "append", "value": "_cvent"}
        ]

    Supported operations:
        - replace: {"op": "replace", "from": "X", "to": "Y"}
        - append: {"op": "append", "value": "X"}
        - prepend: {"op": "prepend", "value": "X"}
        - lowercase: {"op": "lowercase"}
        - uppercase: {"op": "uppercase"}
    """
    if not backstage_username:
        return ""

    result = backstage_username

    # Default Cvent convention if no rules provided
    if transform_rules is None:
        transform_rules = [
            {"op": "replace", "from": ".", "to": "-"},
            {"op": "append", "value": "_cvent"}
        ]

    # Apply each transformation rule in sequence
    for rule in transform_rules:
        op = rule.get("op")

        if op == "replace":
            from_str = rule.get("from", "")
            to_str = rule.get("to", "")
            result = result.replace(from_str, to_str)
        elif op == "append":
            value = rule.get("value", "")
            result = result + value
        elif op == "prepend":
            value = rule.get("value", "")
            result = value + result
        elif op == "lowercase":
            result = result.lower()
        elif op == "uppercase":
            result = result.upper()

    return result


def get_github_session(token):
    """
    Create an authenticated requests.Session for GitHub API.

    Args:
        token: GitHub Personal Access Token (PAT)

    Returns:
        requests.Session with Bearer auth and required headers
    """
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28"
    })
    return session


def github_search_all(session, url, params, date_field="created_at", max_results=1000):
    """
    Paginate through GitHub search results.

    GitHub search is limited to 1000 results per query. For larger result sets,
    this function breaks the query into smaller date windows.

    Args:
        session: Authenticated requests.Session
        url: GitHub API endpoint (e.g., "/search/issues")
        params: Query parameters (including "q" for the search query)
        date_field: Field name to use for date windowing on 422 responses
        max_results: Hard limit on total results returned

    Returns:
        List of result items from all pages
    """
    all_results = []
    page = 1

    while len(all_results) < max_results:
        params_copy = params.copy()
        params_copy["page"] = page
        params_copy["per_page"] = 100

        response = session.get(f"https://api.github.com{url}", params=params_copy)

        # Handle rate limiting
        if response.status_code in [403, 429]:
            remaining = response.headers.get("X-RateLimit-Remaining", "0")
            reset = response.headers.get("X-RateLimit-Reset")
            retry_after = response.headers.get("Retry-After")

            if remaining == "0" or response.status_code == 429:
                if retry_after:
                    sleep_seconds = int(retry_after)
                elif reset:
                    sleep_seconds = max(1, int(reset) - time.time() + 1)
                else:
                    sleep_seconds = 60

                print(f"GitHub rate limited. Sleeping {sleep_seconds} seconds...")
                time.sleep(sleep_seconds)
                continue  # Retry the same request

        response.raise_for_status()
        data = response.json()

        if "items" in data:
            items = data["items"]
            all_results.extend(items)

        # Check if there are more pages
        if len(items) < 100 or len(all_results) >= max_results:
            break

        page += 1

    return all_results[:max_results]


def get_github_metrics_for_user(session, github_username, github_org, start_date, end_date):
    """
    Fetch GitHub metrics for a user over a date range.

    Returns weekly aggregated metrics:
    - prs_opened: PRs authored by the user
    - commits: Commits authored by the user
    - reviews_given: PRs reviewed by the user (excluding own PRs)
    - comments_received: Comments on the user's PRs

    Args:
        session: Authenticated requests.Session
        github_username: GitHub username (e.g., "john-doe_cvent")
        github_org: GitHub organization (e.g., "cvent")
        start_date: Start date (datetime.date or datetime.datetime)
        end_date: End date (datetime.date or datetime.datetime)

    Returns:
        List of dicts with {week_start (date), prs_opened, commits, reviews_given, comments_received}
    """
    if not github_username:
        return []

    # Normalize dates
    if hasattr(start_date, "date"):
        start_date = start_date.date()
    if hasattr(end_date, "date"):
        end_date = end_date.date()

    # Format dates for GitHub API (ISO 8601)
    start_str = start_date.isoformat()
    end_str = end_date.isoformat()

    weekly_metrics = {}  # keyed by ISO week start date

    # Query 1: PRs opened by user
    try:
        query = f"type:pr author:{github_username} org:{github_org} created:{start_str}..{end_str}"
        prs = github_search_all(session, "/search/issues", {"q": query})

        for pr in prs:
            if pr.get("created_at"):
                week_start = _get_week_start(pr["created_at"])
                if week_start not in weekly_metrics:
                    weekly_metrics[week_start] = {
                        "prs_opened": 0,
                        "commits": 0,
                        "reviews_given": 0,
                        "comments_received": 0
                    }
                weekly_metrics[week_start]["prs_opened"] += 1
    except Exception as e:
        print(f"Error fetching PRs for {github_username}: {e}")

    # Query 2: Commits by user
    try:
        query = f"author:{github_username} org:{github_org} author-date:{start_str}..{end_str}"
        commits = github_search_all(session, "/search/commits", {"q": query})

        for commit in commits:
            if commit.get("commit", {}).get("author", {}).get("date"):
                week_start = _get_week_start(commit["commit"]["author"]["date"])
                if week_start not in weekly_metrics:
                    weekly_metrics[week_start] = {
                        "prs_opened": 0,
                        "commits": 0,
                        "reviews_given": 0,
                        "comments_received": 0
                    }
                weekly_metrics[week_start]["commits"] += 1
    except Exception as e:
        print(f"Error fetching commits for {github_username}: {e}")

    # Query 3: PRs reviewed by user (excluding own)
    try:
        query = f"type:pr reviewed-by:{github_username} org:{github_org} updated:{start_str}..{end_str}"
        reviewed_prs = github_search_all(session, "/search/issues", {"q": query})

        for pr in reviewed_prs:
            # Skip if user is the author (reviewed own PR)
            if pr.get("user", {}).get("login") != github_username and pr.get("updated_at"):
                week_start = _get_week_start(pr["updated_at"])
                if week_start not in weekly_metrics:
                    weekly_metrics[week_start] = {
                        "prs_opened": 0,
                        "commits": 0,
                        "reviews_given": 0,
                        "comments_received": 0
                    }
                weekly_metrics[week_start]["reviews_given"] += 1
    except Exception as e:
        print(f"Error fetching reviews for {github_username}: {e}")

    # Query 4: Comments on user's PRs
    try:
        query = f"type:pr author:{github_username} org:{github_org} comments:>0 updated:{start_str}..{end_str}"
        user_prs = github_search_all(session, "/search/issues", {"q": query})

        for pr in user_prs:
            if pr.get("comments", 0) > 0 and pr.get("updated_at"):
                week_start = _get_week_start(pr["updated_at"])
                if week_start not in weekly_metrics:
                    weekly_metrics[week_start] = {
                        "prs_opened": 0,
                        "commits": 0,
                        "reviews_given": 0,
                        "comments_received": 0
                    }
                weekly_metrics[week_start]["comments_received"] += pr["comments"]
    except Exception as e:
        print(f"Error fetching comments for {github_username}: {e}")

    # Convert to list of dicts with week_start as date object
    result = []
    for week_start_str, metrics in weekly_metrics.items():
        result.append({
            "week_start": _parse_date_str(week_start_str),
            **metrics
        })

    return result


def aggregate_github_weekly(metrics_list, start_date, end_date):
    """
    Aggregate GitHub metrics by week, ensuring all weeks in range are represented.

    Args:
        metrics_list: List of dicts from get_github_metrics_for_user()
        start_date: Start date (datetime.date)
        end_date: End date (datetime.date)

    Returns:
        DataFrame with columns: week_start, prs_opened, commits, reviews_given, comments_received
        All weeks in range included (with zeros for missing data)
    """
    if not metrics_list:
        return pd.DataFrame()

    df = pd.DataFrame(metrics_list)
    df["week_start"] = pd.to_datetime(df["week_start"]).dt.date

    # Create a complete date range by week
    all_weeks = pd.date_range(start=start_date, end=end_date, freq="W-MON").date

    # Reindex to include all weeks
    complete_df = df.set_index("week_start").reindex(all_weeks, fill_value=0).reset_index()
    complete_df.rename(columns={"index": "week_start"}, inplace=True)

    return complete_df


def print_github_summary(github_df):
    """
    Print a console summary of GitHub metrics by user.

    Args:
        github_df: DataFrame with columns: user, team, week_start, prs_opened, commits, reviews_given, comments_received
    """
    if github_df.empty:
        return

    print("\n" + "=" * 80)
    print("GitHub Activity Summary")
    print("=" * 80)

    # Group by user
    for username, user_data in github_df.groupby("user"):
        total_prs = user_data["prs_opened"].sum()
        total_commits = user_data["commits"].sum()
        total_reviews = user_data["reviews_given"].sum()
        total_comments = user_data["comments_received"].sum()

        team = user_data["team"].iloc[0] if "team" in user_data.columns else "Unknown"

        print(f"\n{username} ({team})")
        print(f"  PRs opened: {int(total_prs)}")
        print(f"  Commits: {int(total_commits)}")
        print(f"  Reviews given: {int(total_reviews)}")
        print(f"  Comments received: {int(total_comments)}")


def _get_week_start(date_str):
    """
    Convert an ISO 8601 date string to the Monday of that week.
    Returns as ISO 8601 string for consistent sorting/display.

    Args:
        date_str: ISO 8601 datetime string (e.g., "2026-04-30T12:34:56Z")

    Returns:
        ISO 8601 date string of the Monday of that week (e.g., "2026-04-27")
    """
    # Parse ISO 8601 string
    if "T" in date_str:
        dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
    else:
        dt = datetime.fromisoformat(date_str)

    # Get Monday of that week (weekday 0 = Monday)
    days_since_monday = dt.weekday()
    week_start = dt - timedelta(days=days_since_monday)

    return week_start.date().isoformat()


def _parse_date_str(date_str):
    """Parse ISO 8601 date string to date object."""
    if isinstance(date_str, str):
        return datetime.fromisoformat(date_str).date()
    return date_str

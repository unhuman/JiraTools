"""Datadog API client and configuration utilities."""

import json
import os
import time
import hashlib
import glob
import requests
import argparse
from typing import Dict, List, Optional
from collections import defaultdict
from colorama import Fore, Style

# Retry configuration for 500 errors
MAX_RETRIES_ON_500 = 3
RETRY_DELAY_SECONDS = 30

# Cache configuration
CACHE_DIR = 'requestCache'
CACHE_MAX_AGE_SECONDS = 86400  # 1 day


class DatadogClient:
    """Client for interacting with Datadog API with flexible authentication."""

    def __init__(self, host: str, pat: Optional[str] = None, api_key: Optional[str] = None,
                 app_key: Optional[str] = None, cookies: Optional[str] = None, timeout: int = 30,
                 rate_limit_delay: float = 1.0, preserve_rate_limit: int = 1, use_cache: bool = True,
                 ignore_cache_expiry: bool = False):
        """
        Initialize Datadog client with PAT, API keys, or cookie authentication.

        Args:
            host: Datadog site URL (e.g., 'https://app.datadoghq.com')
            pat: Personal Access Token (preferred; optional if API keys or cookies provided)
            api_key: Datadog API key (optional if PAT or cookies provided)
            app_key: Datadog application key (optional if PAT or cookies provided)
            cookies: Cookie string (semicolon separated) for authentication (optional if PAT or API keys provided)
            timeout: Request timeout in seconds
            rate_limit_delay: Delay between requests in seconds
            preserve_rate_limit: Number of requests to preserve from rate limit (default: 1)
            use_cache: Whether to use cached responses (default: True)
            ignore_cache_expiry: Whether to ignore cache expiration time (default: False)
        """
        self.host = host.rstrip('/')
        self.pat = pat
        self.api_key = api_key
        self.app_key = app_key
        self.cookies = cookies
        self.timeout = timeout
        self.rate_limit_delay = rate_limit_delay
        self.preserve_rate_limit = preserve_rate_limit
        self.last_request_time = 0
        self.rate_limit_total = None
        self.rate_limit_validated = False
        self.failed_500_errors = []
        self.use_cache = use_cache
        self.ignore_cache_expiry = ignore_cache_expiry

        if not os.path.exists(CACHE_DIR):
            os.makedirs(CACHE_DIR)
            print(f"{Fore.CYAN}[Cache] Created cache directory: {CACHE_DIR}{Style.RESET_ALL}")

        if not (pat or (api_key and app_key) or cookies):
            print(f"{Fore.YELLOW}Warning: No authentication provided{Style.RESET_ALL}")

    def _rate_limit(self):
        """Apply rate limiting between requests."""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.rate_limit_delay:
            sleep_time = self.rate_limit_delay - time_since_last
            print(f"{Fore.CYAN}[Rate Limit] Waiting {sleep_time:.2f}s before next request{Style.RESET_ALL}")
            time.sleep(sleep_time)
        self.last_request_time = time.time()

    def _check_rate_limit_headers(self, response):
        """Check rate limit headers and sleep if needed."""
        rate_headers = {k: v for k, v in response.headers.items() if 'rate' in k.lower() or 'limit' in k.lower()}
        if rate_headers:
            print(f"{Fore.CYAN}[Rate Limit] Headers: {rate_headers}{Style.RESET_ALL}")

        rate_limit_remaining = response.headers.get('x-ratelimit-remaining')
        rate_limit_reset = response.headers.get('x-ratelimit-reset')
        rate_limit_limit = response.headers.get('x-ratelimit-limit')
        rate_limit_name = response.headers.get('x-ratelimit-name', 'unknown')

        if rate_limit_remaining is not None:
            remaining = int(rate_limit_remaining)
            limit = int(rate_limit_limit) if rate_limit_limit else 100

            if not self.rate_limit_validated:
                self.rate_limit_total = limit
                self.rate_limit_validated = True

                if self.preserve_rate_limit >= limit:
                    print(f"{Fore.RED}[Rate Limit] ERROR: preserve-rate-limit ({self.preserve_rate_limit}) is >= total rate limit ({limit}){Style.RESET_ALL}")
                    raise ValueError(f"preserve-rate-limit ({self.preserve_rate_limit}) must be less than the rate limit total ({limit})")

                if self.preserve_rate_limit > 0:
                    print(f"{Fore.CYAN}[Rate Limit] Preserving {self.preserve_rate_limit} request(s) from rate limit of {limit}{Style.RESET_ALL}")
                else:
                    print(f"{Fore.CYAN}[Rate Limit] No rate limit preservation - will consume full limit of {limit}{Style.RESET_ALL}")

            print(f"{Fore.CYAN}[Rate Limit] {rate_limit_name}: {remaining}/{limit} remaining{Style.RESET_ALL}")

            if remaining == 0 and rate_limit_reset:
                reset_seconds = int(rate_limit_reset)
                self._countdown_sleep(reset_seconds, "LIMIT HIT! Sleeping")
            elif remaining <= self.preserve_rate_limit and rate_limit_reset:
                reset_seconds = int(rate_limit_reset)
                self._countdown_sleep(reset_seconds, f"Only {remaining} requests remaining (preserving {self.preserve_rate_limit}). Sleeping")

    def _countdown_sleep(self, seconds: int, message: str = "Sleeping", context: str = "Rate Limit"):
        """Sleep for the specified seconds with a countdown display."""
        import sys
        for remaining in range(seconds, 0, -1):
            sys.stdout.write(f"\r{Fore.YELLOW}[{context}] {message} {remaining}s...{Style.RESET_ALL}   ")
            sys.stdout.flush()
            time.sleep(1)
        sys.stdout.write(f"\r{Fore.GREEN}[{context}] Done waiting! Continuing...{Style.RESET_ALL}                              \n")
        sys.stdout.flush()

    def _get_headers(self) -> Dict[str, str]:
        """Get headers for authentication based on available credentials."""
        headers = {'Content-Type': 'application/json'}

        if self.pat:
            headers['Authorization'] = f'Bearer {self.pat}'
        elif self.api_key and self.app_key:
            headers['DD-API-KEY'] = self.api_key
            headers['DD-APPLICATION-KEY'] = self.app_key
        elif self.cookies:
            headers['Cookie'] = self.cookies

        return headers

    def _generate_cache_key(self, url: str, payload: Dict = None) -> str:
        """Generate a hash key for caching based on URL and payload."""
        cache_data = f"{url}:{json.dumps(payload, sort_keys=True) if payload else ''}"
        return hashlib.sha256(cache_data.encode()).hexdigest()

    def _get_cached_response(self, cache_key: str) -> Optional[Dict]:
        """Get cached response if available and not expired."""
        if not self.use_cache:
            return None

        pattern = f"{CACHE_DIR}/{cache_key}.*"
        cache_files = glob.glob(pattern)

        if not cache_files:
            return None

        def get_timestamp_from_filename(filepath):
            try:
                filename = filepath.split('/')[-1]
                parts = filename.split('.', 1)
                if len(parts) == 2:
                    return float(parts[1])
                return 0
            except (ValueError, IndexError):
                return 0

        cache_file = max(cache_files, key=get_timestamp_from_filename)

        try:
            filename = cache_file.split('/')[-1]
            parts = filename.split('.', 1)
            if len(parts) == 2:
                cache_time = float(parts[1])
            else:
                raise ValueError("Invalid filename format")
        except (ValueError, IndexError):
            print(f"{Fore.YELLOW}[Cache] Invalid cache filename format: {cache_file}{Style.RESET_ALL}")
            return None

        age_seconds = time.time() - cache_time
        if not self.ignore_cache_expiry and age_seconds > CACHE_MAX_AGE_SECONDS:
            print(f"{Fore.YELLOW}[Cache] Cache expired (age: {age_seconds/3600:.1f}h), deleting: {cache_file}{Style.RESET_ALL}")
            os.remove(cache_file)
            return None

        try:
            with open(cache_file, 'r') as f:
                cached_data = json.load(f)
            if self.ignore_cache_expiry and age_seconds > CACHE_MAX_AGE_SECONDS:
                print(f"{Fore.CYAN}[Cache] Using expired cached response (age: {age_seconds/3600:.1f}h, expiry ignored){Style.RESET_ALL}")
            else:
                print(f"{Fore.GREEN}[Cache] Using cached response (age: {age_seconds/3600:.1f}h){Style.RESET_ALL}")
            return cached_data
        except Exception as e:
            print(f"{Fore.YELLOW}[Cache] Error reading cache file: {e}{Style.RESET_ALL}")
            return None

    def _save_to_cache(self, cache_key: str, data: Dict):
        """Save response data to cache."""
        pattern = f"{CACHE_DIR}/{cache_key}.*"
        old_files = glob.glob(pattern)
        for old_file in old_files:
            try:
                os.remove(old_file)
                print(f"{Fore.CYAN}[Cache] Deleted old cache: {old_file}{Style.RESET_ALL}")
            except Exception as e:
                print(f"{Fore.YELLOW}[Cache] Error deleting old cache: {e}{Style.RESET_ALL}")

        timestamp = time.time()
        cache_file = f"{CACHE_DIR}/{cache_key}.{timestamp}"
        try:
            with open(cache_file, 'w') as f:
                json.dump(data, f)
            print(f"{Fore.GREEN}[Cache] Saved to cache: {cache_file}{Style.RESET_ALL}")
        except Exception as e:
            print(f"{Fore.YELLOW}[Cache] Error saving to cache: {e}{Style.RESET_ALL}")

    def save_errors_to_file(self, output_dir: str = '.') -> Optional[str]:
        """Save 500 errors to an errors.json file."""
        if not self.failed_500_errors:
            return None

        errors_file = f"{output_dir}/errors.json"
        error_report = {
            'total_errors': len(self.failed_500_errors),
            'timestamp': time.strftime('%Y-%m-%d %H:%M:%S'),
            'errors': self.failed_500_errors
        }

        with open(errors_file, 'w') as f:
            json.dump(error_report, f, indent=2)

        return errors_file

    def query_service_consumers(self, env: str, service: str, limit: int = 100, time_period: str = "1h", retry_count: int = 0) -> List[Dict]:
        """Query Datadog for services that consume (call) the specified service."""
        url = f"{self.host}/api/v2/spans/analytics/aggregate"

        print(f"{Fore.CYAN}[Request] Time period: {time_period}{Style.RESET_ALL}")

        query_string = f'@span.kind:client @peer.service:"{service}" env:{env}'
        group_by_facet = 'service'

        payload = {
            "data": {
                "attributes": {
                    "compute": [{"aggregation": "cardinality", "metric": "trace_id", "type": "total"}],
                    "filter": {"from": f"now-{time_period}", "to": "now", "query": query_string},
                    "group_by": [{"facet": group_by_facet, "limit": limit}]
                },
                "type": "aggregate_request"
            }
        }

        cache_key = self._generate_cache_key(url, payload)
        cached_response = self._get_cached_response(cache_key)
        if cached_response is not None:
            return self._parse_analytics_response(cached_response)

        self._rate_limit()

        print(f"{Fore.CYAN}[Request] POST {url}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}[Request] Query: {query_string}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}[Request] Counting: unique requests (cardinality of trace_id){Style.RESET_ALL}")

        try:
            response = requests.post(url, headers=self._get_headers(), json=payload, timeout=self.timeout)

            print(f"{Fore.CYAN}[Response] Status: {response.status_code}{Style.RESET_ALL}")
            self._check_rate_limit_headers(response)

            if response.status_code == 400:
                print(f"{Fore.YELLOW}[Response] Bad request{Style.RESET_ALL}")
                return []

            if response.status_code == 401:
                print(f"{Fore.RED}[Response] Authentication failed (401 Unauthorized){Style.RESET_ALL}")
                return []

            if response.status_code == 429:
                print(f"{Fore.YELLOW}[Rate Limit] Rate limit hit (429){Style.RESET_ALL}")
                self._countdown_sleep(5, "Rate limit exceeded, waiting", "Rate Limit")
                return self.query_service_consumers(env, service, limit, time_period)

            if response.status_code == 404:
                print(f"{Fore.YELLOW}[Response] Service {service} not found (404){Style.RESET_ALL}")
                return []

            if response.status_code == 200:
                data = response.json()
                print(f"{Fore.GREEN}[Response] Success - parsing analytics response{Style.RESET_ALL}")
                self._save_to_cache(cache_key, data)

                all_consumers = self._parse_analytics_response(data)

                meta = data.get('meta', {})
                page_info = meta.get('page', {})
                cursor = page_info.get('after')

                while cursor:
                    print(f"{Fore.CYAN}[Pagination] Found more results, fetching next page...{Style.RESET_ALL}")
                    self._rate_limit()

                    paginated_payload = payload.copy()
                    paginated_payload['data']['attributes']['page'] = {'cursor': cursor}

                    page_response = requests.post(url, headers=self._get_headers(), json=paginated_payload, timeout=self.timeout)
                    self._check_rate_limit_headers(page_response)

                    if page_response.status_code == 429:
                        print(f"{Fore.YELLOW}[Pagination] Rate limit hit (429){Style.RESET_ALL}")
                        self._countdown_sleep(10, "Rate limit on pagination, waiting", "Pagination")
                        continue

                    if page_response.status_code != 200:
                        print(f"{Fore.YELLOW}[Pagination] Failed to fetch page: {page_response.status_code}{Style.RESET_ALL}")
                        break

                    page_data = page_response.json()
                    page_consumers = self._parse_analytics_response(page_data)
                    all_consumers.extend(page_consumers)

                    page_meta = page_data.get('meta', {})
                    page_info = page_meta.get('page', {})
                    cursor = page_info.get('after')

                print(f"{Fore.GREEN}[Pagination] Total consumers collected: {len(all_consumers)}{Style.RESET_ALL}")
                return all_consumers

            if response.status_code == 500:
                if retry_count < MAX_RETRIES_ON_500:
                    print(f"{Fore.YELLOW}[Retry] 500 Internal Server Error - Attempt {retry_count + 1}/{MAX_RETRIES_ON_500}{Style.RESET_ALL}")
                    self._countdown_sleep(RETRY_DELAY_SECONDS, "Waiting before retry", "Retry")
                    return self.query_service_consumers(env, service, limit, time_period, retry_count + 1)
                else:
                    error_details = {
                        'service': service,
                        'environment': env,
                        'time_period': time_period,
                        'query': query_string,
                        'url': url,
                        'error': '500 Internal Server Error',
                        'status_code': 500,
                        'attempts': MAX_RETRIES_ON_500 + 1,
                        'timestamp': time.strftime('%Y-%m-%d %H:%M:%S')
                    }
                    self.failed_500_errors.append(error_details)
                    print(f"{Fore.RED}[Response] 500 Internal Server Error - Max retries reached{Style.RESET_ALL}")
                    return []

            print(f"{Fore.YELLOW}[Response] Unexpected status: {response.status_code}{Style.RESET_ALL}")
            response.raise_for_status()
            return []

        except requests.exceptions.RequestException as e:
            print(f"{Fore.RED}Error querying Datadog for service {service}: {e}{Style.RESET_ALL}")
            return []

    def _parse_analytics_response(self, data: Dict) -> List[Dict]:
        """Parse the v2 analytics API response to extract calling services."""
        consumers = []

        try:
            buckets = data.get('data', [])

            for bucket in buckets:
                attributes = bucket.get('attributes', {})
                by_values = attributes.get('by', {})
                service_name = by_values.get('service')

                if service_name:
                    compute = attributes.get('compute', {})
                    count = compute.get('c0', 0)
                    consumers.append({'service': service_name, 'count': int(count)})

            print(f"{Fore.GREEN}[Parse] Total consumers found: {len(consumers)}{Style.RESET_ALL}")

        except Exception as e:
            print(f"{Fore.YELLOW}[Parse] Error parsing analytics response: {e}{Style.RESET_ALL}")

        return consumers


def load_datadog_config():
    """Load Datadog credentials and configuration from ~/.datadog.cfg."""
    config_path = os.path.expanduser('~/.datadog.cfg')

    if not os.path.exists(config_path):
        return None, None, None, {}, [], {}, [], {}, [], False, [], {}

    try:
        with open(config_path, 'r') as f:
            config = json.load(f)

        pat = config.get('pat')
        api_key = config.get('api-key')
        app_key = config.get('app-key')

        application_aliases = config.get('application-alias', {})
        skip_applications = config.get('skip-applications', [])
        desired_end_categorizations = config.get('desired-end-categorizations', [])
        remap_categorizations = config.get('remap-categorizations', {})
        teams = config.get('teams', [])
        exclude_team_requests = config.get('excludeSpecifiedTeamRequests', False)
        exclude_products = config.get('exclude-products', [])
        map_products = config.get('map-products', {})

        service_mappings_list = config.get('application-assignments', [])
        service_mappings = {}
        for mapping in service_mappings_list:
            service_name = mapping.get('name')
            if service_name:
                service_mappings[service_name] = {
                    'business-unit': mapping.get('business-unit'),
                    'domain': mapping.get('domain'),
                    'platform': mapping.get('platform'),
                    'product': mapping.get('product'),
                    'system': mapping.get('system'),
                    'team': mapping.get('team')
                }

        if pat or (api_key and app_key):
            if pat:
                print(f"{Fore.CYAN}Loaded PAT from ~/.datadog.cfg{Style.RESET_ALL}")
            else:
                print(f"{Fore.CYAN}Loaded API key and app key from ~/.datadog.cfg{Style.RESET_ALL}")
            return pat, api_key, app_key, application_aliases, skip_applications, service_mappings, desired_end_categorizations, remap_categorizations, teams, exclude_team_requests, exclude_products, map_products
        else:
            print(f"{Fore.YELLOW}Warning: ~/.datadog.cfg exists but missing 'pat' or ('api-key' and 'app-key') fields{Style.RESET_ALL}")
            return None, None, None, application_aliases, skip_applications, service_mappings, desired_end_categorizations, remap_categorizations, teams, exclude_team_requests, exclude_products, map_products

    except json.JSONDecodeError as e:
        print(f"{Fore.YELLOW}Warning: ~/.datadog.cfg is not valid JSON: {e}{Style.RESET_ALL}")
        return None, None, None, {}, [], {}, [], {}, [], False, [], {}
    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Could not read ~/.datadog.cfg: {e}{Style.RESET_ALL}")
        return None, None, {}, [], {}, [], {}, [], False, [], {}


def save_credentials_to_config(pat: Optional[str] = None, api_key: Optional[str] = None,
                               app_key: Optional[str] = None) -> bool:
    """Save or update Datadog credentials in ~/.datadog.cfg."""
    config_path = os.path.expanduser('~/.datadog.cfg')

    try:
        config = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r') as f:
                    config = json.load(f)
            except json.JSONDecodeError:
                config = {}

        if pat:
            config['pat'] = pat
            config.pop('api-key', None)
            config.pop('app-key', None)
        elif api_key and app_key:
            config['api-key'] = api_key
            config['app-key'] = app_key
            config.pop('pat', None)
        else:
            return False

        with open(config_path, 'w') as f:
            json.dump(config, f, indent=2)

        auth_type = "PAT" if pat else "API key and app key"
        print(f"{Fore.GREEN}Saved {auth_type} to ~/.datadog.cfg{Style.RESET_ALL}")
        return True

    except Exception as e:
        print(f"{Fore.YELLOW}Warning: Could not save credentials to ~/.datadog.cfg: {e}{Style.RESET_ALL}")
        return False


def sanitize_filename(filename: str) -> str:
    """
    Sanitize a filename to avoid hidden files and other issues.

    Converts leading dots to 'dot' prefix to prevent hidden files in Unix-like systems.

    Args:
        filename: Original filename

    Returns:
        Sanitized filename (e.g., '.hidden' becomes 'dot_hidden')
    """
    if filename.startswith('.'):
        return f"dot_{filename[1:]}"
    return filename


def add_datadog_auth_args(parser: argparse.ArgumentParser):
    """
    Add mutually exclusive Datadog authentication arguments to an argparse parser.

    Creates a mutually exclusive group with three authentication options:
    - --pat: Personal Access Token (recommended)
    - --api-key: API key (must be used with --app-key)
    - --cookies: Cookie-based authentication

    Also adds --app-key separately since it's only valid with --api-key.

    Args:
        parser: argparse.ArgumentParser instance to add arguments to
    """
    auth_group = parser.add_mutually_exclusive_group()
    auth_group.add_argument('--pat', help='Datadog Personal Access Token (recommended; if not provided, reads from ~/.datadog.cfg)')
    auth_group.add_argument('--api-key', help='Datadog API key (use with --app-key; if not provided, reads from ~/.datadog.cfg)')
    auth_group.add_argument('--cookies', help='Cookie string (semicolon separated) for authentication')

    parser.add_argument('--app-key', help='Datadog application key (required if --api-key is provided)')

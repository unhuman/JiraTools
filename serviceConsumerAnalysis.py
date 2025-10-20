#!/usr/bin/env python3
"""
Service Consumer Analysis Tool

This script analyzes service consumers using Datadog trace data. It takes the output
from teamApplicationAttribution.py and queries Datadog to find which services are
calling each team's applications, then generates reports aggregated by domain and system.

Usage:
    python serviceConsumerAnalysis.py <input_file> <environment> <datadog_host> [auth_options] [filters]

Authentication (choose one):
    --api-key KEY --app-key KEY    Use API key authentication
    --cookies COOKIES              Use cookie-based authentication (semicolon separated)

Optional Filters:
    -t, --team TEAM                Process only this team
    -a, --application APP          Process only this application

Examples:
    # Using API keys, filter to one team
    python serviceConsumerAnalysis.py allTeamApplications.json production https://company.datadoghq.com --api-key YOUR_API_KEY --app-key YOUR_APP_KEY -t Oktagon
    
    # Using cookie authentication, filter to one application
    python serviceConsumerAnalysis.py allTeamApplications.json production https://company.datadoghq.com --cookies "_dd_did=...; datadog-theme=light; dogweb=..." -a iam-service
    
    # Filter to both team and application
    python serviceConsumerAnalysis.py allTeamApplications.json production https://company.datadoghq.com --cookies "..." -t Oktagon -a iam-service
"""

import argparse
import json
import sys
import requests
import time
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from colorama import init, Fore, Style

# Initialize colorama for cross-platform colored output
init(autoreset=True)


class DatadogClient:
    """Client for interacting with Datadog API with flexible authentication."""
    
    def __init__(self, host: str, api_key: Optional[str] = None, app_key: Optional[str] = None, 
                 cookies: Optional[str] = None, timeout: int = 30, rate_limit_delay: float = 1.0):
        """
        Initialize Datadog client with either API keys or cookie authentication.
        
        Args:
            host: Datadog site URL (e.g., 'https://app.datadoghq.com')
            api_key: Datadog API key (optional if cookies provided)
            app_key: Datadog application key (optional if cookies provided)
            cookies: Cookie string (semicolon separated) for authentication (optional if API keys provided)
            timeout: Request timeout in seconds
            rate_limit_delay: Delay between requests in seconds
        """
        self.host = host.rstrip('/')
        self.api_key = api_key
        self.app_key = app_key
        self.cookies = cookies
        self.timeout = timeout
        self.rate_limit_delay = rate_limit_delay
        self.last_request_time = 0
        
        # Validate that we have some form of authentication
        if not ((api_key and app_key) or cookies):
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
        # Log all rate limit related headers
        rate_headers = {k: v for k, v in response.headers.items() if 'rate' in k.lower() or 'limit' in k.lower()}
        if rate_headers:
            print(f"{Fore.CYAN}[Rate Limit] Headers: {rate_headers}{Style.RESET_ALL}")
        
        # Datadog uses lowercase headers
        rate_limit_remaining = response.headers.get('x-ratelimit-remaining')
        rate_limit_reset = response.headers.get('x-ratelimit-reset')
        rate_limit_limit = response.headers.get('x-ratelimit-limit')
        rate_limit_name = response.headers.get('x-ratelimit-name', 'unknown')
        
        if rate_limit_remaining is not None:
            remaining = int(rate_limit_remaining)
            limit = int(rate_limit_limit) if rate_limit_limit else 100
            
            print(f"{Fore.CYAN}[Rate Limit] {rate_limit_name}: {remaining}/{limit} remaining{Style.RESET_ALL}")
            
            # If we hit the rate limit (0 remaining), sleep for the reset period
            if remaining == 0 and rate_limit_reset:
                reset_seconds = int(rate_limit_reset)
                self._countdown_sleep(reset_seconds, "LIMIT HIT! Sleeping")
            # If we're running low on requests (less than 2), add a delay
            elif remaining < 2 and rate_limit_reset:
                reset_seconds = int(rate_limit_reset)
                self._countdown_sleep(reset_seconds, f"Only {remaining} requests remaining. Sleeping")
    
    def _countdown_sleep(self, seconds: int, prefix: str = "Sleeping"):
        """Sleep for the specified seconds with a countdown display."""
        import sys
        for remaining in range(seconds, 0, -1):
            sys.stdout.write(f"\r{Fore.YELLOW}[Rate Limit] {prefix} {remaining}s until reset...{Style.RESET_ALL}   ")
            sys.stdout.flush()
            time.sleep(1)
        sys.stdout.write(f"\r{Fore.GREEN}[Rate Limit] Done waiting! Continuing...{Style.RESET_ALL}                              \n")
        sys.stdout.flush()
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for authentication based on available credentials."""
        headers = {
            'Content-Type': 'application/json',
        }
        
        if self.api_key and self.app_key:
            # API key authentication (preferred)
            headers['DD-API-KEY'] = self.api_key
            headers['DD-APPLICATION-KEY'] = self.app_key
        elif self.cookies:
            # Cookie-based authentication (cookies are already semicolon separated)
            headers['Cookie'] = self.cookies
        
        return headers
    
    def query_service_consumers(self, env: str, service: str, limit: int = 100, time_period: str = "1h") -> List[Dict]:
        """
        Query Datadog for services that consume (call) the specified service.
        
        Args:
            env: Environment name (e.g., 'production', 'staging')
            service: Service name to find consumers for
            limit: Maximum number of results to return
            time_period: Time period to query (e.g., '1h', '4h', '1d', '1w')
            
        Returns:
            List of consumer service information with call counts
        """
        # Apply rate limiting
        self._rate_limit()
        
        # Use trace analytics to find which services are calling this service
        # This queries for traces where the target service is being called and groups by the calling service
        url = f"{self.host}/api/v2/spans/analytics/aggregate"
        
        # Parse time period format (e.g., "1h", "4h", "1d", "1w")
        print(f"{Fore.CYAN}[Request] Time period: {time_period}{Style.RESET_ALL}")
        
        # Query: Find all spans where this service is the target, group by parent service
        # This gives us which services are calling our target service
        # Based on Datadog API documentation for aggregate spans
        # Using cardinality on trace_id to count unique requests (traces) instead of all spans
        payload = {
            "data": {
                "attributes": {
                    "compute": [
                        {
                            "aggregation": "cardinality",
                            "metric": "trace_id",
                            "type": "total"
                        }
                    ],
                    "filter": {
                        "from": f"now-{time_period}",
                        "to": "now",
                        "query": f'@span.kind:client @peer.service:"{service}" env:{env}'
                    },
                    "group_by": [
                        {
                            "facet": "service",
                            "limit": limit
                        }
                    ]
                },
                "type": "aggregate_request"
            }
        }
        
        print(f"{Fore.CYAN}[Request] POST {url}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}[Request] Query: @span.kind:client @peer.service:\"{service}\" env:{env}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}[Request] Time range: now-{time_period} to now{Style.RESET_ALL}")
        print(f"{Fore.CYAN}[Request] Grouping by: service (calling service){Style.RESET_ALL}")
        print(f"{Fore.CYAN}[Request] Counting: unique requests (cardinality of trace_id){Style.RESET_ALL}")
        print(f"{Fore.CYAN}[Request] Payload: {json.dumps(payload, indent=2)}{Style.RESET_ALL}")
        
        try:
            response = requests.post(
                url,
                headers=self._get_headers(),
                json=payload,
                timeout=self.timeout
            )
            
            print(f"{Fore.CYAN}[Response] Status: {response.status_code}{Style.RESET_ALL}")
            
            # Check rate limit headers
            self._check_rate_limit_headers(response)
            
            if response.status_code == 400:
                print(f"{Fore.YELLOW}[Response] Bad request - Response: {response.text[:500]}{Style.RESET_ALL}")
                return []
            
            if response.status_code == 401:
                print(f"{Fore.RED}[Response] Authentication failed (401 Unauthorized){Style.RESET_ALL}")
                print(f"{Fore.YELLOW}[Response] Body: {response.text[:500]}{Style.RESET_ALL}")
                return []
            
            if response.status_code == 429:
                print(f"{Fore.YELLOW}[Response] Rate limit hit (429). Waiting 5 seconds...{Style.RESET_ALL}")
                time.sleep(5)
                return self.query_service_consumers(env, service, limit, time_period)
            
            if response.status_code == 404:
                print(f"{Fore.YELLOW}[Response] Service {service} not found (404){Style.RESET_ALL}")
                return []
            
            if response.status_code == 200:
                data = response.json()
                print(f"{Fore.GREEN}[Response] Success - parsing analytics response{Style.RESET_ALL}")
                print(f"{Fore.CYAN}[Response] Response keys: {list(data.keys())}{Style.RESET_ALL}")
                print(f"{Fore.CYAN}[Response] Full response: {json.dumps(data, indent=2)[:2000]}{Style.RESET_ALL}")
                
                # Parse the analytics API response format and collect results
                all_consumers = self._parse_analytics_response(data)
                
                # Check for pagination
                meta = data.get('meta', {})
                page_info = meta.get('page', {})
                cursor = page_info.get('after')
                
                # Fetch additional pages if available
                while cursor:
                    print(f"{Fore.CYAN}[Pagination] Found more results, fetching next page...{Style.RESET_ALL}")
                    self._rate_limit()
                    
                    # Add cursor to payload for next page
                    paginated_payload = payload.copy()
                    paginated_payload['data']['attributes']['page'] = {'cursor': cursor}
                    
                    page_response = requests.post(
                        url,
                        headers=self._get_headers(),
                        json=paginated_payload,
                        timeout=self.timeout
                    )
                    
                    # Check rate limit headers on pagination response
                    self._check_rate_limit_headers(page_response)
                    
                    if page_response.status_code == 429:
                        print(f"{Fore.YELLOW}[Pagination] Rate limit hit (429). Waiting 10 seconds...{Style.RESET_ALL}")
                        time.sleep(10)
                        continue  # Retry this page
                    
                    if page_response.status_code != 200:
                        print(f"{Fore.YELLOW}[Pagination] Failed to fetch page: {page_response.status_code}{Style.RESET_ALL}")
                        break
                    
                    page_data = page_response.json()
                    page_consumers = self._parse_analytics_response(page_data)
                    all_consumers.extend(page_consumers)
                    
                    # Check for next page
                    page_meta = page_data.get('meta', {})
                    page_info = page_meta.get('page', {})
                    cursor = page_info.get('after')
                
                print(f"{Fore.GREEN}[Pagination] Total consumers collected: {len(all_consumers)}{Style.RESET_ALL}")
                return all_consumers
            
            # Handle any other status code
            print(f"{Fore.YELLOW}[Response] Unexpected status: {response.status_code}{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}[Response] Body: {response.text[:500]}{Style.RESET_ALL}")
            response.raise_for_status()
            return []
            
        except requests.exceptions.RequestException as e:
            print(f"{Fore.RED}Error querying Datadog for service {service}: {e}{Style.RESET_ALL}")
            if 'response' in locals():
                print(f"{Fore.YELLOW}Response: {response.text[:500]}{Style.RESET_ALL}")
            return []
    
    def _try_trace_search(self, env: str, service: str, limit: int,
                          from_ts: int, to_ts: int) -> List[Dict]:
        """Try using the trace search/list endpoint."""
        print(f"{Fore.CYAN}[Fallback] Trying trace search API{Style.RESET_ALL}")
        self._rate_limit()
        
        # Use the simpler trace list endpoint
        url = f"{self.host}/api/v1/trace/search"
        
        params = {
            'start': from_ts,
            'end': to_ts,
            'query': f'env:{env} @service.name:{service}',
            'limit': limit
        }
        
        print(f"{Fore.CYAN}[Request] GET {url}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}[Request] Query: env:{env} @service.name:{service}{Style.RESET_ALL}")
        
        try:
            response = requests.get(
                url,
                headers=self._get_headers(),
                params=params,
                timeout=self.timeout
            )
            
            print(f"{Fore.CYAN}[Response] Status: {response.status_code}{Style.RESET_ALL}")
            
            if response.status_code == 429:
                print(f"{Fore.YELLOW}[Response] Rate limit hit (429) on trace search. Waiting 5 seconds...{Style.RESET_ALL}")
                time.sleep(5)
                return self._try_trace_search(env, service, limit, from_ts, to_ts)
            
            if response.status_code != 200:
                print(f"{Fore.YELLOW}[Response] Trace search failed: {response.status_code}{Style.RESET_ALL}")
                print(f"{Fore.YELLOW}[Response] Body: {response.text[:500]}{Style.RESET_ALL}")
                return []
            
            data = response.json()
            print(f"{Fore.GREEN}[Response] Success - parsing trace data{Style.RESET_ALL}")
            return self._parse_trace_search_response(data)
            
        except requests.exceptions.RequestException as e:
            print(f"{Fore.YELLOW}Trace search failed: {e}{Style.RESET_ALL}")
            return []
    
    def _parse_catalog_response(self, data: Dict, limit: int) -> List[Dict]:
        """Parse the catalog API response to extract upstream dependencies."""
        print(f"{Fore.CYAN}[Parse] Parsing catalog API response{Style.RESET_ALL}")
        print(f"{Fore.CYAN}[Parse] Response keys: {list(data.keys())}{Style.RESET_ALL}")
        
        consumers = []
        
        try:
            # Catalog API returns data as a list of entities
            entities = data.get('data', [])
            print(f"{Fore.CYAN}[Parse] Found {len(entities)} entities{Style.RESET_ALL}")
            
            # Log first entity structure to understand the format
            if entities:
                first_entity = entities[0]
                print(f"{Fore.CYAN}[Parse] First entity keys: {list(first_entity.keys())}{Style.RESET_ALL}")
                print(f"{Fore.CYAN}[Parse] First entity type: {first_entity.get('type')}{Style.RESET_ALL}")
                attributes = first_entity.get('attributes', {})
                print(f"{Fore.CYAN}[Parse] First entity attribute keys: {list(attributes.keys())}{Style.RESET_ALL}")
                print(f"{Fore.CYAN}[Parse] First entity sample: {str(first_entity)[:500]}{Style.RESET_ALL}")
            
            # The catalog API might return all entities, so we need to look at all of them
            # or check the 'included' data for dependencies
            included = data.get('included', [])
            print(f"{Fore.CYAN}[Parse] Found {len(included)} included items{Style.RESET_ALL}")
            
            if included:
                first_included = included[0]
                print(f"{Fore.CYAN}[Parse] First included keys: {list(first_included.keys())}{Style.RESET_ALL}")
                print(f"{Fore.CYAN}[Parse] First included type: {first_included.get('type')}{Style.RESET_ALL}")
                print(f"{Fore.CYAN}[Parse] First included sample: {str(first_included)[:500]}{Style.RESET_ALL}")
            
            # Show a few entity names to see what we got
            entity_names = [e.get('attributes', {}).get('name', 'unknown') for e in entities[:10]]
            print(f"{Fore.CYAN}[Parse] First 10 entity names: {entity_names}{Style.RESET_ALL}")
            
            for idx, entity in enumerate(entities):
                # Look for dependencies in the entity attributes
                attributes = entity.get('attributes', {})
                entity_name = attributes.get('name', 'unknown')
                
                # Find dependencies - upstream services that call this service
                dependencies = attributes.get('dependencies', {})
                upstream_services = dependencies.get('upstream', [])
                
                if idx < 5 or len(upstream_services) > 0:
                    # Log first 5 entities and any with upstream services
                    print(f"{Fore.CYAN}[Parse] Entity '{entity_name}': Found {len(upstream_services)} upstream services{Style.RESET_ALL}")
                    if dependencies:
                        print(f"{Fore.CYAN}[Parse]   Dependencies keys: {list(dependencies.keys())}{Style.RESET_ALL}")
                
                for upstream in upstream_services[:limit]:
                    # Upstream can be a string (service name) or dict with details
                    if isinstance(upstream, str):
                        service_name = upstream
                        count = 1  # No count available, default to 1
                    else:
                        service_name = upstream.get('name') or upstream.get('service')
                        count = upstream.get('count', 1)
                    
                    if service_name:
                        consumers.append({
                            'service': service_name,
                            'count': int(count)
                        })
                        print(f"{Fore.CYAN}[Parse]   - {service_name}: {count} calls{Style.RESET_ALL}")
            
            print(f"{Fore.GREEN}[Parse] Total consumers found: {len(consumers)}{Style.RESET_ALL}")
            
        except Exception as e:
            print(f"{Fore.YELLOW}[Parse] Error parsing catalog response: {e}{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}[Parse] Data structure: {str(data)[:500]}{Style.RESET_ALL}")
        
        return consumers
    
    def _parse_analytics_response(self, data: Dict) -> List[Dict]:
        """Parse the v2 analytics API response to extract calling services."""
        print(f"{Fore.CYAN}[Parse] Parsing analytics API response{Style.RESET_ALL}")
        print(f"{Fore.CYAN}[Parse] Response keys: {list(data.keys())}{Style.RESET_ALL}")
        
        # Check meta for pagination info
        meta = data.get('meta', {})
        print(f"{Fore.CYAN}[Parse] Meta: {meta}{Style.RESET_ALL}")
        
        consumers = []
        
        try:
            # Analytics API returns data as a list of buckets
            buckets = data.get('data', [])
            print(f"{Fore.CYAN}[Parse] Found {len(buckets)} buckets (calling services){Style.RESET_ALL}")
            
            # Log the first bucket structure to understand the format
            if buckets:
                first_bucket = buckets[0]
                print(f"{Fore.CYAN}[Parse] First bucket keys: {list(first_bucket.keys())}{Style.RESET_ALL}")
                attributes = first_bucket.get('attributes', {})
                print(f"{Fore.CYAN}[Parse] First bucket attributes keys: {list(attributes.keys())}{Style.RESET_ALL}")
                print(f"{Fore.CYAN}[Parse] First bucket 'by' keys: {list(attributes.get('by', {}).keys())}{Style.RESET_ALL}")
                print(f"{Fore.CYAN}[Parse] First bucket sample: {str(first_bucket)[:300]}{Style.RESET_ALL}")
            
            for bucket in buckets:
                # Get attributes from the bucket
                attributes = bucket.get('attributes', {})
                
                # The 'by' field contains the grouped facet values
                by_values = attributes.get('by', {})
                
                # The service name is in the grouped facet (service = the calling service)
                service_name = by_values.get('service')
                
                if service_name:
                    # Get the count from compute
                    compute = attributes.get('compute', {})
                    count = compute.get('c0', 0)  # c0 is the first compute
                    
                    consumers.append({
                        'service': service_name,
                        'count': int(count)
                    })
                    print(f"{Fore.CYAN}[Parse]   - {service_name}: {count} calls{Style.RESET_ALL}")
                else:
                    print(f"{Fore.YELLOW}[Parse] Bucket missing service name, by_values: {by_values}{Style.RESET_ALL}")
            
            print(f"{Fore.GREEN}[Parse] Total consumers found: {len(consumers)}{Style.RESET_ALL}")
            
        except Exception as e:
            print(f"{Fore.YELLOW}[Parse] Error parsing analytics response: {e}{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}[Parse] Data structure: {str(data)[:500]}{Style.RESET_ALL}")
        
        return consumers
    
    def _parse_apm_service_response(self, data: Dict, limit: int) -> List[Dict]:
        """Parse APM service response to extract upstream services."""
        print(f"{Fore.CYAN}[Parse] Parsing APM service response{Style.RESET_ALL}")
        print(f"{Fore.CYAN}[Parse] Response keys: {list(data.keys())}{Style.RESET_ALL}")
        
        consumers = []
        
        # Look for upstream services in the response
        if 'upstream_services' in data:
            upstream_list = data.get('upstream_services', [])[:limit]
            print(f"{Fore.CYAN}[Parse] Found {len(upstream_list)} upstream services{Style.RESET_ALL}")
            
            for upstream in upstream_list:
                service_name = upstream.get('service') or upstream.get('name')
                count = upstream.get('count', 0) or upstream.get('requests', 0) or 1
                
                if service_name:
                    consumers.append({
                        'service': service_name,
                        'count': int(count)
                    })
                    print(f"{Fore.CYAN}[Parse]   - {service_name}: {count} calls{Style.RESET_ALL}")
        else:
            print(f"{Fore.YELLOW}[Parse] No 'upstream_services' key in response{Style.RESET_ALL}")
            
            # Alternative structure
            if 'dependencies' in data:
                deps = data.get('dependencies', {})
                upstream = deps.get('upstream', [])
                print(f"{Fore.CYAN}[Parse] Found {len(upstream)} dependencies.upstream services{Style.RESET_ALL}")
                
                for svc in upstream[:limit]:
                    service_name = svc.get('service') or svc.get('name')
                    count = svc.get('requests', 0) or svc.get('count', 0) or 1
                    
                    if service_name:
                        consumers.append({
                            'service': service_name,
                            'count': int(count)
                        })
                        print(f"{Fore.CYAN}[Parse]   - {service_name}: {count} calls{Style.RESET_ALL}")
        
        print(f"{Fore.GREEN}[Parse] Total consumers found: {len(consumers)}{Style.RESET_ALL}")
        return consumers
    
    def _parse_trace_search_response(self, data: Dict) -> List[Dict]:
        """Parse trace search response to extract calling services."""
        print(f"{Fore.CYAN}[Parse] Parsing trace search response{Style.RESET_ALL}")
        consumers = defaultdict(int)
        
        traces = data.get('traces', []) or data.get('data', [])
        print(f"{Fore.CYAN}[Parse] Processing {len(traces)} traces{Style.RESET_ALL}")
        
        for trace in traces:
            # Look for spans that call the target service
            spans = trace.get('spans', [])
            for span in spans:
                service_name = span.get('service')
                if service_name:
                    consumers[service_name] += 1
        
        result = [
            {'service': svc, 'count': count}
            for svc, count in sorted(consumers.items(), key=lambda x: x[1], reverse=True)
        ]
        
        print(f"{Fore.GREEN}[Parse] Found {len(result)} unique calling services{Style.RESET_ALL}")
        for consumer in result[:10]:  # Show top 10
            print(f"{Fore.CYAN}[Parse]   - {consumer['service']}: {consumer['count']} calls{Style.RESET_ALL}")
        
        return result
    
    def _parse_consumer_response(self, data: Dict) -> List[Dict]:
        """
        Parse Datadog response to extract consumer information.
        
        Args:
            data: Raw response from Datadog API
            
        Returns:
            List of dictionaries with 'service' and 'count' keys
        """
        consumers = []
        
        # Handle different possible response structures
        if 'data' in data:
            # V2 Analytics API response structure
            buckets = data.get('data', {}).get('buckets', [])
            for bucket in buckets:
                by_values = bucket.get('by', {})
                service_name = by_values.get('service')
                
                # Get count from computes
                computes = bucket.get('computes', {})
                count = computes.get('c0', 0)  # First compute aggregation
                
                if service_name:
                    consumers.append({
                        'service': service_name,
                        'count': int(count)
                    })
        
        elif 'services' in data:
            # Alternative response structure
            for service_data in data.get('services', []):
                service_name = service_data.get('name') or service_data.get('service')
                count = service_data.get('count', 0) or service_data.get('hits', 0)
                
                if service_name:
                    consumers.append({
                        'service': service_name,
                        'count': int(count)
                    })
        
        else:
            # Try to handle generic structure
            print(f"{Fore.YELLOW}Warning: Unknown response structure. Keys: {list(data.keys())}{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}Response sample: {str(data)[:200]}...{Style.RESET_ALL}")
        
        return consumers


class ServiceConsumerAnalyzer:
    """Analyzes service consumers and generates reports."""
    
    def __init__(self, attribution_data: Dict, datadog_client: DatadogClient, environment: str, time_period: str = "1h", application_filter: str = None):
        """
        Initialize analyzer.
        
        Args:
            attribution_data: Data from teamApplicationAttribution.py
            datadog_client: Configured Datadog client
            environment: Environment to analyze
            time_period: Time period to query (e.g., 1h, 4h, 1d, 1w)
            application_filter: If specified, the exact application name provided by user to use in queries
        """
        self.attribution_data = attribution_data
        self.datadog_client = datadog_client
        self.environment = environment
        self.time_period = time_period
        self.application_filter = application_filter  # Store the user-provided application name
        
        # Build reverse lookup maps
        self.service_to_team = {}  # service_name -> team_info
        self.service_to_system = {}  # service_name -> system
        self._build_lookup_maps()
    
    def _build_lookup_maps(self):
        """Build reverse lookup maps from service name to team and system."""
        for team_name, team_data in self.attribution_data.items():
            team_info = {
                'team_name': team_data.get('team_name'),
                'team_title': team_data.get('team_title'),
                'domain': team_data.get('domain'),
                'business_unit': team_data.get('business_unit')
            }
            
            for app in team_data.get('applications', []):
                service_name = app.get('name')
                system = app.get('system')
                
                if service_name:
                    self.service_to_team[service_name] = team_info
                    self.service_to_system[service_name] = system
    
    def analyze_all_teams(self) -> Dict:
        """
        Analyze consumers for all teams' services.
        
        Returns:
            Dictionary with aggregated results by domain and system
        """
        # Aggregation structures
        domain_consumers = defaultdict(lambda: defaultdict(int))  # domain -> {consuming_domain -> count}
        system_consumers = defaultdict(lambda: defaultdict(int))  # domain -> {system -> count}
        
        total_services = sum(len(team_data.get('applications', [])) 
                           for team_data in self.attribution_data.values())
        processed = 0
        
        print(f"\n{Fore.CYAN}Starting consumer analysis for {total_services} services...{Style.RESET_ALL}\n")
        
        for team_name, team_data in self.attribution_data.items():
            team_domain = team_data.get('domain', 'Unknown')
            applications = team_data.get('applications', [])
            
            print(f"{Fore.GREEN}Analyzing team: {team_data.get('team_title', team_name)} (Domain: {team_domain}){Style.RESET_ALL}")
            print(f"  Applications: {len(applications)}")
            
            for app in applications:
                # Use the user-provided application filter if available, otherwise use JSON name
                if self.application_filter:
                    service_name = self.application_filter
                else:
                    service_name = app.get('name')
                
                system = app.get('system', 'Unknown')
                processed += 1
                
                print(f"  [{processed}/{total_services}] Querying consumers for: {service_name}")
                
                # Query Datadog for consumers of this service
                consumers = self.datadog_client.query_service_consumers(
                    self.environment, 
                    service_name,
                    time_period=self.time_period
                )
                
                # Process each consumer
                for consumer in consumers:
                    consumer_service = consumer.get('service')
                    call_count = consumer.get('count', 0)
                    
                    # Skip self-calls (service calling itself)
                    if consumer_service == service_name:
                        continue
                    
                    # Lookup consumer's team/domain
                    consumer_team_info = self.service_to_team.get(consumer_service)
                    
                    if consumer_team_info:
                        consumer_domain = consumer_team_info.get('domain', 'Unknown')
                        
                        # Aggregate by domain
                        domain_consumers[team_domain][consumer_domain] += call_count
                        
                        # Aggregate by system
                        system_consumers[team_domain][system] += call_count
                    else:
                        # Consumer not in our attribution map
                        domain_consumers[team_domain]['External/Unknown'] += call_count
                        system_consumers[team_domain][system] += call_count
                
                print(f"    Found {len(consumers)} consumers")
        
        return {
            'domain_consumers': dict(domain_consumers),
            'system_consumers': dict(system_consumers)
        }
    
    def generate_reports(self, analysis_results: Dict, output_dir: str = '.'):
        """
        Generate domain reports from analysis results.
        
        Args:
            analysis_results: Results from analyze_all_teams()
            output_dir: Directory to save reports
        """
        domain_consumers = analysis_results['domain_consumers']
        system_consumers = analysis_results['system_consumers']
        
        print(f"\n{Fore.CYAN}Generating domain reports...{Style.RESET_ALL}\n")
        
        for domain in domain_consumers.keys():
            report_filename = f"{output_dir}/{domain.replace(' ', '_')}_consumer_report.json"
            
            report = {
                'domain': domain,
                'environment': self.environment,
                'consumer_domains': domain_consumers.get(domain, {}),
                'consumer_by_system': system_consumers.get(domain, {}),
                'total_calls_received': sum(domain_consumers.get(domain, {}).values()),
                'unique_consuming_domains': len(domain_consumers.get(domain, {})),
                'unique_systems': len(system_consumers.get(domain, {}))
            }
            
            with open(report_filename, 'w') as f:
                json.dump(report, f, indent=2)
            
            print(f"{Fore.GREEN}Generated report: {report_filename}{Style.RESET_ALL}")
            print(f"  Total calls received: {report['total_calls_received']}")
            print(f"  Consuming domains: {report['unique_consuming_domains']}")
            print(f"  Systems involved: {report['unique_systems']}")
        
        # Generate summary report
        summary_filename = f"{output_dir}/consumer_analysis_summary.json"
        summary = {
            'environment': self.environment,
            'domains_analyzed': list(domain_consumers.keys()),
            'domain_reports': domain_consumers,
            'system_reports': system_consumers
        }
        
        with open(summary_filename, 'w') as f:
            json.dump(summary, f, indent=2)
        
        print(f"\n{Fore.GREEN}Generated summary report: {summary_filename}{Style.RESET_ALL}")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Analyze service consumers using Datadog trace data',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    
    # Required arguments
    parser.add_argument('input_file', help='Input JSON file from teamApplicationAttribution.py')
    parser.add_argument('environment', help='Environment to analyze (e.g., production, staging)')
    parser.add_argument('datadog_host', help='Datadog host URL (e.g., https://app.datadoghq.com)')
    
    # Authentication options (mutually exclusive groups)
    auth_group = parser.add_mutually_exclusive_group(required=True)
    auth_group.add_argument('--api-key', help='Datadog API key (requires --app-key)')
    auth_group.add_argument('--cookies', help='Cookie string (semicolon separated) for authentication')
    
    parser.add_argument('--app-key', help='Datadog application key (required with --api-key)')
    
    # Optional arguments
    parser.add_argument('-t', '--team', help='Optional: Process only this team (e.g., Oktagon)')
    parser.add_argument('-a', '--application', help='Optional: Process only this application (e.g., iam-service)')
    parser.add_argument('--timeout', type=int, default=30, help='Request timeout in seconds (default: 30)')
    parser.add_argument('--rate-limit', type=float, default=1.0, help='Delay between API requests in seconds (default: 1.0)')
    parser.add_argument('--limit', type=int, default=100, help='Max consumers per service (default: 100)')
    parser.add_argument('--time-period', default='1h', help='Time period to query (e.g., 1h, 4h, 1d, 1w) (default: 1h)')
    parser.add_argument('--output-dir', default='.', help='Output directory for reports (default: current directory)')
    
    args = parser.parse_args()
    
    # Validate authentication combinations
    if args.api_key and not args.app_key:
        parser.error('--app-key is required when using --api-key')
    if args.app_key and not args.api_key:
        parser.error('--api-key is required when using --app-key')
    
    # Load input file
    print(f"{Fore.CYAN}Loading attribution data from: {args.input_file}{Style.RESET_ALL}")
    try:
        with open(args.input_file, 'r') as f:
            attribution_data = json.load(f)
    except FileNotFoundError:
        print(f"{Fore.RED}Error: Input file not found: {args.input_file}{Style.RESET_ALL}")
        sys.exit(1)
    except json.JSONDecodeError as e:
        print(f"{Fore.RED}Error: Invalid JSON in input file: {e}{Style.RESET_ALL}")
        sys.exit(1)
    
    print(f"  Loaded {len(attribution_data)} teams")
    
    # Filter to single team if specified
    if args.team:
        team_found = False
        filtered_data = {}
        
        for team_name, team_data in attribution_data.items():
            # Case-insensitive team matching
            if (team_name.lower() == args.team.lower() or 
                team_data.get('team_name', '').lower() == args.team.lower() or
                team_data.get('team_title', '').lower() == args.team.lower()):
                filtered_data[team_name] = team_data
                team_found = True
                print(f"{Fore.GREEN}  Filtering to single team: {team_data.get('team_title', team_name)}{Style.RESET_ALL}")
                break
        
        if not team_found:
            print(f"{Fore.RED}Error: Team '{args.team}' not found in input file{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}Available teams:{Style.RESET_ALL}")
            for team_name, team_data in list(attribution_data.items())[:10]:
                print(f"  - {team_data.get('team_title', team_name)}")
            if len(attribution_data) > 10:
                print(f"  ... and {len(attribution_data) - 10} more")
            sys.exit(1)
        
        attribution_data = filtered_data
    
    # Filter to single application if specified
    if args.application:
        app_found = False
        filtered_data = {}
        
        for team_name, team_data in attribution_data.items():
            # Check if this team has the specified application
            filtered_applications = []
            for application in team_data.get('applications', []):
                # Case-insensitive application name matching
                if (application.get('name', '').lower() == args.application.lower() or
                    application.get('title', '').lower() == args.application.lower()):
                    filtered_applications.append(application)
                    app_found = True
            
            # Only include team if it has the application
            if filtered_applications:
                filtered_team_data = team_data.copy()
                filtered_team_data['applications'] = filtered_applications
                filtered_team_data['application_count'] = len(filtered_applications)
                filtered_data[team_name] = filtered_team_data
        
        if not app_found:
            print(f"{Fore.RED}Error: Application '{args.application}' not found in input file{Style.RESET_ALL}")
            print(f"{Fore.YELLOW}Available applications (first 20):{Style.RESET_ALL}")
            all_apps = []
            for team_data in attribution_data.values():
                for application in team_data.get('applications', []):
                    app_name = application.get('title') or application.get('name')
                    if app_name:
                        all_apps.append(app_name)
            
            for app_name in sorted(set(all_apps))[:20]:
                print(f"  - {app_name}")
            if len(set(all_apps)) > 20:
                print(f"  ... and {len(set(all_apps)) - 20} more")
            sys.exit(1)
        
        print(f"{Fore.GREEN}  Filtering to single application: {args.application}{Style.RESET_ALL}")
        print(f"{Fore.GREEN}  Found in {len(filtered_data)} team(s){Style.RESET_ALL}")
        attribution_data = filtered_data
    
    # Initialize Datadog client
    print(f"{Fore.CYAN}Initializing Datadog client: {args.datadog_host}{Style.RESET_ALL}")
    if args.api_key:
        print(f"{Fore.CYAN}Authentication: API key + Application key{Style.RESET_ALL}")
    elif args.cookies:
        print(f"{Fore.CYAN}Authentication: Cookie{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Rate limit delay: {args.rate_limit} seconds between requests{Style.RESET_ALL}")
    
    datadog_client = DatadogClient(
        host=args.datadog_host,
        api_key=args.api_key,
        app_key=args.app_key,
        cookies=args.cookies,
        timeout=args.timeout,
        rate_limit_delay=args.rate_limit
    )
    
    # Initialize analyzer
    analyzer = ServiceConsumerAnalyzer(
        attribution_data=attribution_data,
        datadog_client=datadog_client,
        environment=args.environment,
        time_period=args.time_period,
        application_filter=args.application  # Pass the user-provided application name
    )
    
    # Run analysis
    results = analyzer.analyze_all_teams()
    
    # Generate reports
    analyzer.generate_reports(results, output_dir=args.output_dir)
    
    print(f"\n{Fore.GREEN}Consumer analysis complete!{Style.RESET_ALL}")


if __name__ == '__main__':
    main()

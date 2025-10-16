#!/usr/bin/env python3
"""
Service Consumer Analysis Tool

This script analyzes service consumers using Datadog trace data. It takes the output
from teamApplicationAttribution.py and queries Datadog to find which services are
calling each team's applications, then generates reports aggregated by domain and system.

Usage:
    python serviceConsumerAnalysis.py <input_file> <environment> <datadog_host> --api-key API_KEY --app-key APP_KEY [options]

Example:
    python serviceConsumerAnalysis.py allTeamApplications.json production https://company.datadoghq.com --api-key YOUR_API_KEY --app-key YOUR_APP_KEY -t TeamName
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
    """Client for interacting with Datadog API using API key and application key authentication."""
    
    def __init__(self, host: str, api_key: str, app_key: str, timeout: int = 30, rate_limit_delay: float = 0.5):
        """
        Initialize Datadog client.
        
        Args:
            host: Datadog host URL (e.g., https://app.datadoghq.com)
            api_key: Datadog API key
            app_key: Datadog application key
            timeout: Request timeout in seconds
            rate_limit_delay: Delay between requests in seconds to avoid rate limits
        """
        self.host = host.rstrip('/')
        self.api_key = api_key
        self.app_key = app_key
        self.timeout = timeout
        self.rate_limit_delay = rate_limit_delay
        self.last_request_time = 0
        
    def _rate_limit(self):
        """Apply rate limiting between requests."""
        current_time = time.time()
        time_since_last = current_time - self.last_request_time
        if time_since_last < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - time_since_last)
        self.last_request_time = time.time()
    
    def _get_headers(self) -> Dict[str, str]:
        """Get headers for Datadog authentication."""
        return {
            'Content-Type': 'application/json',
            'DD-API-KEY': self.api_key,
            'DD-APPLICATION-KEY': self.app_key
        }
    
    def query_service_consumers(self, env: str, service: str, limit: int = 100) -> List[Dict]:
        """
        Query Datadog for services that consume (call) the specified service.
        
        Args:
            env: Environment name (e.g., 'production', 'staging')
            service: Service name to find consumers for
            limit: Maximum number of results to return
            
        Returns:
            List of consumer service information with call counts
        """
        # Apply rate limiting
        self._rate_limit()
        
        # Try the APM Service Stats endpoint which shows upstream/downstream services
        # This is more reliable than the analytics endpoint
        url = f"{self.host}/api/v1/apm/service/{service}"
        
        # Calculate time range (last 7 days)
        now = int(time.time())
        one_week_ago = now - (7 * 24 * 60 * 60)
        
        params = {
            'start': one_week_ago,
            'end': now,
            'env': env
        }
        
        try:
            response = requests.get(
                url,
                headers=self._get_headers(),
                params=params,
                timeout=self.timeout
            )
            
            if response.status_code == 400:
                print(f"{Fore.YELLOW}Bad request for service {service}. Response: {response.text}{Style.RESET_ALL}")
                # Try the trace search endpoint instead
                return self._try_trace_search(env, service, limit, one_week_ago, now)
            
            if response.status_code == 401:
                print(f"{Fore.RED}Authentication failed (401 Unauthorized){Style.RESET_ALL}")
                print(f"{Fore.YELLOW}Response: {response.text}{Style.RESET_ALL}")
                return []
            
            if response.status_code == 429:
                print(f"{Fore.YELLOW}Rate limit hit. Waiting 5 seconds...{Style.RESET_ALL}")
                time.sleep(5)
                return self.query_service_consumers(env, service, limit)
            
            if response.status_code == 404:
                # Service not found in APM
                print(f"{Fore.YELLOW}Service {service} not found in APM{Style.RESET_ALL}")
                return []
            
            response.raise_for_status()
            data = response.json()
            
            # Extract upstream services (services calling this one)
            return self._parse_apm_service_response(data, limit)
            
        except requests.exceptions.RequestException as e:
            print(f"{Fore.RED}Error querying Datadog for service {service}: {e}{Style.RESET_ALL}")
            if 'response' in locals():
                print(f"{Fore.YELLOW}Response: {response.text}{Style.RESET_ALL}")
            return []
    
    def _try_trace_search(self, env: str, service: str, limit: int,
                          from_ts: int, to_ts: int) -> List[Dict]:
        """Try using the trace search/list endpoint."""
        self._rate_limit()
        
        # Use the simpler trace list endpoint
        url = f"{self.host}/api/v1/trace/search"
        
        params = {
            'start': from_ts,
            'end': to_ts,
            'query': f'env:{env} @service.name:{service}',
            'limit': limit
        }
        
        try:
            response = requests.get(
                url,
                headers=self._get_headers(),
                params=params,
                timeout=self.timeout
            )
            
            if response.status_code == 429:
                print(f"{Fore.YELLOW}Rate limit hit on trace search. Waiting 5 seconds...{Style.RESET_ALL}")
                time.sleep(5)
                return self._try_trace_search(env, service, limit, from_ts, to_ts)
            
            if response.status_code != 200:
                print(f"{Fore.YELLOW}Trace search failed: {response.status_code} - {response.text}{Style.RESET_ALL}")
                return []
            
            data = response.json()
            return self._parse_trace_search_response(data)
            
        except requests.exceptions.RequestException as e:
            print(f"{Fore.YELLOW}Trace search failed: {e}{Style.RESET_ALL}")
            return []
    
    def _parse_apm_service_response(self, data: Dict, limit: int) -> List[Dict]:
        """Parse APM service response to extract upstream services."""
        consumers = []
        
        # Look for upstream services in the response
        if 'upstream_services' in data:
            for upstream in data.get('upstream_services', [])[:limit]:
                service_name = upstream.get('service') or upstream.get('name')
                count = upstream.get('count', 0) or upstream.get('requests', 0) or 1
                
                if service_name:
                    consumers.append({
                        'service': service_name,
                        'count': int(count)
                    })
        
        # Alternative structure
        elif 'dependencies' in data:
            deps = data.get('dependencies', {})
            upstream = deps.get('upstream', [])
            for svc in upstream[:limit]:
                service_name = svc.get('service') or svc.get('name')
                count = svc.get('requests', 0) or svc.get('count', 0) or 1
                
                if service_name:
                    consumers.append({
                        'service': service_name,
                        'count': int(count)
                    })
        
        return consumers
    
    def _parse_trace_search_response(self, data: Dict) -> List[Dict]:
        """Parse trace search response to extract calling services."""
        consumers = defaultdict(int)
        
        traces = data.get('traces', []) or data.get('data', [])
        
        for trace in traces:
            # Look for spans that call the target service
            spans = trace.get('spans', [])
            for span in spans:
                service_name = span.get('service')
                if service_name:
                    consumers[service_name] += 1
        
        return [
            {'service': svc, 'count': count}
            for svc, count in sorted(consumers.items(), key=lambda x: x[1], reverse=True)
        ]
    
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
    
    def __init__(self, attribution_data: Dict, datadog_client: DatadogClient, environment: str):
        """
        Initialize analyzer.
        
        Args:
            attribution_data: Data from teamApplicationAttribution.py
            datadog_client: Configured Datadog client
            environment: Environment to analyze
        """
        self.attribution_data = attribution_data
        self.datadog_client = datadog_client
        self.environment = environment
        
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
                service_name = app.get('name')
                system = app.get('system', 'Unknown')
                processed += 1
                
                print(f"  [{processed}/{total_services}] Querying consumers for: {service_name}")
                
                # Query Datadog for consumers of this service
                consumers = self.datadog_client.query_service_consumers(
                    self.environment, 
                    service_name
                )
                
                # Process each consumer
                for consumer in consumers:
                    consumer_service = consumer.get('service')
                    call_count = consumer.get('count', 0)
                    
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
    
    # Authentication
    parser.add_argument('--api-key', required=True, help='Datadog API key')
    parser.add_argument('--app-key', required=True, help='Datadog application key')
    
    # Optional arguments
    parser.add_argument('-t', '--team', help='Optional: Process only this team (e.g., Oktagon)')
    parser.add_argument('--timeout', type=int, default=30, help='Request timeout in seconds (default: 30)')
    parser.add_argument('--rate-limit', type=float, default=1.0, help='Delay between API requests in seconds (default: 1.0)')
    parser.add_argument('--limit', type=int, default=100, help='Max consumers per service (default: 100)')
    parser.add_argument('--output-dir', default='.', help='Output directory for reports (default: current directory)')
    
    args = parser.parse_args()
    
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
    
    # Initialize Datadog client
    print(f"{Fore.CYAN}Initializing Datadog client: {args.datadog_host}{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Rate limit delay: {args.rate_limit} seconds between requests{Style.RESET_ALL}")
    datadog_client = DatadogClient(
        host=args.datadog_host,
        api_key=args.api_key,
        app_key=args.app_key,
        timeout=args.timeout,
        rate_limit_delay=args.rate_limit
    )
    
    # Initialize analyzer
    analyzer = ServiceConsumerAnalyzer(
        attribution_data=attribution_data,
        datadog_client=datadog_client,
        environment=args.environment
    )
    
    # Run analysis
    results = analyzer.analyze_all_teams()
    
    # Generate reports
    analyzer.generate_reports(results, output_dir=args.output_dir)
    
    print(f"\n{Fore.GREEN}Consumer analysis complete!{Style.RESET_ALL}")


if __name__ == '__main__':
    main()

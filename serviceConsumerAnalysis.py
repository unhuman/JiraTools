#!/usr/bin/env python3
"""
Service Consumer Analysis Tool

This script analyzes service consumers using Datadog trace data. It takes the output
from teamApplicationAttribution.py and queries Datadog to find which services are
calling each team's applications, then generates reports aggregated by product (with
domain fallback) and system.

Services are grouped by their product if available, otherwise by their domain.
This provides more granular analysis of service consumption patterns.

Usage:
    python serviceConsumerAnalysis.py <input_file> <environment> <datadog_host> [auth_options] [filters]

Authentication (choose one):
    --api-key KEY --app-key KEY    Use API key authentication (or read from ~/.datadog.cfg)
    --cookies COOKIES              Use cookie-based authentication (semicolon separated)
    
    If --api-key and --app-key are not provided and --cookies is not used,
    credentials will be read from ~/.datadog.cfg (JSON format with "api-key" and "app-key" fields)

Optional Filters:
    -t, --teams TEAMS              Process only these teams (comma-separated list)
    -a, --applications APPS        Process only these applications (comma-separated list)

Examples:
    # Using credentials from ~/.datadog.cfg (recommended)
    python serviceConsumerAnalysis.py allTeamApplications.json production https://company.datadoghq.com
    
    # Using explicit API keys, filter to one team
    python serviceConsumerAnalysis.py allTeamApplications.json production https://company.datadoghq.com --api-key YOUR_API_KEY --app-key YOUR_APP_KEY -t Oktagon
    
    # Using cookie authentication, filter to one application
    python serviceConsumerAnalysis.py allTeamApplications.json production https://company.datadoghq.com --cookies "_dd_did=...; datadog-theme=light; dogweb=..." -a iam-service
    
    # Filter to multiple teams and applications (using config file for auth)
    python serviceConsumerAnalysis.py allTeamApplications.json production https://company.datadoghq.com -t "Oktagon,Identity" -a "iam-service,auth-service"

Config File (~/.datadog.cfg):
    {
      "api-key": "your_datadog_api_key",
      "app-key": "your_datadog_app_key",
      "application-alias": {
        "service1": "service2",
        "another-service": "canonical-service"
      },
      "skip-applications": [
        "test-service",
        "deprecated-service",
        "internal-tool"
      ],
      "application-assignments": [
        {
          "name": "unknown-service",
          "business-unit": "us-business-unit",
          "domain": "us-domain",
          "platform": "us-platform",
          "product": null,
          "system": null
        }
      ]
    }
    
    The application-alias section is optional and allows services to use metadata from
    another service while maintaining their own identity. For example, if "service1" is
    aliased to "service2", lookups for "service1" will use all the business-unit, domain,
    platform, product, and system data from "service2", but the service will still be
    identified as "service1" in reports.
    
    The skip-applications section is optional and lists applications to completely exclude
    from processing. Any calls involving these services (either as caller or callee) will
    be ignored and will not affect totals, percentages, or any aggregated data.
    
    The application-assignments section is optional and provides fallback domain assignments
    for services that cannot be resolved through the attribution data. Each mapping
    includes metadata like business-unit, domain, platform, product, and system.
    This is useful for external services or services not in your team application catalog.

Output Reports:
    The script generates two types of reports in JSON format:
    
    1. Product/Domain Reports (domain_reports):
       Shows WHICH PRODUCTS/PLATFORMS/DOMAINS are calling your services. Services are grouped by their
       product if available, otherwise platform, otherwise domain. Aggregates by the caller's product/platform/domain.
       
       Structure:
       {
         "Target Product/Platform/Domain": {
           "Calling Product/Platform/Domain": {
             "count": <number of requests>,
             "percentage": <percentage of total requests>,
             "details": [
               {
                 "target_service": "<service being called>",
                 "calling_service": "<service making the call>",
                 "count": <number of requests>
               }
             ]
           }
         }
       }
       
       Example: If the "iam" product receives 1000 total requests, and 300 come from the 
       "event-management" product, the report shows:
       - event-management: count=300, percentage=30.0%
       - Details list shows which specific event-management services called which iam services
       
       If a service doesn't have a product defined, it falls back to showing the platform, then domain.
       
       Use this to answer: "Which products/business areas are consuming our services?"
    
    2. System Reports (system_reports):
       Shows WHICH OF YOUR SYSTEMS are receiving calls, aggregated by system within your product/domain.
       Includes a breakdown of which specific services within each system are receiving calls.
       
       Structure:
       {
         "Target Product/Domain": {
           "System Name": {
             "count": <number of requests>,
             "percentage": <percentage of total requests>,
             "services": [
               {
                 "service": "<service name>",
                 "count": <number of requests to this service>
               }
             ]
           }
         }
       }
       
       Example: If the "iam" product has systems "authentication", "authorization", and "user-profile",
       the report shows how many requests each system received:
       - authentication: count=620170, percentage=88.41%, services=[{service: "auth-service", count: 500000}, ...]
       - authorization: count=5693, percentage=0.81%, services=[{service: "authz-api", count: 5693}]
       - user-profile: count=75134, percentage=10.71%, services=[{service: "profile-service", count: 75134}]
       
       The services list within each system is sorted by count (descending).
       
       Use this to answer: "Which of our internal systems are being called most frequently?" 
       and "Which specific services within each system are receiving the most traffic?"
    
    Key Differences:
    - domain_reports: External view - shows WHO is calling you (by their product/domain)
    - system_reports: Internal view - shows WHICH of your systems are being called
    
    Both reports are sorted by count (descending) for easy consumption.
"""

import argparse
import csv
import json
import sys
import requests
import time
import os
import re
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
from colorama import init, Fore, Style

from libraries.datadogTools import DatadogClient, load_datadog_config, save_credentials_to_config, add_datadog_auth_args, sanitize_filename

# Initialize colorama for cross-platform colored output
init(autoreset=True)

class ServiceConsumerAnalyzer:
    """Analyzes service consumers and generates reports."""
    
    def __init__(self, attribution_data: Dict, datadog_client: DatadogClient, environment: str, time_period: str = "1h", application_filters: list = None, full_attribution_data: Dict = None, service_mappings: Dict = None, application_aliases: Dict = None, skip_applications: List = None, exclude_team_requests: bool = False, desired_end_categorizations: list = None, remap_categorizations: Dict = None, exclude_products: list = None, map_products: Dict = None):
        """
        Initialize analyzer.
        
        Args:
            attribution_data: Data from teamApplicationAttribution.py (filtered for analysis)
            datadog_client: Configured Datadog client
            environment: Environment to analyze
            time_period: Time period to query (e.g., 1h, 4h, 1d, 1w)
            application_filters: List of application name filters (normalized, lowercase) - no longer used for queries
            full_attribution_data: Complete unfiltered attribution data for domain lookups
            service_mappings: Service mappings from config file for external service domain resolution
            application_aliases: Application alias mappings from config file (key=service, value=alias_to_use_for_data)
            skip_applications: List of application names to completely exclude from processing
            exclude_team_requests: If True, exclude requests from services owned by the specified teams when --team is used. Any request from a service owned by one of these teams is ignored/excluded during processing.
            desired_end_categorizations: List of regex patterns to match business concepts for prioritized grouping (from .datadog.cfg), compiled with re.IGNORECASE for case-insensitive matching
            remap_categorizations: Dictionary mapping categorization values to their final consolidated values (case-insensitive)
            exclude_products: List of product/platform/domain names to exclude from totals (case-insensitive matching)
            map_products: Dictionary mapping source product names to target product names (case-insensitive matching)
        """
        self.attribution_data = attribution_data
        self.datadog_client = datadog_client
        self.environment = environment
        self.time_period = time_period
        self.application_filters = application_filters or []  # Store the list of application filters
        self.service_mappings = service_mappings or {}  # Store service mappings from config
        self.application_aliases = application_aliases or {}  # Store application aliases from config
        self.skip_applications = set(skip_applications or [])  # Store as set for O(1) lookup
        self.exclude_team_requests = exclude_team_requests
        # Compile regex patterns for desired categorizations (case-insensitive)
        self.desired_end_categorizations_patterns = [
            re.compile(pattern, re.IGNORECASE) for pattern in (desired_end_categorizations or [])
        ]
        # Store remap categorizations (convert keys to lowercase for case-insensitive matching)
        self.remap_categorizations = {k.lower(): v for k, v in (remap_categorizations or {}).items()}
        # Store exclude products (convert to lowercase for case-insensitive matching)
        self.exclude_products = set(p.lower() for p in (exclude_products or []))
        # Store map products (convert keys to lowercase for case-insensitive matching)
        self.map_products = {k.lower(): v.lower() for k, v in (map_products or {}).items()}
        
        # Build reverse lookup maps from full data (or filtered if full not provided)
        self.service_to_team = {}  # service_name -> team_info
        self.service_to_system = {}  # service_name -> system
        self._build_lookup_maps(full_attribution_data or attribution_data)
        
        # Build set of teams that should have their services excluded
        self.excluded_team_names = set()
        if self.exclude_team_requests:
            for team_name, team_data in attribution_data.items():
                self.excluded_team_names.add(team_name)
    
    def _is_service_from_excluded_team(self, service_name: str) -> bool:
        """
        Check if a service belongs to a team that should be excluded.
        Checks application-assignments first, then uses fuzzy matching on attribution data.
        
        Args:
            service_name: The service name to check
            
        Returns:
            True if the service belongs to an excluded team, False otherwise
        """
        if not self.exclude_team_requests:
            return False
        
        # FIRST: Check application-assignments from config (explicit config takes priority)
        lookup_name = self.application_aliases.get(service_name, service_name)
        for name in [lookup_name, service_name]:
            if name in self.service_mappings:
                mapping = self.service_mappings[name]
                service_team = mapping.get('team')
                if service_team:
                    # Check if this service's team is in the excluded teams
                    return service_team in self.excluded_team_names
        
        # SECOND: Try to find the service in attribution data with fuzzy matching
        team_info, _ = self._lookup_service_with_fallback(service_name)
        
        if team_info:
            # Check if this service's team is in the excluded teams
            service_team_name = team_info.get('team_name')
            return service_team_name in self.excluded_team_names
        
        return False
    
    def _build_lookup_maps(self, data_source: Dict):
        """Build reverse lookup maps from service name to team and system.
        
        Stores both original and lowercase versions of service names and titles
        to support case-insensitive fuzzy matching.
        """
        for team_name, team_data in data_source.items():
            domain = team_data.get('domain')
            
            for app in team_data.get('applications', []):
                service_name = app.get('name')
                title = app.get('title')
                system = app.get('system')
                product = app.get('product')
                platform = app.get('platform')
                
                # Create service info with product, platform, and domain
                service_info = {
                    'team_name': team_data.get('team_name'),
                    'team_title': team_data.get('team_title'),
                    'domain': domain,
                    'business_unit': team_data.get('business_unit'),
                    'product': product,
                    'platform': platform
                }
                
                if service_name:
                    # Store original case
                    self.service_to_team[service_name] = service_info
                    self.service_to_system[service_name] = system
                    # Store lowercase for case-insensitive matching
                    self.service_to_team[service_name.lower()] = service_info
                    self.service_to_system[service_name.lower()] = system
                
                # Also add title as an alternative lookup key (if different from name)
                if title and title != service_name:
                    # Store original case
                    self.service_to_team[title] = service_info
                    self.service_to_system[title] = system
                    # Store lowercase for case-insensitive matching
                    self.service_to_team[title.lower()] = service_info
                    self.service_to_system[title.lower()] = system
    
    def _lookup_service_with_fallback(self, service_name: str) -> tuple:
        """
        Try to find a service in attribution data with fuzzy matching.
        
        Lookup order:
        1. Check application-alias mapping (if service is aliased, look up the alias)
        2. Exact match
        3. Remove '-service' suffix and try again
        4. Remove '-http-client' suffix and try again
        5. Remove '-lambda' suffix and try again
        6. Replace dashes with spaces and try again
        7. Remove ' service' suffix from space-replaced version
        
        Args:
            service_name: Name of the service
            
        Returns:
            Tuple of (team_info, found_name) or (None, None) if not found
        """
        # Check if this service has an alias defined in the config
        # If so, use the aliased service's data but preserve the original service name
        lookup_name = service_name
        if service_name in self.application_aliases:
            lookup_name = self.application_aliases[service_name]
            print(f"{Fore.CYAN}[Alias] Service '{service_name}' aliased to '{lookup_name}' for data lookup{Style.RESET_ALL}")
        
        # Try exact match first (using lookup_name which may be the alias)
        if lookup_name in self.service_to_team:
            return self.service_to_team[lookup_name], lookup_name
        
        # Try removing '-service' suffix
        if lookup_name.endswith('-service'):
            variant = lookup_name[:-8]  # Remove '-service'
            if variant in self.service_to_team:
                return self.service_to_team[variant], variant
        
        # Try removing '-http-client' suffix
        if lookup_name.endswith('-http-client'):
            variant = lookup_name[:-12]  # Remove '-http-client'
            if variant in self.service_to_team:
                return self.service_to_team[variant], variant
        
        # Try removing '-lambda' suffix
        if lookup_name.endswith('-lambda'):
            variant = lookup_name[:-7]  # Remove '-lambda'
            if variant in self.service_to_team:
                return self.service_to_team[variant], variant
        
        # Try replacing dashes with spaces
        variant_spaces = lookup_name.replace('-', ' ')
        if variant_spaces != lookup_name and variant_spaces in self.service_to_team:
            return self.service_to_team[variant_spaces], variant_spaces
        
        # Try lowercase version of space-replaced variant
        variant_spaces_lower = variant_spaces.lower()
        if variant_spaces_lower != variant_spaces and variant_spaces_lower in self.service_to_team:
            return self.service_to_team[variant_spaces_lower], variant_spaces_lower
        
        # Try removing ' service' suffix from space-replaced version
        if variant_spaces.endswith(' service'):
            variant_no_suffix = variant_spaces[:-8]  # Remove ' service'
            if variant_no_suffix in self.service_to_team:
                return self.service_to_team[variant_no_suffix], variant_no_suffix
        
        return None, None
    
    def _apply_remap_categorization(self, category: str) -> str:
        """
        Apply remap categorizations to consolidate similar categories.
        All categories are normalized to lowercase for consistent grouping.
        
        Args:
            category: The categorization value to potentially remap
            
        Returns:
            The remapped category (normalized to lowercase) if a mapping exists, 
            otherwise the original category normalized to lowercase
        """
        if not self.remap_categorizations:
            return category.lower()
        
        # Check for case-insensitive match
        category_lower = category.lower()
        if category_lower in self.remap_categorizations:
            # Return the remapped value, normalized to lowercase
            return self.remap_categorizations[category_lower].lower()
        
        # Return original category normalized to lowercase
        return category_lower
    
    def _get_product_or_domain_for_service(self, service_name: str) -> str:
        """
        Get product for a service, with fallback to platform, then domain if neither is available.
        
        "Shared" Logic:
        ---------------
        Special handling for "shared" values to avoid over-aggregation:
        
        1. If BOTH product="shared" AND platform="shared" AND business_unit is valid:
           → Use business_unit for grouping
           
        2. If product="shared" AND business_unit!="shared":
           → Skip product, fall through to platform
           
        3. If product="shared" AND business_unit="shared":
           → Use "shared" for grouping
           
        4. If platform="shared" AND business_unit!="shared":
           → Skip platform, fall through to domain
           
        5. If platform="shared" AND business_unit="shared":
           → Use "shared" for grouping
        
        Examples:
        ---------
        - product="shared", platform="shared", business_unit="unit-a" → "unit-a"
        - product="shared", platform="platform-x", business_unit="unit-b" → "platform-x"
        - product="product-y", platform="shared", business_unit="unit-c" → "product-y"
        - product="shared", platform="shared", business_unit="shared" → "shared"
        
        Resolution order:
        -----------------
        1. Check desired-end-categorizations regex patterns first (case-insensitive)
           - If any value in hierarchy [product, platform, domain] matches a pattern, use it
           
        2. Check application-assignments from config (explicit configuration takes priority)
           - Check if service has an alias, try the alias first
           - Then try the original service name
           - Apply "shared" logic as described above
           
        3. Check attribution data with fuzzy matching (exact, -service, dashes->spaces, etc.)
           - Handles application-alias mapping internally
           - Apply "shared" logic as described above
           
        4. Default to 'External/Unknown'
        
        All returned values are:
        - Passed through remap-categorizations (if configured)
        - Normalized to lowercase for case-insensitive grouping
        
        Args:
            service_name: Name of the service
            
        Returns:
            Categorization value (product, platform, domain, or business_unit) after applying
            shared logic, remapping, and lowercase normalization, or 'External/Unknown'
        """
        # FIRST: Check application-assignments from config (explicit config takes priority)
        lookup_name = self.application_aliases.get(service_name, service_name)
        for name in [lookup_name, service_name]:
            if name in self.service_mappings:
                mapping = self.service_mappings[name]
                product = mapping.get('product')
                platform = mapping.get('platform')
                domain = mapping.get('domain')
                business_unit = mapping.get('business-unit')
                hierarchy = [product, platform, domain]
                # Check desired categorizations first (case-insensitive regex)
                for value in hierarchy:
                    if value:
                        for pattern in self.desired_end_categorizations_patterns:
                            if pattern.search(str(value)):
                                return self._apply_remap_categorization(value)
                # Special case: if both product and platform are "shared", use business-unit
                if (product and str(product).lower() == 'shared' and 
                    platform and str(platform).lower() == 'shared' and
                    business_unit and str(business_unit).lower() not in ['', 'unknown', 'null', 'none']):
                    return self._apply_remap_categorization(business_unit)
                # Shared logic for product
                if product and str(product).lower() not in ['', 'unknown', 'null', 'none']:
                    if str(product).lower() == 'shared' and str(business_unit).lower() != 'shared':
                        pass  # Skip product, fall through to platform/domain
                    else:
                        return self._apply_remap_categorization(product)
                # Platform check - also skip if platform is "shared" and business_unit is not "shared"
                if platform and str(platform).lower() not in ['', 'unknown', 'null', 'none']:
                    if str(platform).lower() == 'shared' and str(business_unit).lower() != 'shared':
                        pass  # Skip platform, fall through to domain
                    else:
                        return self._apply_remap_categorization(platform)
                if domain and domain.lower() not in ['', 'unknown', 'null']:
                    return self._apply_remap_categorization(domain)
        # SECOND: Try the attribution data with fuzzy matching (handles aliases internally)
        service_info, _ = self._lookup_service_with_fallback(service_name)
        if service_info:
            product = service_info.get('product')
            platform = service_info.get('platform')
            domain = service_info.get('domain')
            business_unit = service_info.get('business_unit')
            hierarchy = [product, platform, domain]
            # Check desired categorizations first (case-insensitive regex)
            for value in hierarchy:
                if value:
                    for pattern in self.desired_end_categorizations_patterns:
                        if pattern.search(str(value)):
                            return self._apply_remap_categorization(value)
            # Special case: if both product and platform are "shared", use business-unit
            if (product and str(product).lower() == 'shared' and 
                platform and str(platform).lower() == 'shared' and
                business_unit and str(business_unit).lower() not in ['', 'unknown', 'null', 'none']):
                return self._apply_remap_categorization(business_unit)
            # Shared logic for product
            if product and str(product).lower() not in ['', 'unknown', 'null', 'none']:
                if str(product).lower() == 'shared' and str(business_unit).lower() != 'shared':
                    pass  # Skip product, fall through to platform/domain
                else:
                    return self._apply_remap_categorization(product)
            # Platform check - also skip if platform is "shared" and business_unit is not "shared"
            if platform and str(platform).lower() not in ['', 'unknown', 'null', 'none']:
                if str(platform).lower() == 'shared' and str(business_unit).lower() != 'shared':
                    pass  # Skip platform, fall through to domain
                else:
                    return self._apply_remap_categorization(platform)
            if domain and domain.lower() not in ['', 'unknown', 'null']:
                return self._apply_remap_categorization(domain)
        # LAST: Default to External/Unknown
        return 'External/Unknown'
    
    def _get_domain_for_service(self, service_name: str) -> str:
        """
        Get domain for a service, using config fallback and fuzzy matching if needed.
        
        Resolution order:
        1. Check application-assignments from config first (explicit configuration takes priority)
           - Check if service has an alias, try the alias first
           - Then try the original service name
        2. Check attribution data with fuzzy matching (exact, -service, dashes->spaces, etc.)
           - Handles application-alias mapping internally
        3. Default to 'External/Unknown'
        
        Args:
            service_name: Name of the service
            
        Returns:
            Domain name or 'External/Unknown'
        """
        # FIRST: Check application-assignments from config (explicit config takes priority)
        # Check if service has an alias, and try the alias first
        lookup_name = self.application_aliases.get(service_name, service_name)
        if lookup_name in self.service_mappings:
            mapping = self.service_mappings[lookup_name]
            domain = mapping.get('domain')
            if domain and domain.lower() not in ['', 'unknown', 'null']:
                return domain
        
        # If alias didn't work, try the original service name
        if lookup_name != service_name and service_name in self.service_mappings:
            mapping = self.service_mappings[service_name]
            domain = mapping.get('domain')
            if domain and domain.lower() not in ['', 'unknown', 'null']:
                return domain
        
        # SECOND: Try the attribution data with fuzzy matching (handles aliases internally)
        team_info, _ = self._lookup_service_with_fallback(service_name)
        if team_info:
            domain = team_info.get('domain')
            if domain and domain.lower() not in ['', 'unknown', 'null']:
                return domain
        
        # LAST: Default to External/Unknown
        return 'External/Unknown'
    
    def analyze_all_teams(self) -> Dict:
        """
        Analyze consumers for all teams' services.
        
        Returns:
            Dictionary with aggregated results by domain, with consumers grouped by product (or domain if no product)
        """
        # Aggregation structures - reports organized by DOMAIN, consumers grouped by product
        domain_consumers = defaultdict(lambda: defaultdict(int))  # domain -> {consuming_product_or_domain -> count}
        system_consumers = defaultdict(lambda: defaultdict(int))  # domain -> {system -> count}
        domain_details = defaultdict(lambda: defaultdict(list))  # domain -> {consuming_product_or_domain -> [details]}
        system_details = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))  # domain -> {system -> {service -> count}}
        
        total_services = sum(len(team_data.get('applications', [])) 
                           for team_data in self.attribution_data.values())
        processed = 0
        skipped = 0
        
        print(f"\n{Fore.CYAN}Starting consumer analysis for {total_services} services...{Style.RESET_ALL}\n")
        if self.skip_applications:
            print(f"{Fore.YELLOW}Skipping {len(self.skip_applications)} application(s): {', '.join(sorted(self.skip_applications))}{Style.RESET_ALL}\n")
        if self.exclude_team_requests:
            excluded_team_titles = [self.attribution_data[team].get('team_title', team) for team in self.excluded_team_names if team in self.attribution_data]
            print(f"{Fore.YELLOW}Excluding requests from services owned by {len(self.excluded_team_names)} team(s): {', '.join(excluded_team_titles)} (requests from these teams' services will be ignored in analysis){Style.RESET_ALL}\n")
        
        for team_name, team_data in self.attribution_data.items():
            team_domain = team_data.get('domain', 'Unknown')
            applications = team_data.get('applications', [])
            
            print(f"{Fore.GREEN}Analyzing team: {team_data.get('team_title', team_name)} (Domain: {team_domain}){Style.RESET_ALL}")
            print(f"  Applications: {len(applications)}")
            
            for app in applications:
                # Use the service name from the application data
                service_name = app.get('name')
                system = app.get('system', 'Unknown')
                
                processed += 1
                
                # Skip if this service is in the skip list
                if service_name in self.skip_applications:
                    skipped += 1
                    print(f"  [{processed}/{total_services}] Skipping: {service_name} (in skip-applications list)")
                    continue
                
                print(f"  [{processed}/{total_services}] Querying consumers for: {service_name} (Domain: {team_domain})")
                
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
                    
                    # Skip if the calling service is in the skip list
                    if consumer_service in self.skip_applications:
                        continue
                    
                    # Get product (with domain fallback) for this consumer service
                    consumer_group = self._get_product_or_domain_for_service(consumer_service)
                    
                    # Apply product mapping if configured (case-insensitive)
                    consumer_group_lower = consumer_group.lower()
                    if consumer_group_lower in self.map_products:
                        consumer_group = self.map_products[consumer_group_lower]
                    
                    # Check if this service should be excluded (from excluded team, External/Unknown, or excluded product)
                    is_excluded_team = self._is_service_from_excluded_team(consumer_service)
                    is_external_unknown = consumer_group == 'External/Unknown'
                    is_excluded_product = consumer_group.lower() in self.exclude_products
                    is_excluded = is_excluded_team or is_external_unknown or is_excluded_product
                    
                    if is_excluded:
                        # For excluded services, don't add to totals but preserve details with excluded_count
                        domain_consumers[team_domain][consumer_group] += 0
                        # Track details with excluded_count instead of count
                        domain_details[team_domain][consumer_group].append({
                            'target_service': service_name,
                            'calling_service': consumer_service,
                            'excluded_count': call_count  # Preserve actual count as excluded_count
                        })
                    else:
                        # Normal aggregation for known services
                        # Aggregate by consumer's product (or domain if no product)
                        domain_consumers[team_domain][consumer_group] += call_count
                        
                        # Aggregate by system
                        system_consumers[team_domain][system] += call_count
                        
                        # Track system details (services within each system)
                        system_details[team_domain][system][service_name] += call_count
                        
                        # Track details
                        domain_details[team_domain][consumer_group].append({
                            'target_service': service_name,
                            'calling_service': consumer_service,
                            'count': call_count
                        })
                
                print(f"    Found {len(consumers)} consumers")
        
        if skipped > 0:
            print(f"\n{Fore.YELLOW}Skipped {skipped} application(s) from skip-applications list{Style.RESET_ALL}\n")
        
        return {
            'domain_consumers': dict(domain_consumers),
            'system_consumers': dict(system_consumers),
            'domain_details': dict(domain_details),
            'system_details': dict(system_details)
        }
    
    def generate_reports(self, analysis_results: Dict, output_dir: str = '.', team_names: str = None, application_names: str = None):
        """
        Generate reports from analysis results.
        
        Reports are organized by domain (one file per domain).
        Within each report, consumers are grouped by product (with domain as fallback).
        
        Args:
            analysis_results: Results from analyze_all_teams()
            output_dir: Directory to save reports
            team_names: Optional comma-separated team names for custom filename
            application_names: Optional comma-separated application names for custom filename
        """
        domain_consumers = analysis_results['domain_consumers']
        system_consumers = analysis_results['system_consumers']
        domain_details = analysis_results.get('domain_details', {})
        system_details = analysis_results.get('system_details', {})
        
        print(f"\n{Fore.CYAN}Generating domain reports (with product-grouped consumers)...{Style.RESET_ALL}\n")
        
        # Format reports with count, percentage, and details
        # Consumers are grouped by their product (or domain if no product)
        formatted_domain_reports = {}
        for target_domain, consumer_products in domain_consumers.items():
            total_calls = sum(consumer_products.values())
            formatted_consumers = {}
            
            for consumer_product, count in consumer_products.items():
                percentage = (count / total_calls * 100) if total_calls > 0 else 0
                details = domain_details.get(target_domain, {}).get(consumer_product, [])
                
                # Calculate total excluded count from details (sum all excluded_count fields)
                excluded_count = sum(d.get('excluded_count', 0) for d in details)
                
                # Also check if this product name should be excluded (case-insensitive)
                is_excluded_product = consumer_product.lower() in self.exclude_products
                
                # If excluded by product name, treat the aggregated count as excluded
                if is_excluded_product:
                    excluded_count = count
                    count = 0  # Zero out the count since it's excluded
                
                # Determine if this product has ANY non-excluded traffic
                has_non_excluded_traffic = count > 0
                
                if has_non_excluded_traffic:
                    # Product has non-excluded traffic - calculate percentage
                    consumer_entry = {
                        'count': count,
                        'percentage': round(percentage, 2),
                        'details': details
                    }
                    
                    # Also include excluded_count if there is any excluded traffic
                    if excluded_count > 0:
                        consumer_entry['excluded_count'] = excluded_count
                else:
                    # Product has ONLY excluded traffic
                    consumer_entry = {
                        'percentage': 0.0,  # Don't count in percentages
                        'excluded_count': excluded_count,  # Show actual count as excluded
                        'details': details
                    }
                
                formatted_consumers[consumer_product] = consumer_entry
            
            formatted_domain_reports[target_domain] = formatted_consumers
        
        # Format system reports with count, percentage, and services list
        formatted_system_reports = {}
        for target_domain, systems in system_consumers.items():
            total_calls = sum(systems.values())
            formatted_systems = {}
            
            for system, count in systems.items():
                percentage = (count / total_calls * 100) if total_calls > 0 else 0
                
                # Get services list for this system
                services_in_system = system_details.get(target_domain, {}).get(system, {})
                services_list = [
                    {'service': service, 'count': svc_count}
                    for service, svc_count in services_in_system.items()
                ]
                # Sort services by count descending
                services_list = sorted(services_list, key=lambda x: x['count'], reverse=True)
                
                formatted_systems[system] = {
                    'count': count,
                    'percentage': round(percentage, 2),
                    'services': services_list
                }
            
            formatted_system_reports[target_domain] = formatted_systems
        
        # Sort all data by count descending before generating any reports
        # 1. Sort details inside each consumer_domains entry by count descending
        for target_domain, consumers in formatted_domain_reports.items():
            for consumer_domain, info in consumers.items():
                if 'details' in info and isinstance(info['details'], list):
                    info['details'] = sorted(
                        info['details'],
                        key=lambda entry: entry.get('count', 0),
                        reverse=True
                    )
        
        # 2. Sort consumer_domains by percentage descending, then by excluded_count descending
        sorted_domain_reports = {}
        for domain_key, consumers in formatted_domain_reports.items():
            sorted_consumers = dict(sorted(
                consumers.items(),
                key=lambda kv: (kv[1].get('percentage', 0), kv[1].get('excluded_count', 0)),
                reverse=True
            ))
            sorted_domain_reports[domain_key] = sorted_consumers
        
        # 3. Sort consumer_by_system by count descending
        sorted_system_reports = {}
        for domain_key, systems in formatted_system_reports.items():
            sorted_systems = dict(sorted(
                systems.items(),
                key=lambda kv: kv[1]['count'],
                reverse=True
            ))
            sorted_system_reports[domain_key] = sorted_systems
        
        # Now generate individual domain reports with sorted data
        for domain in domain_consumers.keys():
            domain_safe = sanitize_filename(domain.replace(' ', '_'))
            report_filename = f"{output_dir}/{domain_safe}_consumer_report.json"
            
            total_calls = sum(domain_consumers.get(domain, {}).values())
            
            report = {
                'domain': domain,
                'environment': self.environment,
                'total_calls_received': total_calls,
                'unique_consuming_products': len(domain_consumers.get(domain, {})),
                'unique_systems': len(system_consumers.get(domain, {})),
                'consumer_products': sorted_domain_reports.get(domain, {}),
                'consumer_by_system': sorted_system_reports.get(domain, {})
            }
            
            with open(report_filename, 'w') as f:
                json.dump(report, f, indent=2)
            
            print(f"{Fore.GREEN}Generated report: {report_filename}{Style.RESET_ALL}")
            print(f"  Total calls received: {report['total_calls_received']}")
            print(f"  Consuming products: {report['unique_consuming_products']}")
            print(f"  Systems involved: {report['unique_systems']}")
        
        # Generate summary report with custom filename
        # Team filter takes precedence over application filter
        if team_names:
            # Check if multiple teams
            if ',' in team_names:
                summary_filename = f"{output_dir}/multiple_teams_report.json"
            else:
                # Single team - use team name (sanitize for filename)
                team_label = sanitize_filename(team_names.replace(' ', '_'))
                summary_filename = f"{output_dir}/{team_label}_analysis_summary.json"
        elif application_names:
            # Check if multiple applications
            if ',' in application_names:
                summary_filename = f"{output_dir}/multiple_applications_analysis_summary.json"
            else:
                # Single application - use app name (sanitize for filename)
                app_label = sanitize_filename(application_names.replace(' ', '_'))
                summary_filename = f"{output_dir}/{app_label}_analysis_summary.json"
        else:
            # Default filename
            summary_filename = f"{output_dir}/consumer_analysis_summary.json"
        
        summary = {
            'environment': self.environment,
            'domains_analyzed': list(domain_consumers.keys()),
            'domain_reports': sorted_domain_reports,
            'system_reports': sorted_system_reports
        }
        
        # Add filter info if present
        if application_names:
            summary['filtered_by'] = {'applications': application_names}
        elif team_names:
            summary['filtered_by'] = {'teams': team_names}
            if self.exclude_team_requests:
                summary['filtered_by']['excluded_team_requests'] = True
                summary['filtered_by']['excluded_teams_count'] = len(self.excluded_team_names)
        
        with open(summary_filename, 'w') as f:
            json.dump(summary, f, indent=2)
        
        print(f"\n{Fore.GREEN}Generated summary report: {summary_filename}{Style.RESET_ALL}")

    def generate_csv(self, analysis_results: Dict, output_dir: str = '.', team_names: str = None, application_names: str = None):
        """
        Generate a pivot table CSV from analysis results.

        Creates a cross-tabulation with domains as columns and consumer products as rows,
        with percentages as cell values.

        Args:
            analysis_results: Results from analyze_all_teams()
            output_dir: Directory to save CSV file
            team_names: (Optional) Team filter string for filename
            application_names: (Optional) Application filter string for filename
        """
        domain_consumers = analysis_results['domain_consumers']
        domain_details = analysis_results['domain_details']

        # Calculate percentages for each domain's consumers
        domain_percentages = {}
        for target_domain, consumer_products in domain_consumers.items():
            total_calls = sum(consumer_products.values())
            domain_percentages[target_domain] = {}

            for consumer_product, count in consumer_products.items():
                percentage = (count / total_calls * 100) if total_calls > 0 else 0
                details = domain_details.get(target_domain, {}).get(consumer_product, [])

                # Calculate total excluded count from details
                excluded_count = sum(d.get('excluded_count', 0) for d in details)

                # Check if this product should be excluded
                is_excluded_product = consumer_product.lower() in self.exclude_products

                # If excluded by product name, treat count as excluded
                if is_excluded_product:
                    excluded_count = count
                    count = 0
                    percentage = 0.0

                # Only include products with non-excluded traffic
                if count > 0:
                    domain_percentages[target_domain][consumer_product] = round(percentage, 2)

        # Collect all unique domains and consumer products
        domains = sorted(domain_percentages.keys())
        all_consumers = set()

        for domain, consumers in domain_percentages.items():
            all_consumers.update(consumers.keys())

        all_consumers = sorted(all_consumers)

        # Determine CSV filename (match JSON naming convention)
        if team_names:
            if ',' in team_names:
                csv_filename = f"{output_dir}/multiple_teams_report.csv"
            else:
                team_label = sanitize_filename(team_names.replace(' ', '_'))
                csv_filename = f"{output_dir}/{team_label}_analysis_summary.csv"
        elif application_names:
            if ',' in application_names:
                csv_filename = f"{output_dir}/multiple_applications_analysis_summary.csv"
            else:
                app_label = sanitize_filename(application_names.replace(' ', '_'))
                csv_filename = f"{output_dir}/{app_label}_analysis_summary.csv"
        else:
            csv_filename = f"{output_dir}/consumer_analysis_summary.csv"

        # Write CSV
        try:
            with open(csv_filename, 'w', newline='') as f:
                writer = csv.writer(f)

                # Write header row (domains)
                writer.writerow(['Consumer'] + domains)

                # Write data rows (consumers with percentages)
                for consumer in all_consumers:
                    row = [consumer]
                    for domain in domains:
                        percentage = domain_percentages.get(domain, {}).get(consumer, 0)
                        row.append(f"{percentage:.2f}" if percentage > 0 else "0.00")
                    writer.writerow(row)

            print(f"{Fore.GREEN}Generated CSV report: {csv_filename}{Style.RESET_ALL}")

        except Exception as e:
            print(f"{Fore.RED}Error generating CSV: {e}{Style.RESET_ALL}")


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
    
    # Authentication options (mutually exclusive - will read from ~/.datadog.cfg if not provided)
    add_datadog_auth_args(parser)
    
    # Optional arguments
    parser.add_argument('-t', '--teams', help='Optional: Process only these teams, comma-separated (e.g., "Oktagon,Identity")')
    parser.add_argument('-a', '--applications', help='Optional: Process only these applications, comma-separated (e.g., "iam-service,auth-service")')
    parser.add_argument('--excludeSpecifiedTeamRequests', action='store_true', help='Exclude requests from services owned by the specified team(s). Only valid when teams are specified (via --teams parameter or config file).')
    parser.add_argument('--excludeProducts', help='Optional: Exclude specified product/platform/domain names from totals, comma-separated (e.g., "External/Unknown,product-name")')
    parser.add_argument('--timeout', type=int, default=30, help='Request timeout in seconds (default: 30)')
    parser.add_argument('--rate-limit', type=float, default=1.0, help='Delay between API requests in seconds (default: 1.0)')
    parser.add_argument('--preserve-rate-limit', type=int, default=1, help='Number of requests to preserve from rate limit (default: 1, use 0 to consume full limit)')
    parser.add_argument('--limit', type=int, default=100, help='Max consumers per service (default: 100)')
    parser.add_argument('--time-period', default='1h', help='Time period to query (e.g., 1h, 4h, 1d, 1w) (default: 1h)')
    parser.add_argument('--output-dir', default='.', help='Output directory for reports (default: current directory)')
    parser.add_argument('--nocache', action='store_true', help='Disable using cached responses (still updates cache)')
    parser.add_argument('--ignoreCacheExpiry', action='store_true', help='Use cached data without checking expiration time')
    
    args = parser.parse_args()
    
    # Load credentials, application aliases, skip list, service mappings, desired-end-categorizations, teams, excludeSpecifiedTeamRequests, and exclude-products from config file if not provided via command line
    application_aliases = {}
    skip_applications = []
    service_mappings = {}
    desired_end_categorizations = []
    remap_categorizations = {}
    config_teams = []
    config_exclude_team_requests = False
    config_exclude_products = []
    map_products = {}
    auth_from_cli = args.pat or args.cookies or args.api_key or args.app_key

    if not auth_from_cli:
        config_pat, config_api_key, config_app_key, application_aliases, skip_applications, service_mappings, desired_end_categorizations, remap_categorizations, config_teams, config_exclude_team_requests, config_exclude_products, map_products = load_datadog_config()
        if config_pat:
            args.pat = config_pat
        elif config_api_key and config_app_key:
            args.api_key = config_api_key
            args.app_key = config_app_key
        else:
            parser.error('Authentication required: provide --pat OR (--api-key and --app-key) OR --cookies OR create ~/.datadog.cfg with credentials')
    else:
        # Still load application aliases, skip list, service mappings, desired-end-categorizations, remap-categorizations, teams, excludeSpecifiedTeamRequests, exclude-products, and map-products even if auth is provided via CLI
        _, _, _, application_aliases, skip_applications, service_mappings, desired_end_categorizations, remap_categorizations, config_teams, config_exclude_team_requests, config_exclude_products, map_products = load_datadog_config()

    # Validate authentication combinations
    if args.api_key and not args.app_key:
        parser.error('--app-key is required when using --api-key')
    if args.app_key and not args.api_key:
        parser.error('--api-key is required when using --app-key')

    # Final check: ensure we have some form of authentication
    if not (args.pat or args.cookies or (args.api_key and args.app_key)):
        parser.error('Authentication required: provide --pat OR (--api-key and --app-key) OR --cookies OR create ~/.datadog.cfg with credentials')

    # Auto-save credentials passed on command line to config file (for future convenience)
    # Only save if credentials explicitly provided on CLI (not loaded from config or cookies)
    if auth_from_cli and not args.cookies and (args.pat or (args.api_key and args.app_key)):
        save_credentials_to_config(pat=args.pat, api_key=args.api_key, app_key=args.app_key)
    
    # Validate preserve-rate-limit
    if args.preserve_rate_limit < 0:
        parser.error('--preserve-rate-limit must be >= 0')
    
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

    # Check if attribution data is fresh (within 30 days)
    import time
    file_mtime = os.path.getmtime(args.input_file)
    file_age_seconds = time.time() - file_mtime
    file_age_days = file_age_seconds / (24 * 3600)

    if file_age_days > 30:
        print(f"\n{Fore.YELLOW}⚠️  WARNING: Team attribution data is {file_age_days:.0f} days old{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}This analysis may not reflect current team/application ownership.{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}Please regenerate {args.input_file} by running:{Style.RESET_ALL}\n")
        print(f"{Fore.CYAN}  python teamApplicationAttribution.py https://backstage.example.com{Style.RESET_ALL}\n")
        print(f"{Fore.YELLOW}Then re-run this analysis with the updated data.{Style.RESET_ALL}\n")
        sys.exit(1)
    elif file_age_days > 14:
        print(f"{Fore.YELLOW}ℹ️  Note: Team attribution data is {file_age_days:.0f} days old. Consider updating it.{Style.RESET_ALL}\n")

    # Keep a copy of full data for domain lookups
    full_attribution_data = attribution_data.copy()
    
    # Filter to specified teams if provided (from command line or config file)
    team_filters = []
    if args.teams:
        # Parse comma-separated list and normalize (strip whitespace, lowercase)
        team_filters = [t.strip().lower() for t in args.teams.split(',') if t.strip()]
    elif config_teams:
        # Use teams from config file if no command line teams provided
        team_filters = [t.strip().lower() for t in config_teams if t.strip()]
        print(f"{Fore.CYAN}Using teams from ~/.datadog.cfg: {', '.join(config_teams)}{Style.RESET_ALL}")
    
    # Use config file excludeSpecifiedTeamRequests if not provided on command line
    exclude_team_requests = args.excludeSpecifiedTeamRequests or config_exclude_team_requests
    
    # Merge exclude-products from command line and config file
    exclude_products = []
    if args.excludeProducts:
        # Parse comma-separated list and normalize (strip whitespace, lowercase for case-insensitive matching)
        exclude_products = [p.strip().lower() for p in args.excludeProducts.split(',') if p.strip()]
    elif config_exclude_products:
        # Use exclude-products from config file if not provided on command line
        exclude_products = [p.strip().lower() for p in config_exclude_products if p.strip()]
        print(f"{Fore.CYAN}Using exclude-products from ~/.datadog.cfg: {', '.join(config_exclude_products)}{Style.RESET_ALL}")
    
    # Validate --excludeSpecifiedTeamRequests usage
    if exclude_team_requests and not team_filters:
        print(f"{Fore.RED}Error: excludeSpecifiedTeamRequests can only be used when teams are specified (via --teams parameter or config file){Style.RESET_ALL}")
        sys.exit(1)
    
    if team_filters:
        # Store the original team names for display
        args.teams = ','.join(config_teams if config_teams and not args.teams else args.teams.split(','))
        
        if team_filters:
            teams_found = []
            filtered_data = {}
            
            for team_name, team_data in attribution_data.items():
                # Case-insensitive team matching against all filters
                team_matches = any(
                    team_name.lower() == filter_team or 
                    team_data.get('team_name', '').lower() == filter_team or
                    team_data.get('team_title', '').lower() == filter_team
                    for filter_team in team_filters
                )
                
                if team_matches:
                    filtered_data[team_name] = team_data
                    teams_found.append(team_data.get('team_title', team_name))
            
            if not teams_found:
                print(f"{Fore.RED}Error: None of the specified teams found in input file: {args.teams}{Style.RESET_ALL}")
                print(f"{Fore.YELLOW}Available teams:{Style.RESET_ALL}")
                for team_name, team_data in list(attribution_data.items())[:10]:
                    print(f"  - {team_data.get('team_title', team_name)}")
                if len(attribution_data) > 10:
                    print(f"  ... and {len(attribution_data) - 10} more")
                sys.exit(1)
            
            print(f"{Fore.GREEN}  Filtering to {len(teams_found)} team(s): {', '.join(teams_found)}{Style.RESET_ALL}")
            attribution_data = filtered_data
    
    # Filter to specified applications if provided
    app_filters = []
    if args.applications:
        # Parse comma-separated list and normalize (strip whitespace, lowercase)
        app_filters = [a.strip().lower() for a in args.applications.split(',') if a.strip()]
        
        if app_filters:
            apps_found = set()
            filtered_data = {}
            
            for team_name, team_data in attribution_data.items():
                # Check if this team has any of the specified applications
                filtered_applications = []
                for application in team_data.get('applications', []):
                    # Case-insensitive application name matching against all filters
                    app_matches = any(
                        application.get('name', '').lower() == filter_app or
                        application.get('title', '').lower() == filter_app
                        for filter_app in app_filters
                    )
                    
                    if app_matches:
                        filtered_applications.append(application)
                        app_name = application.get('title') or application.get('name')
                        apps_found.add(app_name)
                
                # Only include team if it has at least one matching application
                if filtered_applications:
                    filtered_team_data = team_data.copy()
                    filtered_team_data['applications'] = filtered_applications
                    filtered_team_data['application_count'] = len(filtered_applications)
                    filtered_data[team_name] = filtered_team_data
            
            if not apps_found:
                print(f"{Fore.RED}Error: None of the specified applications found in input file: {args.applications}{Style.RESET_ALL}")
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
            
            print(f"{Fore.GREEN}  Filtering to {len(apps_found)} application(s): {', '.join(sorted(apps_found))}{Style.RESET_ALL}")
            print(f"{Fore.GREEN}  Found in {len(filtered_data)} team(s){Style.RESET_ALL}")
            attribution_data = filtered_data
    
    # Initialize Datadog client
    print(f"{Fore.CYAN}Initializing Datadog client: {args.datadog_host}{Style.RESET_ALL}")
    if args.api_key:
        print(f"{Fore.CYAN}Authentication: API key + Application key{Style.RESET_ALL}")
    elif args.cookies:
        print(f"{Fore.CYAN}Authentication: Cookie{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Rate limit delay: {args.rate_limit} seconds between requests{Style.RESET_ALL}")
    print(f"{Fore.CYAN}Preserve rate limit: {args.preserve_rate_limit} request(s){Style.RESET_ALL}")
    
    # Determine cache usage
    use_cache = not args.nocache
    if args.nocache:
        print(f"{Fore.YELLOW}Cache: Disabled (will still update cache){Style.RESET_ALL}")
    elif args.ignoreCacheExpiry:
        print(f"{Fore.CYAN}Cache: Enabled (ignoring expiration time){Style.RESET_ALL}")
    else:
        print(f"{Fore.CYAN}Cache: Enabled (max age: {CACHE_MAX_AGE_SECONDS/3600:.1f}h){Style.RESET_ALL}")
    
    datadog_client = DatadogClient(
        host=args.datadog_host,
        pat=args.pat,
        api_key=args.api_key,
        app_key=args.app_key,
        cookies=args.cookies,
        timeout=args.timeout,
        rate_limit_delay=args.rate_limit,
        preserve_rate_limit=args.preserve_rate_limit,
        use_cache=use_cache,
        ignore_cache_expiry=args.ignoreCacheExpiry
    )
    
    # Initialize analyzer
    analyzer = ServiceConsumerAnalyzer(
        attribution_data=attribution_data,
        datadog_client=datadog_client,
        environment=args.environment,
        time_period=args.time_period,
        application_filters=app_filters,  # Pass the list of application filters
        full_attribution_data=full_attribution_data,  # Pass full data for domain lookups
        service_mappings=service_mappings,  # Pass service mappings from config
        application_aliases=application_aliases,  # Pass application aliases from config
        skip_applications=skip_applications,  # Pass skip list from config
        exclude_team_requests=exclude_team_requests,  # Exclude requests from specified team services (from CLI or config)
        desired_end_categorizations=desired_end_categorizations,  # Pass desired categorizations from config
        remap_categorizations=remap_categorizations,  # Pass remap categorizations from config
        exclude_products=exclude_products,  # Pass exclude products from CLI or config
        map_products=map_products  # Pass product mappings from config
    )
    
    # Run analysis
    results = analyzer.analyze_all_teams()
    
    # Generate reports with custom filenames based on filters
    analyzer.generate_reports(
        results,
        output_dir=args.output_dir,
        team_names=args.teams,
        application_names=args.applications
    )

    # Generate CSV pivot table
    analyzer.generate_csv(
        results,
        output_dir=args.output_dir,
        team_names=args.teams,
        application_names=args.applications
    )

    # Save errors to file if any 500 errors occurred
    errors_file = datadog_client.save_errors_to_file(args.output_dir)
    if errors_file:
        print(f"\n{Fore.YELLOW}Warning: {len(datadog_client.failed_500_errors)} service(s) failed with 500 errors after retries{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}Error details saved to: {errors_file}{Style.RESET_ALL}")
    
    print(f"\n{Fore.GREEN}Consumer analysis complete!{Style.RESET_ALL}")


if __name__ == '__main__':
    main()

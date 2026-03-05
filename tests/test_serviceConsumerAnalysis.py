import os
import sys
import json
import hashlib
import pytest
from unittest.mock import patch, MagicMock
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from serviceConsumerAnalysis import DatadogClient, ServiceConsumerAnalyzer


class TestDatadogClientGetHeaders:
    def test_api_key_auth(self):
        client = DatadogClient(
            host="https://app.datadoghq.com",
            api_key="test-api-key",
            app_key="test-app-key"
        )
        headers = client._get_headers()
        assert headers["DD-API-KEY"] == "test-api-key"
        assert headers["DD-APPLICATION-KEY"] == "test-app-key"
        assert "Cookie" not in headers

    def test_cookie_auth(self):
        client = DatadogClient(
            host="https://app.datadoghq.com",
            cookies="session=abc123"
        )
        headers = client._get_headers()
        assert headers["Cookie"] == "session=abc123"
        assert "DD-API-KEY" not in headers

    def test_content_type_always_present(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        headers = client._get_headers()
        assert headers["Content-Type"] == "application/json"


class TestDatadogClientGenerateCacheKey:
    def test_deterministic(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        key1 = client._generate_cache_key("https://example.com/api", {"service": "foo"})
        key2 = client._generate_cache_key("https://example.com/api", {"service": "foo"})
        assert key1 == key2

    def test_different_urls_different_keys(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        key1 = client._generate_cache_key("https://example.com/api/a")
        key2 = client._generate_cache_key("https://example.com/api/b")
        assert key1 != key2

    def test_different_payloads_different_keys(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        key1 = client._generate_cache_key("https://example.com", {"a": 1})
        key2 = client._generate_cache_key("https://example.com", {"a": 2})
        assert key1 != key2

    def test_returns_sha256_hex(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        key = client._generate_cache_key("https://example.com")
        assert len(key) == 64  # SHA256 hex digest length


class TestDatadogClientParseAnalyticsResponse:
    def test_parses_buckets(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        data = {
            "data": [
                {
                    "attributes": {
                        "by": {"service": "caller-a"},
                        "compute": {"c0": 100}
                    }
                },
                {
                    "attributes": {
                        "by": {"service": "caller-b"},
                        "compute": {"c0": 50}
                    }
                }
            ],
            "meta": {}
        }
        result = client._parse_analytics_response(data)
        assert len(result) == 2
        assert result[0]["service"] == "caller-a"
        assert result[0]["count"] == 100

    def test_empty_data(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        data = {"data": [], "meta": {}}
        result = client._parse_analytics_response(data)
        assert result == []

    def test_missing_service_skipped(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        data = {
            "data": [
                {"attributes": {"by": {}, "compute": {"c0": 10}}}
            ],
            "meta": {}
        }
        result = client._parse_analytics_response(data)
        assert result == []


class TestDatadogClientParseTraceSearchResponse:
    def test_parses_traces(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        data = {
            "traces": [
                {"spans": [{"service": "svc-a"}, {"service": "svc-b"}]},
                {"spans": [{"service": "svc-a"}]}
            ]
        }
        result = client._parse_trace_search_response(data)
        services = {r["service"]: r["count"] for r in result}
        assert services["svc-a"] == 2
        assert services["svc-b"] == 1

    def test_empty_traces(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        result = client._parse_trace_search_response({"traces": []})
        assert result == []


class TestDatadogClientParseConsumerResponse:
    def test_v2_analytics_format(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        data = {
            "data": {
                "buckets": [
                    {"by": {"service": "caller"}, "computes": {"c0": 42}}
                ]
            }
        }
        result = client._parse_consumer_response(data)
        assert len(result) == 1
        assert result[0]["service"] == "caller"
        assert result[0]["count"] == 42

    def test_services_format(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        data = {
            "services": [
                {"name": "svc-1", "count": 10},
                {"service": "svc-2", "hits": 20}
            ]
        }
        result = client._parse_consumer_response(data)
        assert len(result) == 2
        assert result[0]["service"] == "svc-1"
        assert result[1]["count"] == 20

    def test_unknown_format(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        result = client._parse_consumer_response({"unknown": "structure"})
        assert result == []


class TestDatadogClientParseApmServiceResponse:
    def test_upstream_services(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        data = {
            "upstream_services": [
                {"service": "caller-1", "count": 100},
                {"service": "caller-2", "count": 50}
            ]
        }
        result = client._parse_apm_service_response(data, limit=10)
        assert len(result) == 2
        assert result[0]["service"] == "caller-1"

    def test_dependencies_format(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        data = {
            "dependencies": {
                "upstream": [
                    {"name": "svc-x", "requests": 200}
                ]
            }
        }
        result = client._parse_apm_service_response(data, limit=10)
        assert len(result) == 1
        assert result[0]["service"] == "svc-x"
        assert result[0]["count"] == 200

    def test_limit_applied(self):
        client = DatadogClient(host="https://app.datadoghq.com", api_key="k", app_key="a")
        data = {
            "upstream_services": [
                {"service": f"svc-{i}", "count": i} for i in range(20)
            ]
        }
        result = client._parse_apm_service_response(data, limit=5)
        assert len(result) == 5


class TestServiceConsumerAnalyzerBuildLookupMaps:
    def _make_analyzer(self, attribution_data):
        mock_client = MagicMock()
        analyzer = ServiceConsumerAnalyzer(
            attribution_data=attribution_data,
            datadog_client=mock_client,
            environment="prod"
        )
        return analyzer

    def test_builds_service_to_team(self):
        data = {
            "team-alpha": {
                "team_name": "team-alpha",
                "team_title": "Team Alpha",
                "domain": "payments",
                "business_unit": "BU",
                "applications": [
                    {"name": "payment-service", "title": "Payment Service", "system": "core", "product": None, "platform": None}
                ]
            }
        }
        analyzer = self._make_analyzer(data)
        assert "payment-service" in analyzer.service_to_team
        assert analyzer.service_to_team["payment-service"]["team_name"] == "team-alpha"
        assert analyzer.service_to_system["payment-service"] == "core"

    def test_lowercase_lookup(self):
        data = {
            "team-a": {
                "team_name": "team-a",
                "team_title": "Team A",
                "domain": None,
                "business_unit": None,
                "applications": [
                    {"name": "MyService", "title": "MyService", "system": None, "product": None, "platform": None}
                ]
            }
        }
        analyzer = self._make_analyzer(data)
        assert "myservice" in analyzer.service_to_team


class TestServiceConsumerAnalyzerLookupServiceWithFallback:
    def _make_analyzer(self, attribution_data, aliases=None):
        mock_client = MagicMock()
        analyzer = ServiceConsumerAnalyzer(
            attribution_data=attribution_data,
            datadog_client=mock_client,
            environment="prod",
            application_aliases=aliases or {}
        )
        return analyzer

    def _sample_data(self):
        return {
            "team-a": {
                "team_name": "team-a",
                "team_title": "Team A",
                "domain": None,
                "business_unit": None,
                "applications": [
                    {"name": "my-app", "title": "My App", "system": None, "product": None, "platform": None}
                ]
            }
        }

    def test_exact_match(self):
        analyzer = self._make_analyzer(self._sample_data())
        team_info, found = analyzer._lookup_service_with_fallback("my-app")
        assert team_info is not None
        assert found == "my-app"

    def test_strip_service_suffix(self):
        analyzer = self._make_analyzer(self._sample_data())
        team_info, found = analyzer._lookup_service_with_fallback("my-app-service")
        assert team_info is not None
        assert found == "my-app"

    def test_strip_http_client_suffix(self):
        analyzer = self._make_analyzer(self._sample_data())
        team_info, found = analyzer._lookup_service_with_fallback("my-app-http-client")
        assert team_info is not None

    def test_strip_lambda_suffix(self):
        analyzer = self._make_analyzer(self._sample_data())
        team_info, found = analyzer._lookup_service_with_fallback("my-app-lambda")
        assert team_info is not None

    def test_no_match(self):
        analyzer = self._make_analyzer(self._sample_data())
        team_info, found = analyzer._lookup_service_with_fallback("unknown-service")
        assert team_info is None
        assert found is None

    def test_alias_lookup(self):
        data = self._sample_data()
        aliases = {"alias-svc": "my-app"}
        analyzer = self._make_analyzer(data, aliases=aliases)
        team_info, found = analyzer._lookup_service_with_fallback("alias-svc")
        assert team_info is not None


class TestServiceConsumerAnalyzerApplyRemapCategorization:
    def _make_analyzer(self, remap=None):
        mock_client = MagicMock()
        return ServiceConsumerAnalyzer(
            attribution_data={},
            datadog_client=mock_client,
            environment="prod",
            remap_categorizations=remap or {}
        )

    def test_no_remap(self):
        analyzer = self._make_analyzer()
        assert analyzer._apply_remap_categorization("Payments") == "payments"

    def test_with_remap(self):
        analyzer = self._make_analyzer(remap={"old-name": "new-name"})
        assert analyzer._apply_remap_categorization("Old-Name") == "new-name"

    def test_case_insensitive(self):
        analyzer = self._make_analyzer(remap={"payments": "billing"})
        assert analyzer._apply_remap_categorization("PAYMENTS") == "billing"

    def test_no_match_returns_lowercase(self):
        analyzer = self._make_analyzer(remap={"x": "y"})
        assert analyzer._apply_remap_categorization("Other") == "other"


class TestServiceConsumerAnalyzerIsServiceFromExcludedTeam:
    def test_not_excluded_when_flag_off(self):
        mock_client = MagicMock()
        data = {
            "team-a": {
                "team_name": "team-a", "team_title": "Team A",
                "domain": None, "business_unit": None,
                "applications": [{"name": "svc-a", "title": "svc-a", "system": None, "product": None, "platform": None}]
            }
        }
        analyzer = ServiceConsumerAnalyzer(
            attribution_data=data,
            datadog_client=mock_client,
            environment="prod",
            exclude_team_requests=False
        )
        assert analyzer._is_service_from_excluded_team("svc-a") is False

    def test_excluded_when_flag_on(self):
        mock_client = MagicMock()
        data = {
            "team-a": {
                "team_name": "team-a", "team_title": "Team A",
                "domain": None, "business_unit": None,
                "applications": [{"name": "svc-a", "title": "svc-a", "system": None, "product": None, "platform": None}]
            }
        }
        analyzer = ServiceConsumerAnalyzer(
            attribution_data=data,
            datadog_client=mock_client,
            environment="prod",
            exclude_team_requests=True
        )
        assert analyzer._is_service_from_excluded_team("svc-a") is True

    def test_unknown_service_not_excluded(self):
        mock_client = MagicMock()
        data = {
            "team-a": {
                "team_name": "team-a", "team_title": "Team A",
                "domain": None, "business_unit": None,
                "applications": [{"name": "svc-a", "title": "svc-a", "system": None, "product": None, "platform": None}]
            }
        }
        analyzer = ServiceConsumerAnalyzer(
            attribution_data=data,
            datadog_client=mock_client,
            environment="prod",
            exclude_team_requests=True
        )
        assert analyzer._is_service_from_excluded_team("unknown-svc") is False

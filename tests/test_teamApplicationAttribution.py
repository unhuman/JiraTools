import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from teamApplicationAttribution import (
    extract_component_info, extract_team_info, save_to_json, get_domain_info
)


class TestExtractComponentInfo:
    def test_full_component(self):
        component = {
            "metadata": {
                "name": "my-service",
                "title": "My Service",
                "description": "A service"
            },
            "spec": {
                "type": "service",
                "lifecycle": "production",
                "system": "payments"
            },
            "kind": "Component"
        }
        result = extract_component_info(component)
        assert result["name"] == "my-service"
        assert result["title"] == "My Service"
        assert result["type"] == "service"
        assert result["lifecycle"] == "production"
        assert result["system"] == "payments"

    def test_minimal_component(self):
        component = {"metadata": {}, "spec": {}}
        result = extract_component_info(component)
        assert result["name"] == "Unknown"
        assert result["type"] == "Unknown"
        assert result["lifecycle"] == "Unknown"

    def test_platform_fallback_for_system(self):
        component = {
            "metadata": {"name": "svc", "labels": {"platform": "cloud"}},
            "spec": {}
        }
        result = extract_component_info(component)
        assert result["system"] == "cloud"
        assert result["platform"] == "cloud"

    def test_system_takes_precedence_over_platform(self):
        component = {
            "metadata": {"name": "svc", "labels": {"platform": "cloud"}},
            "spec": {"system": "core"}
        }
        result = extract_component_info(component)
        assert result["system"] == "core"
        assert result["platform"] == "cloud"

    def test_product_and_business_unit_from_labels(self):
        component = {
            "metadata": {
                "name": "svc",
                "labels": {"product": "essentials", "business-unit": "event-cloud"}
            },
            "spec": {}
        }
        result = extract_component_info(component)
        assert result["product"] == "essentials"
        assert result["business_unit"] == "event-cloud"


class TestExtractTeamInfo:
    def test_basic_team(self):
        team = {
            "metadata": {
                "name": "my-team",
                "title": "My Team",
                "description": "A team",
                "annotations": {},
                "labels": {}
            },
            "spec": {}
        }
        result = extract_team_info(team)
        assert result["team_name"] == "my-team"
        assert result["team_title"] == "My Team"

    def test_labels_for_business_unit(self):
        team = {
            "metadata": {
                "name": "t", "title": "T",
                "annotations": {},
                "labels": {"business-unit": "event-cloud"}
            },
            "spec": {}
        }
        result = extract_team_info(team)
        assert result["business_unit"] == "Event Cloud"

    def test_labels_for_product(self):
        team = {
            "metadata": {
                "name": "t", "title": "T",
                "annotations": {},
                "labels": {"product": "essentials"}
            },
            "spec": {}
        }
        result = extract_team_info(team)
        assert result["product"] == "Essentials"

    def test_platform_formatting(self):
        team = {
            "metadata": {
                "name": "t", "title": "T",
                "annotations": {},
                "labels": {"platform": "simple-solutions"}
            },
            "spec": {}
        }
        result = extract_team_info(team)
        assert result["platform"] == "Simple Solutions"

    def test_domain_from_parent(self):
        team = {
            "metadata": {
                "name": "t", "title": "T",
                "annotations": {},
                "labels": {}
            },
            "spec": {"parent": "domain:default/iam"}
        }
        result = extract_team_info(team)
        assert result["domain"] == "IAM"


class TestSaveToJson:
    def test_saves_sorted_json(self, tmp_path):
        data = {
            "beta-team": {"application_count": 1, "applications": [{"name": "b-svc"}]},
            "alpha-team": {"application_count": 2, "applications": [{"name": "z-app"}, {"name": "a-app"}]}
        }
        output_file = str(tmp_path / "output.json")
        save_to_json(data, output_file)

        with open(output_file) as f:
            saved = json.load(f)
        keys = list(saved.keys())
        assert keys[0] == "alpha-team"
        assert keys[1] == "beta-team"
        # Applications sorted within team
        assert saved["alpha-team"]["applications"][0]["name"] == "a-app"

    def test_handles_empty_data(self, tmp_path):
        output_file = str(tmp_path / "empty.json")
        save_to_json({}, output_file)

        with open(output_file) as f:
            saved = json.load(f)
        assert saved == {}


class TestGetDomainInfo:
    @patch("teamApplicationAttribution.requests.get")
    def test_returns_domain_info(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "metadata": {"name": "iam", "title": "IAM"},
            "spec": {}
        }
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = get_domain_info("https://backstage.example.com", "domain:default/iam")
        assert result["domain_name"] == "iam"
        assert result["domain_title"] == "IAM"

    @patch("teamApplicationAttribution.requests.get")
    def test_request_error_returns_empty(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.ConnectionError()
        result = get_domain_info("https://backstage.example.com", "domain:default/iam")
        assert result == {}

    def test_invalid_ref_returns_empty(self):
        result = get_domain_info("https://backstage.example.com", "not-a-domain-ref")
        assert result == {}

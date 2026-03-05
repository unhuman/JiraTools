import os
import pytest
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from libraries.backstageTools import (
    matches_team_owner, get_all_teams, get_all_components,
    filter_components_for_team, get_team_components
)


class TestMatchesTeamOwner:
    def test_full_group_default_format(self):
        assert matches_team_owner("group:default/my-team", "my-team") is True

    def test_group_format(self):
        assert matches_team_owner("group:my-team", "my-team") is True

    def test_plain_name(self):
        assert matches_team_owner("my-team", "my-team") is True

    def test_ends_with_slash(self):
        assert matches_team_owner("org/my-team", "my-team") is True

    def test_ends_with_colon(self):
        assert matches_team_owner("namespace:my-team", "my-team") is True

    def test_case_insensitive(self):
        assert matches_team_owner("Group:Default/My-Team", "my-team") is True

    def test_no_match(self):
        assert matches_team_owner("group:default/other-team", "my-team") is False

    def test_empty_owner(self):
        assert matches_team_owner("", "my-team") is False

    def test_none_owner(self):
        assert matches_team_owner(None, "my-team") is False


class TestFilterComponentsForTeam:
    def setup_method(self):
        self.components = [
            {"metadata": {"name": "app1"}, "spec": {"owner": "group:default/team-a", "type": "application"}},
            {"metadata": {"name": "app2"}, "spec": {"owner": "group:default/team-a", "type": "application"}},
            {"metadata": {"name": "lib1"}, "spec": {"owner": "group:default/team-a", "type": "library"}},
            {"metadata": {"name": "app3"}, "spec": {"owner": "group:default/team-b", "type": "application"}},
        ]

    def test_filters_by_team_and_type(self):
        result = filter_components_for_team(self.components, "team-a")
        names = [c["metadata"]["name"] for c in result]
        assert names == ["app1", "app2"]

    def test_filters_by_team_all_types(self):
        result = filter_components_for_team(self.components, "team-a", comp_type=None)
        names = [c["metadata"]["name"] for c in result]
        assert names == ["app1", "app2", "lib1"]

    def test_different_team(self):
        result = filter_components_for_team(self.components, "team-b")
        names = [c["metadata"]["name"] for c in result]
        assert names == ["app3"]

    def test_no_match(self):
        result = filter_components_for_team(self.components, "team-z")
        assert result == []

    def test_filter_library_type(self):
        result = filter_components_for_team(self.components, "team-a", comp_type="library")
        names = [c["metadata"]["name"] for c in result]
        assert names == ["lib1"]


class TestGetAllTeams:
    @patch('libraries.backstageTools.requests.get')
    def test_returns_teams_list(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = [{"metadata": {"name": "team-a"}}]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = get_all_teams("https://backstage.example.com")
        assert len(result) == 1
        assert result[0]["metadata"]["name"] == "team-a"

    @patch('libraries.backstageTools.requests.get')
    def test_returns_teams_from_dict(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = {"items": [{"metadata": {"name": "team-a"}}]}
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = get_all_teams("https://backstage.example.com")
        assert len(result) == 1

    @patch('libraries.backstageTools.requests.get')
    def test_handles_request_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.ConnectionError("fail")

        result = get_all_teams("https://backstage.example.com")
        assert result == []


class TestGetAllComponents:
    @patch('libraries.backstageTools.requests.get')
    def test_returns_components(self, mock_get):
        mock_response = MagicMock()
        mock_response.json.return_value = [
            {"metadata": {"name": "app1"}, "spec": {"type": "application"}}
        ]
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        result = get_all_components("https://backstage.example.com")
        assert len(result) == 1

    @patch('libraries.backstageTools.requests.get')
    def test_handles_request_error(self, mock_get):
        import requests
        mock_get.side_effect = requests.exceptions.ConnectionError("fail")

        result = get_all_components("https://backstage.example.com")
        assert result == []


class TestGetTeamComponents:
    @patch('libraries.backstageTools.get_all_components')
    def test_delegates_to_filter(self, mock_get_all):
        mock_get_all.return_value = [
            {"metadata": {"name": "app1"}, "spec": {"owner": "group:default/team-a", "type": "application"}},
        ]
        result = get_team_components("https://backstage.example.com", "team-a")
        assert len(result) == 1
        assert result[0]["metadata"]["name"] == "app1"

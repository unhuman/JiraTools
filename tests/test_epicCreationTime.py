import os
import sys
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from epicCreationTime import parse_jira_datetime, get_open_epics, get_epic_development_data


class TestParseJiraDatetime:
    def test_utc_z_suffix(self):
        result = parse_jira_datetime("2021-08-12T17:46:44.000Z")
        assert result.year == 2021
        assert result.month == 8
        assert result.day == 12
        assert result.hour == 17

    def test_offset_without_colon(self):
        result = parse_jira_datetime("2021-08-12T17:46:44.000+0000")
        assert result.year == 2021

    def test_offset_with_colon(self):
        result = parse_jira_datetime("2021-08-12T17:46:44.000+00:00")
        assert result.year == 2021

    def test_negative_offset(self):
        result = parse_jira_datetime("2023-01-15T10:30:00.000-0500")
        assert result.year == 2023
        assert result.hour == 10

    def test_no_timezone(self):
        result = parse_jira_datetime("2023-06-01T12:00:00.000")
        assert result.year == 2023
        assert result.month == 6


class TestGetOpenEpics:
    @patch('epicCreationTime.jira')
    def test_returns_epics(self, mock_jira_module):
        mock_client = MagicMock()
        mock_epic = MagicMock()
        mock_epic.key = "PROJ-1"
        mock_client.search_issues.return_value = [mock_epic]

        result = get_open_epics(mock_client)
        assert len(result) == 1
        mock_client.search_issues.assert_called_once()

    @patch('epicCreationTime.jira')
    def test_with_team_filter(self, mock_jira_module):
        mock_client = MagicMock()
        mock_client.search_issues.return_value = []

        result = get_open_epics(mock_client, sprint_team_name_filter="Team Alpha")
        assert result == []
        call_args = mock_client.search_issues.call_args[0][0]
        assert '"Sprint Team" = "Team Alpha"' in call_args

    @patch('epicCreationTime.jira')
    def test_with_project_key(self, mock_jira_module):
        mock_client = MagicMock()
        mock_client.search_issues.return_value = []

        result = get_open_epics(mock_client, project_key="MYPROJ")
        call_args = mock_client.search_issues.call_args[0][0]
        assert 'project = "MYPROJ"' in call_args


class TestGetEpicDevelopmentData:
    def _make_epic(self, key="EPIC-1", summary="Test Epic", created="2023-01-01T00:00:00.000Z"):
        epic = MagicMock()
        epic.key = key
        epic.fields.summary = summary
        epic.fields.created = created
        return epic

    def _make_child(self, created):
        child = MagicMock()
        child.fields.created = created
        return child

    def test_returns_data_with_children(self):
        mock_client = MagicMock()
        epic = self._make_epic()
        children = [
            self._make_child("2023-01-10T00:00:00.000Z"),
            self._make_child("2023-02-15T00:00:00.000Z"),
        ]
        mock_client.search_issues.return_value = children

        result = get_epic_development_data(mock_client, epic)
        assert result is not None
        assert result["epic_key"] == "EPIC-1"
        assert result["relevant_ticket_count"] == 2
        assert result["epic_development_span"] > timedelta(0)

    def test_no_children_returns_none(self):
        mock_client = MagicMock()
        epic = self._make_epic()
        mock_client.search_issues.return_value = []

        result = get_epic_development_data(mock_client, epic)
        assert result is None

    def test_only_prior_children_returns_none(self):
        mock_client = MagicMock()
        epic = self._make_epic(created="2023-06-01T00:00:00.000Z")
        children = [
            self._make_child("2023-01-01T00:00:00.000Z"),  # before epic
        ]
        mock_client.search_issues.return_value = children

        result = get_epic_development_data(mock_client, epic)
        assert result is None

    def test_counts_prior_and_relevant(self):
        mock_client = MagicMock()
        epic = self._make_epic(created="2023-03-01T00:00:00.000Z")
        children = [
            self._make_child("2023-01-01T00:00:00.000Z"),  # prior
            self._make_child("2023-04-01T00:00:00.000Z"),  # relevant
            self._make_child("2023-05-01T00:00:00.000Z"),  # relevant
        ]
        mock_client.search_issues.return_value = children

        result = get_epic_development_data(mock_client, epic)
        assert result["relevant_ticket_count"] == 2
        assert result["number_of_prior_tickets"] == 1

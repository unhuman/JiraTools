import os
import sys
import pytest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from libraries.jiraQueryTools import (
    build_assignee_or_team_query,
    build_epic_query,
    build_subtask_query,
    build_open_epics_query,
    build_points_estimate_query,
    build_remaining_estimate_query,
    search_issues
)


class TestBuildAssigneeOrTeamQuery:
    def test_assignee_query(self):
        conditions = ['"Story Points" > 0']
        result = build_assignee_or_team_query("assignee", "john.doe", conditions)
        assert 'Assignee = "john.doe"' in result
        assert '"Story Points" > 0' in result

    def test_team_query(self):
        conditions = ['"Story Points" > 0']
        result = build_assignee_or_team_query("team", "My Team", conditions)
        assert '"Sprint Team" = "My Team"' in result
        assert '"Story Points" > 0' in result

    def test_case_insensitive_type(self):
        conditions = []
        result = build_assignee_or_team_query("ASSIGNEE", "user", conditions)
        assert 'Assignee = "user"' in result

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Invalid query type"):
            build_assignee_or_team_query("invalid", "name", [])


class TestBuildEpicQuery:
    def test_epic_query(self):
        result = build_epic_query("PROJ-123")
        assert result == '"Epic Link"=PROJ-123'

    def test_epic_query_with_different_key(self):
        result = build_epic_query("OTHER-456")
        assert result == '"Epic Link"=OTHER-456'


class TestBuildSubtaskQuery:
    def test_subtask_query(self):
        result = build_subtask_query("john.doe", "2024-01-01", "2024-12-31")
        assert "issuetype in subTaskIssueTypes()" in result
        assert "assignee = 'john.doe'" in result
        assert "updated >= '2024-01-01'" in result
        assert "updated <= '2024-12-31'" in result

    def test_different_dates(self):
        result = build_subtask_query("user", "2025-01-15", "2025-02-15")
        assert "updated >= '2025-01-15'" in result
        assert "updated <= '2025-02-15'" in result


class TestBuildOpenEpicsQuery:
    def test_basic_open_epics(self):
        result = build_open_epics_query()
        assert 'issueType = Epic' in result
        assert 'statusCategory != "Done"' in result
        assert 'ORDER BY created ASC' in result

    def test_with_project_key(self):
        result = build_open_epics_query(project_key="PROJ")
        assert 'project = "PROJ"' in result

    def test_with_sprint_team(self):
        result = build_open_epics_query(sprint_team="My Team")
        assert '"Sprint Team" = "My Team"' in result

    def test_with_both_filters(self):
        result = build_open_epics_query(project_key="PROJ", sprint_team="My Team")
        assert 'project = "PROJ"' in result
        assert '"Sprint Team" = "My Team"' in result


class TestBuildPointsEstimateQuery:
    def test_assignee_query(self):
        result = build_points_estimate_query("assignee", "john.doe")
        assert 'Assignee = "john.doe"' in result
        assert '"Story Points" > 0' in result
        assert 'originalEstimate is EMPTY' in result

    def test_team_query(self):
        result = build_points_estimate_query("team", "My Team")
        assert '"Sprint Team" = "My Team"' in result

    def test_exclude_done_false(self):
        result = build_points_estimate_query("assignee", "user", exclude_done=False)
        assert "status NOT IN" not in result


class TestBuildRemainingEstimateQuery:
    def test_assignee_query(self):
        result = build_remaining_estimate_query("assignee", "john.doe")
        assert 'Assignee = "john.doe"' in result
        assert '"Story Points" > 0' in result
        assert 'originalEstimate > 0' in result
        assert 'remainingEstimate = 0' in result

    def test_team_query(self):
        result = build_remaining_estimate_query("team", "My Team")
        assert '"Sprint Team" = "My Team"' in result


class TestSearchIssues:
    @patch('libraries.jiraQueryTools.jira.JIRA')
    def test_search_issues_success(self, mock_jira_class):
        mock_client = Mock()
        mock_issue1 = Mock()
        mock_issue1.key = "PROJ-1"
        mock_issue2 = Mock()
        mock_issue2.key = "PROJ-2"
        mock_client.search_issues.return_value = [mock_issue1, mock_issue2]

        result = search_issues(mock_client, '"Story Points" > 0')
        assert len(result) == 2
        assert result[0].key == "PROJ-1"

    @patch('libraries.jiraQueryTools.jira.JIRA')
    def test_search_issues_error(self, mock_jira_class):
        mock_client = Mock()
        mock_client.search_issues.side_effect = Exception("Connection error")

        result = search_issues(mock_client, 'invalid query')
        assert result == []

    def test_search_issues_with_fields(self):
        mock_client = Mock()
        mock_client.search_issues.return_value = []

        search_issues(mock_client, 'query', max_results=50, fields="summary,status")
        mock_client.search_issues.assert_called_once_with(
            'query',
            maxResults=50,
            fields="summary,status"
        )

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from libraries.jiraQueryTools import build_points_estimate_query


class TestBuildPointsEstimateQuery:
    def test_assignee_query(self):
        result = build_points_estimate_query("assignee", "john.doe")
        assert 'Assignee = "john.doe"' in result
        assert '"Story Points" > 0' in result
        assert 'originalEstimate is EMPTY' in result
        assert 'ORDER BY key ASC' in result

    def test_team_query(self):
        result = build_points_estimate_query("team", "My Team")
        assert '"Sprint Team" = "My Team"' in result
        assert '"Story Points" > 0' in result

    def test_case_insensitive_type(self):
        result = build_points_estimate_query("ASSIGNEE", "user")
        assert 'Assignee = "user"' in result

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Invalid query type"):
            build_points_estimate_query("invalid", "name")

    def test_done_statuses_excluded(self):
        result = build_points_estimate_query("assignee", "user")
        assert "status NOT IN" in result

    def test_excludes_subtask_types(self):
        result = build_points_estimate_query("assignee", "user")
        assert "issuetype not in subTaskIssueTypes()" in result or "subTaskIssueTypes()" in result

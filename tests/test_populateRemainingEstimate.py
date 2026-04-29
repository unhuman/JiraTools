import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from libraries.jiraQueryTools import build_remaining_estimate_query


class TestBuildRemainingEstimateQuery:
    def test_assignee_query(self):
        result = build_remaining_estimate_query("assignee", "john.doe")
        assert 'Assignee = "john.doe"' in result
        assert '"Story Points" > 0' in result
        assert 'originalEstimate > 0' in result
        assert 'remainingEstimate = 0' in result
        assert 'ORDER BY key ASC' in result

    def test_team_query(self):
        result = build_remaining_estimate_query("team", "My Team")
        assert '"Sprint Team" = "My Team"' in result

    def test_not_done_statuses(self):
        result = build_remaining_estimate_query("assignee", "user")
        assert "status NOT IN" in result

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Invalid query type"):
            build_remaining_estimate_query("invalid", "name")

    def test_case_insensitive_type(self):
        result = build_remaining_estimate_query("TEAM", "Alpha")
        assert '"Sprint Team" = "Alpha"' in result

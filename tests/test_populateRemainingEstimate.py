import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from populateRemainingEstimate import build_jql_query


class TestBuildJqlQuery:
    def test_assignee_query(self):
        result = build_jql_query("assignee", "john.doe")
        assert 'Assignee = "john.doe"' in result
        assert '"Story Points" > 0' in result
        assert 'originalEstimate > 0' in result
        assert 'remainingEstimate = 0' in result
        assert 'ORDER BY key ASC' in result

    def test_team_query(self):
        result = build_jql_query("team", "My Team")
        assert '"Sprint Team" = "My Team"' in result

    def test_not_done_statuses(self):
        result = build_jql_query("assignee", "user")
        assert "status NOT IN" in result

    def test_invalid_type_raises(self):
        with pytest.raises(ValueError, match="Invalid query type"):
            build_jql_query("invalid", "name")

    def test_case_insensitive_type(self):
        result = build_jql_query("TEAM", "Alpha")
        assert '"Sprint Team" = "Alpha"' in result

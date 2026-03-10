import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from libraries.jiraTicketTools import (
    ASSIGNEE_FIELD, EPIC_LINK_FIELD, EPIC_LINK_TYPE,
    validate_required_fields, add_standard_field,
    prepare_issue_dict, assign_ticket, link_to_epic,
)


class TestValidateRequiredFields:
    def test_all_present(self):
        assert validate_required_fields("PROJ", "Task", "Fix bug") == []

    def test_missing_project(self):
        errors = validate_required_fields("", "Task", "Fix bug")
        assert len(errors) == 1
        assert "project" in errors[0].lower()

    def test_missing_issue_type(self):
        errors = validate_required_fields("PROJ", "", "Fix bug")
        assert len(errors) == 1
        assert "issue type" in errors[0].lower()

    def test_missing_summary(self):
        errors = validate_required_fields("PROJ", "Task", "")
        assert len(errors) == 1
        assert "summary" in errors[0].lower()

    def test_all_missing(self):
        errors = validate_required_fields("", "", "")
        assert len(errors) == 3

    def test_none_values(self):
        errors = validate_required_fields(None, None, None)
        assert len(errors) == 3


class TestAddStandardField:
    def test_name_format_single(self):
        d = {}
        add_standard_field(d, "priority", "High", "name")
        assert d["priority"] == {"name": "High"}

    def test_name_format_list(self):
        d = {}
        add_standard_field(d, "fixVersions", ["1.0", "2.0"], "name")
        assert d["fixVersions"] == [{"name": "1.0"}, {"name": "2.0"}]

    def test_component_wrapped_in_list(self):
        d = {}
        add_standard_field(d, "component", "Backend", "name")
        assert d["components"] == [{"name": "Backend"}]

    def test_no_format(self):
        d = {}
        add_standard_field(d, "duedate", "2025-01-01", None)
        assert d["duedate"] == "2025-01-01"


class TestPrepareIssueDict:
    def test_basic_issue(self):
        issue_dict, epic_value = prepare_issue_dict("PROJ", "Task", "Fix bug", "desc", {})
        assert issue_dict["project"] == {"key": "PROJ"}
        assert issue_dict["summary"] == "Fix bug"
        assert issue_dict["description"] == "desc"
        assert issue_dict["issuetype"] == {"name": "Task"}
        assert epic_value is None

    def test_with_epic_link(self):
        fields = {EPIC_LINK_FIELD: "EPIC-123"}
        issue_dict, epic_value = prepare_issue_dict("PROJ", "Task", "Fix", "d", fields)
        assert epic_value == "EPIC-123"

    def test_fields_not_mutated(self):
        fields = {"labels": ["urgent"]}
        prepare_issue_dict("PROJ", "Task", "Fix", "d", fields)
        assert fields == {"labels": ["urgent"]}


class TestAssignTicket:
    def test_successful_assignment(self):
        client = MagicMock()
        result = assign_ticket(client, "PROJ-1", "john")
        client.assign_issue.assert_called_once_with("PROJ-1", "john")
        assert result is True

    def test_empty_assignee_skipped(self):
        client = MagicMock()
        result = assign_ticket(client, "PROJ-1", "")
        client.assign_issue.assert_not_called()
        assert result is False

    def test_nan_assignee_skipped(self):
        client = MagicMock()
        result = assign_ticket(client, "PROJ-1", "nan")
        client.assign_issue.assert_not_called()
        assert result is False

    def test_exception_returns_false(self):
        client = MagicMock()
        client.assign_issue.side_effect = Exception("user not found")
        result = assign_ticket(client, "PROJ-1", "john")
        assert result is False


class TestLinkToEpic:
    def test_empty_epic_returns_false(self):
        client = MagicMock()
        assert link_to_epic(client, "PROJ-1", "") is False

    def test_nan_epic_returns_false(self):
        client = MagicMock()
        assert link_to_epic(client, "PROJ-1", "nan") is False

    def test_method1_success(self):
        client = MagicMock()
        result = link_to_epic(client, "PROJ-1", "EPIC-1")
        client.update_issue_field.assert_called_once()
        assert result is True

    def test_fallback_to_method2(self):
        client = MagicMock()
        client.update_issue_field.side_effect = Exception("nope")
        result = link_to_epic(client, "PROJ-1", "EPIC-1")
        client.create_issue_link.assert_called_once_with(EPIC_LINK_TYPE, "EPIC-1", "PROJ-1")
        assert result is True

    def test_fallback_to_relates(self):
        client = MagicMock()
        client.update_issue_field.side_effect = Exception("nope")
        client.create_issue_link.side_effect = [Exception("nope"), None]
        result = link_to_epic(client, "PROJ-1", "EPIC-1")
        assert result is True

    def test_all_methods_fail(self):
        client = MagicMock()
        client.update_issue_field.side_effect = Exception("nope")
        client.create_issue_link.side_effect = Exception("nope")
        result = link_to_epic(client, "PROJ-1", "EPIC-1")
        assert result is False

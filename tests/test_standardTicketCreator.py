import os
import sys
import json
import pytest
from unittest.mock import patch, MagicMock
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from standardTicketCreator import (
    TicketInfo, SimulatedTicketCounter,
    get_categories_from_config, map_check_to_category,
    convert_check_id_to_readable_name, extract_level_from_check_id,
    parse_check_details, detect_category_from_name, extract_levels_from_name,
    parse_annotation_health_value, parse_scorecard_levels,
    validate_required_fields, prepare_issue_dict,
    add_standard_field, format_summary, add_team_fields,
    extract_field_prefix, sort_fields_numerically, collect_field_values,
    is_field_empty, has_category_selections,
    analyze_category_compliance, analyze_ownership_compliance,
    analyze_quality_compliance, analyze_security_compliance,
    analyze_reliability_compliance, analyze_generic_compliance,
    create_level_opportunities
)


class TestTicketInfo:
    def test_str_returns_ticket_id(self):
        ti = TicketInfo("PROJ-123", "Fix the bug")
        assert str(ti) == "PROJ-123"

    def test_attributes(self):
        ti = TicketInfo("PROJ-456", "Add feature")
        assert ti.ticket_id == "PROJ-456"
        assert ti.summary == "Add feature"


class TestSimulatedTicketCounter:
    def test_sequential_ids(self):
        counter = SimulatedTicketCounter()
        assert counter.get_next_ticket_id("PROJ") == "simulated-PROJ-1"
        assert counter.get_next_ticket_id("PROJ") == "simulated-PROJ-2"

    def test_separate_projects(self):
        counter = SimulatedTicketCounter()
        assert counter.get_next_ticket_id("A") == "simulated-A-1"
        assert counter.get_next_ticket_id("B") == "simulated-B-1"
        assert counter.get_next_ticket_id("A") == "simulated-A-2"


class TestGetCategoriesFromConfig:
    def test_categories_from_config(self):
        config = {"Categories": "Ownership, Quality, Security"}
        result = get_categories_from_config(config)
        assert result == ["Ownership", "Quality", "Security"]

    def test_default_categories(self):
        result = get_categories_from_config({})
        assert result == ["Ownership", "Quality", "Security", "Reliability"]

    def test_lowercase_key(self):
        config = {"categories": "Alpha, Beta"}
        result = get_categories_from_config(config)
        assert result == ["Alpha", "Beta"]

    def test_empty_string(self):
        config = {"Categories": ""}
        result = get_categories_from_config(config)
        assert result == ["Ownership", "Quality", "Security", "Reliability"]


class TestMapCheckToCategory:
    def test_ownership_rollup(self):
        assert map_check_to_category("ownershipCheck.rollups") == "Ownership"

    def test_quality_sonar(self):
        assert map_check_to_category("sonarCoverageCheckComponent50.rollups") == "Quality"

    def test_security_check(self):
        assert map_check_to_category("securityVulnCheck.rollups") == "Security"

    def test_reliability_deployment(self):
        assert map_check_to_category("deploymentDriftCheck.rollups") == "Reliability"

    def test_non_rollup_skipped(self):
        assert map_check_to_category("ownershipCheck") is None

    def test_non_rollup_allowed(self):
        assert map_check_to_category("ownershipCheck", allow_non_rollups=True) == "Ownership"

    def test_quality_specific_patterns(self):
        assert map_check_to_category("zerosev1sev2prodbugs.rollups") == "Quality"

    def test_security_specific_patterns(self):
        assert map_check_to_category("challengeTimeLessThanDoublePlus.rollups") == "Security"

    def test_unknown_returns_none(self):
        assert map_check_to_category("randomUnknown.rollups") is None


class TestConvertCheckIdToReadableName:
    def test_known_pattern(self):
        assert convert_check_id_to_readable_name("sonarCoverageCheckComponent30.rollups") == "SonarQube Code Coverage (30%)"

    def test_rollup_suffix_removed(self):
        name = convert_check_id_to_readable_name("datadogIntegrationCheck.rollups")
        assert name == "Datadog Integration"

    def test_camel_case_conversion(self):
        name = convert_check_id_to_readable_name("unknownCustomCheck")
        assert " " in name  # Should have spaces from camelCase splitting

    def test_known_deployment_check(self):
        assert convert_check_id_to_readable_name("deploymentDriftCheck") == "Deployment Drift Check"


class TestExtractLevelFromCheckId:
    def test_coverage_30(self):
        result = extract_level_from_check_id("sonar_coverage_check_component_30.rollups")
        assert result["level"] == "L1"
        assert result["threshold"] == "30%"

    def test_coverage_70(self):
        result = extract_level_from_check_id("sonar_coverage_check_component_70.rollups")
        assert result["level"] == "L3"

    def test_no_coverage_known_pattern(self):
        result = extract_level_from_check_id("deploymentDriftCheck.rollups")
        assert result is not None
        assert result["level"] == "L1"

    def test_no_match(self):
        result = extract_level_from_check_id("randomThing")
        assert result is None


class TestParseCheckDetails:
    def test_valid_details(self):
        details = {
            "notes": {
                "data": json.dumps({
                    "value": {"count": 5, "total": 10, "percentage": 50.0},
                    "target": {"lower": 80}
                })
            }
        }
        result = parse_check_details(details)
        assert result["current_count"] == 5
        assert result["total_count"] == 10
        assert result["percentage"] == 50.0

    def test_empty_details(self):
        result = parse_check_details({})
        assert result["current_count"] == 0
        assert result["total_count"] == 0

    def test_malformed_json(self):
        details = {"notes": {"data": "not json"}}
        result = parse_check_details(details)
        assert result["current_count"] == 0


class TestDetectCategoryFromName:
    def test_ownership(self):
        assert detect_category_from_name("ownership check") == "Ownership"

    def test_quality(self):
        assert detect_category_from_name("code coverage test") == "Quality"

    def test_security(self):
        assert detect_category_from_name("vulnerability scan") == "Security"

    def test_reliability(self):
        assert detect_category_from_name("uptime monitor") == "Reliability"

    def test_unknown(self):
        assert detect_category_from_name("random thing") is None


class TestExtractLevelsFromName:
    def test_single_level(self):
        result = extract_levels_from_name("L1 check")
        assert "L1" in result

    def test_multiple_levels(self):
        result = extract_levels_from_name("L1 and L2 compliance")
        assert "L1" in result
        assert "L2" in result

    def test_default_l1(self):
        result = extract_levels_from_name("no level info")
        assert "L1" in result


class TestParseAnnotationHealthValue:
    def test_json_dict(self):
        result = parse_annotation_health_value("key", '{"L1": true, "L2": false}')
        assert "L1" in result
        assert "L2" not in result

    def test_simple_true(self):
        result = parse_annotation_health_value("L1 check", "true")
        assert "L1" in result

    def test_false_value(self):
        result = parse_annotation_health_value("check", "false")
        assert len(result) == 0


class TestParseScorecardLevels:
    def test_json_dict(self):
        result = parse_scorecard_levels('{"L1": "X", "L2": "X"}')
        assert "L1" in result
        assert "L2" in result

    def test_simple_true(self):
        result = parse_scorecard_levels("true")
        assert "L1" in result

    def test_false(self):
        result = parse_scorecard_levels("false")
        assert len(result) == 0

    def test_dict_input(self):
        result = parse_scorecard_levels({"L1": "X", "L2": None})
        assert "L1" in result
        assert "L2" not in result


class TestValidateRequiredFields:
    def test_all_valid(self):
        assert validate_required_fields("PROJ", "Story", "Do something") == []

    def test_missing_project(self):
        errors = validate_required_fields("", "Story", "Do something")
        assert len(errors) == 1
        assert "project key" in errors[0]

    def test_missing_all(self):
        errors = validate_required_fields("", "", "")
        assert len(errors) == 3


class TestPrepareIssueDict:
    def test_basic_issue(self):
        issue_dict, epic_value = prepare_issue_dict(
            "PROJ", "Story", "Test summary", "Test description", {}
        )
        assert issue_dict["project"] == {"key": "PROJ"}
        assert issue_dict["summary"] == "Test summary"
        assert issue_dict["issuetype"] == {"name": "Story"}

    def test_with_epic_link(self):
        fields = {"Epic Link": "PROJ-100"}
        issue_dict, epic_value = prepare_issue_dict(
            "PROJ", "Story", "Summary", "Desc", fields
        )
        assert epic_value == "PROJ-100"


class TestAddStandardField:
    def test_name_format_single(self):
        issue_dict = {}
        add_standard_field(issue_dict, "priority", "High", "name")
        assert issue_dict["priority"] == {"name": "High"}

    def test_name_format_list(self):
        issue_dict = {}
        add_standard_field(issue_dict, "fixVersions", ["1.0", "2.0"], "name")
        assert issue_dict["fixVersions"] == [{"name": "1.0"}, {"name": "2.0"}]

    def test_component_wrapped_in_list(self):
        issue_dict = {}
        add_standard_field(issue_dict, "component", "my-component", "name")
        assert issue_dict["components"] == [{"name": "my-component"}]

    def test_no_format(self):
        issue_dict = {}
        add_standard_field(issue_dict, "labels", ["bug", "fix"], None)
        assert issue_dict["labels"] == ["bug", "fix"]


class TestFormatSummary:
    def test_with_sheet_name(self):
        result = format_summary("{sheet_name}: Fix issue", "Quality")
        assert "Quality" in result

    def test_no_placeholder(self):
        result = format_summary("Plain summary", "Quality")
        assert result == "Plain summary Scorecards Improvement: Quality"


class TestAddTeamFields:
    def test_adds_new_fields(self):
        additional_fields = {}
        team_info = {"Sprint": "Sprint 1", "Component": "web"}
        result = add_team_fields(additional_fields, team_info)
        assert result["Sprint"] == "Sprint 1"

    def test_does_not_override(self):
        additional_fields = {"Sprint": "Sprint 2"}
        team_info = {"Sprint": "Sprint 1"}
        result = add_team_fields(additional_fields, team_info)
        assert result["Sprint"] == "Sprint 2"


class TestExtractFieldPrefix:
    def test_valid_prefix(self):
        assert extract_field_prefix("L1") == "L"

    def test_multi_char_prefix(self):
        assert extract_field_prefix("Level2") == "Level"

    def test_no_number(self):
        assert extract_field_prefix("Summary") is None

    def test_no_prefix(self):
        assert extract_field_prefix("123") is None


class TestSortFieldsNumerically:
    def test_sorts_correctly(self):
        result = sort_fields_numerically(["L3", "L1", "L2"])
        assert result == ["L1", "L2", "L3"]

    def test_double_digit(self):
        result = sort_fields_numerically(["L10", "L2", "L1"])
        assert result == ["L1", "L2", "L10"]


class TestCollectFieldValues:
    def test_collects_valid_fields(self):
        fields = {"L1": "X", "L2": "Y", "L3": None}
        result = collect_field_values(fields, ["L1", "L2", "L3"])
        assert len(result) == 2
        assert "L1: X" in result

    def test_skips_nan(self):
        fields = {"L1": "nan", "L2": "real"}
        result = collect_field_values(fields, ["L1", "L2"])
        assert len(result) == 1


class TestIsFieldEmpty:
    def test_none_is_empty(self):
        assert is_field_empty(None) is True

    def test_empty_string(self):
        assert is_field_empty("") is True
        assert is_field_empty("   ") is True

    def test_nan_string(self):
        assert is_field_empty("nan") is True
        assert is_field_empty("None") is True
        assert is_field_empty("null") is True

    def test_nan_float(self):
        assert is_field_empty(float('nan')) is True

    def test_valid_value(self):
        assert is_field_empty("X") is False
        assert is_field_empty(42) is False


class TestHasCategorySelections:
    def test_has_selections(self):
        fields = {"Summary": "test", "L1": "X", "L2": ""}
        assert has_category_selections(fields) is True

    def test_no_selections(self):
        fields = {"Summary": "test", "L1": None, "L2": "nan"}
        assert has_category_selections(fields) is False

    def test_no_category_fields(self):
        fields = {"Summary": "test", "Description": "desc"}
        assert has_category_selections(fields) is False


class TestAnalyzeCategoryCompliance:
    def test_dispatches_ownership(self):
        checks = [{"checkId": "test.rollups", "state": "passed", "track_name": "Ownership"}]
        result = analyze_category_compliance(checks, "Ownership")
        assert result["category"] == "Ownership"

    def test_dispatches_quality(self):
        checks = []
        result = analyze_category_compliance(checks, "Quality")
        assert result["category"] == "Quality"

    def test_dispatches_security(self):
        checks = []
        result = analyze_category_compliance(checks, "Security")
        assert result["category"] == "Security"

    def test_dispatches_reliability(self):
        checks = []
        result = analyze_category_compliance(checks, "Reliability")
        assert result["category"] == "Reliability"

    def test_dispatches_unknown(self):
        checks = [{"checkId": "test", "state": "passed"}]
        result = analyze_category_compliance(checks, "Unknown")
        assert result is None  # No failing checks


class TestAnalyzeOwnershipCompliance:
    def test_all_passing(self):
        checks = [
            {"checkId": "ownerCheck.rollups", "state": "passed", "track_name": "Ownership"}
        ]
        result = analyze_ownership_compliance(checks)
        assert result["current_level"] == "L3"
        assert result["improvement_needed"] is False

    def test_failing_check(self):
        checks = [
            {"checkId": "ownerCheck.rollups", "state": "failed", "track_name": "Ownership",
             "backstage_level": "Level 1", "details": {}}
        ]
        result = analyze_ownership_compliance(checks)
        assert result["improvement_needed"] is True
        assert len(result["improvement_details"]) == 1

    def test_no_ownership_checks(self):
        checks = [
            {"checkId": "qualityCheck.rollups", "state": "passed", "track_name": "Quality"}
        ]
        result = analyze_ownership_compliance(checks)
        assert result["current_level"] == "L1"


class TestAnalyzeGenericCompliance:
    def test_failing_checks(self):
        checks = [
            {"checkId": "customCheck", "state": "failed", "details": {}}
        ]
        result = analyze_generic_compliance(checks, "Custom")
        assert result["improvement_needed"] is True

    def test_all_passing(self):
        checks = [
            {"checkId": "customCheck", "state": "passed"}
        ]
        result = analyze_generic_compliance(checks, "Custom")
        assert result is None


class TestCreateLevelOpportunities:
    def test_basic(self):
        result = create_level_opportunities("Quality", "L1", ["L2", "L3"])
        assert result["current_level"] == "L1"
        assert result["improvement_needed"] is True
        assert len(result["improvement_details"]) == 2

    def test_nl_current(self):
        result = create_level_opportunities("Security", "NL", ["L1"])
        assert result["current_level"] == "NL"
        assert result["improvement_details"][0]["current_count"] == 0

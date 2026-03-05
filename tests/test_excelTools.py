import os
import pytest
import pandas as pd

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from libraries.excelTools import (
    validate_file, get_excel_sheets, read_config_sheet,
    get_backstage_url_from_config, filter_excel_columns,
    process_key_rows, transform_to_key_value_format,
    read_excel_file, validate_data, format_sprint_value,
    add_to_team_field, create_team_mapping, filter_team_mapping,
    SPRINT_FIELD
)


class TestValidateFile:
    def test_valid_xlsx(self, tmp_path):
        f = tmp_path / "test.xlsx"
        pd.DataFrame({"A": [1]}).to_excel(f, index=False)
        assert validate_file(str(f)) is True

    def test_valid_xls_extension(self, tmp_path):
        # Just check extension validation (file may not be real xls format)
        f = tmp_path / "test.xls"
        f.write_text("fake")
        assert validate_file(str(f)) is True

    def test_invalid_extension(self, tmp_path):
        f = tmp_path / "test.csv"
        f.write_text("a,b,c")
        assert validate_file(str(f)) is False

    def test_nonexistent_file(self):
        assert validate_file("/nonexistent/path/file.xlsx") is False


class TestGetExcelSheets:
    def test_returns_sheet_names(self, tmp_path):
        f = tmp_path / "test.xlsx"
        with pd.ExcelWriter(str(f)) as writer:
            pd.DataFrame({"A": [1]}).to_excel(writer, sheet_name="Sheet1", index=False)
            pd.DataFrame({"B": [2]}).to_excel(writer, sheet_name="Config", index=False)
        result = get_excel_sheets(str(f))
        assert "Sheet1" in result
        assert "Config" in result

    def test_invalid_file_returns_none(self):
        result = get_excel_sheets("/nonexistent/file.xlsx")
        assert result is None


class TestReadConfigSheet:
    def test_reads_key_value_pairs(self, tmp_path):
        f = tmp_path / "test.xlsx"
        df = pd.DataFrame({"Key": ["Backstage", "Option"], "Value": ["https://backstage.example.com", "enabled"]})
        df.to_excel(str(f), sheet_name="Config", index=False)
        result = read_config_sheet(str(f))
        assert result["Backstage"] == "https://backstage.example.com"
        assert result["Option"] == "enabled"

    def test_custom_sheet_name(self, tmp_path):
        f = tmp_path / "test.xlsx"
        df = pd.DataFrame({"Key": ["Foo"], "Value": ["Bar"]})
        df.to_excel(str(f), sheet_name="MyConfig", index=False)
        result = read_config_sheet(str(f), config_sheet_name="MyConfig")
        assert result["Foo"] == "Bar"

    def test_missing_sheet_returns_empty(self, tmp_path):
        f = tmp_path / "test.xlsx"
        pd.DataFrame({"A": [1]}).to_excel(str(f), sheet_name="Other", index=False)
        result = read_config_sheet(str(f))
        assert result == {}


class TestGetBackstageUrlFromConfig:
    def test_returns_url(self):
        assert get_backstage_url_from_config({"Backstage": "https://backstage.example.com"}) == "https://backstage.example.com"

    def test_strips_trailing_slash(self):
        assert get_backstage_url_from_config({"Backstage": "https://backstage.example.com/"}) == "https://backstage.example.com"

    def test_returns_none_when_missing(self):
        assert get_backstage_url_from_config({}) is None
        assert get_backstage_url_from_config({"Other": "value"}) is None


class TestFilterExcelColumns:
    def test_removes_column_prefixed(self):
        df = pd.DataFrame({"Name": [1], "Column1": [2], "Value": [3], "Column2": [4]})
        result = filter_excel_columns(df)
        assert list(result.columns) == ["Name", "Value"]

    def test_keeps_all_when_no_column_prefix(self):
        df = pd.DataFrame({"Name": [1], "Value": [2]})
        result = filter_excel_columns(df)
        assert list(result.columns) == ["Name", "Value"]


class TestFormatSprintValue:
    def test_float_to_int(self):
        assert format_sprint_value(42.0) == 42

    def test_int_unchanged(self):
        assert format_sprint_value(42) == 42

    def test_nan_returns_none(self):
        assert format_sprint_value(float('nan')) is None

    def test_string_float_to_int(self):
        assert format_sprint_value("42.0") == 42

    def test_non_numeric_string_unchanged(self):
        assert format_sprint_value("Sprint 1") == "Sprint 1"


class TestAddToTeamField:
    def test_add_new_field(self):
        result = add_to_team_field({}, "Project", "PROJ")
        assert result == {"Project": "PROJ"}

    def test_add_duplicate_creates_list(self):
        data = {"Project": "PROJ1"}
        result = add_to_team_field(data, "Project", "PROJ2")
        assert result == {"Project": ["PROJ1", "PROJ2"]}

    def test_add_to_existing_list(self):
        data = {"Project": ["PROJ1", "PROJ2"]}
        result = add_to_team_field(data, "Project", "PROJ3")
        assert result == {"Project": ["PROJ1", "PROJ2", "PROJ3"]}

    def test_sprint_field_formats_value(self):
        result = add_to_team_field({}, SPRINT_FIELD, 42.0)
        assert result == {SPRINT_FIELD: 42}


class TestValidateData:
    def test_valid_dataframe(self):
        df = pd.DataFrame({"Key": ["a"], "Field": ["b"], "Value": ["c"]})
        assert validate_data(df) is True

    def test_missing_columns(self):
        df = pd.DataFrame({"Key": ["a"], "Other": ["b"]})
        assert validate_data(df) is False


class TestCreateTeamMapping:
    def test_creates_mapping(self):
        df = pd.DataFrame({
            "Key": ["TeamA", "TeamA", "TeamB"],
            "Field": ["Project", "Assignee", "Project"],
            "Value": ["PROJ1", "user1", "PROJ2"]
        })
        result = create_team_mapping(df)
        assert result["TeamA"]["Project"] == "PROJ1"
        assert result["TeamA"]["Assignee"] == "user1"
        assert result["TeamB"]["Project"] == "PROJ2"

    def test_missing_columns_returns_empty(self):
        df = pd.DataFrame({"X": [1], "Y": [2]})
        result = create_team_mapping(df)
        assert result == {}


class TestFilterTeamMapping:
    def setup_method(self):
        self.mapping = {
            "TeamA": {"Project": "PROJ1"},
            "TeamB": {"Project": "PROJ2"},
            "TeamC": {"Project": "PROJ3"},
        }

    def test_no_filter(self):
        result = filter_team_mapping(self.mapping)
        assert result == self.mapping

    def test_process_teams(self):
        result = filter_team_mapping(self.mapping, process_teams="TeamA,TeamC")
        assert "TeamA" in result
        assert "TeamC" in result
        assert "TeamB" not in result

    def test_process_teams_case_insensitive(self):
        result = filter_team_mapping(self.mapping, process_teams="teama")
        assert "TeamA" in result

    def test_exclude_teams(self):
        result = filter_team_mapping(self.mapping, exclude_teams="TeamB")
        assert "TeamA" in result
        assert "TeamC" in result
        assert "TeamB" not in result

    def test_exclude_teams_case_insensitive(self):
        result = filter_team_mapping(self.mapping, exclude_teams="teamb")
        assert "TeamB" not in result

    def test_no_match_returns_empty(self):
        result = filter_team_mapping(self.mapping, process_teams="NonExistent")
        assert result == {}


class TestTransformToKeyValueFormat:
    def test_transforms_correctly(self):
        df = pd.DataFrame({
            "Team": ["TeamA", "TeamA"],
            "Project": ["PROJ1", "PROJ2"],
            "Sprint": [10, 11]
        })
        result = transform_to_key_value_format(df)
        assert result is not None
        assert "Key" in result.columns
        assert "Field" in result.columns
        assert "Value" in result.columns

    def test_single_column_returns_none(self):
        df = pd.DataFrame({"Team": ["TeamA"]})
        result = transform_to_key_value_format(df)
        assert result is None

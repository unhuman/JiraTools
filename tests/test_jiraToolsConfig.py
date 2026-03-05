import json
import os
import pytest
from unittest.mock import patch, mock_open, MagicMock

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from libraries.jiraToolsConfig import (
    load_config, save_config, statusIsDone, get_backstage_url,
    safe_jira_update, convert_story_points_to_estimate, MINUTES_PER_POINT
)


class TestStatusIsDone:
    def test_done_statuses(self):
        for status in ["closed", "deployed", "done", "released", "resolved"]:
            assert statusIsDone(status) is True

    def test_case_insensitive(self):
        assert statusIsDone("Done") is True
        assert statusIsDone("CLOSED") is True
        assert statusIsDone("Resolved") is True

    def test_not_done_statuses(self):
        for status in ["open", "in progress", "to do", "blocked", "review"]:
            assert statusIsDone(status) is False

    def test_empty_string(self):
        assert statusIsDone("") is False


class TestConvertStoryPointsToEstimate:
    def test_one_point(self):
        assert convert_story_points_to_estimate(1) == f"{MINUTES_PER_POINT}m"

    def test_two_points(self):
        assert convert_story_points_to_estimate(2) == f"{MINUTES_PER_POINT * 2}m"

    def test_half_point(self):
        expected = f"{int(MINUTES_PER_POINT * 0.5)}m"
        assert convert_story_points_to_estimate(0.5) == expected

    def test_zero_points(self):
        assert convert_story_points_to_estimate(0) == "0m"


class TestMinutesPerPoint:
    def test_value(self):
        assert MINUTES_PER_POINT == 360


class TestLoadConfig:
    def test_loads_valid_config(self, tmp_path):
        config_data = {"jira_server": "https://jira.example.com", "personal_access_token": "abc123"}
        config_file = tmp_path / ".jiraTools"
        config_file.write_text(json.dumps(config_data))

        with patch('libraries.jiraToolsConfig.config_file', str(config_file)):
            result = load_config()
            assert result["jira_server"] == "https://jira.example.com"
            assert result["personal_access_token"] == "abc123"

    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        with patch('libraries.jiraToolsConfig.config_file', str(tmp_path / "nonexistent")):
            result = load_config()
            assert result == {}


class TestSaveConfig:
    def test_saves_config(self, tmp_path):
        config_file = tmp_path / ".jiraTools"
        config_data = {"jira_server": "https://jira.example.com"}

        with patch('libraries.jiraToolsConfig.config_file', str(config_file)):
            save_config(config_data)
            saved = json.loads(config_file.read_text())
            assert saved == config_data


class TestGetBackstageUrl:
    def test_cli_override_takes_precedence(self):
        config = {"backstageUrl": "https://config.example.com"}
        result = get_backstage_url(config, cli_override="https://cli.example.com")
        assert result == "https://cli.example.com"

    def test_falls_back_to_config(self):
        config = {"backstageUrl": "https://config.example.com"}
        result = get_backstage_url(config)
        assert result == "https://config.example.com"

    def test_returns_none_when_missing(self):
        assert get_backstage_url({}) is None
        assert get_backstage_url({"jira_server": "https://jira.example.com"}) is None

    def test_strips_trailing_slash(self):
        assert get_backstage_url({"backstageUrl": "https://backstage.example.com/"}) == "https://backstage.example.com"
        assert get_backstage_url({}, cli_override="https://backstage.example.com/") == "https://backstage.example.com"

    def test_cli_override_none_falls_back(self):
        config = {"backstageUrl": "https://config.example.com"}
        result = get_backstage_url(config, cli_override=None)
        assert result == "https://config.example.com"


class TestSafeJiraUpdate:
    def test_successful_update(self):
        mock_issue = MagicMock()
        mock_issue.update.return_value = True

        with patch('time.sleep'):
            result = safe_jira_update(mock_issue, {"summary": "test"})

        mock_issue.update.assert_called_once_with(fields={"summary": "test"})
        assert result is True

    def test_rate_limited_retries(self):
        mock_issue = MagicMock()
        jira_error = type('JIRAError', (Exception,), {'status_code': 429})()
        mock_issue.update.side_effect = [jira_error, True]

        # Mock both the jira module import and time.sleep
        mock_jira_module = MagicMock()
        mock_jira_module.exceptions.JIRAError = type(jira_error)

        with patch('time.sleep'), \
             patch.dict('sys.modules', {'jira': mock_jira_module}):
            result = safe_jira_update(mock_issue, {"summary": "test"})

        assert mock_issue.update.call_count == 2

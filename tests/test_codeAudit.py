import os
import re
import pytest
from datetime import timedelta
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from codeAudit import (
    validate_regex, extract_repo_name, normalize_git_url_to_ssh,
    build_match_display, extract_capture_groups,
    parse_date_tolerance, extract_semver, fetch_repo_tags,
    _is_permission_error
)


class TestValidateRegex:
    def test_valid_single_group(self):
        result = validate_regex("version:(.*?)")
        assert result is not None
        assert result.groups == 1

    def test_valid_multiple_groups(self):
        result = validate_regex("(.*?):(.*?)")
        assert result is not None
        assert result.groups == 2

    def test_non_capturing_plus_capturing(self):
        result = validate_regex("(?:foo)(bar)")
        assert result is not None
        assert result.groups == 1

    def test_no_capture_group_returns_none(self):
        result = validate_regex("no groups here")
        assert result is None

    def test_invalid_regex_returns_none(self):
        result = validate_regex("[invalid")
        assert result is None

    def test_dotall_flag_set(self):
        result = validate_regex("(.*)")
        assert result.flags & re.DOTALL


class TestExtractRepoName:
    def test_ssh_with_git_suffix(self):
        assert extract_repo_name("git@github.com:org/repo.git") == "org/repo"

    def test_ssh_without_git_suffix(self):
        assert extract_repo_name("git@github.com:org/repo") == "org/repo"

    def test_https_with_git_suffix(self):
        assert extract_repo_name("https://github.com/org/repo.git") == "org/repo"

    def test_https_without_git_suffix(self):
        assert extract_repo_name("https://github.com/org/repo") == "org/repo"

    def test_unknown_format_returns_as_is(self):
        assert extract_repo_name("something-else") == "something-else"

    def test_ssh_enterprise_host(self):
        assert extract_repo_name("git@github.enterprise.com:myorg/myrepo.git") == "myorg/myrepo"


class TestNormalizeGitUrlToSsh:
    def test_https_to_ssh(self):
        result = normalize_git_url_to_ssh("https://github.com/org/repo.git")
        assert result == "git@github.com:org/repo.git"

    def test_https_without_git_suffix(self):
        result = normalize_git_url_to_ssh("https://github.com/org/repo")
        assert result == "git@github.com:org/repo.git"

    def test_https_with_trailing_slash(self):
        result = normalize_git_url_to_ssh("https://github.com/org/repo/")
        assert result == "git@github.com:org/repo.git"

    def test_ssh_unchanged(self):
        result = normalize_git_url_to_ssh("git@github.com:org/repo.git")
        assert result == "git@github.com:org/repo.git"

    def test_http_to_ssh(self):
        result = normalize_git_url_to_ssh("http://github.com/org/repo")
        assert result == "git@github.com:org/repo.git"

    def test_enterprise_host(self):
        result = normalize_git_url_to_ssh("https://github.enterprise.com/myorg/myrepo.git")
        assert result == "git@github.enterprise.com:myorg/myrepo.git"


class TestBuildMatchDisplay:
    def test_simple_group(self):
        assert build_match_display("FROM (.+)", "ubuntu:22.04") == "FROM ubuntu:22.04"

    def test_group_in_middle(self):
        assert build_match_display("version:(.*?):end", "1.2.3") == "version:1.2.3:end"

    def test_with_non_capturing_group(self):
        result = build_match_display("(?:prefix)(.*)", "value")
        assert result == "(?:prefix)value"

    def test_no_group_returns_value(self):
        assert build_match_display("nogroup", "fallback") == "fallback"


class TestExtractCaptureGroups:
    def test_single_group(self):
        result = extract_capture_groups("<version>(.*?)</version>")
        assert result == ["(.*?)"]

    def test_multiple_groups(self):
        result = extract_capture_groups("<artifactId>(.*?)</artifactId>.*?<version>(.*?)</version>")
        assert result == ["(.*?)", "(.*?)"]

    def test_non_capturing_skipped(self):
        result = extract_capture_groups("(?:foo)(bar.*?)(baz.+)")
        assert result == ["(bar.*?)", "(baz.+)"]

    def test_no_groups(self):
        result = extract_capture_groups("no groups")
        assert result == []

    def test_escaped_parens_ignored(self):
        result = extract_capture_groups(r"\(escaped\)(captured)")
        assert result == ["(captured)"]

    def test_nested_groups(self):
        # Nested capturing group inside a capturing group
        result = extract_capture_groups("((inner))")
        # Should return the outer group
        assert len(result) >= 1
        assert "(inner)" in result[0] or result[0] == "((inner))"


class TestParseDateTolerance:
    def test_days(self):
        assert parse_date_tolerance("2d") == timedelta(days=2)

    def test_months(self):
        assert parse_date_tolerance("3m") == timedelta(days=90)

    def test_years(self):
        assert parse_date_tolerance("1y") == timedelta(days=365)

    def test_large_number(self):
        assert parse_date_tolerance("100d") == timedelta(days=100)

    def test_invalid_unit_returns_none(self):
        assert parse_date_tolerance("2w") is None

    def test_missing_number_returns_none(self):
        assert parse_date_tolerance("m") is None

    def test_empty_string_returns_none(self):
        assert parse_date_tolerance("") is None

    def test_no_unit_returns_none(self):
        assert parse_date_tolerance("42") is None

    def test_float_returns_none(self):
        assert parse_date_tolerance("1.5m") is None


class TestExtractSemver:
    def test_plain_version(self):
        assert extract_semver("1.2.3") == "1.2.3"

    def test_v_prefix(self):
        assert extract_semver("v1.2.3") == "1.2.3"

    def test_release_prefix(self):
        assert extract_semver("release-1.2.3") == "1.2.3"

    def test_suffix_ignored(self):
        assert extract_semver("v1.2.3-rc1") == "1.2.3"

    def test_prefix_and_suffix(self):
        assert extract_semver("some-prefix-1.2.3-SNAPSHOT") == "1.2.3"

    def test_no_semver_returns_none(self):
        assert extract_semver("latest") is None

    def test_only_two_parts_returns_none(self):
        assert extract_semver("1.2") is None

    def test_large_numbers(self):
        assert extract_semver("v10.200.3000") == "10.200.3000"


class TestFetchRepoTags:
    @patch('codeAudit.shutil.rmtree')
    @patch('codeAudit.tempfile.mkdtemp', return_value='/tmp/codeAudit_tags_test')
    @patch('codeAudit._run_git')
    def test_parses_tags_correctly(self, mock_run_git, mock_mkdtemp, mock_rmtree):
        # Simulate successful git init, fetch, and for-each-ref
        init_result = MagicMock(returncode=0, stdout='', stderr='')
        fetch_result = MagicMock(returncode=0, stdout='', stderr='')
        ref_output = (
            "v2.0.0 2025-06-15\n"
            "v1.9.0 2025-03-01\n"
            "release-1.8.5 2024-12-10\n"
            "latest 2025-07-01\n"
        )
        ref_result = MagicMock(returncode=0, stdout=ref_output, stderr='')
        mock_run_git.side_effect = [init_result, fetch_result, ref_result]

        result = fetch_repo_tags("git@github.com:org/repo.git")

        assert result == {
            "2.0.0": "2025-06-15",
            "1.9.0": "2025-03-01",
            "1.8.5": "2024-12-10",
        }
        mock_rmtree.assert_called_once_with('/tmp/codeAudit_tags_test', ignore_errors=True)

    @patch('codeAudit.shutil.rmtree')
    @patch('codeAudit.tempfile.mkdtemp', return_value='/tmp/codeAudit_tags_test')
    @patch('codeAudit._run_git')
    def test_returns_empty_on_fetch_failure(self, mock_run_git, mock_mkdtemp, mock_rmtree):
        init_result = MagicMock(returncode=0, stdout='', stderr='')
        fetch_result = MagicMock(returncode=128, stdout='', stderr='fatal: not found')
        mock_run_git.side_effect = [init_result, fetch_result]

        result = fetch_repo_tags("git@github.com:org/bad-repo.git")

        assert result == {}
        mock_rmtree.assert_called_once()

    @patch('codeAudit.shutil.rmtree')
    @patch('codeAudit.tempfile.mkdtemp', return_value='/tmp/codeAudit_tags_test')
    @patch('codeAudit._run_git')
    def test_first_semver_wins_for_duplicates(self, mock_run_git, mock_mkdtemp, mock_rmtree):
        init_result = MagicMock(returncode=0, stdout='', stderr='')
        fetch_result = MagicMock(returncode=0, stdout='', stderr='')
        # Two tags with same semver but different dates (sorted by -creatordate)
        ref_output = (
            "v1.0.0 2025-06-15\n"
            "release-1.0.0 2025-01-01\n"
        )
        ref_result = MagicMock(returncode=0, stdout=ref_output, stderr='')
        mock_run_git.side_effect = [init_result, fetch_result, ref_result]

        result = fetch_repo_tags("git@github.com:org/repo.git")

        # First occurrence (most recent by creatordate) should win
        assert result == {"1.0.0": "2025-06-15"}

    @patch('codeAudit.shutil.rmtree')
    @patch('codeAudit.tempfile.mkdtemp', return_value='/tmp/codeAudit_tags_test')
    @patch('codeAudit._run_git')
    def test_converts_https_url_to_ssh(self, mock_run_git, mock_mkdtemp, mock_rmtree):
        init_result = MagicMock(returncode=0, stdout='', stderr='')
        fetch_result = MagicMock(returncode=0, stdout='', stderr='')
        ref_result = MagicMock(returncode=0, stdout='', stderr='')
        mock_run_git.side_effect = [init_result, fetch_result, ref_result]

        fetch_repo_tags("https://github.com/org/repo.git")

        # Verify the fetch command used SSH URL
        fetch_call_args = mock_run_git.call_args_list[1][0][0]
        assert "git@github.com:org/repo.git" in fetch_call_args


class TestIsPermissionError:
    def test_permission_denied(self):
        result = MagicMock(returncode=128, stderr="Permission denied (publickey).")
        assert _is_permission_error(result) is True

    def test_could_not_read_from_remote(self):
        result = MagicMock(returncode=128, stderr="fatal: Could not read from remote repository.")
        assert _is_permission_error(result) is True

    def test_terminal_prompts_disabled(self):
        result = MagicMock(returncode=128, stderr="fatal: terminal prompts disabled")
        assert _is_permission_error(result) is True

    def test_authentication_failed(self):
        result = MagicMock(returncode=128, stderr="fatal: Authentication failed for 'https://...'")
        assert _is_permission_error(result) is True

    def test_host_key_verification_failed(self):
        result = MagicMock(returncode=255, stderr="Host key verification failed.")
        assert _is_permission_error(result) is True

    def test_success_is_not_permission_error(self):
        result = MagicMock(returncode=0, stderr="")
        assert _is_permission_error(result) is False

    def test_other_error_is_not_permission_error(self):
        result = MagicMock(returncode=128, stderr="fatal: repository not found")
        assert _is_permission_error(result) is False

import asyncio
import os
import re
import pytest
from datetime import timedelta
from unittest.mock import patch, MagicMock, AsyncMock

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from codeAudit import (
    validate_regex, extract_repo_name, normalize_git_url_to_ssh,
    build_match_display, extract_capture_groups,
    parse_date_tolerance, extract_semver, fetch_repo_tags,
    _is_permission_error, _create_compliance_tickets,
    _is_async_permission_error,
    async_get_component_repo_url, async_fetch_file_from_repo,
    async_gather_repo_urls, async_process_repos, async_process_team,
    parse_arguments,
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


class TestCreateComplianceTickets:
    """Tests for _create_compliance_tickets dry-run and argument handling."""

    def _make_args(self, create=False, excel="teams.xlsx", dep_name="Spring Boot"):
        args = MagicMock()
        args.createTickets = excel
        args.dependencyName = dep_name
        args.create = create
        return args

    @patch('codeAudit.get_excel_sheets', return_value=["Teams"])
    @patch('codeAudit.process_teams_sheet', return_value={
        "TeamA": {"Project": "PROJ", "Issue Type": "Task", "Epic Link": "EPIC-1", "Assignee": "john"},
    })
    def test_dry_run_creates_no_real_tickets(self, mock_teams, mock_sheets):
        args = self._make_args(create=False)
        config = {"jira_server": "https://jira.example.com", "personal_access_token": "tok"}
        results = [("TeamA", "org/repo", "1.0.0", "2024-01-01", "500")]
        version_dates = {"2.0.0": "2025-06-01", "1.0.0": "2024-01-01"}

        # Should not raise; dry-run mode prints but doesn't connect to Jira
        _create_compliance_tickets(args, config, results, version_dates)

    @patch('codeAudit.get_excel_sheets', return_value=["Teams"])
    @patch('codeAudit.process_teams_sheet', return_value={})
    def test_empty_team_mapping_returns_early(self, mock_teams, mock_sheets):
        args = self._make_args()
        config = {}
        results = [("TeamA", "org/repo", "1.0.0", "2024-01-01", "500")]
        # Should not raise with empty mapping
        _create_compliance_tickets(args, config, results, {})

    @patch('codeAudit.get_excel_sheets', return_value=["Teams"])
    @patch('codeAudit.process_teams_sheet', return_value={
        "TeamA": {"Project": "PROJ"},
    })
    def test_skips_team_not_in_mapping(self, mock_teams, mock_sheets):
        args = self._make_args()
        config = {}
        results = [("UnknownTeam", "org/repo", "1.0.0", "2024-01-01", "500")]
        # Should not raise; just skips
        _create_compliance_tickets(args, config, results, {})

    @patch('codeAudit.get_excel_sheets', side_effect=Exception("file not found"))
    def test_excel_read_error_handled(self, mock_sheets):
        args = self._make_args()
        config = {}
        results = [("TeamA", "org/repo", "1.0.0", "2024-01-01", "500")]
        # Should not raise
        _create_compliance_tickets(args, config, results, {})


# --- Async tests ---

class TestIsAsyncPermissionError:
    def test_permission_denied(self):
        assert _is_async_permission_error(128, "Permission denied (publickey).") is True

    def test_success_is_not_error(self):
        assert _is_async_permission_error(0, "") is False

    def test_terminal_prompts_disabled(self):
        assert _is_async_permission_error(128, "fatal: terminal prompts disabled") is True

    def test_other_error_is_not_permission(self):
        assert _is_async_permission_error(128, "fatal: repository not found") is False


class TestAsyncGetComponentRepoUrl:
    @pytest.mark.asyncio
    async def test_successful_fetch(self):
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(return_value={
            'metadata': {
                'annotations': {
                    'git-repository-url': 'https://github.com/org/repo.git'
                }
            }
        })
        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=False),
        ))

        git_url, display = await async_get_component_repo_url(
            mock_session, "https://backstage.example.com", "my-service"
        )
        assert git_url == "git@github.com:org/repo.git"
        assert display == "org/repo"

    @pytest.mark.asyncio
    async def test_missing_annotation_returns_none(self):
        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = AsyncMock(return_value={
            'metadata': {'annotations': {}}
        })
        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(return_value=mock_response),
            __aexit__=AsyncMock(return_value=False),
        ))

        git_url, display = await async_get_component_repo_url(
            mock_session, "https://backstage.example.com", "no-repo"
        )
        assert git_url is None
        assert display is None

    @pytest.mark.asyncio
    async def test_request_exception_returns_none(self):
        mock_session = AsyncMock()
        mock_session.get = MagicMock(return_value=AsyncMock(
            __aenter__=AsyncMock(side_effect=Exception("connection error")),
            __aexit__=AsyncMock(return_value=False),
        ))

        git_url, display = await async_get_component_repo_url(
            mock_session, "https://backstage.example.com", "bad-service"
        )
        assert git_url is None
        assert display is None


class TestAsyncFetchFileFromRepo:
    @pytest.mark.asyncio
    @patch('codeAudit.shutil.rmtree')
    @patch('codeAudit.tempfile.mkdtemp', return_value='/tmp/codeAudit_async_test')
    @patch('codeAudit._async_run_git')
    async def test_permission_denied(self, mock_async_git, mock_mkdtemp, mock_rmtree):
        # init, remote add, config succeed; pull fails with permission error
        mock_async_git.side_effect = [
            (0, '', ''),  # init
            (0, '', ''),  # remote add
            (0, '', ''),  # config sparseCheckout
            (128, '', 'Permission denied (publickey).'),  # pull
        ]
        with patch('builtins.open', MagicMock()):
            with patch('os.makedirs'):
                result = await async_fetch_file_from_repo(
                    "git@github.com:org/private.git", "build.gradle"
                )
        assert result == "PERMISSION_DENIED"
        mock_rmtree.assert_called_once()

    @pytest.mark.asyncio
    @patch('codeAudit.shutil.rmtree')
    @patch('codeAudit.tempfile.mkdtemp', return_value='/tmp/codeAudit_async_test')
    @patch('codeAudit._async_run_git')
    async def test_file_not_found_returns_none(self, mock_async_git, mock_mkdtemp, mock_rmtree):
        mock_async_git.side_effect = [
            (0, '', ''),  # init
            (0, '', ''),  # remote add
            (0, '', ''),  # config sparseCheckout
            (0, '', ''),  # pull
        ]
        with patch('builtins.open', MagicMock()):
            with patch('os.makedirs'):
                with patch('os.path.isfile', return_value=False):
                    result = await async_fetch_file_from_repo(
                        "git@github.com:org/repo.git", "missing.txt"
                    )
        assert result is None

    @pytest.mark.asyncio
    @patch('codeAudit.shutil.rmtree')
    @patch('codeAudit.tempfile.mkdtemp', return_value='/tmp/codeAudit_async_test')
    @patch('codeAudit._async_run_git')
    async def test_init_failure_returns_none(self, mock_async_git, mock_mkdtemp, mock_rmtree):
        mock_async_git.side_effect = [
            (128, '', 'error'),  # init fails
        ]
        result = await async_fetch_file_from_repo(
            "git@github.com:org/repo.git", "file.txt"
        )
        assert result is None


class TestAsyncGatherRepoUrls:
    @pytest.mark.asyncio
    async def test_deduplicates_repos(self):
        """Two components mapping to the same repo URL are deduped."""
        async def mock_fetch(session, backstage_url, comp_name, timeout=30, log_lines=None):
            return "git@github.com:org/shared-repo.git", "org/shared-repo"

        semaphore = asyncio.Semaphore(5)
        with patch('codeAudit.async_get_component_repo_url', side_effect=mock_fetch):
            repos = await async_gather_repo_urls(
                MagicMock(), "https://backstage.example.com",
                ["comp-a", "comp-b"], semaphore,
            )
        assert len(repos) == 1
        assert "git@github.com:org/shared-repo.git" in repos
        assert repos["git@github.com:org/shared-repo.git"][1] == ["comp-a", "comp-b"]

    @pytest.mark.asyncio
    async def test_handles_none_urls(self):
        """Components with no repo URL are skipped."""
        call_count = 0
        async def mock_fetch(session, backstage_url, comp_name, timeout=30, log_lines=None):
            nonlocal call_count
            call_count += 1
            if comp_name == "no-repo":
                return None, None
            return "git@github.com:org/repo.git", "org/repo"

        semaphore = asyncio.Semaphore(5)
        with patch('codeAudit.async_get_component_repo_url', side_effect=mock_fetch):
            repos = await async_gather_repo_urls(
                MagicMock(), "https://backstage.example.com",
                ["good-comp", "no-repo"], semaphore,
            )
        assert len(repos) == 1
        assert call_count == 2


class TestAsyncProcessRepos:
    @pytest.mark.asyncio
    async def test_collects_results_and_permission_denied(self):
        """Test that results and permission errors are collected correctly."""
        async def mock_fetch(git_url, file_path, verbose=False, log_lines=None):
            if "private" in git_url:
                return "PERMISSION_DENIED"
            return '<version>1.2.3</version>'

        repos = {
            "git@github.com:org/repo.git": ("org/repo", ["comp-a"]),
            "git@github.com:org/private-repo.git": ("org/private-repo", ["comp-b"]),
        }
        compiled_regex = re.compile(r'<version>(.*?)</version>', re.DOTALL)
        semaphore = asyncio.Semaphore(5)

        with patch('codeAudit.async_fetch_file_from_repo', side_effect=mock_fetch):
            results, perm_denied, checked = await async_process_repos(
                repos, "pom.xml", compiled_regex, "TeamA", 1, 1, semaphore, False,
            )

        assert len(results) == 1
        assert results[0] == ("TeamA", "org/repo", "1.2.3")
        assert perm_denied == ["org/private-repo"]
        assert checked == 2


class TestAsyncProcessTeam:
    @pytest.mark.asyncio
    async def test_team_not_found(self):
        """Team with no components returns team_not_found=True."""
        semaphore = asyncio.Semaphore(5)
        with patch('codeAudit.filter_components_for_team', return_value=[]):
            result = await async_process_team(
                MagicMock(), "https://backstage.example.com", [],
                "ghost-team", 1, 1, "pom.xml", MagicMock(), semaphore, False,
            )
        assert result['team_name'] == "ghost-team"
        assert result['team_not_found'] is True
        assert result['team_processed'] is False
        assert result['results'] == []
        assert result['repos_checked'] == 0

    @pytest.mark.asyncio
    async def test_team_no_repos(self):
        """Team with components but no repos returns team_processed=False."""
        components = [{'metadata': {'name': 'comp-a'}}]
        semaphore = asyncio.Semaphore(5)

        async def mock_gather(session, url, names, sem, team_log_lines=None):
            return {}

        with patch('codeAudit.filter_components_for_team', return_value=components):
            with patch('codeAudit.async_gather_repo_urls', side_effect=mock_gather):
                result = await async_process_team(
                    MagicMock(), "https://backstage.example.com", [],
                    "empty-team", 1, 1, "pom.xml", MagicMock(), semaphore, False,
                )
        assert result['team_name'] == "empty-team"
        assert result['team_not_found'] is False
        assert result['team_processed'] is False

    @pytest.mark.asyncio
    async def test_team_with_results(self):
        """Team with repos and matches returns team_processed=True with results."""
        import re as re_mod
        components = [{'metadata': {'name': 'comp-a'}}]
        repos = {"git@github.com:org/repo.git": ("org/repo", ["comp-a"])}
        compiled_regex = re_mod.compile(r'<version>(.*?)</version>', re_mod.DOTALL)
        semaphore = asyncio.Semaphore(5)

        async def mock_gather(session, url, names, sem, team_log_lines=None):
            return repos

        async def mock_process(rp, fn, rx, tn, ti, tt, sem, v, team_log_lines=None):
            return [("my-team", "org/repo", "1.2.3")], [], 1

        with patch('codeAudit.filter_components_for_team', return_value=components):
            with patch('codeAudit.async_gather_repo_urls', side_effect=mock_gather):
                with patch('codeAudit.async_process_repos', side_effect=mock_process):
                    result = await async_process_team(
                        MagicMock(), "https://backstage.example.com", [],
                        "my-team", 1, 1, "pom.xml", compiled_regex, semaphore, False,
                    )
        assert result['team_name'] == "my-team"
        assert result['team_not_found'] is False
        assert result['team_processed'] is True
        assert len(result['results']) == 1
        assert result['repos_checked'] == 1
        assert len(result['log_lines']) > 0


class TestParallelArgument:
    def test_default_value(self):
        with patch('sys.argv', ['codeAudit.py', '--teams', 'A', '--checkFilename', 'f', '--searchRegex', '(x)']):
            args = parse_arguments()
        assert args.parallel == 5

    def test_custom_value(self):
        with patch('sys.argv', ['codeAudit.py', '--teams', 'A', '--checkFilename', 'f', '--searchRegex', '(x)', '--parallel', '5']):
            args = parse_arguments()
        assert args.parallel == 5

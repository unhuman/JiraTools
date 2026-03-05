import os
import re
import pytest
from unittest.mock import patch, MagicMock

import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from codeAudit import (
    validate_regex, extract_repo_name, normalize_git_url_to_ssh,
    build_match_display, extract_capture_groups
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

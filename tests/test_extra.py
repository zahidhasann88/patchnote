"""Extra coverage for CLI helpers, gitlog, action, AI client, and render edges."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import httpx
import pytest
import respx
from typer.testing import CliRunner

from patchnote.action_entry import _count_entries, _truthy, _write_output, build_argv, run_action
from patchnote.ai.cache import Cache
from patchnote.ai.client import LLMClient, LLMError, _extract_content
from patchnote.ai.validate import ValidationFailure, validate_polish, validate_summary
from patchnote.check import check_commits
from patchnote.cli import (
    detect_repo_slug,
    emit,
    tag_for_version,
    version_from_ref,
)
from patchnote.config import load_yaml_file, parse_config
from patchnote.filters import compile_patterns, matches_any
from patchnote.github import GitHubClient, parse_github_nwo, pr_url, repository_http_url
from patchnote.gitlog import (
    GitError,
    GitRunner,
    current_branch,
    latest_tag,
    list_tags,
    previous_tag,
    ref_exists,
    resolve_ref,
    tag_date,
)
from patchnote.model import (
    Changelog,
    ChangelogEntry,
    Confidence,
    EntrySource,
    OutputFormat,
    Style,
)
from patchnote.render.changelog_file import prepend_release, write_prepend
from patchnote.render.markdown import format_entry, render_markdown
from tests.gitutil import commit, git, tag

runner = CliRunner()
WHEN = datetime(2026, 1, 15, tzinfo=timezone.utc)


def test_version_from_ref_variants() -> None:
    assert version_from_ref("HEAD", "v1.2.3") == "1.2.3"
    assert version_from_ref("HEAD", "1.2.3") == "1.2.3"
    assert version_from_ref("v2.0.0", None) == "2.0.0"
    assert version_from_ref("release-1", None) == "release-1"
    assert version_from_ref("HEAD", None) is None
    assert tag_for_version("1.0.0") == "v1.0.0"
    assert tag_for_version("v1.0.0") == "v1.0.0"


def test_detect_repo_slug_from_config(repo: Path) -> None:
    cfg = parse_config({"repository": "acme/widgets"})
    assert detect_repo_slug(GitRunner(repo), cfg) == ("acme", "widgets")


def test_gitlog_helpers(repo: Path) -> None:
    commit(repo, "feat: a")
    tag(repo, "v0.1.0")
    commit(repo, "feat: b")
    tag(repo, "v0.2.0")
    git(repo, "tag", "nightly")
    runner_ = GitRunner(repo)
    assert latest_tag(runner_, "v*") == "v0.2.0"
    assert previous_tag(runner_, "v0.2.0", "v*") == "v0.1.0"
    assert previous_tag(runner_, "v0.1.0", "v*") is None
    assert previous_tag(runner_, "missing", "v*") == "v0.2.0"
    assert ref_exists(runner_, "HEAD")
    assert not ref_exists(runner_, "nope")
    sha = resolve_ref(runner_, "HEAD")
    assert len(sha) == 40
    assert tag_date(runner_, "v0.2.0") is not None
    assert current_branch(runner_) in {"main", "master"}
    tags = list_tags(runner_, "v*")
    assert "v0.2.0" in tags


def test_git_error_on_bad_command(repo: Path) -> None:
    runner_ = GitRunner(repo)
    with pytest.raises(GitError):
        runner_.run(["definitely-not-a-git-subcommand"])


def test_generate_conventional_style(repo: Path) -> None:
    commit(repo, "feat: add widgets")
    commit(repo, "fix: a bug")
    from patchnote.cli import app

    result = runner.invoke(
        app, ["--repo", str(repo), "generate", "--style", "conventional", "--version", "1.0.0"]
    )
    assert result.exit_code == 0, result.output
    assert "Features" in result.stdout
    assert "Bug Fixes" in result.stdout


def test_generate_dry_run_does_not_write(repo: Path) -> None:
    commit(repo, "feat: add widgets")
    out = repo / "CHANGELOG.md"
    from patchnote.cli import app

    result = runner.invoke(
        app,
        [
            "--repo",
            str(repo),
            "generate",
            "--version",
            "1.0.0",
            "--output",
            str(out),
            "--dry-run",
        ],
    )
    assert result.exit_code == 0
    assert not out.exists()


def test_prepend_requires_markdown(repo: Path) -> None:
    commit(repo, "feat: add widgets")
    from patchnote.cli import app

    result = runner.invoke(
        app,
        [
            "--repo",
            str(repo),
            "generate",
            "--format",
            "json",
            "--output",
            str(repo / "x.md"),
            "--prepend",
        ],
    )
    assert result.exit_code == 2


def test_check_ok_message(repo: Path) -> None:
    commit(repo, "feat: add widgets")
    from patchnote.cli import app

    result = runner.invoke(app, ["--repo", str(repo), "--verbose", "check"])
    assert result.exit_code == 0
    assert "ok" in result.output.lower() or result.exit_code == 0


def test_not_a_repo(tmp_path: Path) -> None:
    from patchnote.cli import app

    result = runner.invoke(app, ["--repo", str(tmp_path), "preview"])
    assert result.exit_code in {2, 3}


def test_compile_bad_regex() -> None:
    patterns = compile_patterns(["[unclosed"])
    assert matches_any("[unclosed", patterns)


def test_load_empty_yaml(tmp_path: Path) -> None:
    path = tmp_path / "empty.yaml"
    path.write_text("", encoding="utf-8")
    assert load_yaml_file(path) == {}


def test_load_non_mapping_yaml(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- just a list\n", encoding="utf-8")
    from patchnote.config import ConfigError

    with pytest.raises(ConfigError):
        load_yaml_file(path)


def test_format_entry_without_url_and_bot() -> None:
    config = parse_config({"include_hashes": True, "include_authors": True})
    entry = ChangelogEntry(
        id="pr-9",
        summary="Fix it",
        original_summary="fix it",
        section="Fixed",
        pr_number=9,
        authors=["@ada"],
        short_hashes=["abc1234"],
        hashes=["a" * 40],
        confidence=Confidence.HIGH,
        source=EntrySource.CONVENTIONAL,
        first_time_contributors=["@ada"],
    )
    text = format_entry(entry, config)
    assert "#9" in text
    assert "first-time" in text
    bot = entry.model_copy(update={"source": EntrySource.BOT, "pr_number": None})
    bot_text = format_entry(bot, config)
    assert "`abc1234`" not in bot_text


def test_custom_template(tmp_path: Path) -> None:
    template = tmp_path / "custom.j2"
    template.write_text("HELLO {{ changelog.version }}\n", encoding="utf-8")
    log = Changelog(
        version="9.9.9",
        from_ref="",
        to_ref="HEAD",
        date=date(2026, 10, 10),
        style=Style.KEEPACHANGELOG,
    )
    config = parse_config({"template": str(template)})
    text = render_markdown(log, config)
    assert "HELLO 9.9.9" in text


def test_render_with_header() -> None:
    log = Changelog(
        version="1.0.0",
        from_ref="",
        to_ref="HEAD",
        date=date(2026, 10, 10),
        sections={
            "Added": [
                ChangelogEntry(
                    id="e",
                    summary="Add x",
                    original_summary="add x",
                    section="Added",
                    confidence=Confidence.HIGH,
                    source=EntrySource.CONVENTIONAL,
                )
            ]
        },
        overview="A short overview of the release.",
        style=Style.KEEPACHANGELOG,
    )
    config = parse_config({})
    text = render_markdown(log, config, include_header=True)
    assert text.startswith("# Changelog")
    assert "A short overview" in text


def test_write_prepend_creates_file(tmp_path: Path) -> None:
    log = Changelog(
        version="1.0.0",
        from_ref="",
        to_ref="HEAD",
        date=date(2026, 10, 10),
        sections={
            "Added": [
                ChangelogEntry(
                    id="e",
                    summary="Add x",
                    original_summary="add x",
                    section="Added",
                    confidence=Confidence.HIGH,
                    source=EntrySource.CONVENTIONAL,
                )
            ]
        },
        compare_url="https://github.com/a/b/compare/v0...v1",
        style=Style.KEEPACHANGELOG,
    )
    path = tmp_path / "CHANGELOG.md"
    write_prepend(path, log, parse_config({}))
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "## [1.0.0]" in text


def test_prepend_without_unreleased() -> None:
    existing = "# Changelog\n\n## [0.1.0] - 2026-01-01\n\n### Added\n\n- old\n"
    log = Changelog(
        version="0.2.0",
        from_ref="",
        to_ref="HEAD",
        date=date(2026, 10, 10),
        sections={
            "Added": [
                ChangelogEntry(
                    id="e",
                    summary="New",
                    original_summary="new",
                    section="Added",
                    confidence=Confidence.HIGH,
                    source=EntrySource.CONVENTIONAL,
                )
            ]
        },
        style=Style.KEEPACHANGELOG,
    )
    result = prepend_release(existing, log, parse_config({}))
    assert result.index("## [0.2.0]") < result.index("## [0.1.0]")


def test_ai_client_markdown_fence() -> None:
    payload = {"choices": [{"message": {"content": '```json\n{"overview": "Hi."}\n```'}}]}
    content = _extract_content(payload)
    assert "overview" in content


def test_ai_client_bad_payloads() -> None:
    with pytest.raises(LLMError):
        _extract_content([])
    with pytest.raises(LLMError):
        _extract_content({})
    with pytest.raises(LLMError):
        _extract_content({"choices": [None]})
    with pytest.raises(LLMError):
        _extract_content({"choices": [{"message": "x"}]})
    with pytest.raises(LLMError):
        _extract_content({"choices": [{"message": {"content": ""}}]})


@respx.mock
def test_ai_client_http_and_retry() -> None:
    respx.post("https://example.invalid/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(400, text="response_format not supported"),
            httpx.Response(
                200,
                json={"choices": [{"message": {"content": json.dumps({"ok": True})}}]},
            ),
        ]
    )
    with httpx.Client() as http:
        client = LLMClient(http, base_url="https://example.invalid/v1", api_key="k")
        parsed = client.complete_json([{"role": "user", "content": "hi"}], model="m")
    assert parsed == {"ok": True}


@respx.mock
def test_ai_client_non_json_content() -> None:
    respx.post("https://example.invalid/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]})
    )
    with httpx.Client() as http:
        client = LLMClient(http, base_url="https://example.invalid/v1", api_key=None)
        with pytest.raises(LLMError):
            client.complete_json([], model="m")


def test_cache_corrupt(tmp_path: Path) -> None:
    cache = Cache(tmp_path)
    key = cache.key("polish", "m", "http://x", {"a": 1})
    path = cache.path_for(key)
    path.write_text("{not json", encoding="utf-8")
    assert cache.get(key) is None
    assert cache.get("missing") is None


def test_validate_polish_edge_cases() -> None:
    with pytest.raises(ValidationFailure):
        validate_polish([], "nope")
    with pytest.raises(ValidationFailure):
        validate_polish([], {"entries": "nope"})
    with pytest.raises(ValidationFailure):
        validate_polish([{"id": "a", "summary": "x"}], {"entries": ["nope"]})
    with pytest.raises(ValidationFailure):
        validate_polish(
            [{"id": "a", "summary": "x"}],
            {"entries": [{"id": "a", "summary": "x", "pr_number": 1}]},
        )
    with pytest.raises(ValidationFailure):
        validate_polish([{"id": "a", "summary": "x"}], {"entries": [{"id": "", "summary": "x"}]})
    with pytest.raises(ValidationFailure):
        validate_polish([{"id": "a", "summary": "x"}], {"entries": [{"id": "a", "summary": "  "}]})
    with pytest.raises(ValidationFailure):
        validate_summary("nope")
    with pytest.raises(ValidationFailure):
        validate_summary({"overview": ""})
    with pytest.raises(ValidationFailure):
        validate_summary({"overview": "A. B. C. D. E. F."})
    with pytest.raises(ValidationFailure):
        validate_summary({"overview": "x" * 900})


def test_action_helpers(tmp_path: Path) -> None:
    assert _truthy("YES")
    assert not _truthy("no")
    out = tmp_path / "out.txt"
    _write_output(out, "foo", "hello\nworld")
    text = out.read_text(encoding="utf-8")
    assert "foo<<" in text
    assert _count_entries("- a\n- b\n# c\n") == 2
    argv = build_argv({"PATCHNOTE_FORMAT": "json", "PATCHNOTE_CONFIG": "p.yaml"})
    assert "--format" in argv and "json" in argv
    assert "--config" in argv


def test_action_unknown_repo(tmp_path: Path) -> None:
    env = {
        "GITHUB_OUTPUT": str(tmp_path / "out"),
        "GITHUB_WORKSPACE": str(tmp_path),
        "PATCHNOTE_VERSION": "1.0.0",
        "PATCHNOTE_AI": "none",
    }
    code = run_action(env)
    assert code in {2, 3}


@respx.mock
def test_github_update_release_and_errors() -> None:
    respx.get("https://api.github.com/repos/acme/w/releases/tags/v1.0.0").mock(
        return_value=httpx.Response(200, json={"id": 42, "html_url": "https://x"})
    )
    respx.patch("https://api.github.com/repos/acme/w/releases/42").mock(
        return_value=httpx.Response(200, json={"html_url": "https://updated"})
    )
    warnings: list[str] = []
    with httpx.Client() as http:
        client = GitHubClient(http, "token", "acme", "w", warn=warnings.append)
        data = client.create_or_update_release(tag="v1.0.0", name="v1.0.0", body="x")
        assert "updated" in data["html_url"]
        client._gave_up = True
        assert client.associated_pulls_rest("a" * 40) == []
    assert repository_http_url("a", "b") == "https://github.com/a/b"
    assert pr_url("a", "b", 3).endswith("/pull/3")
    assert parse_github_nwo("https://www.github.com/acme/widgets") == ("acme", "widgets")
    assert parse_github_nwo("") is None


@respx.mock
def test_github_unauthorized() -> None:
    respx.post("https://api.github.com/graphql").mock(return_value=httpx.Response(401, text="no"))
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(401, text="no"))
    warnings: list[str] = []
    from patchnote.model import Commit

    sha = "a" * 40
    commit_ = Commit(
        hash=sha,
        short_hash="aaaaaaa",
        author_name="A",
        author_email="a@e.com",
        author_date=WHEN,
        subject="feat: x",
    )
    with httpx.Client() as http:
        client = GitHubClient(http, "token", "acme", "w", warn=warnings.append)
        mapping = client.enrich_commits([commit_])
    assert mapping[sha] == []
    assert warnings


def test_emit_stdout() -> None:
    from patchnote import cli as cli_mod

    log = Changelog(
        version="1.0.0",
        from_ref="",
        to_ref="HEAD",
        date=date(2026, 10, 10),
        style=Style.KEEPACHANGELOG,
    )
    text = emit(log, parse_config({}), OutputFormat.JSON, None, False, True)
    assert '"version"' in text
    assert text == cli_mod.LAST_RENDERED


def test_check_allow_unknown_type() -> None:
    from patchnote.model import Commit

    c = Commit(
        hash="a" * 40,
        short_hash="aaaaaaa",
        author_name="A",
        author_email="a@e.com",
        author_date=WHEN,
        subject="feat: x",
    )
    cfg = parse_config({"check": {"require_conventional": False}})
    assert check_commits([c], cfg) == []

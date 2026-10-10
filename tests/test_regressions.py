"""Checks for the publishing review's correctness fixes."""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from patchnote import cli
from patchnote.action_entry import build_argv, run_action
from patchnote.ai import apply_ai
from patchnote.ai.cache import Cache
from patchnote.ai.client import LLMClient, LLMError
from patchnote.ai.validate import ValidationFailure, validate_polish
from patchnote.classify import build_changelog
from patchnote.config import AiConfig, load_config, parse_config
from patchnote.conventional import parse_commit
from patchnote.github import GitHubClient, parse_github_nwo
from patchnote.gitlog import GitRunner, ref_exists
from patchnote.model import AiMode
from patchnote.render.changelog_file import prepend_release, write_prepend
from tests.gitutil import commit, git, tag
from tests.test_ai import changelog
from tests.test_changelog_file import cl
from tests.test_classify import make_commit

runner = CliRunner()


def test_release_tag_range_includes_release_changes(repo: Path) -> None:
    commit(repo, "chore: base")
    tag(repo, "v0.1.0")
    commit(repo, "feat: new release")
    tag(repo, "v0.2.0")
    result = runner.invoke(
        cli.app, ["--repo", str(repo), "generate", "--to", "v0.2.0", "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["from_ref"] == "v0.1.0"
    assert data["sections"]["Added"][0]["summary"] == "New release"


def test_default_range_ignores_unrelated_branch_tag(repo: Path) -> None:
    commit(repo, "chore: base")
    tag(repo, "v1.0.0")
    git(repo, "checkout", "-b", "unrelated")
    commit(repo, "feat: unrelated")
    tag(repo, "v9.0.0")
    git(repo, "checkout", "main")
    commit(repo, "feat: current branch")
    result = runner.invoke(cli.app, ["--repo", str(repo), "generate", "--format", "json"])
    assert json.loads(result.stdout)["from_ref"] == "v1.0.0"


def test_stdout_preserves_json_and_markdown(repo: Path) -> None:
    commit(repo, "feat: preserve [red] and Unicode café")
    result = runner.invoke(cli.app, ["--repo", str(repo), "generate", "--format", "json"])
    assert result.exit_code == 0
    assert "[red]" in json.loads(result.stdout)["sections"]["Added"][0]["summary"]
    markdown = runner.invoke(cli.app, ["--repo", str(repo), "generate", "--version", "1.0.0"])
    assert "## [1.0.0]" in markdown.stdout


def test_action_config_and_counts_ignore_contributors_and_old_releases(
    repo: Path, tmp_path: Path
) -> None:
    commit(repo, "feat: widget")
    cfg = repo / "custom.yaml"
    cfg.write_text("include_contributors: true\n", encoding="utf-8")
    (repo / "CHANGELOG.md").write_text(
        "# Changelog\n\n## [0.0.1]\n\n- Old entry\n", encoding="utf-8"
    )
    out = tmp_path / "outputs"
    before = dict(os.environ)
    env = {
        **before,
        "GITHUB_WORKSPACE": str(repo),
        "PATCHNOTE_CONFIG": str(cfg),
        "PATCHNOTE_VERSION": "0.1.0",
        "PATCHNOTE_OUTPUT": "CHANGELOG.md",
        "PATCHNOTE_PREPEND": "true",
        "GITHUB_OUTPUT": str(out),
    }
    assert run_action(env) == 0
    assert dict(os.environ) == before
    text = out.read_text(encoding="utf-8")
    value = text.split("unreleased-entries<<", 1)[1].splitlines()[1]
    assert value == "1"
    assert "Old entry" not in text
    assert "Old entry" in (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    argv = build_argv(env)
    assert argv.index("--config") < argv.index("generate")


def test_action_tag_push_does_not_generate_empty_notes(repo: Path, tmp_path: Path) -> None:
    commit(repo, "feat: first release")
    tag(repo, "v0.1.0")
    out = tmp_path / "outputs"
    assert (
        run_action(
            {
                **os.environ,
                "GITHUB_WORKSPACE": str(repo),
                "GITHUB_REF_TYPE": "tag",
                "GITHUB_REF_NAME": "v0.1.0",
                "GITHUB_OUTPUT": str(out),
            }
        )
        == 0
    )
    assert "First release" in out.read_text(encoding="utf-8")


@pytest.mark.parametrize("include_all,collapse", [(True, True), (True, False), (False, False)])
def test_bot_entries_are_not_duplicated(include_all: bool, collapse: bool) -> None:
    config = parse_config({"bots": {"collapse": collapse}})
    items = [make_commit("feat: bot feature", author="dependabot[bot]")]
    result = build_changelog(
        items, config, from_ref="", to_ref="HEAD", version="1", include_all=include_all
    )
    assert result.entry_count == 1
    assert sum(len(entries) for entries in result.sections.values()) == 1


@pytest.mark.parametrize(
    "subject,author",
    [("chore!: remove old config", "Ada"), ("docs!: remove API contract", "dependabot[bot]")],
)
def test_breaking_changes_survive_skip_types_and_bot_collapsing(subject: str, author: str) -> None:
    result = build_changelog(
        [make_commit(subject, author=author)],
        parse_config({}),
        from_ref="",
        to_ref="HEAD",
        version="1",
    )
    assert len(result.breaking) == 1
    assert result.entry_count == 1


def test_issue_references_do_not_dedupe_unrelated_commits() -> None:
    items = [
        make_commit("fix: first issue #12", 1),
        make_commit("fix: second", 2, body="Fixes #12"),
    ]
    result = build_changelog(items, parse_config({}), from_ref="", to_ref="HEAD", version="1")
    assert result.entry_count == 2
    assert all(entry.pr_number is None for entry in result.all_entries)


def test_multiline_breaking_footer() -> None:
    parsed = parse_commit(
        make_commit(
            "chore: config", body="BREAKING CHANGE: old key removed\nUse the new key instead."
        )
    )
    assert parsed.breaking
    assert "Use the new key" in parsed.footers[0].value


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_prepend_preserves_older_section_whitespace_and_file_bytes(
    tmp_path: Path, newline: str
) -> None:
    older = "## [0.1.0] - 2026-01-01\n\n### Added\n\n- Old note  \n\n\n"
    prefix = "# Changelog\n\nIntro  \n\n## [Unreleased]\n\n- Pending note\n\n\n"
    original = (prefix + older).replace("\n", newline)
    path = tmp_path / "CHANGELOG.md"
    path.write_bytes(original.encode("utf-8"))
    config = parse_config({})
    log = cl("0.2.0", "New note")
    write_prepend(path, log, config)
    once = path.read_bytes()
    write_prepend(path, log, config)
    assert path.read_bytes() == once
    result = once.decode("utf-8")
    assert result.startswith(prefix.replace("\n", newline))
    assert older.replace("\n", newline) in result


def test_overview_is_replaced_with_its_release_section() -> None:
    log = cl("0.2.0", "New note").model_copy(update={"overview": "One overview. Two sentences."})
    once = prepend_release("# Changelog\n", log, parse_config({}))
    twice = prepend_release(once, log, parse_config({}))
    assert once == twice
    assert twice.count("One overview") == 1


@pytest.mark.parametrize(
    "summary",
    ["Changed #999", "Fine\n# Injected heading", "+ Extra bullet", "<script>bad</script>"],
)
def test_ai_rejects_injected_structure_and_references(summary: str) -> None:
    with pytest.raises(ValidationFailure):
        validate_polish(
            [{"id": "e", "summary": "Fix #12", "pr_number": 12}],
            {"entries": [{"id": "e", "summary": summary}]},
        )


def test_ai_cache_failure_keeps_validated_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = AiConfig(base_url="http://local.invalid/v1", model="test")
    payload = {
        "entries": [
            {"id": "pr-1", "summary": "Add widgets"},
            {"id": "pr-2", "summary": "Fix crash"},
        ]
    }
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(payload)}}]}
        )
    )
    cache = Cache(tmp_path)

    def reject_write(*args: object) -> None:
        raise OSError("read-only cache")

    monkeypatch.setattr(cache, "put", reject_write)
    warnings: list[str] = []
    with httpx.Client(transport=transport) as http:
        result = apply_ai(
            changelog(),
            config,
            AiMode.POLISH,
            LLMClient(http, base_url=config.base_url, api_key=None),
            cache,
            warn=warnings.append,
        )
    assert result.entry_count == 2
    assert warnings


def test_ai_http_error_does_not_echo_secret() -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(500, text="sk-secret"))
    with httpx.Client(transport=transport) as http, pytest.raises(LLMError) as exc:
        LLMClient(http, base_url="https://example.invalid/v1", api_key="sk-secret").complete_json(
            [], model="test"
        )
    assert "sk-secret" not in str(exc.value)


def test_config_relative_template_path(tmp_path: Path) -> None:
    path = tmp_path / "patchnote.yaml"
    path.write_text("template: templates/notes.j2\n", encoding="utf-8")
    assert load_config(tmp_path, path).template == str((tmp_path / "templates/notes.j2").resolve())


@pytest.mark.parametrize("value", [0, -1, float("inf"), float("nan")])
def test_invalid_ai_timeout(value: float) -> None:
    with pytest.raises(ValueError):
        AiConfig(timeout_seconds=value)


def test_git_option_is_not_accepted_as_a_ref(repo: Path) -> None:
    commit(repo, "feat: safe")
    assert not ref_exists(GitRunner(repo), "--all")


@pytest.mark.parametrize(
    "url", ["https://notgithub.com/owner/repo", "https://github.com.evil/owner/repo"]
)
def test_github_hostname_matching_is_exact(url: str) -> None:
    assert parse_github_nwo(url) is None


def test_unmerged_pr_is_not_used_for_enrichment() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200, json=[{"number": 1, "title": "feat: open", "merged_at": None}]
        )
    )
    with httpx.Client(transport=transport) as http:
        assert GitHubClient(http, "token", "owner", "repo").associated_pulls_rest("a" * 40) == []


def test_pr_title_is_cleaned_and_classified_without_labels() -> None:
    from patchnote.model import PullRequest

    commit_data = make_commit("chore: internal refactor")
    pull = PullRequest(number=12, title="feat(api): add stable API", body="Sensitive PR body")
    result = build_changelog(
        [commit_data],
        parse_config({}),
        from_ref="",
        to_ref="HEAD",
        version="1",
        pr_by_sha={commit_data.hash: [pull]},
    )
    entry = result.all_entries[0]
    assert entry.section == "Added"
    assert entry.summary == "Add stable API"
    assert "Sensitive PR body" not in json.dumps(result.to_ai_payload())
    assert "Sensitive PR body" in json.dumps(result.to_ai_payload(include_bodies=True))
    assert "pr_body" not in result.model_dump(mode="json")["sections"]["Added"][0]


def test_ai_notice_uses_effective_endpoint(repo: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    commit(repo, "feat: an entry")
    monkeypatch.setenv("PATCHNOTE_AI_BASE_URL", "http://actual.local/v1")
    monkeypatch.setenv("PATCHNOTE_AI_MODEL", "local-test-model")
    real_client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(500))
    monkeypatch.setattr(cli.httpx, "Client", lambda: real_client(transport=transport))
    result = runner.invoke(cli.app, ["--repo", str(repo), "generate", "--ai", "polish", "--yes"])
    assert "http://actual.local/v1" in result.stderr
    assert "local-test-model" in result.stderr
    assert "https://api.openai.com" not in result.stderr


def test_release_rerun_does_not_erase_existing_notes(repo: Path) -> None:
    commit(repo, "feat: first release")
    tag(repo, "v0.1.0")
    result = runner.invoke(cli.app, ["--repo", str(repo), "release", "--version", "0.1.0"])
    assert result.exit_code == 0, result.output
    assert "First release" in result.stdout


@pytest.mark.parametrize("publish,expected_draft", [("false", True), ("true", False)])
def test_action_draft_false_requires_explicit_publish(
    repo: Path, monkeypatch: pytest.MonkeyPatch, publish: str, expected_draft: bool
) -> None:
    captured: list[bool] = []

    def fake_release(*args: object, draft: bool) -> str:
        captured.append(draft)
        return "https://example.invalid/release"

    monkeypatch.setattr(cli, "_push_github_release", fake_release)
    cli.state.repo = repo
    cli.state.config_path = None
    assert cli.run_release_from_env(
        {"PATCHNOTE_VERSION": "1.0.0", "PATCHNOTE_DRAFT": "false", "PATCHNOTE_PUBLISH": publish},
        "notes",
    )
    assert captured == [expected_draft]

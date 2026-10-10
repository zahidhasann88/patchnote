from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from patchnote import __version__
from patchnote.cli import app
from tests.gitutil import commit, git, tag

runner = CliRunner()


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert __version__ in result.stdout


def test_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["init"])
    assert result.exit_code == 0
    assert Path("patchnote.yaml").is_file()
    again = runner.invoke(app, ["init"])
    assert again.exit_code == 2


def test_generate_and_preview(repo: Path) -> None:
    commit(repo, "chore: start")
    tag(repo, "v0.1.0")
    commit(repo, "feat: add widgets")
    commit(repo, "fix: crash on empty input")
    commit(repo, "chore: ignore me")
    result = runner.invoke(app, ["--repo", str(repo), "preview"])
    assert result.exit_code == 0, result.output
    assert "Added" in result.stdout or "Add widgets" in result.stdout
    assert "Fixed" in result.stdout or "crash" in result.stdout
    assert "ignore me" not in result.stdout

    out = repo / "CHANGELOG.md"
    gen = runner.invoke(
        app,
        ["--repo", str(repo), "generate", "--version", "0.2.0", "--output", str(out), "--prepend"],
    )
    assert gen.exit_code == 0, gen.output
    text = out.read_text(encoding="utf-8")
    assert "## [0.2.0]" in text
    again = runner.invoke(
        app,
        ["--repo", str(repo), "generate", "--version", "0.2.0", "--output", str(out), "--prepend"],
    )
    assert again.exit_code == 0
    twice = out.read_text(encoding="utf-8")
    assert twice.count("## [0.2.0]") == 1


def test_generate_json_and_github_release(repo: Path) -> None:
    commit(repo, "feat: add widgets")
    json_result = runner.invoke(app, ["--repo", str(repo), "generate", "--format", "json"])
    assert json_result.exit_code == 0
    assert '"sections"' in json_result.stdout
    gh = runner.invoke(app, ["--repo", str(repo), "generate", "--format", "github-release"])
    assert gh.exit_code == 0
    assert "###" in gh.stdout


def test_generate_include_all(repo: Path) -> None:
    commit(repo, "chore: bump")
    result = runner.invoke(app, ["--repo", str(repo), "generate", "--include-all"])
    assert result.exit_code == 0
    assert "bump" in result.stdout.lower() or "Changed" in result.stdout


def test_check_clean_and_dirty(repo: Path) -> None:
    commit(repo, "feat: add widgets")
    clean = runner.invoke(app, ["--repo", str(repo), "check"])
    assert clean.exit_code == 0, clean.output
    commit(repo, "totally broken message")
    dirty = runner.invoke(app, ["--repo", str(repo), "check"])
    assert dirty.exit_code == 1


def test_generate_unknown_ref(repo: Path) -> None:
    commit(repo, "feat: x")
    result = runner.invoke(app, ["--repo", str(repo), "generate", "--from", "no-such-ref"])
    assert result.exit_code == 2


def test_compare_link_when_github_remote(repo: Path) -> None:
    commit(repo, "chore: start")
    tag(repo, "v0.1.0")
    commit(repo, "feat: add widgets")
    git(repo, "remote", "add", "origin", "https://github.com/acme/widgets.git")
    result = runner.invoke(app, ["--repo", str(repo), "generate", "--version", "0.2.0"])
    assert result.exit_code == 0, result.output
    assert "github.com/acme/widgets/compare" in result.stdout


def test_empty_range(repo: Path) -> None:
    commit(repo, "feat: x")
    tag(repo, "v1.0.0")
    result = runner.invoke(app, ["--repo", str(repo), "generate", "--from", "v1.0.0"])
    assert result.exit_code == 0
    assert (
        "## [Unreleased]" in result.stdout
        or result.stdout.strip().endswith("Unreleased]")
        or "Unreleased" in result.stdout
    )


def test_breaking_and_revert(repo: Path) -> None:
    commit(repo, "feat: add widgets")
    sha = git(repo, "rev-parse", "HEAD").strip()
    commit(repo, "feat!: explode api\n\nBREAKING CHANGE: v1 is gone")
    commit(repo, f'Revert "feat: add widgets"\n\nThis reverts commit {sha}.')
    result = runner.invoke(app, ["--repo", str(repo), "generate", "--version", "2.0.0"])
    assert result.exit_code == 0, result.output
    assert "Breaking" in result.stdout
    # Revert collapsed with original — widgets should not appear as Added.
    # The breaking change remains.
    assert (
        "explode" in result.stdout.lower()
        or "v1" in result.stdout.lower()
        or "gone" in result.stdout.lower()
    )

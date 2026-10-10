"""Shared fixtures. Git tests use real temporary repositories."""

from __future__ import annotations

from pathlib import Path

import pytest

from patchnote.config import Config, parse_config
from patchnote.gitlog import GitRunner
from tests.gitutil import commit, git, tag

__all__ = ["commit", "git", "tag"]


@pytest.fixture(autouse=True)
def isolated_service_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "GITHUB_REPOSITORY",
        "GITHUB_REF",
        "GITHUB_REF_NAME",
        "GITHUB_REF_TYPE",
        "GITHUB_TOKEN",
        "PATCHNOTE_TOKEN",
        "OPENAI_API_KEY",
        "PATCHNOTE_AI_BASE_URL",
        "PATCHNOTE_AI_MODEL",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "Test User")
    git(root, "config", "user.email", "test@example.com")
    git(root, "config", "commit.gpgsign", "false")
    git(root, "config", "tag.gpgsign", "false")
    git(root, "config", "init.defaultBranch", "main")
    return root


@pytest.fixture
def runner(repo: Path) -> GitRunner:
    return GitRunner(repo)


@pytest.fixture
def config() -> Config:
    return parse_config({})


@pytest.fixture
def isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    return tmp_path

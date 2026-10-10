from __future__ import annotations

from pathlib import Path

from patchnote.gitlog import GitRunner, latest_tag, list_commits, parse_log_output, remote_url
from tests.gitutil import commit, git, tag


def test_list_commits_range(repo: Path) -> None:
    commit(repo, "chore: start", "README.md")
    tag(repo, "v0.1.0")
    commit(repo, "feat: one")
    commit(repo, "fix: two")
    runner = GitRunner(repo)
    commits = list_commits(runner, "v0.1.0", "HEAD")
    subjects = [c.subject for c in commits]
    assert "feat: one" in subjects
    assert "fix: two" in subjects
    assert "chore: start" not in subjects


def test_empty_range(repo: Path) -> None:
    commit(repo, "chore: start")
    tag(repo, "v0.1.0")
    runner = GitRunner(repo)
    commits = list_commits(runner, "v0.1.0", "HEAD")
    assert commits == []


def test_full_history_without_from(repo: Path) -> None:
    commit(repo, "feat: a")
    commit(repo, "feat: b")
    runner = GitRunner(repo)
    commits = list_commits(runner, None, "HEAD")
    assert len(commits) == 2


def test_latest_tag_pattern(repo: Path) -> None:
    commit(repo, "chore: start")
    tag(repo, "v0.1.0")
    commit(repo, "feat: x")
    tag(repo, "v0.2.0")
    tag(repo, "nightly-1")
    runner = GitRunner(repo)
    assert latest_tag(runner, "v*") == "v0.2.0"


def test_merge_commit_parents(repo: Path) -> None:
    commit(repo, "feat: base")
    git(repo, "checkout", "-b", "topic")
    commit(repo, "feat: topic work", "topic.txt")
    git(repo, "checkout", "main")
    git(repo, "merge", "--no-ff", "-m", "Merge branch 'topic'", "topic")
    runner = GitRunner(repo)
    commits = list_commits(runner, None, "HEAD")
    merges = [c for c in commits if c.is_merge]
    assert len(merges) == 1
    assert len(merges[0].parents) == 2


def test_parse_log_output_roundtrip() -> None:
    raw = (
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\x1fabcdef1\x1fAda\x1fada@x\x1f"
        "2026-01-15T12:00:00+00:00\x1fbbbbbbbb\x1ffeat: x\x1fbody line\x1e\n"
    )
    commits = parse_log_output(raw)
    assert len(commits) == 1
    assert commits[0].subject == "feat: x"
    assert commits[0].body == "body line"


def test_remote_url(repo: Path) -> None:
    git(repo, "remote", "add", "origin", "git@github.com:acme/widgets.git")
    runner = GitRunner(repo)
    assert remote_url(runner) == "git@github.com:acme/widgets.git"


def test_body_with_newlines(repo: Path) -> None:
    commit(repo, "feat: x\n\nA longer body.\n\nBREAKING CHANGE: nope")
    runner = GitRunner(repo)
    commits = list_commits(runner, None, "HEAD")
    assert "BREAKING CHANGE" in commits[0].body

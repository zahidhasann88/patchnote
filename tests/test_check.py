from __future__ import annotations

from datetime import datetime, timezone

import pytest

from patchnote.check import check_commits, run_check
from patchnote.config import parse_config
from patchnote.model import CheckViolationError, Commit, PullRequest

WHEN = datetime(2026, 1, 15, tzinfo=timezone.utc)


def c(subject: str, n: int = 1) -> Commit:
    return Commit(
        hash=f"{n:040x}",
        short_hash=f"{n:040x}"[:7],
        author_name="Ada",
        author_email="a@e.com",
        author_date=WHEN,
        parents=[],
        subject=subject,
        body="",
    )


def test_clean_range() -> None:
    config = parse_config({})
    violations = check_commits([c("feat: add"), c("fix(core): crash")], config)
    assert violations == []


def test_non_conventional_fails() -> None:
    config = parse_config({})
    violations = check_commits([c("oops no prefix")], config)
    assert len(violations) == 1
    assert "not a Conventional Commit" in violations[0]


def test_disallowed_type() -> None:
    config = parse_config({"check": {"types": ["feat", "fix"]}})
    violations = check_commits([c("chore: bump")], config)
    assert violations
    assert "not allowed" in violations[0]


def test_require_scope() -> None:
    config = parse_config({"check": {"require_scope": True}})
    violations = check_commits([c("feat: no scope")], config)
    assert any("missing scope" in v for v in violations)


def test_allow_merge() -> None:
    merge = Commit(
        hash="b" * 40,
        short_hash="b" * 7,
        author_name="Ada",
        author_email="a@e.com",
        author_date=WHEN,
        parents=["a" * 40, "c" * 40],
        subject="Merge branch 'x'",
        body="",
    )
    config = parse_config({})
    assert check_commits([merge], config) == []
    config2 = parse_config({"check": {"allow_merge": False}})
    assert check_commits([merge], config2)


def test_pr_title_checked() -> None:
    config = parse_config({})
    commit = c("feat: x")
    prs = {commit.hash: [PullRequest(number=1, title="totally not conventional")]}
    violations = check_commits([commit], config, pr_by_sha=prs)
    assert any("PR #1" in v for v in violations)


def test_run_check_raises() -> None:
    config = parse_config({})
    with pytest.raises(CheckViolationError) as exc:
        run_check([c("nope")], config)
    assert exc.value.exit_code == 1

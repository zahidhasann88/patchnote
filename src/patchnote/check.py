"""Lint commit messages (and optional PR titles) against Conventional Commits."""

from __future__ import annotations

from patchnote.config import Config
from patchnote.conventional import parse_commit
from patchnote.filters import is_merge_noise
from patchnote.model import CheckViolationError, Commit, PullRequest


def check_commits(
    commits: list[Commit],
    config: Config,
    *,
    pr_by_sha: dict[str, list[PullRequest]] | None = None,
) -> list[str]:
    """Return human-readable violation strings. Empty means the range is clean."""
    violations: list[str] = []
    allowed = {item.lower() for item in config.check.types}
    for commit in commits:
        if is_merge_noise(commit) and config.check.allow_merge:
            continue
        parsed = parse_commit(commit)
        short = commit.short_hash
        subject = commit.subject
        if config.check.require_conventional and not parsed.is_conventional:
            violations.append(f"{short}: not a Conventional Commit: {subject!r}")
            continue
        if parsed.type and parsed.type not in allowed:
            violations.append(
                f"{short}: type {parsed.type!r} is not allowed (subject: {subject!r})"
            )
        if config.check.require_scope and parsed.is_conventional and not parsed.scope:
            violations.append(f"{short}: missing scope (subject: {subject!r})")
        prs = (pr_by_sha or {}).get(commit.hash) or []
        for pr in prs:
            if config.check.require_conventional:
                fake = Commit(
                    hash=commit.hash,
                    short_hash=commit.short_hash,
                    author_name=commit.author_name,
                    author_email=commit.author_email,
                    author_date=commit.author_date,
                    parents=commit.parents,
                    subject=pr.title,
                    body="",
                )
                pr_parsed = parse_commit(fake)
                if not pr_parsed.is_conventional:
                    violations.append(
                        f"PR #{pr.number}: title is not a Conventional Commit: {pr.title!r}"
                    )
    return violations


def run_check(
    commits: list[Commit],
    config: Config,
    *,
    pr_by_sha: dict[str, list[PullRequest]] | None = None,
) -> None:
    violations = check_commits(commits, config, pr_by_sha=pr_by_sha)
    if violations:
        raise CheckViolationError(
            f"{len(violations)} commit/PR convention violation(s)",
            violations,
        )

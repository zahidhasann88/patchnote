"""Filter, dedupe, and collapse commits before they become changelog entries."""

from __future__ import annotations

import re
from collections.abc import Iterable

from patchnote.config import Config
from patchnote.conventional import parse_commit, strip_pr_suffix
from patchnote.model import Commit, ParsedCommit, PullRequest


def compile_patterns(patterns: Iterable[str]) -> list[re.Pattern[str]]:
    compiled: list[re.Pattern[str]] = []
    for pattern in patterns:
        try:
            compiled.append(re.compile(pattern, re.IGNORECASE))
        except re.error:
            compiled.append(re.compile(re.escape(pattern), re.IGNORECASE))
    return compiled


def matches_any(text: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(pattern.search(text) for pattern in patterns)


def is_merge_noise(commit: Commit) -> bool:
    subject = commit.subject.strip()
    if commit.is_merge:
        return True
    return subject.startswith(
        ("Merge branch ", "Merge pull request ", "Merge remote-tracking branch ")
    )


def is_bot_commit(commit: Commit, patterns: list[re.Pattern[str]]) -> bool:
    haystack = f"{commit.author_name} {commit.author_email}"
    return matches_any(haystack, patterns)


def should_skip_raw(
    commit: Commit,
    config: Config,
    *,
    ignore: list[re.Pattern[str]],
    include_all: bool = False,
) -> bool:
    subject = commit.subject
    if matches_any(subject, ignore) or matches_any(commit.message, ignore):
        return True
    return bool(config.skip_merge_commits and is_merge_noise(commit))


def parse_and_filter(
    commits: list[Commit],
    config: Config,
    *,
    include_all: bool = False,
) -> tuple[list[ParsedCommit], list[ParsedCommit]]:
    """Return (kept, bot_commits). Merge/ignore-pattern noise is dropped."""
    ignore = compile_patterns(config.ignore_patterns)
    bot_patterns = compile_patterns(config.bots.patterns)
    kept: list[ParsedCommit] = []
    bots: list[ParsedCommit] = []
    for commit in commits:
        if should_skip_raw(commit, config, ignore=ignore, include_all=include_all):
            continue
        parsed = parse_commit(commit)
        if is_bot_commit(commit, bot_patterns):
            if config.bots.skip and not include_all:
                continue
            bots.append(parsed)
            if config.bots.collapse and not include_all and not parsed.breaking:
                continue
            if parsed.breaking:
                bots.pop()
        if parsed.type and parsed.type in config.skip_types and not include_all:
            # A later label can rescue this commit; keep it tagged.
            parsed_skip = parsed.model_copy()
            # stash skip intent via a convention: we still return it and let
            # classify drop it if no label overrides.
            kept.append(parsed_skip)
            continue
        kept.append(parsed)
    return kept, bots


def collapse_reverts(parsed: list[ParsedCommit]) -> list[ParsedCommit]:
    """Drop a commit and its revert when both appear in the range.

    A revert whose original is *not* in the range is kept (so a later release
    can document the rollback).
    """
    by_hash = {item.raw.hash.lower(): item for item in parsed}
    by_short = {item.raw.short_hash.lower(): item for item in parsed}
    drop: set[str] = set()
    for item in parsed:
        if item.raw.hash in drop:
            continue
        reverted = item.reverted_hash
        is_revert = item.type == "revert" or reverted is not None
        if not is_revert or not reverted:
            continue
        key = reverted.lower()
        original = by_hash.get(key)
        if original is None:
            for short, candidate in by_short.items():
                if key.startswith(short) or short.startswith(key):
                    original = candidate
                    break
        if original is None:
            # Match by stripped description.
            target = strip_pr_suffix(item.description).lower()
            for candidate in parsed:
                if candidate.raw.hash == item.raw.hash:
                    continue
                if strip_pr_suffix(candidate.description).lower() == target:
                    original = candidate
                    break
        if original is not None:
            drop.add(item.raw.hash)
            drop.add(original.raw.hash)
    return [item for item in parsed if item.raw.hash not in drop]


def attach_pull_requests(
    parsed: list[ParsedCommit],
    pr_by_sha: dict[str, list[PullRequest]],
) -> dict[str, list[PullRequest]]:
    """Map commit hash → PRs using API data plus numbers parsed from messages."""
    attached: dict[str, list[PullRequest]] = {}
    for item in parsed:
        sha = item.raw.hash
        prs = list(pr_by_sha.get(sha, []))
        if not prs:
            prs = list(pr_by_sha.get(sha.lower(), []))
        attached[sha] = prs
    return attached


def dedupe_by_pr(
    parsed: list[ParsedCommit],
    prs_for: dict[str, list[PullRequest]],
) -> list[tuple[ParsedCommit, PullRequest | None, list[ParsedCommit]]]:
    """Keep one representative per pull request, merging hashes.

    Returns tuples of (representative, pr_or_none, grouped_commits).
    Commits without a PR stay as individual entries.
    """
    groups: dict[int, list[ParsedCommit]] = {}
    loners: list[ParsedCommit] = []
    pr_objects: dict[int, PullRequest] = {}
    for item in parsed:
        prs = prs_for.get(item.raw.hash) or []
        number: int | None = None
        pr_obj: PullRequest | None = None
        if prs:
            pr_obj = prs[0]
            number = pr_obj.number
            pr_objects[number] = pr_obj
        elif item.pr_numbers:
            number = item.pr_numbers[0]
        if number is None:
            loners.append(item)
            continue
        groups.setdefault(number, []).append(item)
        if pr_obj is not None:
            pr_objects[number] = pr_obj
    result: list[tuple[ParsedCommit, PullRequest | None, list[ParsedCommit]]] = []
    seen_hashes: set[str] = set()
    for item in parsed:
        if item.raw.hash in seen_hashes:
            continue
        prs = prs_for.get(item.raw.hash) or []
        number = prs[0].number if prs else (item.pr_numbers[0] if item.pr_numbers else None)
        if number is None:
            seen_hashes.add(item.raw.hash)
            result.append((item, None, [item]))
            continue
        group = groups.get(number, [item])
        for member in group:
            seen_hashes.add(member.raw.hash)
        representative = _pick_representative(group)
        result.append((representative, pr_objects.get(number), group))
    return result


def _pick_representative(group: list[ParsedCommit]) -> ParsedCommit:
    """Prefer a conventional commit, then the earliest."""
    conventional = [item for item in group if item.is_conventional]
    pool = conventional or group
    return min(pool, key=lambda item: item.raw.author_date)


def author_handle(commit: Commit, pr: PullRequest | None) -> str:
    if pr is not None and pr.author:
        return pr.author if pr.author.startswith("@") else f"@{pr.author}"
    name = commit.author_name.strip()
    return name

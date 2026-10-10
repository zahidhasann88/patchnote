from __future__ import annotations

from datetime import datetime, timezone

from patchnote.classify import build_changelog
from patchnote.config import parse_config
from patchnote.conventional import parse_commit
from patchnote.filters import collapse_reverts, dedupe_by_pr, is_bot_commit, is_merge_noise
from patchnote.model import Commit, PullRequest

WHEN = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def c(
    subject: str, n: int, body: str = "", author: str = "Ada", email: str = "ada@ex.com"
) -> Commit:
    return Commit(
        hash=f"{n:040x}",
        short_hash=f"{n:040x}"[:7],
        author_name=author,
        author_email=email,
        author_date=WHEN,
        parents=[f"{n - 1:040x}"] if n > 1 else [],
        subject=subject,
        body=body,
    )


def test_skip_changelog_pattern() -> None:
    config = parse_config({})
    commits = [
        c("feat: secret [skip changelog]", 1),
        c("feat: public", 2),
    ]
    log = build_changelog(commits, config, from_ref="", to_ref="HEAD", version="1")
    assert log.entry_count == 1
    assert "public" in log.all_entries[0].summary.lower()


def test_merge_noise_skipped() -> None:
    merge = Commit(
        hash="b" * 40,
        short_hash="bbbbbbb",
        author_name="Ada",
        author_email="a@e.com",
        author_date=WHEN,
        parents=["a" * 40, "c" * 40],
        subject="Merge branch 'foo' into 'main'",
        body="",
    )
    assert is_merge_noise(merge)
    config = parse_config({})
    log = build_changelog(
        [merge, c("feat: real", 3)], config, from_ref="", to_ref="HEAD", version="1"
    )
    assert log.entry_count == 1


def test_revert_collapses_with_original() -> None:
    original = c("feat: add widgets", 1)
    revert = c(
        'Revert "feat: add widgets"',
        2,
        body=f"This reverts commit {original.hash}.",
    )
    parsed = [parse_commit(original), parse_commit(revert)]
    kept = collapse_reverts(parsed)
    assert kept == []


def test_revert_without_original_kept() -> None:
    revert = c('Revert "feat: add widgets"', 2, body="This reverts commit deadbeef.")
    parsed = [parse_commit(revert)]
    kept = collapse_reverts(parsed)
    assert len(kept) == 1


def test_dedupe_same_pr() -> None:
    a = parse_commit(c("feat: part one", 1))
    b = parse_commit(c("feat: part two", 2))
    pr = PullRequest(number=5, title="feat: the feature", labels=["feature"], author="ada")
    grouped = dedupe_by_pr([a, b], {a.raw.hash: [pr], b.raw.hash: [pr]})
    assert len(grouped) == 1
    _, attached, members = grouped[0]
    assert attached is not None and attached.number == 5
    assert len(members) == 2


def test_bot_collapse() -> None:
    config = parse_config({})
    commits = [
        c(
            "chore(deps): bump foo",
            1,
            author="dependabot[bot]",
            email="dependabot@users.noreply.github.com",
        ),
        c(
            "chore(deps): bump bar",
            2,
            author="renovate[bot]",
            email="renovate@users.noreply.github.com",
        ),
        c("feat: humans", 3),
    ]
    log = build_changelog(commits, config, from_ref="", to_ref="HEAD", version="1")
    summaries = [e.summary.lower() for e in log.all_entries]
    assert any("dependency" in s for s in summaries)
    assert any("human" in s for s in summaries)
    assert log.entry_count == 2


def test_bot_skip() -> None:
    config = parse_config({"bots": {"skip": True, "collapse": False}})
    commits = [
        c(
            "chore(deps): bump",
            1,
            author="dependabot[bot]",
            email="dependabot@users.noreply.github.com",
        ),
        c("feat: humans", 2),
    ]
    log = build_changelog(commits, config, from_ref="", to_ref="HEAD", version="1")
    assert log.entry_count == 1


def test_is_bot_commit() -> None:
    from patchnote.filters import compile_patterns

    patterns = compile_patterns(["dependabot", r"\[bot\]"])
    bot = c("x", 1, author="dependabot[bot]", email="x@x")
    human = c("x", 2, author="Ada Lovelace", email="ada@ex.com")
    assert is_bot_commit(bot, patterns)
    assert not is_bot_commit(human, patterns)

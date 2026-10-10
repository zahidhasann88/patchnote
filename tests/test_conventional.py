from __future__ import annotations

from datetime import datetime, timezone

from patchnote.conventional import extract_pr_numbers, parse_commit, strip_pr_suffix
from patchnote.model import Commit

WHEN = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def make(subject: str, body: str = "", merge: bool = False) -> Commit:
    parents = ["abc", "def"] if merge else ["abc"]
    return Commit(
        hash="a" * 40,
        short_hash="abcdef1",
        author_name="Ada",
        author_email="ada@example.com",
        author_date=WHEN,
        parents=parents,
        subject=subject,
        body=body,
    )


def test_simple_feat() -> None:
    parsed = parse_commit(make("feat: add widgets"))
    assert parsed.type == "feat"
    assert parsed.scope is None
    assert parsed.description == "add widgets"
    assert not parsed.breaking
    assert parsed.is_conventional


def test_scope_and_bang() -> None:
    parsed = parse_commit(make("feat(api)!: remove v1 endpoint"))
    assert parsed.type == "feat"
    assert parsed.scope == "api"
    assert parsed.breaking
    assert parsed.description == "remove v1 endpoint"


def test_breaking_change_footer() -> None:
    parsed = parse_commit(
        make(
            "feat: rename Thing",
            "Details here.\n\nBREAKING CHANGE: Thing is now Stuff\nFixes #12",
        )
    )
    assert parsed.breaking
    keys = {f.key: f.value for f in parsed.footers}
    assert "Thing is now Stuff" in keys["BREAKING CHANGE"]
    assert parsed.pr_numbers == []  # Fixes #12 references an issue, not a PR


def test_breaking_hyphen_footer() -> None:
    parsed = parse_commit(make("fix: x", "BREAKING-CHANGE: gone"))
    assert parsed.breaking


def test_uppercase_type_normalized() -> None:
    parsed = parse_commit(make("FEAT: Add Widgets"))
    assert parsed.type == "feat"
    assert parsed.description == "Add Widgets"


def test_lenient_missing_space_after_colon() -> None:
    parsed = parse_commit(make("fix:typo in docs"))
    assert parsed.type == "fix"
    assert parsed.description == "typo in docs"


def test_non_conventional() -> None:
    parsed = parse_commit(make("oops I forgot the prefix"))
    assert parsed.type is None
    assert parsed.description == "oops I forgot the prefix"
    assert not parsed.is_conventional


def test_revert_quoted_subject() -> None:
    parsed = parse_commit(
        make(
            'Revert "feat: add widgets"',
            "This reverts commit abcdef1234567890.",
        )
    )
    assert parsed.type == "revert"
    assert parsed.description == "feat: add widgets"
    assert parsed.reverted_hash == "abcdef1234567890"


def test_revert_type() -> None:
    parsed = parse_commit(make("revert: feat: add widgets", "This reverts commit deadbeef."))
    assert parsed.type == "revert"
    assert parsed.reverted_hash == "deadbeef"


def test_empty_scope() -> None:
    parsed = parse_commit(make("feat(): nothing"))
    assert parsed.type == "feat"
    assert parsed.scope is None


def test_squash_pr_number() -> None:
    parsed = parse_commit(make("feat: add widgets (#42)"))
    assert parsed.pr_numbers == [42]


def test_merge_pr_subject() -> None:
    parsed = parse_commit(make("Merge pull request #99 from alice/branch"))
    assert 99 in parsed.pr_numbers


def test_extract_pr_numbers_unique() -> None:
    assert extract_pr_numbers("foo (#1) bar #1 baz #2") == [1, 2]


def test_strip_pr_suffix() -> None:
    assert strip_pr_suffix("Add widgets (#12)") == "Add widgets"


def test_odd_whitespace_around_colon() -> None:
    parsed = parse_commit(make("chore : ignore me"))
    assert parsed.type == "chore"


def test_multiline_body_without_footers() -> None:
    parsed = parse_commit(make("docs: readme", "More words\n\nStill body"))
    assert parsed.body.startswith("More words")
    assert parsed.footers == []

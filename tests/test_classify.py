from __future__ import annotations

from datetime import datetime, timezone

from patchnote.classify import build_changelog, classify_one, heuristic_section
from patchnote.config import parse_config
from patchnote.conventional import parse_commit
from patchnote.model import Commit, Confidence, EntrySource, PullRequest

WHEN = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)


def sha(n: int) -> str:
    return f"{n:040x}"


def make_commit(subject: str, n: int = 1, body: str = "", author: str = "Ada") -> Commit:
    return Commit(
        hash=sha(n),
        short_hash=sha(n)[:7],
        author_name=author,
        author_email=f"{author.lower()}@example.com",
        author_date=WHEN,
        parents=[sha(n - 1)] if n else [],
        subject=subject,
        body=body,
    )


def test_labels_override_conventional() -> None:
    config = parse_config({})
    parsed = parse_commit(make_commit("feat: something that is actually a bug"))
    pr = PullRequest(number=7, title="fix the thing", labels=["bug"], author="ada")
    entry = classify_one(parsed, config, pr, [parsed])
    assert entry is not None
    assert entry.section == "Fixed"
    assert entry.source is EntrySource.LABEL


def test_conventional_maps_feat_to_added() -> None:
    config = parse_config({})
    parsed = parse_commit(make_commit("feat(parser): add ranges"))
    entry = classify_one(parsed, config, None, [parsed])
    assert entry is not None
    assert entry.section == "Added"
    assert entry.scope == "parser"
    assert entry.source is EntrySource.CONVENTIONAL
    assert entry.summary.startswith("Add") or entry.summary.startswith("add")


def test_heuristics_low_confidence() -> None:
    config = parse_config({})
    parsed = parse_commit(make_commit("fix the crash on empty input"))
    entry = classify_one(parsed, config, None, [parsed])
    assert entry is not None
    assert entry.section == "Fixed"
    assert entry.confidence is Confidence.LOW
    assert entry.source is EntrySource.HEURISTIC


def test_unknown_goes_to_other() -> None:
    config = parse_config({})
    parsed = parse_commit(make_commit("tweak timing of retries"))
    # "tweak" is in Changed heuristics
    entry = classify_one(parsed, config, None, [parsed])
    assert entry is not None
    assert entry.section == "Changed"


def test_truly_unknown_other() -> None:
    config = parse_config({})
    parsed = parse_commit(make_commit("misc housekeeping of leftovers"))
    entry = classify_one(parsed, config, None, [parsed])
    assert entry is not None
    assert entry.section == "Other"
    assert entry.source is EntrySource.OTHER


def test_breaking_surfaces_in_own_section() -> None:
    config = parse_config({})
    commits = [
        make_commit("feat!: drop python 3.9", 1, "BREAKING CHANGE: 3.9 is no longer supported"),
        make_commit("feat: add widgets", 2),
    ]
    log = build_changelog(commits, config, from_ref="v0.1.0", to_ref="HEAD", version="0.2.0")
    assert len(log.breaking) == 1
    assert "Added" in log.sections
    assert log.breaking[0].section == "Breaking Changes"


def test_skip_types_by_default() -> None:
    config = parse_config({})
    commits = [
        make_commit("chore: ignore me", 1),
        make_commit("docs: readme", 2),
        make_commit("feat: keep me", 3),
        make_commit("ci: pipeline", 4),
        make_commit("test: coverage", 5),
    ]
    log = build_changelog(commits, config, from_ref="", to_ref="HEAD", version="1.0.0")
    assert log.entry_count == 1
    assert (
        log.all_entries[0].summary.lower().startswith("keep")
        or "Keep" in log.all_entries[0].summary
    )


def test_include_all_keeps_chores() -> None:
    config = parse_config({})
    commits = [make_commit("chore: bump", 1), make_commit("feat: x", 2)]
    log = build_changelog(
        commits, config, from_ref="", to_ref="HEAD", version="1.0.0", include_all=True
    )
    assert log.entry_count == 2


def test_label_rescues_skipped_type() -> None:
    config = parse_config({})
    parsed = parse_commit(make_commit("docs: rewrite intro"))
    pr = PullRequest(number=3, title="docs: rewrite intro", labels=["enhancement"])
    entry = classify_one(parsed, config, pr, [parsed], include_all=False)
    assert entry is not None
    assert entry.section == "Added"


def test_sentence_case_and_no_trailing_period() -> None:
    config = parse_config({})
    parsed = parse_commit(make_commit("feat: add widgets."))
    entry = classify_one(parsed, config, None, [parsed])
    assert entry is not None
    assert entry.summary == "Add widgets"


def test_heuristic_section_security() -> None:
    config = parse_config({})
    section, conf = heuristic_section("plug xss in login form", config)
    assert section == "Security"
    assert conf is Confidence.LOW


def test_conventional_style_grouping() -> None:
    config = parse_config({"style": "conventional"})
    commits = [make_commit("feat: x", 1), make_commit("fix: y", 2)]
    log = build_changelog(commits, config, from_ref="", to_ref="HEAD", version="1.0.0")
    assert "Features" in log.sections
    assert "Bug Fixes" in log.sections


def test_pr_title_used_as_summary() -> None:
    config = parse_config({})
    parsed = parse_commit(make_commit("feat: wip"))
    pr = PullRequest(number=8, title="feat: add the real thing", labels=["feature"])
    entry = classify_one(parsed, config, pr, [parsed])
    assert entry is not None
    assert "real thing" in entry.summary.lower()
    assert entry.pr_number == 8

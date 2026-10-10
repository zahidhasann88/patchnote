from __future__ import annotations

from datetime import date
from pathlib import Path

from patchnote.classify import build_changelog
from patchnote.config import parse_config
from patchnote.model import (
    Changelog,
    ChangelogEntry,
    Confidence,
    Contributor,
    EntrySource,
    OutputFormat,
    Style,
)
from patchnote.render import render
from patchnote.render.github_release import render_github_release
from patchnote.render.json import render_json
from patchnote.render.markdown import format_entry, render_markdown
from tests.test_classify import make_commit

GOLDEN = Path(__file__).resolve().parent / "golden"


def sample_changelog() -> Changelog:
    added = ChangelogEntry(
        id="pr-1",
        summary="Add widgets",
        original_summary="feat: add widgets",
        section="Added",
        scope="core",
        pr_number=1,
        pr_url="https://github.com/acme/w/pull/1",
        authors=["@ada"],
        hashes=["a" * 40],
        short_hashes=["aaaaaaa"],
        confidence=Confidence.HIGH,
        source=EntrySource.CONVENTIONAL,
    )
    fixed = ChangelogEntry(
        id="sha-bbbbbbb",
        summary="Fix crash on empty input",
        original_summary="fix the crash on empty input",
        section="Fixed",
        authors=["Ada"],
        hashes=["b" * 40],
        short_hashes=["bbbbbbb"],
        confidence=Confidence.LOW,
        source=EntrySource.HEURISTIC,
    )
    breaking = ChangelogEntry(
        id="pr-2",
        summary="Remove the v1 endpoint",
        original_summary="feat!: remove the v1 endpoint",
        section="Breaking Changes",
        pr_number=2,
        pr_url="https://github.com/acme/w/pull/2",
        authors=["@ada"],
        hashes=["c" * 40],
        short_hashes=["ccccccc"],
        breaking=True,
        breaking_description="Clients must call v2",
        confidence=Confidence.HIGH,
        source=EntrySource.CONVENTIONAL,
    )
    return Changelog(
        version="1.4.0",
        from_ref="v1.3.0",
        to_ref="v1.4.0",
        date=date(2026, 10, 10),
        sections={"Added": [added], "Fixed": [fixed]},
        breaking=[breaking],
        contributors=[Contributor(login="ada", name="@ada", first_time=True)],
        compare_url="https://github.com/acme/w/compare/v1.3.0...v1.4.0",
        repository_url="https://github.com/acme/w",
        style=Style.KEEPACHANGELOG,
    )


def test_keepachangelog_markdown_golden() -> None:
    config = parse_config({})
    text = render_markdown(sample_changelog(), config)
    golden = GOLDEN / "keepachangelog.md"
    if not golden.exists():
        golden.parent.mkdir(parents=True, exist_ok=True)
        golden.write_text(text, encoding="utf-8")
    assert text == golden.read_text(encoding="utf-8")


def test_github_release_omits_h2() -> None:
    config = parse_config({})
    text = render_github_release(sample_changelog(), config)
    assert not text.startswith("## [")
    assert "### Breaking Changes" in text
    assert "### Added" in text
    golden = GOLDEN / "github-release.md"
    if not golden.exists():
        golden.write_text(text, encoding="utf-8")
    assert text == golden.read_text(encoding="utf-8")


def test_json_roundtrip() -> None:
    text = render_json(sample_changelog())
    assert '"version": "1.4.0"' in text
    assert "Add widgets" in text
    golden = GOLDEN / "changelog.json"
    if not golden.exists():
        golden.write_text(text, encoding="utf-8")
    assert text == golden.read_text(encoding="utf-8")


def test_conventional_style_headings() -> None:
    config = parse_config({"style": "conventional"})
    commits = [make_commit("feat: x", 1), make_commit("fix: y", 2)]
    log = build_changelog(commits, config, from_ref="", to_ref="HEAD", version="1.0.0")
    text = render(log, config, OutputFormat.MARKDOWN)
    assert "### Features" in text
    assert "### Bug Fixes" in text
    golden = GOLDEN / "conventional.md"
    if not golden.exists():
        golden.write_text(text, encoding="utf-8")
    assert text == golden.read_text(encoding="utf-8")


def test_format_entry_parts() -> None:
    config = parse_config({})
    entry = sample_changelog().sections["Added"][0]
    line = format_entry(entry, config)
    assert "**core:**" in line
    assert "[#1]" in line
    assert "@ada" in line
    assert "`aaaaaaa`" in line

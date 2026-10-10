from __future__ import annotations

from datetime import date

from patchnote.config import parse_config
from patchnote.model import Changelog, ChangelogEntry, Confidence, EntrySource, Style
from patchnote.render.changelog_file import prepend_release


def entry(summary: str, section: str = "Added") -> ChangelogEntry:
    return ChangelogEntry(
        id="e1",
        summary=summary,
        original_summary=summary,
        section=section,
        short_hashes=["abc1234"],
        hashes=["a" * 40],
        confidence=Confidence.HIGH,
        source=EntrySource.CONVENTIONAL,
    )


def cl(version: str, summary: str) -> Changelog:
    return Changelog(
        version=version,
        from_ref="v0.0.1",
        to_ref="HEAD",
        date=date(2026, 10, 10),
        sections={"Added": [entry(summary)]},
        compare_url=f"https://github.com/acme/w/compare/v0.0.1...v{version}",
        style=Style.KEEPACHANGELOG,
    )


HEADER = """# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-01-01

### Added

- First release

[Unreleased]: https://github.com/acme/w/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/acme/w/compare/v0.0.1...v0.1.0
"""


def test_prepend_inserts_after_unreleased() -> None:
    config = parse_config({})
    result = prepend_release(HEADER, cl("0.2.0", "Add bees"), config)
    assert result.index("## [Unreleased]") < result.index("## [0.2.0]")
    assert result.index("## [0.2.0]") < result.index("## [0.1.0]")
    assert "- First release" in result
    assert "Add bees" in result
    # Original 0.1.0 content preserved exactly.
    assert "### Added\n\n- First release" in result


def test_prepend_idempotent_same_version() -> None:
    config = parse_config({})
    once = prepend_release(HEADER, cl("0.2.0", "Add bees"), config)
    twice = prepend_release(once, cl("0.2.0", "Add bees and birds"), config)
    assert twice.count("## [0.2.0]") == 1
    assert "Add bees and birds" in twice
    assert "Add bees\n" not in twice or "Add bees and birds" in twice
    # Older section still present.
    assert "## [0.1.0]" in twice
    assert "- First release" in twice


def test_prepend_preserves_unrelated_text() -> None:
    config = parse_config({})
    existing = HEADER.replace("- First release", "- First release\n- Custom note that must survive")
    result = prepend_release(existing, cl("0.2.0", "Add bees"), config)
    assert "- Custom note that must survive" in result


def test_prepend_empty_file_writes_header() -> None:
    config = parse_config({})
    result = prepend_release("", cl("0.1.0", "Add bees"), config)
    assert result.startswith("# Changelog")
    assert "## [0.1.0]" in result


def test_prepend_replaces_without_duplicating_links() -> None:
    config = parse_config({})
    once = prepend_release(HEADER, cl("0.2.0", "Add bees"), config)
    twice = prepend_release(once, cl("0.2.0", "Add bees"), config)
    assert twice.count("[0.2.0]:") == 1

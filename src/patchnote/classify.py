"""Rule-based classification of commits into changelog sections."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime, timezone

from patchnote.config import Config
from patchnote.conventional import parse_commit, strip_pr_suffix
from patchnote.filters import (
    collapse_reverts,
    dedupe_by_pr,
    parse_and_filter,
)
from patchnote.model import (
    Changelog,
    ChangelogEntry,
    Commit,
    Confidence,
    Contributor,
    EntrySource,
    ParsedCommit,
    PullRequest,
    Style,
)

Clock = Callable[[], date]

HEURISTIC_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("Security", ("security", "cve-", "xss", "csrf", "vulnerability", "advisory")),
    ("Fixed", ("fix", "bug", "hotfix", "patch", "resolve", "repair")),
    ("Deprecated", ("deprecat",)),
    ("Removed", ("remove", "delete", "drop ", "dropped")),
    ("Added", ("add ", "added", "introduce", "implement", "create", "new ")),
    ("Changed", ("update", "change", "improve", "refactor", "enhance", "tweak")),
]


def sentence_case(text: str) -> str:
    if not text:
        return text
    return text[0].upper() + text[1:]


def apply_trailing_period(text: str, keep: bool) -> str:
    stripped = text.rstrip()
    if not stripped:
        return stripped
    if keep:
        return stripped if stripped.endswith(".") else stripped + "."
    return stripped[:-1] if stripped.endswith(".") else stripped


def clean_summary(text: str, config: Config) -> str:
    summary = strip_pr_suffix(text.strip())
    summary = re.sub(r"\s+", " ", summary).strip()
    if config.sentence_case:
        summary = sentence_case(summary)
    summary = apply_trailing_period(summary, config.trailing_period)
    return summary


def heuristic_section(text: str, config: Config) -> tuple[str, Confidence]:
    lowered = text.lower()
    for section, keywords in HEURISTIC_RULES:
        # Map keep-a-changelog names onto conventional section names if needed.
        mapped = _map_section_name(section, config)
        for keyword in keywords:
            if keyword in lowered:
                return mapped, Confidence.LOW
    return config.other_section, Confidence.LOW


def _map_section_name(keepachangelog: str, config: Config) -> str:
    if config.style is Style.KEEPACHANGELOG:
        return keepachangelog
    mapping = {
        "Added": "Features",
        "Fixed": "Bug Fixes",
        "Changed": "Other",
        "Deprecated": "Other",
        "Removed": "Reverts",
        "Security": "Other",
    }
    return mapping.get(keepachangelog, config.other_section)


def _label_section(labels: list[str], config: Config) -> str | None:
    mapping = config.label_to_section()
    for label in labels:
        section = mapping.get(label.lower())
        if section:
            return section
    return None


def _type_section(ctype: str | None, config: Config) -> str | None:
    if not ctype:
        return None
    return config.type_to_section().get(ctype.lower())


def classify_one(
    parsed: ParsedCommit,
    config: Config,
    pr: PullRequest | None,
    group: list[ParsedCommit],
    *,
    include_all: bool = False,
) -> ChangelogEntry | None:
    if pr is not None and pr.title.strip():
        titled = parse_commit(parsed.raw.model_copy(update={"subject": pr.title, "body": ""}))
        if titled.is_conventional:
            parsed = titled.model_copy(
                update={
                    "breaking": parsed.breaking or titled.breaking,
                    "footers": parsed.footers,
                    "pr_numbers": parsed.pr_numbers,
                }
            )
    labels = list(pr.labels) if pr is not None else []
    label_section = _label_section(labels, config)
    type_section = _type_section(parsed.type, config)

    skip_by_type = (
        parsed.type is not None
        and parsed.type in config.skip_types
        and not include_all
        and label_section is None
    )

    breaking = parsed.breaking or any(item.breaking for item in group)
    if pr is not None:
        lowered_labels = {label.lower() for label in pr.labels}
        if lowered_labels & {"breaking", "breaking-change", "breaking change"}:
            breaking = True

    if skip_by_type and not breaking:
        return None

    if label_section and label_section.lower() not in {"breaking changes", "breaking"}:
        section = label_section
        source = EntrySource.LABEL
        confidence = Confidence.HIGH
    elif type_section:
        section = type_section
        source = EntrySource.CONVENTIONAL
        confidence = Confidence.HIGH
    elif parsed.is_conventional:
        section = config.other_section
        source = EntrySource.CONVENTIONAL
        confidence = Confidence.MEDIUM
    else:
        section, confidence = heuristic_section(parsed.description, config)
        source = EntrySource.HEURISTIC if section != config.other_section else EntrySource.OTHER

    if breaking:
        section = config.breaking_section

    summary_source = parsed.description
    if pr is not None and pr.title.strip():
        title_parsed = parse_commit(parsed.raw.model_copy(update={"subject": pr.title, "body": ""}))
        summary_source = title_parsed.description
    summary = clean_summary(summary_source, config)

    breaking_description = None
    for item in group:
        for footer in item.footers:
            if footer.key.upper().replace("-", " ") == "BREAKING CHANGE":
                breaking_description = footer.value
                break

    hashes = [item.raw.hash for item in group]
    short_hashes = [item.raw.short_hash for item in group]
    authors: list[str] = []
    seen_authors: set[str] = set()
    if pr is not None and pr.author:
        handle = pr.author if pr.author.startswith("@") else f"@{pr.author}"
        authors.append(handle)
        seen_authors.add(handle.lower())
    for item in group:
        name = item.raw.author_name.strip()
        key = name.lower()
        if name and key not in seen_authors:
            authors.append(name)
            seen_authors.add(key)

    pr_number = (
        pr.number if pr is not None else (parsed.pr_numbers[0] if parsed.pr_numbers else None)
    )
    pr_url = pr.url if pr is not None else None
    scope = parsed.scope
    identity = f"pr-{pr_number}" if pr_number is not None else f"sha-{parsed.raw.hash}"

    return ChangelogEntry(
        id=identity,
        summary=summary,
        original_summary=(pr.title if pr is not None else parsed.raw.subject).strip(),
        pr_body=pr.body if pr is not None else None,
        section=section,
        scope=scope,
        pr_number=pr_number,
        pr_url=pr_url,
        authors=authors,
        hashes=hashes,
        short_hashes=short_hashes,
        breaking=breaking,
        breaking_description=breaking_description,
        confidence=confidence,
        source=source,
        labels=labels,
        linked_issues=list(pr.linked_issues) if pr is not None else [],
    )


def collapse_bot_entries(
    bots: list[ParsedCommit],
    config: Config,
) -> ChangelogEntry | None:
    if not bots or not config.bots.collapse:
        return None
    count = len(bots)
    noun = "update" if count == 1 else "updates"
    summary = clean_summary(f"{count} dependency {noun}", config)
    return ChangelogEntry(
        id="bots-collapsed",
        summary=summary,
        original_summary=summary,
        section=config.bots.section,
        authors=[],
        hashes=[item.raw.hash for item in bots],
        short_hashes=[item.raw.short_hash for item in bots],
        confidence=Confidence.MEDIUM,
        source=EntrySource.BOT,
    )


def collect_contributors(
    entries: list[ChangelogEntry],
    parsed: list[ParsedCommit],
    first_time: set[str] | None = None,
) -> list[Contributor]:
    contribs: dict[str, Contributor] = {}
    for entry in entries:
        for author in entry.authors:
            key = author.lower()
            if key not in contribs:
                login = author[1:] if author.startswith("@") else None
                contribs[key] = Contributor(
                    login=login,
                    name=author,
                    first_time=bool(first_time and (author.lstrip("@").lower() in first_time)),
                    pr_count=1 if entry.pr_number else 0,
                )
            else:
                if entry.pr_number:
                    contribs[key].pr_count += 1
    for item in parsed:
        key = item.raw.author_name.strip().lower()
        if key and key not in contribs:
            contribs[key] = Contributor(
                name=item.raw.author_name.strip(),
                email=item.raw.author_email,
            )
    return sorted(contribs.values(), key=lambda c: c.name.lower())


def build_changelog(
    commits: list[Commit],
    config: Config,
    *,
    from_ref: str,
    to_ref: str,
    version: str | None,
    today: date | None = None,
    pr_by_sha: dict[str, list[PullRequest]] | None = None,
    include_all: bool = False,
    repository_url: str | None = None,
    compare_url: str | None = None,
    previous_tag: str | None = None,
    first_time_logins: set[str] | None = None,
    tag_name: str | None = None,
) -> Changelog:
    """Pure assembly of a changelog from already-fetched commits and PRs."""
    kept, bots = parse_and_filter(commits, config, include_all=include_all)
    kept = collapse_reverts(kept)
    pr_by_sha = pr_by_sha or {}
    prs_for: dict[str, list[PullRequest]] = {}
    for item in kept:
        sha = item.raw.hash
        prs_for[sha] = list(pr_by_sha.get(sha, []) or pr_by_sha.get(sha.lower(), []))

    grouped = dedupe_by_pr(kept, prs_for)
    entries: list[ChangelogEntry] = []
    for representative, pr, group in grouped:
        entry = classify_one(representative, config, pr, group, include_all=include_all)
        if entry is not None:
            if first_time_logins:
                entry.first_time_contributors = [
                    author
                    for author in entry.authors
                    if author.lstrip("@").lower() in first_time_logins
                ]
            entries.append(entry)

    bot_entry = collapse_bot_entries(bots, config) if not include_all else None
    if bot_entry is not None:
        entries.append(bot_entry)
    elif bots and (include_all or not config.bots.collapse):
        existing_hashes = {sha for entry in entries for sha in entry.hashes}
        for bot in bots:
            if bot.raw.hash in existing_hashes:
                continue
            entry = classify_one(bot, config, None, [bot], include_all=True)
            if entry is not None:
                entry.source = EntrySource.BOT
                entries.append(entry)

    breaking: list[ChangelogEntry] = []
    sections: dict[str, list[ChangelogEntry]] = {name: [] for name in config.section_order()}
    for entry in entries:
        if entry.breaking or entry.section == config.breaking_section:
            breaking.append(entry)
            continue
        sections.setdefault(entry.section, []).append(entry)

    # Drop empty sections (keep order by using section_order at render time).
    sections = {name: items for name, items in sections.items() if items}

    contributors: list[Contributor] = []
    if config.include_contributors:
        contributors = collect_contributors(entries, kept, first_time_logins)

    when = today or datetime.now(timezone.utc).date()
    return Changelog(
        version=version,
        from_ref=from_ref,
        to_ref=to_ref,
        date=when,
        sections=sections,
        breaking=breaking,
        contributors=contributors,
        compare_url=compare_url,
        previous_tag=previous_tag,
        repository_url=repository_url,
        style=config.style,
        tag_name=tag_name,
    )


def default_clock() -> date:
    return datetime.now(timezone.utc).date()

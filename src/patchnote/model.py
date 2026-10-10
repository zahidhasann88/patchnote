"""Domain models and error types for Patchnote."""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class PatchnoteError(Exception):
    """Base error. ``exit_code`` is used by the CLI."""

    exit_code = 3

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class UsageError(PatchnoteError):
    """Bad arguments or configuration the user can fix."""

    exit_code = 2


class ConfigError(UsageError):
    """Invalid configuration file."""

    exit_code = 2


class CheckViolationError(PatchnoteError):
    """``patchnote check`` found convention violations."""

    exit_code = 1

    def __init__(self, message: str, violations: list[str]) -> None:
        super().__init__(message)
        self.violations = violations


class GitError(PatchnoteError):
    """The git binary failed or the repository is unusable."""

    exit_code = 3


class RuntimeNetworkError(PatchnoteError):
    """A required network call failed (release publish, etc.)."""

    exit_code = 3


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class EntrySource(str, Enum):
    CONVENTIONAL = "conventional"
    LABEL = "label"
    HEURISTIC = "heuristic"
    OTHER = "other"
    BOT = "bot"
    REVERT = "revert"


class Style(str, Enum):
    KEEPACHANGELOG = "keepachangelog"
    CONVENTIONAL = "conventional"


class OutputFormat(str, Enum):
    MARKDOWN = "markdown"
    JSON = "json"
    GITHUB_RELEASE = "github-release"


class AiMode(str, Enum):
    NONE = "none"
    POLISH = "polish"
    SUMMARY = "summary"
    BOTH = "both"


class Commit(BaseModel):
    """A single git commit as returned by the git binary."""

    hash: str
    short_hash: str
    author_name: str
    author_email: str
    author_date: datetime
    parents: list[str] = Field(default_factory=list)
    subject: str
    body: str = ""

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1

    @property
    def message(self) -> str:
        if self.body.strip():
            return f"{self.subject}\n\n{self.body}"
        return self.subject


class Footer(BaseModel):
    key: str
    value: str


class ParsedCommit(BaseModel):
    """Conventional-commit parse result. ``type`` is None when unmatched."""

    type: str | None = None
    scope: str | None = None
    breaking: bool = False
    description: str
    body: str = ""
    footers: list[Footer] = Field(default_factory=list)
    raw: Commit
    reverted_hash: str | None = None
    pr_numbers: list[int] = Field(default_factory=list)

    @property
    def is_conventional(self) -> bool:
        return self.type is not None


class PullRequest(BaseModel):
    number: int
    title: str
    body: str | None = None
    labels: list[str] = Field(default_factory=list)
    author: str | None = None
    url: str = ""
    merged_at: datetime | None = None
    merge_commit_sha: str | None = None
    linked_issues: list[int] = Field(default_factory=list)


class Contributor(BaseModel):
    login: str | None = None
    name: str
    email: str | None = None
    first_time: bool = False
    pr_count: int = 0


class ChangelogEntry(BaseModel):
    """A single rendered changelog bullet."""

    id: str
    summary: str
    original_summary: str
    section: str
    scope: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    authors: list[str] = Field(default_factory=list)
    hashes: list[str] = Field(default_factory=list)
    short_hashes: list[str] = Field(default_factory=list)
    breaking: bool = False
    breaking_description: str | None = None
    confidence: Confidence = Confidence.HIGH
    source: EntrySource = EntrySource.CONVENTIONAL
    labels: list[str] = Field(default_factory=list)
    first_time_contributors: list[str] = Field(default_factory=list)
    linked_issues: list[int] = Field(default_factory=list)

    def to_ai_payload(self, *, include_bodies: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "summary": self.summary,
            "section": self.section,
            "scope": self.scope,
            "pr_number": self.pr_number,
            "breaking": self.breaking,
            "hashes": list(self.short_hashes),
        }
        if include_bodies:
            payload["original_summary"] = self.original_summary
        return payload


class Changelog(BaseModel):
    version: str | None = None
    from_ref: str
    to_ref: str
    date: date
    sections: dict[str, list[ChangelogEntry]] = Field(default_factory=dict)
    breaking: list[ChangelogEntry] = Field(default_factory=list)
    contributors: list[Contributor] = Field(default_factory=list)
    compare_url: str | None = None
    previous_tag: str | None = None
    repository_url: str | None = None
    overview: str | None = None
    style: Style = Style.KEEPACHANGELOG
    tag_name: str | None = None

    @property
    def all_entries(self) -> list[ChangelogEntry]:
        items: list[ChangelogEntry] = list(self.breaking)
        seen = {e.id for e in items}
        for entries in self.sections.values():
            for entry in entries:
                if entry.id not in seen:
                    items.append(entry)
                    seen.add(entry.id)
        return items

    @property
    def entry_count(self) -> int:
        return len(self.all_entries)

    def to_ai_payload(self, *, include_bodies: bool = False) -> dict[str, Any]:
        return {
            "version": self.version,
            "entries": [e.to_ai_payload(include_bodies=include_bodies) for e in self.all_entries],
        }

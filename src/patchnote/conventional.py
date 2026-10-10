"""Parse Conventional Commits, including breaking changes and odd formats."""

from __future__ import annotations

import re

from patchnote.model import Commit, Footer, ParsedCommit

# Spec: type(scope)!: description
# Be slightly lenient about whitespace around the colon.
HEADER_RE = re.compile(
    r"^(?P<type>[A-Za-z]+)"
    r"(?:\((?P<scope>[^)]*)\))?"
    r"(?P<breaking>!)?"
    r"\s*:\s+"
    r"(?P<description>.+)$"
)

# "feat : message" (space before colon) is non-spec; still try a fallback.
HEADER_LOOSE_RE = re.compile(
    r"^(?P<type>[A-Za-z]+)"
    r"(?:\((?P<scope>[^)]*)\))?"
    r"(?P<breaking>!)?"
    r"\s*:\s*"
    r"(?P<description>.+)$"
)

FOOTER_RE = re.compile(
    r"^(?P<key>BREAKING CHANGE|BREAKING-CHANGE|[A-Za-z0-9-]+)"
    r"(?P<sep>: | #)"
    r"(?P<value>.*)$"
)

REVERT_SUBJECT_RE = re.compile(r'^Revert\s+"(?P<subject>.*)"\s*$', re.IGNORECASE)
REVERT_HASH_RE = re.compile(
    r"This reverts commit (?P<hash>[0-9a-f]{7,40})\b",
    re.IGNORECASE,
)
PR_REF_RE = re.compile(r"(?:#|pull/|pulls/|issues/)(\d+)\b")
PR_PARENS_RE = re.compile(r"\(#(\d+)\)")
MERGE_PR_RE = re.compile(r"^Merge pull request #(\d+)\b", re.IGNORECASE)

KNOWN_TYPES = {
    "feat",
    "fix",
    "perf",
    "refactor",
    "docs",
    "style",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
}


def extract_pr_numbers(text: str) -> list[int]:
    """Return unique PR/issue numbers referenced in ``text``, in order."""
    found: list[int] = []
    seen: set[int] = set()
    for match in PR_PARENS_RE.finditer(text):
        number = int(match.group(1))
        if number not in seen:
            found.append(number)
            seen.add(number)
    merge = MERGE_PR_RE.match(text.strip().split("\n", 1)[0])
    if merge:
        number = int(merge.group(1))
        if number not in seen:
            found.append(number)
            seen.add(number)
    for match in PR_REF_RE.finditer(text):
        number = int(match.group(1))
        if number not in seen:
            found.append(number)
            seen.add(number)
    return found


def _parse_footers(body: str) -> tuple[str, list[Footer]]:
    """Split a conventional body into description body + git-style footers."""
    if not body.strip():
        return "", []
    lines = body.splitlines()
    # Footers are a trailing block separated by a blank line.
    # Walk from the end while lines look like footers.
    footer_start: int | None = None
    i = len(lines) - 1
    while i >= 0:
        stripped = lines[i].strip()
        if not stripped:
            if footer_start is not None:
                i -= 1
                continue
            break
        if FOOTER_RE.match(stripped) or (
            footer_start is not None and lines[i].startswith((" ", "\t"))
        ):
            footer_start = i
            i -= 1
            continue
        break
    if footer_start is None:
        return body.strip("\n"), []
    raw_body = "\n".join(lines[:footer_start]).strip("\n")
    footers: list[Footer] = []
    current: Footer | None = None
    for line in lines[footer_start:]:
        stripped = line.strip()
        if not stripped:
            continue
        match = FOOTER_RE.match(stripped)
        if match:
            key = match.group("key")
            value = match.group("value").strip()
            if match.group("sep") == " #":
                value = "#" + value if not value.startswith("#") else value
            current = Footer(key=key, value=value)
            footers.append(current)
        elif current is not None:
            current.value = f"{current.value} {stripped}".strip()
    return raw_body, footers


def _breaking_from_footers(footers: list[Footer]) -> tuple[bool, str | None]:
    for footer in footers:
        if footer.key.upper().replace("-", " ") == "BREAKING CHANGE":
            return True, footer.value or None
    return False, None


def _reverted_hash(subject: str, body: str) -> str | None:
    match = REVERT_HASH_RE.search(body) or REVERT_HASH_RE.search(subject)
    if match:
        return match.group("hash").lower()
    return None


def parse_commit(commit: Commit) -> ParsedCommit:
    """Parse a commit message into a Conventional Commits structure."""
    subject = commit.subject.strip()
    body = commit.body.strip("\n")
    match = HEADER_RE.match(subject) or HEADER_LOOSE_RE.match(subject)
    pr_numbers = extract_pr_numbers(f"{subject}\n{body}")

    if match:
        ctype = match.group("type").lower()
        scope_raw = match.group("scope")
        scope = scope_raw.strip() if scope_raw is not None and scope_raw.strip() else None
        bang = bool(match.group("breaking"))
        description = match.group("description").strip()
        parsed_body, footers = _parse_footers(body)
        footer_breaking, _ = _breaking_from_footers(footers)
        breaking = bang or footer_breaking
        reverted = _reverted_hash(subject, body)
        if ctype == "revert" and reverted is None:
            reverted = _reverted_hash(description, body)
        return ParsedCommit(
            type=ctype,
            scope=scope,
            breaking=breaking,
            description=description,
            body=parsed_body,
            footers=footers,
            raw=commit,
            reverted_hash=reverted,
            pr_numbers=pr_numbers,
        )

    revert_match = REVERT_SUBJECT_RE.match(subject)
    if revert_match:
        parsed_body, footers = _parse_footers(body)
        return ParsedCommit(
            type="revert",
            scope=None,
            breaking=False,
            description=revert_match.group("subject").strip(),
            body=parsed_body,
            footers=footers,
            raw=commit,
            reverted_hash=_reverted_hash(subject, body),
            pr_numbers=pr_numbers,
        )

    parsed_body, footers = _parse_footers(body)
    footer_breaking, _ = _breaking_from_footers(footers)
    return ParsedCommit(
        type=None,
        scope=None,
        breaking=footer_breaking,
        description=subject,
        body=parsed_body,
        footers=footers,
        raw=commit,
        reverted_hash=_reverted_hash(subject, body),
        pr_numbers=pr_numbers,
    )


def strip_pr_suffix(summary: str) -> str:
    """Remove a trailing ``(#123)`` used by GitHub squash merges."""
    return re.sub(r"\s*\(#\d+\)\s*$", "", summary).strip()

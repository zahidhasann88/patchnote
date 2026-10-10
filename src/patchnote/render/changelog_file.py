"""Insert or replace a version section in an existing Keep a Changelog file."""

from __future__ import annotations

import re
from pathlib import Path

from patchnote.config import Config
from patchnote.model import Changelog
from patchnote.render.markdown import render_section_body

VERSION_HEADER_RE = re.compile(
    r"^## \[([^\]\n]+)\](?:\s*-\s*\d{4}-\d{2}-\d{2})?\s*$",
    re.MULTILINE,
)
UNRELEASED_RE = re.compile(r"^## \[Unreleased\]\s*$", re.MULTILINE | re.IGNORECASE)
LINK_REF_RE = re.compile(r"^\[([^\]]+)\]:\s+\S+\s*$", re.MULTILINE)


def _section_bounds(text: str, start: int) -> tuple[int, int]:
    """Return [start, end) of a `## …` section starting at ``start``."""
    next_header = re.search(r"^## ", text[start + 3 :], re.MULTILINE)
    end = start + 3 + next_header.start() if next_header else len(text)
    return start, end


def _find_section(text: str, version: str) -> tuple[int, int] | None:
    for match in VERSION_HEADER_RE.finditer(text):
        if match.group(1).lower() == version.lower():
            return _section_bounds(text, match.start())
    if version.lower() == "unreleased":
        unreleased = UNRELEASED_RE.search(text)
        if unreleased:
            return _section_bounds(text, unreleased.start())
    return None


def _split_link_footer(text: str) -> tuple[str, str]:
    """Peel a trailing Keep-a-Changelog link-reference block off ``text``."""
    lines = text.splitlines(keepends=True)
    idx = len(lines)
    saw_link = False
    while idx > 0:
        stripped = lines[idx - 1].strip()
        if not stripped:
            idx -= 1
            continue
        if LINK_REF_RE.match(stripped):
            saw_link = True
            idx -= 1
            continue
        break
    if not saw_link:
        return text, ""
    body = "".join(lines[:idx])
    footer = "".join(lines[idx:])
    return body, footer


def _upsert_link(footer: str, label: str, url: str) -> str:
    pattern = re.compile(rf"^\[{re.escape(label)}\]:\s+\S+\s*$", re.MULTILINE)
    line = f"[{label}]: {url}\n"
    if pattern.search(footer):
        return pattern.sub(line.rstrip(), footer, count=1)
    if footer and not footer.endswith("\n"):
        footer += "\n"
    return footer + line


def prepend_release(
    existing: str,
    changelog: Changelog,
    config: Config,
) -> str:
    """Insert ``changelog`` after the file header / Unreleased section.

    Running twice for the same version replaces the existing section instead of
    duplicating it. Everything else in the file is preserved byte-for-byte
    outside the replaced span and the compare-link footer.
    """
    section = render_section_body(changelog, config).rstrip() + "\n"
    # Drop the compare link from the section; we maintain those at file bottom.
    section_lines = [
        line for line in section.splitlines(keepends=True) if not LINK_REF_RE.match(line.strip())
    ]
    section = "".join(section_lines)
    if not section.endswith("\n"):
        section += "\n"

    if not existing.strip():
        header = config.header.rstrip() + "\n\n" if config.header else ""
        body = header + section
        footer = ""
        if changelog.compare_url:
            footer = _upsert_link(footer, changelog.version or "Unreleased", changelog.compare_url)
        return body.rstrip() + "\n\n" + footer if footer else body

    newline = "\r\n" if "\r\n" in existing else "\n"
    section = section.replace("\n", newline).rstrip("\r\n") + newline * 2
    body, link_footer = _split_link_footer(existing)
    version = changelog.version or "Unreleased"
    found = _find_section(body, version)
    if found is not None:
        start, end = found
        if end == len(body) and link_footer.startswith(("\n", "\r")):
            section = section.rstrip("\r\n") + newline
        body = body[:start] + section + body[end:]
    else:
        unreleased = _find_section(body, "Unreleased")
        first_version = VERSION_HEADER_RE.search(body)
        if unreleased is not None:
            at = unreleased[1]
        elif first_version is not None:
            at = first_version.start()
        else:
            at = len(body)
        prefix, suffix = body[:at], body[at:]
        separator = "" if not prefix or prefix.endswith(newline * 2) else newline * 2
        body = prefix + separator + section + suffix
    if changelog.compare_url:
        label = changelog.version or "Unreleased"
        pattern = re.compile(rf"^\[{re.escape(label)}\]:[^\r\n]*(?:\r?\n|$)", re.MULTILINE)
        line = f"[{label}]: {changelog.compare_url}{newline}"
        if pattern.search(link_footer):
            link_footer = pattern.sub(lambda _match: line, link_footer, count=1)
        else:
            if link_footer and not link_footer.endswith(("\n", "\r")):
                link_footer += newline
            link_footer += line
    return body + link_footer


def write_prepend(path: Path, changelog: Changelog, config: Config) -> str:
    existing = path.read_bytes().decode("utf-8") if path.exists() else ""
    updated = prepend_release(existing, changelog, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(updated.encode("utf-8"))
    return updated

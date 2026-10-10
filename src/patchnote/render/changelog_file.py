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

    text = existing
    if not text.endswith("\n"):
        text += "\n"
    body, link_footer = _split_link_footer(text)
    version = changelog.version or "Unreleased"
    found = _find_section(body, version)
    if found is not None:
        start, end = found
        # Preserve a single blank line after the replaced section.
        new_body = body[:start] + section
        rest = body[end:]
        if (
            rest
            and not rest.startswith("\n")
            and not section.endswith("\n\n")
            and not rest.startswith("##")
        ):
            new_body += "\n"
        if rest.startswith("\n"):
            rest = rest.lstrip("\n")
            new_body += "\n" + rest
        else:
            if rest:
                new_body += "\n" + rest if not new_body.endswith("\n") else rest
        body = new_body
    else:
        unreleased = _find_section(body, "Unreleased")
        if unreleased is not None:
            _, end = unreleased
            insert_at = end
            prefix = body[:insert_at].rstrip() + "\n\n"
            suffix = body[insert_at:].lstrip("\n")
            body = prefix + section + ("\n" + suffix if suffix else "")
        else:
            # After the first heading block / intro paragraph.
            first_version = VERSION_HEADER_RE.search(body)
            if first_version:
                at = first_version.start()
                prefix = body[:at]
                if prefix and not prefix.endswith("\n\n"):
                    prefix = prefix.rstrip() + "\n\n"
                body = prefix + section + "\n" + body[at:].lstrip("\n")
            else:
                header = config.header.rstrip() + "\n\n" if config.header else ""
                body = body.rstrip() + "\n\n" + section if body.strip() else header + section

    if changelog.compare_url:
        link_footer = _upsert_link(
            link_footer, changelog.version or "Unreleased", changelog.compare_url
        )
    result = body.rstrip() + "\n"
    if link_footer.strip():
        if not link_footer.startswith("\n"):
            result += "\n"
        result += link_footer if link_footer.endswith("\n") else link_footer + "\n"
    return result


def write_prepend(path: Path, changelog: Changelog, config: Config) -> str:
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    updated = prepend_release(existing, changelog, config)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding="utf-8")
    return updated

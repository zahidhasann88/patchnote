"""GitHub Release body: markdown without the H2 version heading or compare link."""

from __future__ import annotations

from patchnote.config import Config
from patchnote.model import Changelog
from patchnote.render.markdown import format_entry, iter_ordered_sections


def render_github_release(changelog: Changelog, config: Config) -> str:
    lines: list[str] = []
    if changelog.overview:
        lines.append(changelog.overview.rstrip())
        lines.append("")
    if changelog.breaking:
        lines.append(f"### {config.breaking_section}")
        lines.append("")
        for entry in changelog.breaking:
            lines.append(f"- {format_entry(entry, config)}")
            if entry.breaking_description:
                lines.append(f"  - **BREAKING CHANGE:** {entry.breaking_description}")
        lines.append("")
    for name, entries in iter_ordered_sections(changelog, config):
        lines.append(f"### {name}")
        lines.append("")
        for entry in entries:
            lines.append(f"- {format_entry(entry, config)}")
        lines.append("")
    if changelog.contributors and config.include_contributors:
        lines.append("### Contributors")
        lines.append("")
        for person in changelog.contributors:
            suffix = " (first-time contributor)" if person.first_time else ""
            lines.append(f"- {person.name}{suffix}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"

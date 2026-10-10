"""Markdown rendering (Keep a Changelog and conventional styles)."""

from __future__ import annotations

from collections.abc import Iterator
from importlib import resources
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

from patchnote.config import Config
from patchnote.model import Changelog, ChangelogEntry

PACKAGE_TEMPLATES = resources.files("patchnote.render") / "templates"


def _env(search_path: Path | None = None) -> Environment:
    if search_path is not None:
        loader = FileSystemLoader(str(search_path))
        env = Environment(
            loader=loader,
            autoescape=select_autoescape(default=False),
            trim_blocks=True,
            lstrip_blocks=True,
            undefined=StrictUndefined,
            keep_trailing_newline=True,
        )
        return env
    # Load from package data as a string — FileSystemLoader cannot always see
    # importlib resources. We still expose an Environment for custom templates.
    env = Environment(
        autoescape=select_autoescape(default=False),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    return env


def format_entry(entry: ChangelogEntry, config: Config) -> str:
    parts: list[str] = []
    if entry.scope:
        parts.append(f"**{entry.scope}:**")
    parts.append(entry.summary)
    extras: list[str] = []
    if config.include_pr_links and entry.pr_number is not None:
        if entry.pr_url:
            extras.append(f"[#{entry.pr_number}]({entry.pr_url})")
        else:
            extras.append(f"#{entry.pr_number}")
    if config.include_authors and entry.authors:
        extras.append(", ".join(entry.authors))
    if config.include_hashes and entry.short_hashes and entry.source.value != "bot":
        extras.append("`" + "`, `".join(entry.short_hashes[:3]) + "`")
    text = " ".join(parts)
    if extras:
        text = f"{text} ({'; '.join(extras)})"
    if entry.first_time_contributors:
        names = ", ".join(entry.first_time_contributors)
        text = f"{text} — first-time contributor: {names}"
    return text


def heading(changelog: Changelog) -> str:
    if changelog.version:
        return f"## [{changelog.version}] - {changelog.date.isoformat()}"
    return "## [Unreleased]"


def iter_ordered_sections(
    changelog: Changelog, config: Config
) -> Iterator[tuple[str, list[ChangelogEntry]]]:
    seen: set[str] = set()
    for name in config.section_order():
        items = changelog.sections.get(name) or []
        if items:
            yield name, items
            seen.add(name)
    for name, items in changelog.sections.items():
        if name not in seen and items:
            yield name, items


def render_section_body(changelog: Changelog, config: Config) -> str:
    """Render just the version section (no file-level header)."""
    lines: list[str] = []
    if changelog.overview:
        lines.append(changelog.overview.rstrip())
        lines.append("")
    lines.append(heading(changelog))
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
    if changelog.compare_url:
        label = changelog.version or "Unreleased"
        lines.append(f"[{label}]: {changelog.compare_url}")
        lines.append("")
    text = "\n".join(lines).rstrip() + "\n"
    return text


def default_template_source() -> str:
    return (PACKAGE_TEMPLATES / "changelog.md.j2").read_text(encoding="utf-8")


def render_with_template(
    changelog: Changelog,
    config: Config,
    *,
    include_header: bool,
    template_path: Path | None = None,
) -> str:
    def render_entry(entry: ChangelogEntry) -> str:
        return format_entry(entry, config)

    def iter_sections() -> Iterator[tuple[str, list[ChangelogEntry]]]:
        return iter_ordered_sections(changelog, config)

    context = {
        "changelog": changelog,
        "include_header": include_header,
        "header": config.header.rstrip() + "\n" if include_header else "",
        "footer": config.footer,
        "breaking_section": config.breaking_section,
        "include_contributors": config.include_contributors,
        "render_entry": render_entry,
        "iter_sections": iter_sections,
        "heading": heading(changelog),
    }
    if template_path is not None:
        env = _env(template_path.parent)
        template = env.get_template(template_path.name)
        return str(template.render(**context))
    env = _env(None)
    # Built-in path: prefer the deterministic Python renderer so golden tests
    # stay stable; custom templates still go through Jinja.
    return render_markdown(changelog, config, include_header=include_header)


def render_markdown(
    changelog: Changelog,
    config: Config,
    *,
    include_header: bool = False,
) -> str:
    if config.template:
        path = Path(config.template)
        return render_with_template(
            changelog, config, include_header=include_header, template_path=path
        )
    parts: list[str] = []
    if include_header and config.header:
        parts.append(config.header.rstrip() + "\n")
    parts.append(render_section_body(changelog, config))
    if config.footer:
        parts.append(config.footer.rstrip() + "\n")
    text = "\n".join(part.rstrip("\n") + "\n" for part in parts if part)
    return text if text.endswith("\n") else text + "\n"


def make_jinja_environment() -> Environment:
    """Exposed for callers that want to register extra filters."""
    env = _env(None)
    env.filters["changelog_entry"] = lambda entry, config: format_entry(entry, config)
    return env

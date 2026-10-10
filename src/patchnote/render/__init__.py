"""Render changelogs to markdown, JSON, or GitHub-release body."""

from __future__ import annotations

from patchnote.config import Config
from patchnote.model import Changelog, OutputFormat
from patchnote.render.github_release import render_github_release
from patchnote.render.json import render_json
from patchnote.render.markdown import render_markdown


def render(changelog: Changelog, config: Config, fmt: OutputFormat) -> str:
    if fmt is OutputFormat.JSON:
        return render_json(changelog)
    if fmt is OutputFormat.GITHUB_RELEASE:
        return render_github_release(changelog, config)
    return render_markdown(changelog, config, include_header=False)

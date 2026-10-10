"""Load, validate, and default ``patchnote.yaml`` configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from patchnote.model import ConfigError, Style

DEFAULT_CONFIG_NAME = "patchnote.yaml"

KEEPACHANGELOG_SECTIONS = (
    "Added",
    "Changed",
    "Deprecated",
    "Removed",
    "Fixed",
    "Security",
)

CONVENTIONAL_SECTIONS = (
    "Features",
    "Bug Fixes",
    "Performance",
    "Refactoring",
    "Documentation",
    "Tests",
    "Build",
    "Continuous Integration",
    "Chores",
    "Reverts",
    "Other",
)

DEFAULT_TYPE_TO_KEEPACHANGELOG: dict[str, str] = {
    "feat": "Added",
    "fix": "Fixed",
    "perf": "Changed",
    "refactor": "Changed",
    "docs": "Changed",
    "style": "Changed",
    "test": "Changed",
    "build": "Changed",
    "ci": "Changed",
    "chore": "Changed",
    "revert": "Removed",
}

DEFAULT_TYPE_TO_CONVENTIONAL: dict[str, str] = {
    "feat": "Features",
    "fix": "Bug Fixes",
    "perf": "Performance",
    "refactor": "Refactoring",
    "docs": "Documentation",
    "style": "Other",
    "test": "Tests",
    "build": "Build",
    "ci": "Continuous Integration",
    "chore": "Chores",
    "revert": "Reverts",
}

DEFAULT_LABEL_MAP: dict[str, str] = {
    "bug": "Fixed",
    "bugfix": "Fixed",
    "fix": "Fixed",
    "enhancement": "Added",
    "feature": "Added",
    "feat": "Added",
    "breaking": "Breaking Changes",
    "breaking-change": "Breaking Changes",
    "breaking change": "Breaking Changes",
    "deprecated": "Deprecated",
    "deprecation": "Deprecated",
    "removed": "Removed",
    "remove": "Removed",
    "security": "Security",
    "change": "Changed",
    "changed": "Changed",
    "documentation": "Changed",
    "dependencies": "Changed",
    "deps": "Changed",
}

DEFAULT_SKIP_TYPES = ("chore", "ci", "test", "docs", "build", "style")

DEFAULT_IGNORE_PATTERNS = (
    r"\[skip changelog\]",
    r"\[changelog skip\]",
    r"\[skip-changelog\]",
    r"^Merge branch ",
    r"^Merge remote-tracking branch ",
    r"^Merge pull request ",
)

DEFAULT_BOT_PATTERNS = (
    r"dependabot",
    r"renovate",
    r"github-actions",
    r"\[bot\]",
    r"^bot@",
)

CONVENTIONAL_TYPES = (
    "feat",
    "fix",
    "perf",
    "refactor",
    "docs",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
    "style",
)


class SectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    types: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)


class BotsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skip: bool = False
    collapse: bool = True
    patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_BOT_PATTERNS))
    section: str = "Changed"


class AiConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    api_key_env: str = "OPENAI_API_KEY"
    include_bodies: bool = False
    cache_dir: str = ".patchnote-cache"
    temperature: float = 0.2
    timeout_seconds: float = 60.0

    @field_validator("temperature")
    @classmethod
    def _temp_range(cls, value: float) -> float:
        if not 0.0 <= value <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        return value

    @field_validator("base_url")
    @classmethod
    def _strip_slash(cls, value: str) -> str:
        return value.rstrip("/")


class GitHubCfg(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token_env: str = "GITHUB_TOKEN"
    api_url: str = "https://api.github.com"
    graphql_url: str = "https://api.github.com/graphql"


class CheckConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    require_conventional: bool = True
    allow_merge: bool = True
    types: list[str] = Field(default_factory=lambda: list(CONVENTIONAL_TYPES))
    require_scope: bool = False


class Config(BaseModel):
    """Validated Patchnote configuration."""

    model_config = ConfigDict(extra="forbid")

    style: Style = Style.KEEPACHANGELOG
    tag_pattern: str = "v*"
    repository: str | None = None
    sections: dict[str, SectionConfig] = Field(default_factory=dict)
    label_map: dict[str, str] = Field(default_factory=lambda: dict(DEFAULT_LABEL_MAP))
    skip_types: list[str] = Field(default_factory=lambda: list(DEFAULT_SKIP_TYPES))
    ignore_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_IGNORE_PATTERNS))
    skip_merge_commits: bool = True
    bots: BotsConfig = Field(default_factory=BotsConfig)
    include_contributors: bool = True
    mark_first_time_contributors: bool = True
    trailing_period: bool = False
    sentence_case: bool = True
    include_hashes: bool = True
    include_authors: bool = True
    include_pr_links: bool = True
    header: str = (
        "# Changelog\n\n"
        "All notable changes to this project will be documented in this file.\n\n"
        "The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),\n"
        "and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).\n"
    )
    footer: str = ""
    template: str | None = None
    other_section: str = "Other"
    breaking_section: str = "Breaking Changes"
    ai: AiConfig = Field(default_factory=AiConfig)
    github: GitHubCfg = Field(default_factory=GitHubCfg)
    check: CheckConfig = Field(default_factory=CheckConfig)

    @field_validator("style", mode="before")
    @classmethod
    def _coerce_style(cls, value: Any) -> Any:
        if isinstance(value, str):
            lowered = value.lower().replace("-", "").replace("_", "")
            if lowered in {"keepachangelog", "keepachange"}:
                return Style.KEEPACHANGELOG
            if lowered == "conventional":
                return Style.CONVENTIONAL
        return value

    @field_validator("skip_types")
    @classmethod
    def _lower_types(cls, value: list[str]) -> list[str]:
        return [item.lower() for item in value]

    @model_validator(mode="after")
    def _default_sections(self) -> Config:
        if not self.sections:
            if self.style is Style.KEEPACHANGELOG:
                grouped: dict[str, list[str]] = {name: [] for name in KEEPACHANGELOG_SECTIONS}
                for ctype, section in DEFAULT_TYPE_TO_KEEPACHANGELOG.items():
                    grouped.setdefault(section, []).append(ctype)
                self.sections = {
                    name: SectionConfig(types=grouped.get(name, []), labels=[])
                    for name in KEEPACHANGELOG_SECTIONS
                }
            else:
                grouped = {name: [] for name in CONVENTIONAL_SECTIONS}
                for ctype, section in DEFAULT_TYPE_TO_CONVENTIONAL.items():
                    grouped.setdefault(section, []).append(ctype)
                self.sections = {
                    name: SectionConfig(types=grouped.get(name, []), labels=[])
                    for name in CONVENTIONAL_SECTIONS
                    if grouped.get(name)
                }
        return self

    def type_to_section(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        for section, cfg in self.sections.items():
            for ctype in cfg.types:
                mapping[ctype.lower()] = section
        return mapping

    def label_to_section(self) -> dict[str, str]:
        mapping = {key.lower(): value for key, value in self.label_map.items()}
        for section, cfg in self.sections.items():
            for label in cfg.labels:
                mapping[label.lower()] = section
        return mapping

    def section_order(self) -> list[str]:
        if self.style is Style.KEEPACHANGELOG:
            names = list(KEEPACHANGELOG_SECTIONS)
        else:
            names = list(CONVENTIONAL_SECTIONS)
        extra = [name for name in self.sections if name not in names]
        if self.other_section not in names and self.other_section not in extra:
            extra.append(self.other_section)
        return names + extra


def format_validation_error(exc: ValidationError) -> str:
    lines = ["Invalid configuration in patchnote.yaml:"]
    for err in exc.errors():
        loc = ".".join(str(part) for part in err.get("loc", ()))
        msg = err.get("msg", "invalid value")
        lines.append(f"  - {loc or '<root>'}: {msg}")
    return "\n".join(lines)


def load_yaml_file(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"Cannot read config file {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Cannot parse YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file {path} must contain a mapping at the top level")
    return data


def parse_config(data: dict[str, Any] | None = None) -> Config:
    try:
        return Config.model_validate(data or {})
    except ValidationError as exc:
        raise ConfigError(format_validation_error(exc)) from exc


def find_config_path(start: Path, explicit: Path | None = None) -> Path | None:
    if explicit is not None:
        if not explicit.is_file():
            raise ConfigError(f"Config file not found: {explicit}")
        return explicit
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for directory in [current, *current.parents]:
        candidate = directory / DEFAULT_CONFIG_NAME
        if candidate.is_file():
            return candidate
        if (directory / ".git").exists():
            break
    return None


def load_config(repo: Path, explicit: Path | None = None) -> Config:
    path = find_config_path(repo, explicit)
    if path is None:
        return parse_config({})
    return parse_config(load_yaml_file(path))


INIT_YAML = """\
# Patchnote configuration
# https://github.com/YOUR_NAME/patchnote
#
# All keys are optional. Uncomment and edit to override defaults.

# Grouping style: "keepachangelog" (Added/Fixed/...) or "conventional"
# (Features/Bug Fixes/...).
# style: keepachangelog

# Glob used to pick the previous release tag when --from is omitted.
# tag_pattern: "v*"

# Override the GitHub repository (owner/name). Auto-detected from `origin`.
# repository: YOUR_NAME/patchnote

# Conventional types and PR labels mapped into changelog sections.
# Labels take precedence over the conventional type of a commit.
# sections:
#   Added:
#     types: [feat]
#     labels: [enhancement, feature]
#   Changed:
#     types: [perf, refactor]
#     labels: [change]
#   Deprecated:
#     types: []
#     labels: [deprecated]
#   Removed:
#     types: []
#     labels: [removed]
#   Fixed:
#     types: [fix]
#     labels: [bug, bugfix]
#   Security:
#     types: []
#     labels: [security]

# Extra label → section mapping (merged with section labels above).
# label_map:
#   bug: Fixed
#   enhancement: Added
#   security: Security

# Conventional types omitted from the changelog unless --include-all.
# skip_types: [chore, ci, test, docs, build, style]

# Regexes matched against the commit subject. Matching commits are dropped.
# ignore_patterns:
#   - "\\\\[skip changelog\\\\]"
#   - "\\\\[changelog skip\\\\]"
#   - "^Merge branch "
#   - "^Merge pull request "

# skip_merge_commits: true

# bots:
#   skip: false          # drop bot commits entirely
#   collapse: true       # or fold them into one "N dependency updates" line
#   patterns:
#     - dependabot
#     - renovate
#     - "\\\\[bot\\\\]"
#   section: Changed

# include_contributors: true
# mark_first_time_contributors: true

# Summary cleanup.
# trailing_period: false   # strip a trailing "." from entries
# sentence_case: true      # capitalize the first character
# include_hashes: true
# include_authors: true
# include_pr_links: true

# other_section: Other
# breaking_section: Breaking Changes

# header: |
#   # Changelog
#
#   All notable changes to this project will be documented in this file.

# footer: ""

# Path (relative to the config file) of a custom Jinja2 markdown template.
# template: null

# ai:
#   enabled: false
#   base_url: https://api.openai.com/v1
#   model: gpt-4o-mini
#   api_key_env: OPENAI_API_KEY
#   include_bodies: false
#   cache_dir: .patchnote-cache
#   temperature: 0.2
#   timeout_seconds: 60

# github:
#   token_env: GITHUB_TOKEN
#   api_url: https://api.github.com
#   graphql_url: https://api.github.com/graphql

# check:
#   require_conventional: true
#   allow_merge: true
#   require_scope: false
#   types: [feat, fix, perf, refactor, docs, test, build, ci, chore, revert, style]
"""


def write_init_config(directory: Path, *, force: bool = False) -> Path:
    path = directory / DEFAULT_CONFIG_NAME
    if path.exists() and not force:
        raise ConfigError(f"{path} already exists (pass --force to overwrite)")
    path.write_text(INIT_YAML, encoding="utf-8")
    return path

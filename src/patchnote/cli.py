"""Typer CLI for Patchnote."""

from __future__ import annotations

import os
import sys
from datetime import date
from pathlib import Path

import httpx
import typer
from rich.console import Console
from rich.theme import Theme

from patchnote import __version__
from patchnote.ai import apply_ai, default_cache
from patchnote.ai.client import LLMClient
from patchnote.check import run_check
from patchnote.classify import build_changelog
from patchnote.config import Config, load_config, parse_config, write_init_config
from patchnote.github import (
    GitHubClient,
    enrich_or_empty,
    parse_github_nwo,
    repository_http_url,
    resolve_token,
)
from patchnote.github import (
    compare_url as gh_compare_url,
)
from patchnote.gitlog import (
    GitRunner,
    create_tag,
    discover_repo,
    list_commits,
    list_tags,
    ref_exists,
    remote_url,
    tag_date,
)
from patchnote.gitlog import (
    push_tag as git_push_tag,
)
from patchnote.model import (
    AiMode,
    Changelog,
    CheckViolationError,
    ConfigError,
    GitError,
    OutputFormat,
    PatchnoteError,
    Style,
    UsageError,
)
from patchnote.render import render
from patchnote.render.changelog_file import write_prepend
from patchnote.render.github_release import render_github_release

LAST_RENDERED: str = ""
LAST_CHANGELOG: Changelog | None = None

theme = Theme({"warning": "yellow", "error": "red", "ok": "green"})


def _console(no_color: bool, stderr: bool = False) -> Console:
    return Console(theme=theme, no_color=no_color, stderr=stderr, highlight=False)


class AppState:
    def __init__(self) -> None:
        self.config_path: Path | None = None
        self.repo: Path = Path.cwd()
        self.no_color: bool = False
        self.verbose: bool = False
        self.console: Console = _console(False)
        self.err: Console = _console(False, stderr=True)


state = AppState()
app = typer.Typer(
    name="patchnote",
    help="Generate changelogs and release notes from git history and pull requests.",
    no_args_is_help=True,
    add_completion=False,
    pretty_exceptions_enable=False,
    pretty_exceptions_show_locals=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"patchnote {__version__}")
        raise typer.Exit(0)


@app.callback()
def main(
    config: Path | None = typer.Option(
        None, "--config", help="Path to patchnote.yaml", exists=False, dir_okay=False
    ),
    repo: Path | None = typer.Option(
        None, "--repo", help="Path to the git repository", file_okay=False
    ),
    no_color: bool = typer.Option(False, "--no-color", help="Disable ANSI colours"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Verbose diagnostics"),
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show version and exit",
    ),
) -> None:
    state.config_path = config
    state.repo = repo or Path.cwd()
    state.no_color = no_color or not sys.stdout.isatty()
    state.verbose = verbose
    state.console = _console(state.no_color)
    state.err = _console(state.no_color, stderr=True)


def warn(message: str) -> None:
    state.err.print(f"[warning]warning:[/warning] {message}")


def die(exc: BaseException) -> None:
    if isinstance(exc, CheckViolationError):
        for line in exc.violations:
            state.err.print(f"[error]{line}[/error]")
        state.err.print(f"[error]{exc.message}[/error]")
        raise typer.Exit(exc.exit_code)
    if isinstance(exc, PatchnoteError):
        state.err.print(f"[error]error:[/error] {exc.message}")
        raise typer.Exit(exc.exit_code)
    if state.verbose:
        raise exc
    state.err.print(f"error: {type(exc).__name__}; use --verbose for details", markup=False)
    raise typer.Exit(3)


def load_runtime_config() -> tuple[Config, Path, GitRunner]:
    try:
        repo = discover_repo(state.repo)
    except GitError:
        # init does not need a repo; other commands do. Re-raise later.
        repo = state.repo.resolve()
        runner = GitRunner(repo)
        try:
            cfg = load_config(repo, state.config_path)
        except ConfigError as exc:
            die(exc)
            raise
        return cfg, repo, runner
    runner = GitRunner(repo)
    try:
        cfg = load_config(repo, state.config_path)
    except ConfigError as exc:
        die(exc)
        raise
    return cfg, repo, runner


def resolve_range(
    runner: GitRunner,
    config: Config,
    from_ref: str | None,
    to_ref: str | None,
) -> tuple[str | None, str, str | None]:
    to_name = to_ref or "HEAD"
    if not ref_exists(runner, to_name):
        raise UsageError(f"Unknown git ref: {to_name}")
    prev: str | None
    if from_ref:
        if not ref_exists(runner, from_ref):
            raise UsageError(f"Unknown git ref: {from_ref}")
        prev = from_ref
    else:
        prev = None
        for candidate in list_tags(runner, config.tag_pattern):
            if to_name != "HEAD" and candidate == to_name:
                continue
            if runner.run_ok(["merge-base", "--is-ancestor", candidate, to_name]):
                prev = candidate
                break
    return prev, to_name, prev


def detect_repo_slug(runner: GitRunner, config: Config) -> tuple[str, str] | None:
    if config.repository:
        parsed = parse_github_nwo(config.repository)
        if parsed:
            return parsed
    env_slug = os.environ.get("GITHUB_REPOSITORY")
    if env_slug:
        parsed = parse_github_nwo(env_slug)
        if parsed:
            return parsed
    url = remote_url(runner)
    if url:
        return parse_github_nwo(url)
    return None


def version_from_ref(to_ref: str, explicit: str | None) -> str | None:
    if explicit:
        return (
            explicit.lstrip("v")
            if explicit.startswith("v") and explicit[1:2].isdigit()
            else explicit
        )
    if to_ref != "HEAD" and to_ref.startswith("v") and to_ref[1:2].isdigit():
        return to_ref[1:]
    if to_ref != "HEAD":
        return to_ref
    return None


def tag_for_version(version: str) -> str:
    if version.startswith("v"):
        return version
    return f"v{version}"


def confirm_ai(config: Config, mode: AiMode, *, yes: bool, include_bodies: bool) -> bool:
    endpoint = config.ai.base_url
    model = config.ai.model
    bodies = (
        "titles, summaries, and PR bodies" if include_bodies else "titles and commit subjects only"
    )
    notice = (
        f"Patchnote is about to send classified changelog entries ({bodies}) "
        f"to {endpoint} (model {model}). "
        "API keys are read from the environment and are never logged. "
        "Commit messages are treated as untrusted data."
    )
    state.err.print(f"[warning]{notice}[/warning]")
    if yes:
        return True
    if not sys.stdin.isatty():
        raise UsageError("Refusing to call the LLM non-interactively without --yes")
    return typer.confirm("Proceed?", default=False)


def make_github_client(
    config: Config,
    slug: tuple[str, str] | None,
    token: str | None,
    http: httpx.Client,
) -> GitHubClient | None:
    if slug is None:
        return None
    owner, repo = slug
    return GitHubClient(
        http,
        token,
        owner,
        repo,
        api_url=config.github.api_url,
        graphql_url=config.github.graphql_url,
        warn=warn,
    )


def generate_changelog(
    *,
    from_ref: str | None,
    to_ref: str | None,
    version: str | None,
    style: Style | None,
    include_all: bool,
    token: str | None,
    ai_mode: AiMode,
    yes: bool,
    ai_include_bodies: bool,
    http: httpx.Client | None = None,
    clock: date | None = None,
) -> Changelog:
    config, repo, runner = load_runtime_config()
    if not runner.run_ok(["rev-parse", "--is-inside-work-tree"]):
        raise GitError(f"{repo} is not a git repository")
    if style is not None:
        config = config.model_copy(update={"style": style})
        # Rebuild default sections for the new style if the user didn't custom-set them.
        if style is Style.CONVENTIONAL and set(config.sections) == {
            "Added",
            "Changed",
            "Deprecated",
            "Removed",
            "Fixed",
            "Security",
        }:
            config = parse_config(
                config.model_dump(mode="python") | {"style": "conventional", "sections": {}}
            )
    prev, to_name, _ = resolve_range(runner, config, from_ref, to_ref)
    commits = list_commits(runner, prev, to_name)
    slug = detect_repo_slug(runner, config)
    token_value = resolve_token(config, token)
    owns_http = http is None
    client_http = http or httpx.Client()
    try:
        # No token → no network. Public unauthenticated GitHub calls are skipped
        # so a missing GITHUB_TOKEN never surprises CI with rate limits.
        gh = make_github_client(config, slug, token_value, client_http) if token_value else None
        pr_by_sha = enrich_or_empty(commits, gh, warn=warn) if gh is not None else {}
        first_time: set[str] = set()
        if gh is not None and config.mark_first_time_contributors and token_value:
            logins: set[str] = set()
            current_prs: set[int] = set()
            for pulls in pr_by_sha.values():
                for pr in pulls:
                    if pr.author:
                        logins.add(pr.author)
                    current_prs.add(pr.number)
            if logins:
                try:
                    first_time = gh.first_time_contributors(logins, current_prs)
                except (ValueError, TypeError, KeyError, AttributeError):
                    warn("Could not determine first-time contributors; keeping commit notes")
    finally:
        if owns_http:
            client_http.close()

    repo_url = None
    cmp_url = None
    if slug is not None:
        owner, name = slug
        repo_url = repository_http_url(owner, name)
        right = tag_for_version(version) if version else to_name
        if prev:
            cmp_url = gh_compare_url(owner, name, prev, right)

    resolved_version = version_from_ref(
        to_name if to_ref else (to_name if to_name != "HEAD" else "HEAD"), version
    )
    if to_name == "HEAD" and version is None:
        resolved_version = None
    elif version:
        resolved_version = (
            version[1:]
            if version.startswith("v") and len(version) > 1 and version[1].isdigit()
            else version
        )

    when = clock
    if when is None and to_name != "HEAD" and ref_exists(runner, to_name):
        when = tag_date(runner, to_name)
    changelog = build_changelog(
        commits,
        config,
        from_ref=prev or "",
        to_ref=to_name,
        version=resolved_version,
        today=when,
        pr_by_sha=pr_by_sha,
        include_all=include_all,
        repository_url=repo_url,
        compare_url=cmp_url,
        previous_tag=prev,
        first_time_logins=first_time or None,
        tag_name=tag_for_version(resolved_version) if resolved_version else None,
    )

    if ai_mode is not AiMode.NONE:
        include_bodies = ai_include_bodies or config.ai.include_bodies
        config.ai = config.ai.model_copy(
            update={
                "base_url": os.environ.get("PATCHNOTE_AI_BASE_URL") or config.ai.base_url,
                "model": os.environ.get("PATCHNOTE_AI_MODEL") or config.ai.model,
            }
        )
        if confirm_ai(config, ai_mode, yes=yes, include_bodies=include_bodies):
            api_key = os.environ.get(config.ai.api_key_env)
            # Also accept a generic override used by the GitHub Action.
            api_key = os.environ.get("PATCHNOTE_AI_API_KEY") or api_key
            base_url = os.environ.get("PATCHNOTE_AI_BASE_URL") or config.ai.base_url
            model = os.environ.get("PATCHNOTE_AI_MODEL") or config.ai.model
            ai_cfg = config.ai.model_copy(update={"base_url": base_url, "model": model})
            cache_dir = Path(ai_cfg.cache_dir)
            if not cache_dir.is_absolute():
                cache_dir = repo / cache_dir
            owns = http is None
            http_client = http or httpx.Client()
            try:
                llm = LLMClient(
                    http_client,
                    base_url=ai_cfg.base_url,
                    api_key=api_key,
                    timeout=ai_cfg.timeout_seconds,
                    temperature=ai_cfg.temperature,
                )
                changelog = apply_ai(
                    changelog,
                    ai_cfg,
                    ai_mode,
                    llm,
                    default_cache(cache_dir),
                    include_bodies=include_bodies,
                    warn=warn,
                )
            finally:
                if owns:
                    http_client.close()
    return changelog


def emit(
    changelog: Changelog,
    config: Config,
    fmt: OutputFormat,
    output: Path | None,
    prepend: bool,
    dry_run: bool,
) -> str:
    global LAST_RENDERED, LAST_CHANGELOG
    LAST_CHANGELOG = changelog
    text = render(changelog, config, fmt)
    LAST_RENDERED = text
    if dry_run:
        typer.echo(text, nl=not text.endswith("\n"))
        return text
    if output is not None:
        if prepend:
            if fmt is not OutputFormat.MARKDOWN:
                raise UsageError("--prepend is only supported with --format markdown")
            write_prepend(output, changelog, config)
            state.err.print(f"[ok]updated[/ok] {output}")
        else:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
            state.err.print(f"[ok]wrote[/ok] {output}")
        return text
    typer.echo(text, nl=not text.endswith("\n"))
    return text


@app.command()
def init(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing patchnote.yaml"),
) -> None:
    """Write a commented patchnote.yaml in the current directory."""
    try:
        path = write_init_config(Path.cwd(), force=force)
    except ConfigError as exc:
        die(exc)
        return
    state.err.print(f"[ok]wrote[/ok] {path}")


@app.command()
def generate(
    from_ref: str | None = typer.Option(
        None, "--from", help="Start ref (exclusive). Default: latest tag"
    ),
    to_ref: str | None = typer.Option(None, "--to", help="End ref (inclusive). Default: HEAD"),
    version: str | None = typer.Option(None, "--version", help="Version heading, e.g. 1.4.0"),
    format_name: OutputFormat = typer.Option(
        OutputFormat.MARKDOWN, "--format", help="Output format", case_sensitive=False
    ),
    output: Path | None = typer.Option(None, "--output", "-o", help="Write to this file"),
    ai: AiMode = typer.Option(
        AiMode.NONE, "--ai", help="Optional LLM rewrite", case_sensitive=False
    ),
    style: Style | None = typer.Option(
        None, "--style", help="Grouping style", case_sensitive=False
    ),
    include_all: bool = typer.Option(False, "--include-all", help="Do not skip chore/ci/test/docs"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print without writing --output"),
    prepend: bool = typer.Option(
        False, "--prepend", help="Insert into an existing Keep a Changelog file"
    ),
    token: str | None = typer.Option(
        None, "--token", help="GitHub token (else GITHUB_TOKEN)", envvar=None
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not prompt before calling an LLM"),
    ai_include_bodies: bool = typer.Option(
        False, "--ai-include-bodies", help="Opt in to sending PR bodies to the LLM"
    ),
) -> None:
    """Generate a changelog for a git revision range."""
    try:
        changelog = generate_changelog(
            from_ref=from_ref,
            to_ref=to_ref,
            version=version,
            style=style,
            include_all=include_all,
            token=token,
            ai_mode=ai,
            yes=yes,
            ai_include_bodies=ai_include_bodies,
        )
        config, _, _ = load_runtime_config()
        if style is not None:
            config = config.model_copy(update={"style": style})
        emit(changelog, config, format_name, output, prepend, dry_run)
    except PatchnoteError as exc:
        die(exc)
    except typer.Exit:
        raise
    except Exception as exc:
        die(exc)


@app.command()
def preview(
    include_all: bool = typer.Option(False, "--include-all"),
    style: Style | None = typer.Option(None, "--style", case_sensitive=False),
    ai: AiMode = typer.Option(AiMode.NONE, "--ai", case_sensitive=False),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Show notes for HEAD since the last tag without writing anything."""
    try:
        changelog = generate_changelog(
            from_ref=None,
            to_ref="HEAD",
            version=None,
            style=style,
            include_all=include_all,
            token=None,
            ai_mode=ai,
            yes=yes,
            ai_include_bodies=False,
        )
        config, _, _ = load_runtime_config()
        emit(changelog, config, OutputFormat.MARKDOWN, None, False, True)
    except PatchnoteError as exc:
        die(exc)
    except typer.Exit:
        raise
    except Exception as exc:
        die(exc)


@app.command()
def check(
    from_ref: str | None = typer.Option(None, "--from"),
    to_ref: str | None = typer.Option(None, "--to"),
    token: str | None = typer.Option(None, "--token"),
) -> None:
    """Lint commit messages in a range. Exit 1 when any violate the convention."""
    try:
        config, repo, runner = load_runtime_config()
        if not runner.run_ok(["rev-parse", "--is-inside-work-tree"]):
            raise GitError(f"{repo} is not a git repository")
        prev, to_name, _ = resolve_range(runner, config, from_ref, to_ref)
        commits = list_commits(runner, prev, to_name)
        slug = detect_repo_slug(runner, config)
        token_value = resolve_token(config, token)
        pr_by_sha = {}
        if slug is not None and token_value:
            with httpx.Client() as http:
                gh = make_github_client(config, slug, token_value, http)
                pr_by_sha = enrich_or_empty(commits, gh, warn=warn)
        run_check(commits, config, pr_by_sha=pr_by_sha)
        state.err.print(f"[ok]ok[/ok] {len(commits)} commit(s) match the convention")
    except CheckViolationError as exc:
        die(exc)
    except PatchnoteError as exc:
        die(exc)
    except Exception as exc:
        die(exc)


@app.command()
def release(
    version: str = typer.Option(..., "--version", help="Version to release, e.g. 1.4.0"),
    tag: bool = typer.Option(False, "--tag", help="Create an annotated git tag"),
    push_tag: bool = typer.Option(False, "--push-tag", help="Push the tag to origin"),
    push_release: bool = typer.Option(
        False, "--push-release", help="Create or update a GitHub Release"
    ),
    publish: bool = typer.Option(
        False, "--publish", help="Publish the GitHub Release (otherwise it stays a draft)"
    ),
    from_ref: str | None = typer.Option(None, "--from"),
    to_ref: str | None = typer.Option(None, "--to"),
    token: str | None = typer.Option(None, "--token"),
    include_all: bool = typer.Option(False, "--include-all"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Generate notes and optionally tag / open a GitHub Release draft."""
    try:
        _, _, release_runner = load_runtime_config()
        existing_tag = tag_for_version(version)
        release_to = to_ref or (
            existing_tag if ref_exists(release_runner, existing_tag) else "HEAD"
        )
        changelog = generate_changelog(
            from_ref=from_ref,
            to_ref=release_to,
            version=version,
            style=None,
            include_all=include_all,
            token=token,
            ai_mode=AiMode.NONE,
            yes=yes,
            ai_include_bodies=False,
        )
        config, _, runner = load_runtime_config()
        body = render_github_release(changelog, config)
        typer.echo(body, nl=False)
        tag_name = tag_for_version(version)
        if tag:
            if not ref_exists(runner, tag_name):
                create_tag(runner, tag_name, f"Release {tag_name}")
                state.err.print(f"[ok]tagged[/ok] {tag_name}")
            else:
                warn(f"tag {tag_name} already exists")
        if push_tag:
            if not tag and not ref_exists(runner, tag_name):
                raise UsageError("--push-tag requires --tag (or an existing tag)")
            git_push_tag(runner, tag_name)
            state.err.print(f"[ok]pushed tag[/ok] {tag_name}")
        if push_release:
            url = _push_github_release(
                config, runner, token, tag_name, version, body, draft=not publish
            )
            state.err.print(f"[ok]release[/ok] {url}")
    except PatchnoteError as exc:
        die(exc)
    except Exception as exc:
        die(exc)


def _push_github_release(
    config: Config,
    runner: GitRunner,
    token: str | None,
    tag_name: str,
    version: str,
    body: str,
    *,
    draft: bool,
) -> str:
    slug = detect_repo_slug(runner, config)
    if slug is None:
        raise UsageError("Cannot determine GitHub repository (set repository: in config)")
    token_value = resolve_token(config, token)
    if not token_value:
        raise UsageError("A GitHub token is required to create a release")
    with httpx.Client() as http:
        gh = GitHubClient(
            http,
            token_value,
            slug[0],
            slug[1],
            api_url=config.github.api_url,
            graphql_url=config.github.graphql_url,
            warn=warn,
        )
        data = gh.create_or_update_release(
            tag=tag_name,
            name=f"v{version}" if not version.startswith("v") else version,
            body=body,
            draft=draft,
        )
    return str(data.get("html_url") or "")


def run_release_from_env(env: dict[str, str], changelog_text: str) -> str | None:
    """Used by the GitHub Action to create a release without shell interpolation."""
    version = env.get("PATCHNOTE_VERSION") or env.get("GITHUB_REF_NAME") or ""
    if version.startswith("refs/tags/"):
        version = version.removeprefix("refs/tags/")
    if not version:
        raise UsageError("create-release requires a version or a tag ref")
    tag_name = version if version.startswith("v") else f"v{version.lstrip('v')}"
    is_draft = True
    publish = env.get("PATCHNOTE_PUBLISH", "")
    if publish.strip().lower() in {"1", "true", "yes", "on"}:
        is_draft = False
    token = env.get("PATCHNOTE_TOKEN") or env.get("GITHUB_TOKEN")
    config, _, runner = load_runtime_config()
    body = changelog_text
    if LAST_CHANGELOG is not None:
        body = render_github_release(LAST_CHANGELOG, config)
    return _push_github_release(config, runner, token, tag_name, version, body, draft=is_draft)


def run() -> None:
    try:
        app()
    except PatchnoteError as exc:
        die(exc)


if __name__ == "__main__":
    run()

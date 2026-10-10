"""GitHub Action entrypoint. Reads inputs from the environment only."""

from __future__ import annotations

import os
import secrets
import sys
from pathlib import Path

import typer

from patchnote.model import PatchnoteError


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _write_output(file: Path, name: str, value: str) -> None:
    """Append a (possibly multi-line) output using a random heredoc delimiter."""
    delimiter = "PATCHNOTE_" + secrets.token_hex(16)
    # Guard against the delimiter appearing in the value.
    while delimiter in value:
        delimiter = "PATCHNOTE_" + secrets.token_hex(16)
    with file.open("a", encoding="utf-8") as handle:
        handle.write(f"{name}<<{delimiter}\n")
        handle.write(value)
        if value and not value.endswith("\n"):
            handle.write("\n")
        handle.write(f"{delimiter}\n")


def _count_entries(markdown: str) -> int:
    return sum(1 for line in markdown.splitlines() if line.startswith("- "))


def build_argv(environ: dict[str, str] | None = None) -> list[str]:
    """Translate action inputs (env vars) into a ``patchnote generate`` argv."""
    env = environ if environ is not None else os.environ
    argv: list[str] = []
    if env.get("PATCHNOTE_CONFIG", "").strip():
        argv.extend(["--config", env["PATCHNOTE_CONFIG"].strip()])
    argv.extend(["generate", "--yes"])
    mapping = {
        "PATCHNOTE_FROM": "--from",
        "PATCHNOTE_TO": "--to",
        "PATCHNOTE_VERSION": "--version",
        "PATCHNOTE_STYLE": "--style",
        "PATCHNOTE_OUTPUT": "--output",
    }
    for env_name, flag in mapping.items():
        value = env.get(env_name, "").strip()
        if value:
            argv.extend([flag, value])
    ai = env.get("PATCHNOTE_AI", "").strip().lower()
    if ai and ai not in {"none", ""}:
        argv.extend(["--ai", ai])
    if _truthy(env.get("PATCHNOTE_PREPEND", "")):
        argv.append("--prepend")
    if _truthy(env.get("PATCHNOTE_INCLUDE_ALL", "")):
        argv.append("--include-all")
    fmt = env.get("PATCHNOTE_FORMAT", "").strip()
    if fmt:
        argv.extend(["--format", fmt])
    return argv


def run_action(environ: dict[str, str] | None = None) -> int:
    env = dict(os.environ if environ is None else environ)
    previous_env = dict(os.environ)
    os.environ.clear()
    os.environ.update(env)
    if env.get("PATCHNOTE_TOKEN"):
        os.environ["GITHUB_TOKEN"] = env["PATCHNOTE_TOKEN"]
    output_path = env.get("GITHUB_OUTPUT", "")
    workspace = Path(env.get("GITHUB_WORKSPACE") or env.get("PATCHNOTE_REPO") or ".")
    previous_cwd = Path.cwd()
    try:
        os.chdir(workspace)
        return _run_action_in_workspace(env, output_path)
    finally:
        os.chdir(previous_cwd)
        os.environ.clear()
        os.environ.update(previous_env)


def _exit_code(value: object) -> int:
    if value in {0, None}:
        return 0
    if isinstance(value, int):
        return value
    return 3


def _run_action_in_workspace(env: dict[str, str], output_path: str) -> int:
    from patchnote import cli as cli_mod
    from patchnote.cli import app

    cli_mod.LAST_RENDERED = ""
    cli_mod.LAST_CHANGELOG = None
    if env.get("GITHUB_REF_TYPE") == "tag" and not env.get("PATCHNOTE_TO"):
        env["PATCHNOTE_TO"] = env.get("GITHUB_REF_NAME", "HEAD")
    argv = build_argv(env)
    try:
        result = app(args=argv, standalone_mode=False)
        if isinstance(result, int) and result != 0:
            return result
    except PatchnoteError as exc:
        print(exc.message, file=sys.stderr)
        return exc.exit_code
    except typer.Exit as exc:
        return _exit_code(exc.exit_code)
    except SystemExit as exc:
        return _exit_code(exc.code)

    changelog_text = cli_mod.LAST_RENDERED or ""
    count = cli_mod.LAST_CHANGELOG.entry_count if cli_mod.LAST_CHANGELOG else 0

    release_url = ""
    create_release = _truthy(env.get("PATCHNOTE_CREATE_RELEASE", ""))
    if create_release:
        from patchnote.cli import run_release_from_env

        try:
            release_url = run_release_from_env(env, changelog_text) or ""
        except PatchnoteError as exc:
            print(exc.message, file=sys.stderr)
            return exc.exit_code

    if output_path:
        dest = Path(output_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _write_output(dest, "changelog", changelog_text)
        _write_output(dest, "release-url", release_url)
        _write_output(dest, "unreleased-entries", str(count))
    return 0


def main() -> None:
    sys.exit(run_action())


if __name__ == "__main__":
    main()

"""Helpers for building throwaway git repositories in tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ENV = {
    "GIT_AUTHOR_NAME": "Test User",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test User",
    "GIT_COMMITTER_EMAIL": "test@example.com",
    "GIT_AUTHOR_DATE": "2026-01-15T12:00:00+00:00",
    "GIT_COMMITTER_DATE": "2026-01-15T12:00:00+00:00",
    "GIT_TERMINAL_PROMPT": "0",
}


def git(repo: Path, *args: str, date: str | None = None) -> str:
    env = {**os.environ, **ENV, "LC_ALL": "C"}
    if date:
        env["GIT_AUTHOR_DATE"] = date
        env["GIT_COMMITTER_DATE"] = date
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        env=env,
    )
    if result.returncode != 0:
        raise AssertionError(f"git {args} failed: {result.stderr or result.stdout}")
    return result.stdout


def commit(repo: Path, message: str, filename: str | None = None, date: str | None = None) -> str:
    name = filename or "file.txt"
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    path.write_text(existing + message + "\n", encoding="utf-8")
    git(repo, "add", str(name), date=date)
    git(repo, "commit", "-m", message, date=date)
    return git(repo, "rev-parse", "HEAD").strip()


def tag(repo: Path, name: str, message: str | None = None) -> None:
    if message:
        git(repo, "tag", "-a", name, "-m", message)
    else:
        git(repo, "tag", name)

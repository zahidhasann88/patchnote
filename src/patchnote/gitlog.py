"""Read git history by calling the ``git`` binary (no GitPython)."""

from __future__ import annotations

import fnmatch
import os
import subprocess
from collections.abc import Sequence
from datetime import date, datetime, timezone
from pathlib import Path

from patchnote.model import Commit, GitError, UsageError

FIELD_SEP = "\x1f"
RECORD_SEP = "\x1e"

# hash, short, author name, email, author date, parents, subject, body
PRETTY = (
    f"%H{FIELD_SEP}%h{FIELD_SEP}%an{FIELD_SEP}%ae{FIELD_SEP}"
    f"%aI{FIELD_SEP}%P{FIELD_SEP}%s{FIELD_SEP}%b{RECORD_SEP}"
)


class GitRunner:
    """Thin wrapper around ``git`` subprocess calls, injectable in tests."""

    def __init__(self, repo: Path, env: dict[str, str] | None = None) -> None:
        self.repo = repo
        extra = env or {}
        self.env = {**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0", **extra}

    def run(self, args: Sequence[str], *, check: bool = True, timeout: int = 60) -> str:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
                env=self.env,
            )
        except FileNotFoundError as exc:
            raise GitError("git executable not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {' '.join(args)} timed out after {timeout}s") from exc
        if check and result.returncode != 0:
            err = (result.stderr or result.stdout or "").strip()
            raise GitError(f"git {' '.join(args)} failed: {err or result.returncode}")
        return result.stdout

    def run_ok(self, args: Sequence[str]) -> bool:
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=self.repo,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
                check=False,
                env=self.env,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0


def discover_repo(path: Path) -> Path:
    runner = GitRunner(path)
    try:
        root = runner.run(["rev-parse", "--show-toplevel"]).strip()
    except GitError as exc:
        raise GitError(f"{path} is not a git repository") from exc
    return Path(root)


def _parse_datetime(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_log_output(raw: str) -> list[Commit]:
    commits: list[Commit] = []
    if not raw.strip():
        return commits
    records = raw.split(RECORD_SEP)
    for record in records:
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split(FIELD_SEP)
        if len(parts) < 8:
            # Body may have been empty and split short; pad.
            parts = parts + [""] * (8 - len(parts))
        full, short, name, email, when, parents, subject = parts[:7]
        body = FIELD_SEP.join(parts[7:]) if len(parts) > 8 else parts[7]
        body = body.strip("\n")
        parent_list = [p for p in parents.split() if p]
        commits.append(
            Commit(
                hash=full.strip(),
                short_hash=short.strip(),
                author_name=name.strip(),
                author_email=email.strip(),
                author_date=_parse_datetime(when),
                parents=parent_list,
                subject=subject.strip("\n"),
                body=body,
            )
        )
    return commits


def list_commits(runner: GitRunner, from_ref: str | None, to_ref: str) -> list[Commit]:
    """Return commits in ``from_ref..to_ref`` (exclusive of from, inclusive of to).

    When ``from_ref`` is None, the full history reachable from ``to_ref`` is used.
    """
    for ref in (from_ref, to_ref):
        if ref and ref.startswith("-"):
            raise UsageError("Git references must not start with a dash")
    rev_range = f"{from_ref}..{to_ref}" if from_ref else to_ref
    raw = runner.run(["log", f"--pretty=format:{PRETTY}", "--date=iso-strict", rev_range, "--"])
    return parse_log_output(raw)


def list_tags(runner: GitRunner, pattern: str = "v*") -> list[str]:
    raw = runner.run(["tag", "--list", pattern, "--sort=-v:refname"], check=True)
    tags = [line.strip() for line in raw.splitlines() if line.strip()]
    if tags:
        return tags
    # Fall back to every tag, then filter with fnmatch (in case git glob differs).
    raw_all = runner.run(["tag", "--list", "--sort=-v:refname"])
    all_tags = [line.strip() for line in raw_all.splitlines() if line.strip()]
    return [tag for tag in all_tags if fnmatch.fnmatch(tag, pattern)]


def latest_tag(runner: GitRunner, pattern: str = "v*") -> str | None:
    tags = list_tags(runner, pattern)
    return tags[0] if tags else None


def previous_tag(runner: GitRunner, current: str, pattern: str = "v*") -> str | None:
    tags = list_tags(runner, pattern)
    if current in tags:
        idx = tags.index(current)
        if idx + 1 < len(tags):
            return tags[idx + 1]
        return None
    return tags[0] if tags else None


def resolve_ref(runner: GitRunner, ref: str) -> str:
    return runner.run(["rev-parse", ref]).strip()


def ref_exists(runner: GitRunner, ref: str) -> bool:
    if ref.startswith("-"):
        return False
    return runner.run_ok(
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}"]
    )


def tag_date(runner: GitRunner, tag: str) -> date | None:
    raw = runner.run(["log", "-1", "--format=%aI", tag], check=False).strip()
    if not raw:
        return None
    return _parse_datetime(raw).date()


def remote_url(runner: GitRunner, name: str = "origin") -> str | None:
    raw = runner.run(["remote", "get-url", name], check=False).strip()
    return raw or None


def current_branch(runner: GitRunner) -> str | None:
    raw = runner.run(["rev-parse", "--abbrev-ref", "HEAD"], check=False).strip()
    if not raw or raw == "HEAD":
        return None
    return raw


def create_tag(runner: GitRunner, tag: str, message: str) -> None:
    runner.run(["tag", "-a", tag, "-m", message])


def push_tag(runner: GitRunner, tag: str, remote: str = "origin") -> None:
    runner.run(["push", remote, tag])

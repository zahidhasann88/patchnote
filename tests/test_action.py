from __future__ import annotations

import os
from pathlib import Path

from patchnote.action_entry import build_argv, run_action
from tests.gitutil import commit, tag


def test_build_argv_maps_env() -> None:
    argv = build_argv(
        {
            "PATCHNOTE_FROM": "v1.0.0",
            "PATCHNOTE_TO": "HEAD",
            "PATCHNOTE_VERSION": "1.1.0",
            "PATCHNOTE_STYLE": "keepachangelog",
            "PATCHNOTE_AI": "polish",
            "PATCHNOTE_PREPEND": "true",
            "PATCHNOTE_INCLUDE_ALL": "1",
            "PATCHNOTE_OUTPUT": "CHANGELOG.md",
            "PATCHNOTE_TOKEN": "ghs_secret",
        }
    )
    assert argv[0] == "generate"
    assert "--yes" in argv
    assert "--from" in argv and "v1.0.0" in argv
    assert "--ai" in argv and "polish" in argv
    assert "--prepend" in argv
    # Secrets must not be interpolated into a shell string — we only pass argv lists.
    assert "ghs_secret" in argv  # passed as an argument, not a shell interpolation


def test_run_action_writes_github_output(repo: Path, tmp_path: Path, monkeypatch: object) -> None:
    commit(repo, "chore: start")
    tag(repo, "v0.1.0")
    commit(repo, "feat: add widgets")
    out = tmp_path / "github_output"
    env = {
        **os.environ,
        "GITHUB_OUTPUT": str(out),
        "GITHUB_WORKSPACE": str(repo),
        "PATCHNOTE_VERSION": "0.2.0",
        "PATCHNOTE_FROM": "v0.1.0",
        "PATCHNOTE_TO": "HEAD",
        "PATCHNOTE_AI": "none",
        "PATCHNOTE_CREATE_RELEASE": "false",
    }
    code = run_action(env)
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "changelog<<" in text
    assert "unreleased-entries<<" in text
    assert "Add widgets" in text or "widgets" in text
    # Delimiter should be random-ish and closed.
    assert text.count("PATCHNOTE_") >= 3


def test_run_action_no_secrets_in_output(repo: Path, tmp_path: Path) -> None:
    commit(repo, "feat: add widgets")
    out = tmp_path / "github_output"
    env = {
        **os.environ,
        "GITHUB_OUTPUT": str(out),
        "GITHUB_WORKSPACE": str(repo),
        "PATCHNOTE_TOKEN": "ghs_this_must_not_leak",
        "PATCHNOTE_AI_API_KEY": "sk-also-secret",
        "PATCHNOTE_VERSION": "0.1.0",
    }
    code = run_action(env)
    assert code == 0
    text = out.read_text(encoding="utf-8")
    assert "ghs_this_must_not_leak" not in text
    assert "sk-also-secret" not in text


def test_multiline_output_safe_delimiter(repo: Path, tmp_path: Path) -> None:
    commit(repo, "feat: add widgets")
    out = tmp_path / "github_output"
    env = {
        **os.environ,
        "GITHUB_OUTPUT": str(out),
        "GITHUB_WORKSPACE": str(repo),
        "PATCHNOTE_VERSION": "1.0.0",
    }
    assert run_action(env) == 0
    raw = out.read_text(encoding="utf-8")
    # Each write uses name<<DELIM / DELIM
    lines = raw.splitlines()
    opens = [line for line in lines if "<<" in line]
    assert len(opens) == 3
    for line in opens:
        delim = line.split("<<", 1)[1]
        assert lines.count(delim) == 1


def test_build_argv_ignores_empty() -> None:
    argv = build_argv({"PATCHNOTE_FROM": "", "PATCHNOTE_AI": "none"})
    assert "--from" not in argv
    assert "--ai" not in argv

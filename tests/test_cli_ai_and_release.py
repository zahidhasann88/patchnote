"""CLI coverage for AI polish, release helper, and emit-to-file."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

from patchnote.cli import app
from tests.gitutil import commit, git

runner = CliRunner()


def test_generate_writes_file(repo: Path) -> None:
    commit(repo, "feat: add widgets")
    out = repo / "NOTES.md"
    result = runner.invoke(
        app, ["--repo", str(repo), "generate", "--version", "1.0.0", "--output", str(out)]
    )
    assert result.exit_code == 0, result.output
    assert out.is_file()
    assert "Add widgets" in out.read_text(encoding="utf-8") or "widgets" in out.read_text(
        encoding="utf-8"
    )


def test_release_tag_only(repo: Path) -> None:
    commit(repo, "feat: add widgets")
    result = runner.invoke(app, ["--repo", str(repo), "release", "--version", "1.0.0", "--tag"])
    assert result.exit_code == 0, result.output
    tags = git(repo, "tag").split()
    assert "v1.0.0" in tags
    # Second run should warn that the tag exists, not fail.
    again = runner.invoke(app, ["--repo", str(repo), "release", "--version", "1.0.0", "--tag"])
    assert again.exit_code == 0


def test_release_push_tag_without_tag_fails(repo: Path) -> None:
    commit(repo, "feat: add widgets")
    result = runner.invoke(
        app, ["--repo", str(repo), "release", "--version", "9.9.9", "--push-tag"]
    )
    assert result.exit_code == 2


@respx.mock
def test_generate_ai_polish(repo: Path, monkeypatch: object, tmp_path: Path) -> None:
    commit(repo, "feat: add widgets")
    commit(repo, "fix: crash")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")  # type: ignore[attr-defined]
    monkeypatch.setenv("PATCHNOTE_AI_BASE_URL", "https://example.invalid/v1")  # type: ignore[attr-defined]
    monkeypatch.setenv("PATCHNOTE_AI_MODEL", "dummy")  # type: ignore[attr-defined]
    # Cache into the repo so we don't pollute.
    (repo / "patchnote.yaml").write_text(
        "ai:\n  cache_dir: .cache-ai\n  base_url: https://example.invalid/v1\n  model: dummy\n",
        encoding="utf-8",
    )

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        # Always return a valid polish of whatever ids we were sent.
        user = body["messages"][-1]["content"]
        start = user.find("-----BEGIN DATA-----")
        end = user.find("-----END DATA-----")
        blob = user[start:end]
        # Extract ids from the data JSON loosely.
        import re

        ids = re.findall(r'"id": "([^"]+)"', blob)
        unique: list[str] = []
        for ident in ids:
            if ident not in unique:
                unique.append(ident)
        entries = [{"id": ident, "summary": f"Polished {ident}"} for ident in unique]
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps({"entries": entries})}}]},
        )

    respx.post("https://example.invalid/v1/chat/completions").mock(side_effect=handler)
    result = runner.invoke(
        app,
        ["--repo", str(repo), "generate", "--version", "1.0.0", "--ai", "polish", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "Polished" in result.stdout


@respx.mock
def test_generate_ai_malformed_falls_back(repo: Path, monkeypatch: object) -> None:
    commit(repo, "feat: add widgets")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")  # type: ignore[attr-defined]
    (repo / "patchnote.yaml").write_text(
        "ai:\n  cache_dir: .cache-ai\n  base_url: https://example.invalid/v1\n  model: dummy\n",
        encoding="utf-8",
    )
    respx.post("https://example.invalid/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"entries": [{"id": "injected", "summary": "pwned"}]}
                            )
                        }
                    }
                ]
            },
        )
    )
    result = runner.invoke(
        app,
        ["--repo", str(repo), "generate", "--version", "1.0.0", "--ai", "polish", "--yes"],
    )
    assert result.exit_code == 0, result.output
    assert "pwned" not in result.stdout
    assert (
        "warning" in result.output.lower()
        or "Add widgets" in result.stdout
        or "widgets" in result.stdout
    )


def test_init_force(tmp_path: Path, monkeypatch: object) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]
    first = runner.invoke(app, ["init"])
    assert first.exit_code == 0
    second = runner.invoke(app, ["init", "--force"])
    assert second.exit_code == 0


def test_generate_unknown_to_ref(repo: Path) -> None:
    commit(repo, "feat: x")
    result = runner.invoke(app, ["--repo", str(repo), "generate", "--to", "missing"])
    assert result.exit_code == 2


@respx.mock
def test_release_push_release(repo: Path, monkeypatch: object) -> None:
    commit(repo, "feat: add widgets")
    git(repo, "remote", "add", "origin", "https://github.com/acme/widgets.git")
    monkeypatch.setenv("GITHUB_TOKEN", "ghs_test")  # type: ignore[attr-defined]
    respx.get("https://api.github.com/repos/acme/widgets/releases/tags/v1.0.0").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    respx.post("https://api.github.com/repos/acme/widgets/releases").mock(
        return_value=httpx.Response(
            201, json={"html_url": "https://github.com/acme/widgets/releases/1"}
        )
    )
    # GraphQL / REST enrichment should not fail the run.
    respx.post("https://api.github.com/graphql").mock(
        return_value=httpx.Response(200, json={"data": {}})
    )
    respx.get(url__regex=r".*/pulls").mock(return_value=httpx.Response(200, json=[]))
    result = runner.invoke(
        app,
        ["--repo", str(repo), "release", "--version", "1.0.0", "--push-release"],
    )
    assert result.exit_code == 0, result.output
    assert "github.com/acme/widgets/releases/1" in result.output

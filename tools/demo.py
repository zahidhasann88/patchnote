"""Exercise Patchnote with a temporary Git repository and a local fake LLM."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from socketserver import TCPServer
from typing import Any


class FakeLLMHandler(BaseHTTPRequestHandler):
    malicious = False

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_POST(self) -> None:
        data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        content = data["messages"][-1]["content"]
        payload = json.loads(
            content.split("-----BEGIN DATA-----\n", 1)[1].split("\n-----END DATA-----", 1)[0]
        )
        entries = [
            {"id": entry["id"], "summary": entry["summary"] + " (reviewed)"}
            for entry in payload["entries"]
        ]
        if self.malicious:
            entries.append({"id": "injected", "summary": "Invented change"})
        body = json.dumps(
            {"choices": [{"message": {"content": json.dumps({"entries": entries})}}]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class LocalServer(ThreadingHTTPServer):
    def server_bind(self) -> None:
        TCPServer.server_bind(self)
        self.server_name = str(self.server_address[0])
        self.server_port = int(self.server_address[1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", type=Path, default=Path("demo-output"))
    args = parser.parse_args()
    out = args.outdir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    for key in (
        "GITHUB_TOKEN",
        "PATCHNOTE_TOKEN",
        "PATCHNOTE_AI_API_KEY",
        "PATCHNOTE_AI_BASE_URL",
        "PATCHNOTE_AI_MODEL",
        "GITHUB_REPOSITORY",
    ):
        env.pop(key, None)
    env.update(
        GIT_AUTHOR_NAME="Ada Example",
        GIT_AUTHOR_EMAIL="ada@example.com",
        GIT_COMMITTER_NAME="Ada Example",
        GIT_COMMITTER_EMAIL="ada@example.com",
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        PYTHONIOENCODING="utf-8",
    )
    with tempfile.TemporaryDirectory(prefix="patchnote-demo-") as directory:
        repo = Path(directory)

        def git(*argv: str, bot: bool = False) -> str:
            git_env = dict(env)
            if bot:
                git_env.update(
                    GIT_AUTHOR_NAME="dependabot[bot]", GIT_AUTHOR_EMAIL="dependabot@example.com"
                )
            result = subprocess.run(
                ["git", *argv],
                cwd=repo,
                env=git_env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
            )
            return result.stdout.strip()

        git("init", "-b", "main")
        git("config", "commit.gpgsign", "false")
        git("config", "tag.gpgsign", "false")
        git("commit", "--allow-empty", "-m", "chore: initial baseline")
        git("tag", "v0.1.0")
        for message in (
            "feat(core): add batch export (#12)",
            "fix: handle empty input",
            "fix the retry timer",
            "feat(api)!: remove the v1 endpoint\n\nBREAKING CHANGE: clients must call v2",
            "docs: update guide",
        ):
            git("commit", "--allow-empty", "-m", message)
        git("commit", "--allow-empty", "-m", "feat: temporary experiment")
        reverted = git("rev-parse", "HEAD")
        git(
            "commit",
            "--allow-empty",
            "-m",
            f'Revert "feat: temporary experiment"\n\nThis reverts commit {reverted}.',
        )
        git("commit", "--allow-empty", "-m", "chore(deps): update dependencies", bot=True)

        def run(
            name: str, *argv: str, expected: int = 0, extra_env: dict[str, str] | None = None
        ) -> str:
            command = [sys.executable, "-m", "patchnote", "--repo", str(repo), *argv]
            result = subprocess.run(
                command,
                env={**env, **(extra_env or {})},
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            text = result.stdout + result.stderr
            (out / name).write_text(text, encoding="utf-8")
            print(f"\n$ patchnote {' '.join(argv)}\n{text}exit: {result.returncode}")
            if result.returncode != expected:
                raise AssertionError(f"{name}: expected {expected}, got {result.returncode}")
            return text

        run("preview.txt", "preview")
        for fmt in ("markdown", "json", "github-release"):
            text = run(f"report.{fmt}", "generate", "--version", "0.2.0", "--format", fmt)
            if fmt == "json":
                assert json.loads(text)["version"] == "0.2.0"
        target = out / "CHANGELOG.md"
        target.write_text(
            "# Changelog\n\n## [0.1.0] - 2026-01-01\n\n- Original release  \n", encoding="utf-8"
        )
        run(
            "prepend-first.txt",
            "generate",
            "--version",
            "0.2.0",
            "--output",
            str(target),
            "--prepend",
        )
        once = target.read_bytes()
        run(
            "prepend-second.txt",
            "generate",
            "--version",
            "0.2.0",
            "--output",
            str(target),
            "--prepend",
        )
        assert target.read_bytes() == once
        assert b"- Original release  " in once
        run("check.txt", "check", expected=1)
        server = LocalServer(("127.0.0.1", 0), FakeLLMHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            cfg = repo / "patchnote.yaml"
            cfg.write_text(
                f"ai:\n  base_url: http://127.0.0.1:{server.server_port}/v1\n"
                "  model: demo\n  cache_dir: .ai-cache\n",
                encoding="utf-8",
            )
            polished = run("ai-polish.txt", "generate", "--ai", "polish", "--yes")
            assert "(reviewed)" in polished
            FakeLLMHandler.malicious = True
            cfg.write_text(
                cfg.read_text(encoding="utf-8").replace(".ai-cache", ".bad-ai-cache"),
                encoding="utf-8",
            )
            rejected = run("ai-fallback.txt", "generate", "--ai", "polish", "--yes")
            assert "AI polish failed" in rejected
            assert "Invented change" not in rejected
            action_output = out / "action-output.txt"
            action_output.write_text("", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, "-m", "patchnote.action_entry"],
                env={
                    **env,
                    "GITHUB_WORKSPACE": str(repo),
                    "GITHUB_OUTPUT": str(action_output),
                    "PATCHNOTE_VERSION": "0.2.0",
                },
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            assert result.returncode == 0, result.stderr
            assert "changelog<<PATCHNOTE_" in action_output.read_text(encoding="utf-8")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    print(f"\nDemo passed. Reports saved in {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

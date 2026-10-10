# Contributing to Patchnote

Thanks for helping. This document is the short version; the code is the source
of truth.

## Development setup

Python 3.10 or newer and a `git` binary on `PATH` are required.

```bash
git clone https://github.com/zahidhasann88/patchnote.git
cd patchnote
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"
pre-commit install
make test
```

## Layout

The package lives under `src/patchnote` (src layout). Tests live under
`tests/` and must not touch the network or a real LLM — GitHub and OpenAI
calls are mocked with `respx`. Git tests create real temporary repositories.

Pure functions (`classify`, `conventional`, `filters`, `render`) should stay
side-effect free so they stay easy to test. The git runner, HTTP client, LLM
client, and clock are injected.

## Checks

```bash
make lint        # ruff check + ruff format --check
make typecheck   # mypy --strict on src
make test        # pytest with 85% coverage gate
make fmt         # apply ruff fixes
```

CI runs the same matrix on Ubuntu, macOS, and Windows for Python 3.10–3.13.

## Commit messages

This project dogfoods itself. Use [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add GraphQL batching for associated PRs
fix(cli): honour --no-color on Windows
docs: explain the AI threat model
test: cover revert collapsing across abbreviated hashes
chore: bump ruff
```

`patchnote check` is the CI linter for this.

## Pull requests

- Add or update tests for the behaviour you change.
- Run `make lint typecheck test` locally.
- Do not add runtime dependencies without a strong reason. The allowed set is
  typer, rich, httpx, pydantic v2, pyyaml, jinja2.
- Do not add GitPython or PyGithub.

## Release

Tags matching `v*` trigger `.github/workflows/release.yml`:

1. Validate that the tag, `pyproject.toml`, and `src/patchnote/__init__.py` versions match.
2. Run lint, types and tests, build distributions, and install the wheel in a clean environment.
3. Generate notes with the local composite action and attach assets to a GitHub Release.
4. Publish to PyPI using OIDC only when `PUBLISH_TO_PYPI` is `true`. Configure the
   matching trusted publisher and GitHub `pypi` environment before enabling it.

## License

By contributing you agree that your work is released under the MIT License.

# Patchnote

**Turn your git commits and pull requests into clean changelogs and release notes.**

Patchnote reads your git history, sorts each change into a section (Added, Fixed, Breaking Changes, …) and writes ready-to-use release notes. It works fully offline. AI rewording is optional and off by default.

![Patchnote generating release notes](docs/screenshots/demo.png)

## What you need

- **Python 3.10 or newer**
- **git** installed and available in your terminal
- A git repository with some commits (tags like `v1.0.0` are helpful but not required)

Optional:

- A **GitHub token** (`GITHUB_TOKEN`) to add pull request titles, labels and authors
- An **OpenAI-compatible API key** (or a local model via Ollama) to reword the notes with AI

## Install

```bash
pip install patchnote
```

You can also use `pipx install patchnote` or `uv tool install patchnote` to install it as a standalone tool.

## How it works

1. **Read history** — Patchnote reads the commits between your last tag and `HEAD` using your local `git`.
2. **Add PR details (optional)** — with a GitHub token, it looks up the pull request behind each commit.
3. **Sort changes** — each change is placed in a section (Breaking Changes, Added, Fixed, …). Housekeeping commits, merges and duplicates are removed, and bot updates are grouped.
4. **Reword (optional)** — an AI model can polish the wording, but cannot add or remove entries.
5. **Write output** — as Markdown, JSON, a GitHub Release body, or added to the top of your `CHANGELOG.md`.

## Quick start

Run these inside your project:

```bash
patchnote init                              # create a patchnote.yaml config (optional)
patchnote preview                           # see the notes for changes since the last tag
patchnote generate --version 1.4.0          # print the notes
patchnote generate --version 1.4.0 --output CHANGELOG.md --prepend   # add them to CHANGELOG.md
patchnote check                             # check commit messages (useful in CI)
```

By default Patchnote covers everything from your latest `v*` tag up to `HEAD` (or the full history if there are no tags). Use `--from` and `--to` to pick a different range.

Other output formats: `--format json` or `--format github-release`.

## Example

These commits:

```
feat(api)!: remove the v1 endpoint
feat: add widget factory (#12)
fix: crash on empty input
fix the retry timer
chore: ignore me
chore(deps): bump requests from 2.31.0 to 2.32.0   (dependabot)
```

become:

```markdown
## [1.4.0] - 2026-10-10

### Breaking Changes

- **api:** Remove the v1 endpoint (`a1b2c3d`)

### Added

- Add widget factory ([#12](https://github.com/acme/widgets/pull/12); @ada; `d4e5f6a`)

### Fixed

- Crash on empty input (`aa11bb2`)
- Fix the retry timer (`cc22dd3`)

### Changed

- 1 dependency update

[1.4.0]: https://github.com/acme/widgets/compare/v1.3.0...v1.4.0
```

The `chore` commit is skipped, and the dependabot update is grouped into one line.

## How changes are sorted

Patchnote uses the first rule that matches:

1. **Breaking changes** — `feat!:` or a `BREAKING CHANGE:` note.
2. **PR labels** — e.g. `bug` → Fixed, `enhancement` → Added.
3. **Conventional Commits** — `feat:`, `fix:`, `perf:`, …
4. **Keywords** — words like "fix", "add", "remove" in plain messages.
5. **Other** — everything else.

`chore`, `ci`, `test`, `docs`, `build` and `style` commits are skipped by default. Use `--include-all` to keep them.

## GitHub Action

Create release notes automatically when you push a tag:

```yaml
# .github/workflows/release.yml
name: Release
on:
  push:
    tags: ["v*"]
permissions:
  contents: write
jobs:
  notes:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: zahidhasann88/patchnote@v0.1.1
        with:
          version: ${{ github.ref_name }}
          create-release: true
```

Releases are created as drafts unless you set `publish: true`.

| Input | Default | What it does |
| --- | --- | --- |
| `version` | — | Version heading, e.g. `1.4.0` |
| `from` / `to` | latest tag / `HEAD` | Range of commits to include |
| `format` | `markdown` | `markdown`, `json` or `github-release` |
| `style` | `keepachangelog` | `keepachangelog` or `conventional` section names |
| `output-file` | — | Write the notes to this file |
| `prepend` | `false` | Add the notes to the top of an existing changelog |
| `create-release` | `false` | Create a GitHub Release |
| `publish` | `false` | Publish the release instead of leaving it as a draft |
| `include-all` | `false` | Keep `chore`, `ci`, `test` and `docs` commits |
| `ai` | `none` | `none`, `polish`, `summary` or `both` |
| `ai-base-url` / `ai-model` / `ai-api-key` | — | AI provider settings |
| `config-path` | — | Path to `patchnote.yaml` |
| `github-token` | `${{ github.token }}` | Used for PR details and releases |

Outputs: `changelog`, `release-url`, `unreleased-entries`.

More ready-to-copy workflows are in [`examples/workflows/`](examples/workflows/).

## GitHub pull request details (optional)

If `GITHUB_TOKEN` is set, Patchnote adds PR titles, numbers, labels and authors to the notes. If GitHub can't be reached, it simply continues with commit data only.

## AI wording (optional)

```bash
export OPENAI_API_KEY=sk-...
patchnote generate --ai polish      # reword entries
patchnote generate --ai summary     # add a short overview
```

Works with any OpenAI-compatible API, including local models through Ollama. The AI can only reword existing entries — if it adds, drops or reorders anything, Patchnote ignores its answer and keeps the original text. It asks before sending anything (skip with `--yes`), and PR descriptions are never sent unless you add `--ai-include-bodies`.

## Configuration

Run `patchnote init` to create a `patchnote.yaml` with comments explaining each option. Everything is optional. See [`examples/config/patchnote.yaml`](examples/config/patchnote.yaml) for a full example.

## Limitations

- Pull request details work with GitHub only. GitLab, Gitea and Bitbucket repos still get notes from commits.
- Squash-merged PRs without `(#123)` in the commit message need a GitHub token to be linked.
- Keyword sorting works best with English commit messages.
- AI rewording can still be wrong — review the notes before publishing.

## License

[MIT](LICENSE) © 2026 Zahid Hasan

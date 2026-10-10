"""GitHub REST + GraphQL enrichment. Failures never abort a changelog run."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from patchnote.config import Config
from patchnote.model import Commit, PullRequest, RuntimeNetworkError

WarnFn = Callable[[str], None]


GITHUB_REMOTE_RE = re.compile(
    r"(?:github\.com[:/])(?P<owner>[^/]+)/(?P<repo>[^/.]+?)(?:\.git)?/?$",
    re.IGNORECASE,
)


def parse_github_nwo(url: str) -> tuple[str, str] | None:
    """Return (owner, repo) from a git remote URL or ``owner/repo`` slug."""
    text = url.strip()
    if not text:
        return None
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", text):
        owner, repo = text.split("/", 1)
        return owner, repo.removesuffix(".git")
    if text.lower().startswith("git@github.com:"):
        return parse_github_nwo(text.split(":", 1)[1])
    try:
        parsed = urlparse(text)
        if parsed.hostname and parsed.hostname.lower() in {"github.com", "www.github.com"}:
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) == 2:
                return parse_github_nwo("/".join(parts))
    except ValueError:
        return None
    return None


def repository_http_url(owner: str, repo: str) -> str:
    return f"https://github.com/{owner}/{repo}"


def compare_url(owner: str, repo: str, from_ref: str, to_ref: str) -> str:
    left, right = quote(from_ref, safe=""), quote(to_ref, safe="")
    return f"https://github.com/{owner}/{repo}/compare/{left}...{right}"


def pr_url(owner: str, repo: str, number: int) -> str:
    return f"https://github.com/{owner}/{repo}/pull/{number}"


def resolve_token(config: Config, explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    env_name = config.github.token_env
    return os.environ.get(env_name) or os.environ.get("GITHUB_TOKEN") or None


class GitHubClient:
    """Minimal GitHub API client. Every public method is best-effort except releases."""

    def __init__(
        self,
        client: httpx.Client,
        token: str | None,
        owner: str,
        repo: str,
        *,
        api_url: str = "https://api.github.com",
        graphql_url: str = "https://api.github.com/graphql",
        warn: WarnFn | None = None,
    ) -> None:
        self._http = client
        self._token = token
        self.owner = owner
        self.repo = repo
        self.api_url = api_url.rstrip("/")
        self.graphql_url = graphql_url
        self._warn = warn or (lambda _msg: None)
        self._gave_up = False

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "patchnote",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if extra:
            headers.update(extra)
        return headers

    def _handle_error(self, response: httpx.Response, action: str) -> None:
        if response.status_code == 429:
            self._gave_up = True
            self._warn(f"GitHub API rate-limited while {action}; skipping remaining enrichment")
            return
        if response.status_code in {401, 403}:
            self._gave_up = True
            self._warn(
                f"GitHub API returned {response.status_code} while {action}; skipping enrichment"
            )
            return
        if response.status_code >= 400:
            self._warn(f"GitHub API {response.status_code} while {action}")

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response | None:
        if self._gave_up:
            return None
        url = path if path.startswith("http") else f"{self.api_url}{path}"
        try:
            response = self._http.get(url, headers=self._headers(), params=params, timeout=20.0)
        except httpx.HTTPError as exc:
            self._warn(f"GitHub request failed ({exc.__class__.__name__}); skipping enrichment")
            self._gave_up = True
            return None
        if response.status_code >= 400:
            self._handle_error(response, f"GET {path}")
            return None
        return response

    def associated_pulls_rest(self, sha: str) -> list[PullRequest]:
        path = f"/repos/{self.owner}/{self.repo}/commits/{sha}/pulls"
        response = self._get(path)
        if response is None:
            return []
        try:
            payload = response.json()
        except ValueError:
            self._warn("GitHub returned non-JSON for associated pulls")
            return []
        if not isinstance(payload, list):
            return []
        return [
            self._pr_from_rest(item)
            for item in payload
            if isinstance(item, dict) and ("merged_at" not in item or item["merged_at"])
        ]

    def _pr_from_rest(self, item: dict[str, Any]) -> PullRequest:
        labels_raw = item.get("labels") or []
        labels = [
            str(label.get("name", ""))
            for label in labels_raw
            if isinstance(label, dict) and label.get("name")
        ]
        user = item.get("user") or {}
        author = user.get("login") if isinstance(user, dict) else None
        body = item.get("body")
        number = int(item.get("number", 0))
        return PullRequest(
            number=number,
            title=str(item.get("title") or ""),
            body=body if isinstance(body, str) else None,
            labels=labels,
            author=author,
            url=str(item.get("html_url") or pr_url(self.owner, self.repo, number)),
            merge_commit_sha=item.get("merge_commit_sha"),
            linked_issues=_issues_from_text(f"{item.get('title') or ''}\n{body or ''}"),
        )

    def associated_pulls_graphql(self, shas: list[str]) -> dict[str, list[PullRequest]] | None:
        if not shas or self._gave_up or not self._token:
            return None
        aliases: list[str] = []
        for index, sha in enumerate(shas):
            if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha):
                continue
            aliases.append(
                f'c{index}: repository(owner: "{self.owner}", name: "{self.repo}") '
                f'{{ object(oid: "{sha}") {{ ... on Commit {{ oid '
                f"associatedPullRequests(first: 5) {{ nodes {{ "
                f"number title body url mergedAt author {{ login }} "
                f"labels(first: 20) {{ nodes {{ name }} }} "
                f"closingIssuesReferences(first: 10) {{ nodes {{ number }} }} "
                f"}} }} }} }} }}"
            )
        query = "query {\n" + "\n".join(aliases) + "\n}"
        try:
            response = self._http.post(
                self.graphql_url,
                headers=self._headers({"Content-Type": "application/json"}),
                json={"query": query},
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            self._warn(f"GitHub GraphQL failed ({exc.__class__.__name__}); falling back to REST")
            return None
        if response.status_code >= 400:
            self._handle_error(response, "GraphQL associatedPullRequests")
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict) or payload.get("errors"):
            self._warn("GitHub GraphQL returned errors; falling back to REST")
            return None
        data = payload.get("data") or {}
        result: dict[str, list[PullRequest]] = {}
        for index, sha in enumerate(shas):
            node = (data.get(f"c{index}") or {}).get("object") if isinstance(data, dict) else None
            if not isinstance(node, dict):
                result[sha] = []
                continue
            pr_nodes = (
                ((node.get("associatedPullRequests") or {}).get("nodes"))
                if isinstance(node.get("associatedPullRequests"), dict)
                else None
            )
            pulls: list[PullRequest] = []
            if isinstance(pr_nodes, list):
                for raw in pr_nodes:
                    if isinstance(raw, dict) and ("mergedAt" not in raw or raw["mergedAt"]):
                        pulls.append(self._pr_from_graphql(raw))
            result[sha] = pulls
        return result

    def _pr_from_graphql(self, raw: dict[str, Any]) -> PullRequest:
        labels_nodes = (
            ((raw.get("labels") or {}).get("nodes")) if isinstance(raw.get("labels"), dict) else []
        )
        labels = [
            str(node.get("name"))
            for node in labels_nodes or []
            if isinstance(node, dict) and node.get("name")
        ]
        author_raw = raw.get("author") or {}
        author = author_raw.get("login") if isinstance(author_raw, dict) else None
        issue_nodes = (
            ((raw.get("closingIssuesReferences") or {}).get("nodes"))
            if isinstance(raw.get("closingIssuesReferences"), dict)
            else []
        )
        issues = [
            int(node["number"])
            for node in issue_nodes or []
            if isinstance(node, dict) and "number" in node
        ]
        number = int(raw.get("number", 0))
        body = raw.get("body")
        return PullRequest(
            number=number,
            title=str(raw.get("title") or ""),
            body=body if isinstance(body, str) else None,
            labels=labels,
            author=author,
            url=str(raw.get("url") or pr_url(self.owner, self.repo, number)),
            linked_issues=issues or _issues_from_text(f"{raw.get('title') or ''}\n{body or ''}"),
        )

    def enrich_commits(self, commits: list[Commit]) -> dict[str, list[PullRequest]]:
        """Map commit SHA → associated PRs. Never raises for API problems."""
        mapping: dict[str, list[PullRequest]] = {commit.hash: [] for commit in commits}
        if not commits or self._gave_up:
            return mapping
        shas = [commit.hash for commit in commits]
        chunk_size = 15
        for start in range(0, len(shas), chunk_size):
            chunk = shas[start : start + chunk_size]
            batched = self.associated_pulls_graphql(chunk)
            if batched is None:
                for sha in chunk:
                    if self._gave_up:
                        return mapping
                    mapping[sha] = self.associated_pulls_rest(sha)
            else:
                for sha, pulls in batched.items():
                    mapping[sha] = pulls
        return mapping

    def first_time_contributors(self, logins: set[str], current_pr_numbers: set[int]) -> set[str]:
        """Return logins whose only merged PRs are in ``current_pr_numbers``."""
        first: set[str] = set()
        for login in logins:
            if self._gave_up:
                break
            query = f"repo:{self.owner}/{self.repo} author:{login} is:pr is:merged"
            response = self._get("/search/issues", params={"q": query, "per_page": 10})
            if response is None:
                continue
            try:
                payload = response.json()
            except ValueError:
                continue
            total = int(payload.get("total_count") or 0) if isinstance(payload, dict) else 0
            items = payload.get("items") if isinstance(payload, dict) else None
            numbers: set[int] = set()
            if isinstance(items, list):
                for item in items:
                    if isinstance(item, dict) and "number" in item:
                        numbers.add(int(item["number"]))
            if total == 0 or (total <= len(current_pr_numbers) and numbers <= current_pr_numbers):
                first.add(login.lower())
        return first

    def get_release_by_tag(self, tag: str) -> dict[str, Any] | None:
        response = self._get(f"/repos/{self.owner}/{self.repo}/releases/tags/{tag}")
        if response is None:
            return None
        try:
            payload = response.json()
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None

    def create_or_update_release(
        self,
        *,
        tag: str,
        name: str,
        body: str,
        draft: bool = True,
        prerelease: bool = False,
        target: str | None = None,
    ) -> dict[str, Any]:
        if not self._token:
            raise RuntimeNetworkError("A GitHub token is required to create a release")
        existing = self.get_release_by_tag(tag)
        payload: dict[str, Any] = {
            "tag_name": tag,
            "name": name,
            "body": body,
            "draft": draft,
            "prerelease": prerelease,
        }
        if target:
            payload["target_commitish"] = target
        headers = self._headers({"Content-Type": "application/json"})
        try:
            if existing and "id" in existing:
                url = f"{self.api_url}/repos/{self.owner}/{self.repo}/releases/{existing['id']}"
                response = self._http.patch(url, headers=headers, json=payload, timeout=30.0)
            else:
                url = f"{self.api_url}/repos/{self.owner}/{self.repo}/releases"
                response = self._http.post(url, headers=headers, json=payload, timeout=30.0)
        except httpx.HTTPError as exc:
            raise RuntimeNetworkError(
                f"Failed to publish GitHub release: {type(exc).__name__}"
            ) from exc
        if response.status_code >= 400:
            raise RuntimeNetworkError(f"GitHub release API returned {response.status_code}")
        try:
            data = response.json()
        except ValueError as exc:
            raise RuntimeNetworkError("GitHub release API returned non-JSON") from exc
        if not isinstance(data, dict):
            raise RuntimeNetworkError("Unexpected GitHub release payload")
        return data


ISSUE_RE = re.compile(
    r"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+#(\d+)",
    re.IGNORECASE,
)


def _issues_from_text(text: str) -> list[int]:
    found: list[int] = []
    seen: set[int] = set()
    for match in ISSUE_RE.finditer(text):
        number = int(match.group(1))
        if number not in seen:
            found.append(number)
            seen.add(number)
    return found


def enrich_or_empty(
    commits: list[Commit],
    client: GitHubClient | None,
    warn: WarnFn | None = None,
) -> dict[str, list[PullRequest]]:
    if client is None:
        return {}
    try:
        return client.enrich_commits(commits)
    except Exception as exc:
        (warn or (lambda _m: None))(f"GitHub enrichment failed: {type(exc).__name__}")
        return {}

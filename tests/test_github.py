from __future__ import annotations

from datetime import datetime, timezone

import httpx
import respx

from patchnote.github import GitHubClient, enrich_or_empty, parse_github_nwo
from patchnote.model import Commit

WHEN = datetime(2026, 1, 15, tzinfo=timezone.utc)
SHA = "a" * 40


def commit() -> Commit:
    return Commit(
        hash=SHA,
        short_hash="aaaaaaaa",
        author_name="Ada",
        author_email="ada@e.com",
        author_date=WHEN,
        parents=[],
        subject="feat: x",
        body="",
    )


def test_parse_github_nwo_variants() -> None:
    assert parse_github_nwo("git@github.com:acme/widgets.git") == ("acme", "widgets")
    assert parse_github_nwo("https://github.com/acme/widgets.git") == ("acme", "widgets")
    assert parse_github_nwo("https://github.com/acme/widgets") == ("acme", "widgets")
    assert parse_github_nwo("ssh://git@github.com/acme/widgets.git") == ("acme", "widgets")
    assert parse_github_nwo("acme/widgets") == ("acme", "widgets")
    assert parse_github_nwo("https://gitlab.com/acme/widgets") is None


@respx.mock
def test_rest_associated_pulls() -> None:
    route = respx.get("https://api.github.com/repos/acme/w/commits/" + SHA + "/pulls").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "number": 12,
                    "title": "feat: add widgets",
                    "body": "Fixes #4",
                    "html_url": "https://github.com/acme/w/pull/12",
                    "labels": [{"name": "enhancement"}],
                    "user": {"login": "ada"},
                }
            ],
        )
    )
    # Force REST by making GraphQL fail.
    respx.post("https://api.github.com/graphql").mock(return_value=httpx.Response(500))
    warnings: list[str] = []
    with httpx.Client() as http:
        client = GitHubClient(http, "token", "acme", "w", warn=warnings.append)
        mapping = client.enrich_commits([commit()])
    assert route.called
    prs = mapping[SHA]
    assert len(prs) == 1
    assert prs[0].number == 12
    assert prs[0].labels == ["enhancement"]
    assert prs[0].author == "ada"
    assert 4 in prs[0].linked_issues


@respx.mock
def test_graphql_batch() -> None:
    respx.post("https://api.github.com/graphql").mock(
        return_value=httpx.Response(
            200,
            json={
                "data": {
                    "c0": {
                        "object": {
                            "oid": SHA,
                            "associatedPullRequests": {
                                "nodes": [
                                    {
                                        "number": 3,
                                        "title": "fix: crash",
                                        "body": "",
                                        "url": "https://github.com/acme/w/pull/3",
                                        "author": {"login": "bob"},
                                        "labels": {"nodes": [{"name": "bug"}]},
                                        "closingIssuesReferences": {"nodes": [{"number": 9}]},
                                    }
                                ]
                            },
                        }
                    }
                }
            },
        )
    )
    with httpx.Client() as http:
        client = GitHubClient(http, "token", "acme", "w")
        mapping = client.enrich_commits([commit()])
    prs = mapping[SHA]
    assert prs[0].number == 3
    assert prs[0].labels == ["bug"]
    assert prs[0].linked_issues == [9]


@respx.mock
def test_api_failure_falls_back_empty() -> None:
    respx.post("https://api.github.com/graphql").mock(return_value=httpx.Response(500))
    respx.get(url__regex=r".*/pulls").mock(return_value=httpx.Response(500))
    warnings: list[str] = []
    with httpx.Client() as http:
        client = GitHubClient(http, "token", "acme", "w", warn=warnings.append)
        mapping = client.enrich_commits([commit()])
    assert mapping[SHA] == []
    assert warnings


@respx.mock
def test_rate_limit_stops_enrichment() -> None:
    respx.post("https://api.github.com/graphql").mock(return_value=httpx.Response(429))
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(429))
    warnings: list[str] = []
    with httpx.Client() as http:
        client = GitHubClient(http, "token", "acme", "w", warn=warnings.append)
        mapping = client.enrich_commits([commit()])
    assert mapping[SHA] == []
    assert any("rate" in w.lower() or "429" in w for w in warnings)


@respx.mock
def test_network_error_graceful() -> None:
    respx.post("https://api.github.com/graphql").mock(side_effect=httpx.ConnectError("nope"))
    respx.get(url__regex=r".*").mock(side_effect=httpx.ConnectError("nope"))
    warnings: list[str] = []
    with httpx.Client() as http:
        client = GitHubClient(http, None, "acme", "w", warn=warnings.append)
        mapping = enrich_or_empty([commit()], client, warn=warnings.append)
    assert mapping[SHA] == [] or mapping.get(SHA) == []


@respx.mock
def test_create_release() -> None:
    respx.get("https://api.github.com/repos/acme/w/releases/tags/v1.0.0").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    respx.post("https://api.github.com/repos/acme/w/releases").mock(
        return_value=httpx.Response(
            201, json={"html_url": "https://github.com/acme/w/releases/1", "id": 1}
        )
    )
    with httpx.Client() as http:
        client = GitHubClient(http, "token", "acme", "w")
        data = client.create_or_update_release(tag="v1.0.0", name="v1.0.0", body="hi", draft=True)
    assert data["html_url"].endswith("/1")


@respx.mock
def test_first_time_contributors() -> None:
    respx.get("https://api.github.com/search/issues").mock(
        return_value=httpx.Response(200, json={"total_count": 1, "items": [{"number": 12}]})
    )
    with httpx.Client() as http:
        client = GitHubClient(http, "token", "acme", "w")
        first = client.first_time_contributors({"ada"}, {12})
    assert "ada" in first

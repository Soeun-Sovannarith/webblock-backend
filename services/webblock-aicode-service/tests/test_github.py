import json
import httpx
from app.services.github import GitHubClient, parse_repo_url

def _client(handler) -> tuple[GitHubClient, list[tuple[str, str]]]:
    calls: list[tuple[str, str]] = []
    def record(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path))
        return handler(request)
    client = GitHubClient("t", "https://api.github.test")
    client.http = httpx.Client(base_url="https://api.github.test", transport=httpx.MockTransport(record))
    return client, calls

def test_push_creates_missing_branch_from_repo_default_branch():
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(404, json={})
        if path.endswith("/git/ref/heads/master"):
            return httpx.Response(200, json={"object": {"sha": "base"}})
        if path == "/repos/org/site":
            return httpx.Response(200, json={"default_branch": "master"})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "tree"})
        if path.endswith("/git/commits"):
            assert json.loads(req.content)["parents"] == ["base"]
            return httpx.Response(201, json={"sha": "c1"})
        if path.endswith("/git/refs") and req.method == "POST":
            assert json.loads(req.content) == {"ref": "refs/heads/main", "sha": "c1"}
            return httpx.Response(201, json={})
        raise AssertionError(f"unexpected {req.method} {path}")
    client, calls = _client(handler)
    assert client.push_snapshot("org", "site", "main", {"a.txt": "x"}, "msg") == "c1"
    assert ("POST", "/repos/org/site/git/refs") in calls

def test_push_fast_forwards_existing_branch():
    def handler(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/git/ref/heads/main"):
            return httpx.Response(200, json={"object": {"sha": "head"}})
        if path.endswith("/git/trees"):
            return httpx.Response(201, json={"sha": "tree"})
        if path.endswith("/git/commits"):
            return httpx.Response(201, json={"sha": "c2"})
        if path.endswith("/git/refs/heads/main") and req.method == "PATCH":
            assert json.loads(req.content) == {"sha": "c2", "force": False}
            return httpx.Response(200, json={})
        raise AssertionError(f"unexpected {req.method} {path}")
    client, _ = _client(handler)
    assert client.push_snapshot("org", "site", "main", {"a.txt": "x"}, "msg") == "c2"

def test_parse_repo_url():
    assert parse_repo_url("https://github.com/WebBlock-Organization/shop.git") == ("WebBlock-Organization", "shop")
    assert parse_repo_url("git@github.com:o/r") == ("o", "r")
    assert parse_repo_url("https://gitlab.com/o/r") is None

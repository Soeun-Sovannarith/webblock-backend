import json
import uuid
import httpx
import pytest
from fastapi.testclient import TestClient
from app.core.config import settings
from app.main import app
from app.services import deployment, portal_sso

client = TestClient(app)
PROJECT = {"project_id": uuid.UUID("11111111-1111-1111-1111-111111111111"), "subdomain": "sok-pottery",
           "name": "Sok's Pottery", "owner_user_id": "aaaaaaaa-0000-0000-0000-000000000001"}
ADMIN = "http://kc.test/admin/realms/webblock"


class FakeKeycloak:
    """Just enough of the Keycloak Admin REST API for portal_sso."""

    def __init__(self):
        self.clients: dict[str, dict] = {}
        self.roles: dict[str, dict] = {}
        self.mappers: dict[str, list] = {}
        self.user_roles: dict[tuple[str, str], bool] = {}
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method, url, headers=None, timeout=None, params=None, json=None, data=None):
        self.calls.append((method, url))
        req = httpx.Request(method, url)
        if url.endswith("/protocol/openid-connect/token"):
            assert data["grant_type"] == "client_credentials"
            return httpx.Response(200, json={"access_token": "admin-token"}, request=req)
        assert headers == {"Authorization": "Bearer admin-token"}
        path = url.removeprefix(ADMIN)
        parts = path.strip("/").split("/")
        if path == "/clients" and method == "GET":
            found = [c for c in self.clients.values() if c["clientId"] == params["clientId"]]
            return httpx.Response(200, json=found, request=req)
        if path == "/clients" and method == "POST":
            uid = str(uuid.uuid4())
            self.mappers[uid] = json.pop("protocolMappers", [])
            self.clients[uid] = {**json, "id": uid}
            return httpx.Response(201, request=req)
        uid = parts[1] if parts[0] == "clients" else None
        if parts[0] == "clients" and len(parts) == 2 and method == "PUT":
            self.clients[uid] = json
            return httpx.Response(204, request=req)
        if parts[2:] == ["protocol-mappers", "models"]:
            if method == "POST":
                self.mappers[uid].append(json)
                return httpx.Response(201, request=req)
            return httpx.Response(200, json=self.mappers[uid], request=req)
        if parts[2:] == ["roles"] and method == "POST":
            self.roles[uid] = {"id": "role-" + uid, **json}
            return httpx.Response(201, request=req)
        if parts[2:] == ["roles", "owner"]:
            return httpx.Response(200, json=self.roles[uid], request=req) if uid in self.roles else httpx.Response(404, request=req)
        if parts[2:] == ["roles", "owner", "users"]:
            users = [{"id": u, "username": f"user-{u[:4]}"} for (u, c), on in self.user_roles.items() if c == uid and on]
            return httpx.Response(200, json=users, request=req)
        if parts[2:] == ["client-secret"]:
            return httpx.Response(200, json={"type": "secret", "value": "s3cret-" + uid[:4]}, request=req)
        if parts[0] == "users" and parts[2:4] == ["role-mappings", "clients"]:
            assert json == [self.roles[parts[4]]]
            self.user_roles[(parts[1], parts[4])] = method == "POST"
            return httpx.Response(204, request=req)
        raise AssertionError(f"unexpected Keycloak call {method} {url}")


def _keycloak(monkeypatch):
    fake = FakeKeycloak()
    monkeypatch.setattr(settings, "keycloak_issuer_uri", "http://kc.test/realms/webblock")
    monkeypatch.setattr(settings, "keycloak_admin_client_id", "webblock-harness")
    monkeypatch.setattr(settings, "keycloak_admin_client_secret", "admin-secret")
    monkeypatch.setattr(settings, "keycloak_admin_url", "")  # .env may name an admin host
    monkeypatch.setattr(settings, "site_url_template", "https://{subdomain}.sites.test")
    monkeypatch.setattr(portal_sso.httpx, "request", fake)
    return fake


def test_ensure_site_client_creates_the_client_role_mapper_and_owner(monkeypatch):
    keycloak = _keycloak(monkeypatch)
    sso = portal_sso.ensure_site_client(PROJECT, portal_sso.site_urls(PROJECT))
    cid = "webblock-site-11111111-1111-1111-1111-111111111111"
    assert sso == {"KEYCLOAK_ISSUER": "http://kc.test/realms/webblock", "KEYCLOAK_CLIENT_ID": cid,
                   "KEYCLOAK_CLIENT_SECRET": sso["KEYCLOAK_CLIENT_SECRET"], "SITE_URL": "https://sok-pottery.sites.test"}
    (uid, created), = keycloak.clients.items()
    assert created["clientId"] == cid and created["publicClient"] is False and created["directAccessGrantsEnabled"] is False
    assert created["redirectUris"] == ["https://sok-pottery.sites.test/portal/callback"]
    assert created["attributes"]["pkce.code.challenge.method"] == "S256"
    assert created["attributes"]["backchannel.logout.url"] == "https://sok-pottery.sites.test/portal/backchannel-logout"
    mapper = keycloak.mappers[uid][0]["config"]
    assert mapper["claim.name"] == "webblock_roles" and mapper["id.token.claim"] == "true"
    assert keycloak.roles[uid]["name"] == "owner"
    assert keycloak.user_roles[(PROJECT["owner_user_id"], uid)] is True


def test_ensure_site_client_is_idempotent_and_keeps_known_urls(monkeypatch):
    keycloak = _keycloak(monkeypatch)
    portal_sso.ensure_site_client(PROJECT, portal_sso.site_urls(PROJECT, "https://sok.vercel.app/"))
    portal_sso.ensure_site_client(PROJECT, portal_sso.site_urls(PROJECT))
    (uid, updated), = keycloak.clients.items()
    assert updated["redirectUris"] == ["https://sok-pottery.sites.test/portal/callback", "https://sok.vercel.app/portal/callback"]
    assert len(keycloak.mappers[uid]) == 1
    assert sum(1 for m, u in keycloak.calls if m == "POST" and u.endswith(f"/clients/{uid}/roles")) == 1


def test_owners_can_be_granted_listed_and_revoked(monkeypatch):
    _keycloak(monkeypatch)
    portal_sso.ensure_site_client(PROJECT, [])
    member = "bbbbbbbb-0000-0000-0000-000000000002"
    pid = PROJECT["project_id"]
    assert client.put(f"/internal/v1/projects/{pid}/portal-owners/{member}").status_code == 204
    owners = client.get(f"/internal/v1/projects/{pid}/portal-owners").json()
    assert {o["user_id"] for o in owners} == {PROJECT["owner_user_id"], member}
    assert client.delete(f"/internal/v1/projects/{pid}/portal-owners/{member}").status_code == 204
    assert [o["user_id"] for o in client.get(f"/internal/v1/projects/{pid}/portal-owners").json()] == [PROJECT["owner_user_id"]]


def test_portal_owner_endpoints_report_missing_client_and_missing_config(monkeypatch):
    _keycloak(monkeypatch)
    other = uuid.uuid4()
    resp = client.put(f"/internal/v1/projects/{other}/portal-owners/{uuid.uuid4()}")
    assert resp.status_code == 404 and "first deploy" in resp.json()["detail"]
    assert client.put(f"/internal/v1/projects/{other}/portal-owners/not-a-uuid").status_code == 422
    monkeypatch.setattr(settings, "keycloak_admin_client_id", "")
    assert client.get(f"/internal/v1/projects/{other}/portal-owners").status_code == 503


def test_keycloak_failures_become_keycloak_errors(monkeypatch):
    _keycloak(monkeypatch)
    def down(*args, **kwargs):
        raise httpx.ConnectError("refused")
    monkeypatch.setattr(portal_sso.httpx, "request", down)
    with pytest.raises(portal_sso.KeycloakError):
        portal_sso.ensure_site_client(PROJECT, [])
    resp = client.get(f"/internal/v1/projects/{PROJECT['project_id']}/portal-owners")
    assert resp.status_code == 502


def test_admin_api_on_another_host_behind_cloudflare_access(monkeypatch):
    # The realm's public host serves no Admin REST API; an admin hostname behind Cloudflare Access does. The service
    # token goes to that host only, never to the issuer the token comes from.
    monkeypatch.setattr(settings, "keycloak_issuer_uri", "https://auth.test/realms/webblock")
    monkeypatch.setattr(settings, "keycloak_admin_client_id", "webblock-harness")
    monkeypatch.setattr(settings, "keycloak_admin_client_secret", "admin-secret")
    monkeypatch.setattr(settings, "keycloak_admin_url", "https://kc-admin.test/")
    monkeypatch.setattr(settings, "keycloak_cf_access_client_id", "cf-id")
    monkeypatch.setattr(settings, "keycloak_cf_access_client_secret", "cf-secret")
    calls = []

    def request(method, url, headers=None, timeout=None, **kwargs):
        calls.append((method, url, headers))
        req = httpx.Request(method, url)
        if url.endswith("/protocol/openid-connect/token"):
            return httpx.Response(200, json={"access_token": "admin-token"}, request=req)
        return httpx.Response(200, json=[], request=req)
    monkeypatch.setattr(portal_sso.httpx, "request", request)
    admin = portal_sso._Admin()
    assert admin.client("webblock-site-x") is None
    token_call, admin_call = calls
    assert token_call[1] == "https://auth.test/realms/webblock/protocol/openid-connect/token" and token_call[2] == {}
    assert admin_call[1] == "https://kc-admin.test/admin/realms/webblock/clients"
    assert admin_call[2] == {"CF-Access-Client-Id": "cf-id", "CF-Access-Client-Secret": "cf-secret",
                             "Authorization": "Bearer admin-token"}

    monkeypatch.setattr(settings, "keycloak_admin_url", "")  # no admin host: the issuer's, and no Cloudflare token
    calls.clear()
    portal_sso._Admin().client("webblock-site-x")
    assert calls[1][1] == "https://auth.test/admin/realms/webblock/clients" and "CF-Access-Client-Id" not in calls[1][2]


def test_site_urls_and_issuer_validation(monkeypatch):
    monkeypatch.setattr(settings, "site_url_template", "")
    assert portal_sso.site_urls(PROJECT, "https://a.test/", None, "ftp://x", "https://a.test") == ["https://a.test"]
    monkeypatch.setattr(settings, "keycloak_issuer_uri", "http://kc.test/auth")
    with pytest.raises(portal_sso.KeycloakError):
        portal_sso._realm()


def test_deploy_webhook_carries_site_env(monkeypatch):
    sent: list[dict] = []
    monkeypatch.setattr(settings, "deployment_provider", "webhook")
    monkeypatch.setattr(settings, "deployment_webhook_url", "https://deploy.test/hook")

    def post(url, content, headers, timeout):
        sent.append(json.loads(content))
        return httpx.Response(200, json={"url": "https://sok.vercel.app"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(deployment.httpx, "post", post)
    deployment.deploy(PROJECT, "rel-1", "abc", None, site_env={"KEYCLOAK_CLIENT_ID": "webblock-site-1"})
    deployment.deploy(PROJECT, "rel-2", "abc", None)
    assert sent[0]["site_env"] == {"KEYCLOAK_CLIENT_ID": "webblock-site-1"}
    assert "site_env" not in sent[1]

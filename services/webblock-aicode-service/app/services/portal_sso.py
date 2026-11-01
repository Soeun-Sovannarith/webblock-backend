"""Product portal single sign-on: one Keycloak client per generated site.

Every site is its own confidential OpenID Connect client in the WebBlock realm (settings.keycloak_issuer_uri), named
<keycloak_site_client_prefix><project_id>. The site signs owners in through Keycloak (lib/portal-auth.ts in the site
templates) and admits only users holding the client's "owner" role, which a protocol mapper puts in the ID token as
the "webblock_roles" claim. Keycloak notifies the site at /portal/backchannel-logout when a session ends.

The DEPLOY step calls ensure_site_client before and after every deploy (it is idempotent), so nobody creates clients
by hand. The Project Service keeps the owner role in sync with project membership through the
/internal/v1/projects/{id}/portal-owners endpoints (grant_owner / revoke_owner / list_owners).
"""
import logging
from typing import Any
from urllib.parse import quote
import httpx
from app.core.config import settings

log = logging.getLogger("webblock.portal_sso")

OWNER_ROLE = "owner"
ROLES_CLAIM = "webblock_roles"  # keep in sync with ROLES_CLAIM in the site's lib/portal-auth.ts
ROLES_MAPPER = "webblock site roles"
TIMEOUT_SECONDS = 15.0


class KeycloakError(Exception):
    pass


class SiteClientMissing(KeycloakError):
    """The project has no portal client yet (it is created on the site's first deploy)."""


def is_configured() -> bool:
    return bool(settings.keycloak_issuer_uri and settings.keycloak_admin_client_id and settings.keycloak_admin_client_secret)


def _realm() -> tuple[str, str]:
    """(Keycloak base URL, realm name) from the issuer, e.g. http://kc:8080/realms/webblock."""
    base, sep, realm = settings.keycloak_issuer_uri.rstrip("/").rpartition("/realms/")
    if not sep or not realm:
        raise KeycloakError(f"KEYCLOAK_ISSUER_URI must look like https://<host>/realms/<realm>, got {settings.keycloak_issuer_uri!r}")
    return base, realm


def site_client_id(project_id: Any) -> str:
    return f"{settings.keycloak_site_client_prefix}{project_id}"


def site_urls(project: dict[str, Any], *extra: str | None) -> list[str]:
    """The site's public origins: SITE_URL_TEMPLATE for its subdomain plus any deploy URLs, without trailing slashes."""
    urls = []
    if settings.site_url_template and project.get("subdomain"):
        urls.append(settings.site_url_template.format(subdomain=project["subdomain"]))
    urls += [u for u in extra if u]
    out: list[str] = []
    for url in urls:
        url = url.rstrip("/")
        if url.startswith(("https://", "http://")) and url not in out:
            out.append(url)
    return out


class _Admin:
    """A Keycloak Admin REST API session authenticated with the admin client's service account."""

    def __init__(self) -> None:
        if not is_configured():
            raise KeycloakError("Portal SSO is not configured (set KEYCLOAK_ADMIN_CLIENT_ID and KEYCLOAK_ADMIN_CLIENT_SECRET).")
        base, self.realm = _realm()
        admin_base = settings.keycloak_admin_url.rstrip("/")
        self.api = f"{admin_base or base}/admin/realms/{quote(self.realm)}"
        # The Cloudflare Access token goes only to an admin host named in KEYCLOAK_ADMIN_URL, never to the issuer.
        self.cf_access = {"CF-Access-Client-Id": settings.keycloak_cf_access_client_id,
                          "CF-Access-Client-Secret": settings.keycloak_cf_access_client_secret} \
            if admin_base and settings.keycloak_cf_access_client_id else {}
        token = self._send("POST", f"{base}/realms/{quote(self.realm)}/protocol/openid-connect/token", token=None, data={
            "grant_type": "client_credentials",
            "client_id": settings.keycloak_admin_client_id,
            "client_secret": settings.keycloak_admin_client_secret,
        })
        self.token = token.json()["access_token"]

    def _send(self, method: str, url: str, *, token: str | None, ok_404: bool = False,
              extra_headers: dict[str, str] | None = None, **kwargs: Any) -> httpx.Response:
        headers = {**(extra_headers or {}), **({"Authorization": f"Bearer {token}"} if token else {})}
        try:
            resp = httpx.request(method, url, headers=headers, timeout=TIMEOUT_SECONDS, **kwargs)
        except httpx.HTTPError as e:
            raise KeycloakError(f"Keycloak request failed: {method} {url}: {e}") from e
        if resp.status_code >= 400 and not (ok_404 and resp.status_code == 404):
            raise KeycloakError(f"Keycloak returned HTTP {resp.status_code} for {method} {url}: {resp.text[:300]}")
        return resp

    def call(self, method: str, path: str, *, ok_404: bool = False, **kwargs: Any) -> httpx.Response:
        return self._send(method, f"{self.api}{path}", token=self.token, ok_404=ok_404, extra_headers=self.cf_access, **kwargs)

    def client(self, client_id: str) -> dict[str, Any] | None:
        found = self.call("GET", "/clients", params={"clientId": client_id}).json()
        return found[0] if found else None

    def owner_role(self, client_uuid: str) -> dict[str, Any] | None:
        resp = self.call("GET", f"/clients/{client_uuid}/roles/{OWNER_ROLE}", ok_404=True)
        return None if resp.status_code == 404 else resp.json()


def _client_representation(client_id: str, urls: list[str], project: dict[str, Any]) -> dict[str, Any]:
    attributes = {
        "pkce.code.challenge.method": "S256",
        "post.logout.redirect.uris": "##".join(f"{u}/" for u in urls),
        "backchannel.logout.session.required": "true",
        "backchannel.logout.revoke.offline.tokens": "false",
    }
    if urls:
        attributes["backchannel.logout.url"] = f"{urls[0]}/portal/backchannel-logout"
    return {
        "clientId": client_id,
        "name": f"WebBlock site {project.get('subdomain') or project.get('name') or project['project_id']}",
        "description": f"Product portal of WebBlock project {project['project_id']}. Managed by the AI harness; do not edit.",
        "protocol": "openid-connect",
        "enabled": True,
        "publicClient": False,
        "clientAuthenticatorType": "client-secret",
        "standardFlowEnabled": True,
        "implicitFlowEnabled": False,
        "directAccessGrantsEnabled": False,
        "serviceAccountsEnabled": False,
        "frontchannelLogout": False,
        "redirectUris": [f"{u}/portal/callback" for u in urls],
        "webOrigins": [],
        "attributes": attributes,
    }


def _roles_mapper(client_id: str) -> dict[str, Any]:
    return {
        "name": ROLES_MAPPER,
        "protocol": "openid-connect",
        "protocolMapper": "oidc-usermodel-client-role-mapper",
        "config": {
            "usermodel.clientRoleMapping.clientId": client_id,
            "claim.name": ROLES_CLAIM,
            "multivalued": "true",
            "jsonType.label": "String",
            "id.token.claim": "true",
            "access.token.claim": "false",
            "userinfo.token.claim": "false",
        },
    }


def ensure_site_client(project: dict[str, Any], urls: list[str]) -> dict[str, str]:
    """Create or update the site's Keycloak client (redirect URIs for urls, kept together with the ones already
    registered), its "owner" role and roles claim, and give the project's owner the role.

    Returns the environment variables the site needs (KEYCLOAK_ISSUER, KEYCLOAK_CLIENT_ID, KEYCLOAK_CLIENT_SECRET and,
    when the URL is known, SITE_URL). Raises KeycloakError."""
    admin = _Admin()
    client_id = site_client_id(project["project_id"])
    existing = admin.client(client_id)
    if existing:
        registered = [u.removesuffix("/portal/callback") for u in existing.get("redirectUris", []) if u.endswith("/portal/callback")]
        all_urls = list(dict.fromkeys([*urls, *registered]))
        wanted = _client_representation(client_id, all_urls, project)
        merged = {**existing, **wanted, "attributes": {**existing.get("attributes", {}), **wanted["attributes"]}}
        merged.pop("protocolMappers", None)
        admin.call("PUT", f"/clients/{existing['id']}", json=merged)
        client_uuid = existing["id"]
    else:
        admin.call("POST", "/clients", json={**_client_representation(client_id, urls, project),
                                             "protocolMappers": [_roles_mapper(client_id)]})
        created = admin.client(client_id)
        if not created:
            raise KeycloakError(f"Keycloak client {client_id} was not found after creating it.")
        client_uuid = created["id"]

    mappers = admin.call("GET", f"/clients/{client_uuid}/protocol-mappers/models").json()
    if not any(m.get("name") == ROLES_MAPPER for m in mappers):
        admin.call("POST", f"/clients/{client_uuid}/protocol-mappers/models", json=_roles_mapper(client_id))

    role = admin.owner_role(client_uuid)
    if role is None:
        admin.call("POST", f"/clients/{client_uuid}/roles",
                   json={"name": OWNER_ROLE, "description": "May manage this site's products in its product portal."})
        role = admin.owner_role(client_uuid)
    if project.get("owner_user_id") and role:
        admin.call("POST", f"/users/{project['owner_user_id']}/role-mappings/clients/{client_uuid}", json=[role])

    secret = admin.call("GET", f"/clients/{client_uuid}/client-secret").json().get("value")
    if not secret:
        raise KeycloakError(f"Keycloak client {client_id} has no client secret.")
    env = {"KEYCLOAK_ISSUER": settings.keycloak_issuer_uri.rstrip("/"), "KEYCLOAK_CLIENT_ID": client_id,
           "KEYCLOAK_CLIENT_SECRET": secret}
    if urls:
        env["SITE_URL"] = urls[0]
    return env


def _client_and_role(admin: _Admin, project_id: Any) -> tuple[str, dict[str, Any]]:
    client = admin.client(site_client_id(project_id))
    role = admin.owner_role(client["id"]) if client else None
    if not client or not role:
        raise SiteClientMissing(f"Project {project_id} has no portal client yet; it is created on the site's first deploy.")
    return client["id"], role


def grant_owner(project_id: Any, user_id: str) -> None:
    """Let a Keycloak user manage the site's products (idempotent)."""
    admin = _Admin()
    client_uuid, role = _client_and_role(admin, project_id)
    admin.call("POST", f"/users/{quote(user_id)}/role-mappings/clients/{client_uuid}", json=[role])


def revoke_owner(project_id: Any, user_id: str) -> None:
    """Remove a user's access to the portal (idempotent). Their open portal session ends within the site's session
    lifetime (one hour); signing them out of Keycloak ends it at once (back-channel logout)."""
    admin = _Admin()
    client_uuid, role = _client_and_role(admin, project_id)
    admin.call("DELETE", f"/users/{quote(user_id)}/role-mappings/clients/{client_uuid}", json=[role])


def list_owners(project_id: Any) -> list[dict[str, Any]]:
    admin = _Admin()
    client_uuid, _ = _client_and_role(admin, project_id)
    users = admin.call("GET", f"/clients/{client_uuid}/roles/{OWNER_ROLE}/users").json()
    return [{"user_id": u["id"], "username": u.get("username"), "email": u.get("email")} for u in users]

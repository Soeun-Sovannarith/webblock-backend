import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any
import httpx
from app.core.config import settings

class DeploymentError(Exception):
    pass

SECRET_KEY = re.compile(r"PASSWORD|SECRET|TOKEN|KEY")  # site_env keys whose values must never be logged


def _redact(text: str, site_env: dict[str, str] | None) -> str:
    """text without the secret site_env values, also in their JSON-escaped form (\\/). A receiver may echo the
    request body in its error (a 422 listing the input does), and the error ends up in the run log and the database."""
    secrets = {v for k, v in (site_env or {}).items() if SECRET_KEY.search(k) and v}
    for value in sorted(secrets, key=len, reverse=True):
        text = text.replace(value, "[redacted]").replace(value.replace("/", "\\/"), "[redacted]")
    return text


@dataclass
class DeployResult:
    status: str  # SUCCEEDED | SKIPPED
    url: str | None = None
    provider_id: str | None = None
    message: str = ""

def deploy(project: dict[str, Any], release_id: str, commit_sha: str | None, repository_url: str | None,
           site_env: dict[str, str] | None = None) -> DeployResult:
    """Trigger a deployment. A 2xx from the webhook counts as an accepted deployment; the provider builds
    asynchronously. Response JSON may carry `url`/`deployment_url` and `id`/`job.id` (Vercel deploy hooks).

    site_env (the database login and object storage settings from site_catalog.prepare_site, and the portal SSO settings from
    portal_sso.ensure_site_client, both including secrets) is sent as "site_env" for the receiver to set as the site's
    environment variables. It is never logged."""
    if settings.deployment_provider == "none":
        return DeployResult("SKIPPED", message="Deployment provider disabled (DEPLOYMENT_PROVIDER=none).")
    if not settings.deployment_webhook_url:
        raise DeploymentError("DEPLOYMENT_PROVIDER=webhook but DEPLOYMENT_WEBHOOK_URL is empty.")
    body = json.dumps({
        "event": "webblock.deploy",
        "project_id": str(project["project_id"]),
        "release_id": release_id,
        "repository_url": repository_url,
        "branch": settings.git_branch,
        "commit_sha": commit_sha,
        **({"site_env": site_env} if site_env else {}),
    }).encode()
    headers = {"Content-Type": "application/json"}
    if settings.deployment_webhook_secret:
        sig = hmac.new(settings.deployment_webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        headers["X-WebBlock-Signature"] = f"sha256={sig}"
    try:
        resp = httpx.post(settings.deployment_webhook_url, content=body, headers=headers,
                          timeout=settings.request_timeout_seconds)
    except httpx.HTTPError as e:
        raise DeploymentError(f"Deployment webhook failed: {e}") from e
    if resp.status_code >= 300:
        # Redact before truncating, so a cut can't leave the start of a secret behind.
        raise DeploymentError(f"Deployment webhook returned HTTP {resp.status_code}: {_redact(resp.text, site_env)[:500]}")
    try:
        data = resp.json()
    except ValueError:
        data = {}
    data = data if isinstance(data, dict) else {}
    job = data.get("job") if isinstance(data.get("job"), dict) else {}
    url = data.get("url") or data.get("deployment_url")
    provider_id = data.get("id") or job.get("id")
    return DeployResult("SUCCEEDED", str(url) if url else None, str(provider_id) if provider_id else None,
                        f"Deployment accepted (HTTP {resp.status_code}).")

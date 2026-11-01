from typing import Literal
from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL, make_url

# Postgres URL schemes other tools use, read as the psycopg (v3) driver SQLAlchemy needs.
POSTGRES_SCHEMES = ("jdbc:postgresql://", "postgresql://", "postgres://")

class Settings(BaseSettings):
    app_name: str = "WebBlock AI Code Service"
    service_name: str = "aicode-service"
    environment: str = "development"
    database_url: str = "postgresql+psycopg://webblock:webblock@localhost:5434/aicode_db"
    # Production: DATABASE_URL may leave out the credentials; these then fill them in (and win over any in the URL).
    user_name: str = ""
    db_password: str = ""
    # Alembic only (migrations/env.py): the role to SET before migrating, when the login may not create schemas
    # itself. Production: ai_codegen, the database owner, which dev_svr may switch to. Empty: migrate as the login.
    db_migration_role: str = ""
    # Generated sites' data: the shared catalog (sql/007_catalog.sql) in this database. Each site logs in as its own
    # role, site_<project id>, whose password SITE_DB_SECRET derives; without it, sites deploy with no database
    # settings. Host, port and database default to DATABASE_URL's (set them when sites reach the database by another
    # address). Creating those roles needs CREATEROLE: SITE_ROLE_ADMIN is the role the harness switches to for it
    # (empty: the login itself), which also needs ADMIN OPTION on the group role webblock_site.
    site_db_host: str = ""
    site_db_port: int = 0
    site_db_name: str = ""
    site_db_sslmode: Literal["disable", "allow", "prefer", "require", "verify-ca", "verify-full"] = "prefer"
    site_db_secret: str = ""
    site_role_admin: str = ""
    # Each new website's limits (catalog.websites) and the time zone of its opening hours.
    site_storage_limit_bytes: int = 100 * 1024 * 1024
    site_max_image_bytes: int = 5 * 1024 * 1024
    site_timezone: str = "Asia/Phnom_Penh"
    # S3-compatible object storage for product images and logos (AWS S3, MinIO, Cloudflare R2, ...), passed to every
    # site. The endpoint is the service's base URL (https://s3.<region>.amazonaws.com, https://minio.example.com);
    # objects are addressed path-style, <endpoint>/<bucket>/websites/<website_id>/... Empty: sites can't upload images.
    site_storage_endpoint: str = ""
    site_storage_region: str = "us-east-1"
    site_storage_bucket: str = ""
    site_storage_access_key_id: str = ""
    site_storage_secret_access_key: str = ""
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000", "http://localhost:8090"]
    request_timeout_seconds: int = 90

    # RabbitMQ settings (matches WebBlock distributed event bus)
    rabbitmq_host: str = "localhost"
    rabbitmq_port: int = 5672
    rabbitmq_username: str = "webblock"
    rabbitmq_password: str = "webblock"
    run_consumer: bool = False

    # Keycloak JWT authentication. keycloak_issuer_uri is also the realm that generated sites sign their owners in with
    # (product portal SSO), so it must be the realm's public URL, reachable from every site.
    keycloak_issuer_uri: str = "http://localhost:8180/realms/webblock"
    keycloak_jwk_set_uri: str = "http://localhost:8180/realms/webblock/protocol/openid-connect/certs"
    # Product portal SSO: a confidential Keycloak client in that realm with a service account holding the
    # realm-management roles manage-clients and manage-users. Empty: sites are deployed without a portal login.
    keycloak_admin_client_id: str = ""
    keycloak_admin_client_secret: str = ""
    # Base URL (no /realms) of the Admin REST API when another host serves it, such as an admin hostname behind
    # Cloudflare Access. Empty: the issuer's host. The admin client's token always comes from the issuer.
    keycloak_admin_url: str = ""
    # Cloudflare Access service token sent only to KEYCLOAK_ADMIN_URL. Falls back to CF_ACCESS_CLIENT_ID/SECRET, the
    # token shared with GLM; the Access application in front of the admin host must allow it (a Service Auth policy).
    keycloak_cf_access_client_id: str = Field(
        default="", validation_alias=AliasChoices("KEYCLOAK_CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_ID"))
    keycloak_cf_access_client_secret: str = Field(
        default="", validation_alias=AliasChoices("KEYCLOAK_CF_ACCESS_CLIENT_SECRET", "CF_ACCESS_CLIENT_SECRET"))
    # Each site's Keycloak client is <prefix><project_id>.
    keycloak_site_client_prefix: str = "webblock-site-"

    # Inter-service HTTP endpoints
    project_service_url: str = "http://localhost:8082"
    provisioning_service_url: str = "http://localhost:8084"
    downstream_timeout_seconds: float = 5.0

    # GLM (OpenAI-compatible chat completions API) for AI code updates.
    glm_base_url: str = ""
    glm_api_key: str = ""
    glm_model: str = "glm-4.6"
    glm_timeout_seconds: int = 180
    # Stream the reply (server-sent events), so a proxy in front of GLM that drops a silent connection (Cloudflare
    # answers 524 after about 100 s) sees data at once. Off: Instructor + LiteLLM first, then one plain HTTP call.
    glm_stream: bool = True
    # Let reasoning models think before answering. Off (default): faster replies, and the build plus the template
    # fallback catch mistakes. Sent as chat_template_kwargs.enable_thinking (llama.cpp, vLLM) and thinking.type (Z.ai).
    glm_thinking: bool = False
    # Cloudflare Access service token, for a self-hosted endpoint behind Cloudflare Access.
    glm_cf_access_client_id: str = Field(
        default="", validation_alias=AliasChoices("GLM_CF_ACCESS_CLIENT_ID", "CF_ACCESS_CLIENT_ID"))
    glm_cf_access_client_secret: str = Field(
        default="", validation_alias=AliasChoices("GLM_CF_ACCESS_CLIENT_SECRET", "CF_ACCESS_CLIENT_SECRET"))

    # Run artifacts (source archive, check report, logs), stored as <artifact_dir>/<project_id>/<harness_run_id>/<file>.
    # Every API and worker process must see the same directory.
    artifact_dir: str = "var/artifacts"
    # Version of the Project -> codegen input format recorded on each generation contract.
    codegen_api_contract_version: str = "webblock.codegen.v1"
    # Codegen mode: "hybrid" (deterministic template + GLM instructions) or "llm" (GLM generates code from Puck JSON on generate & update)
    codegen_mode: Literal["hybrid", "llm"] = "hybrid"


    # GitHub. Projects without a repository get one auto-created under github_owner (a user or an organization).
    git_branch: str = "main"
    github_token: str = ""
    github_owner: str = Field(default="", validation_alias=AliasChoices("GITHUB_OWNER", "GITHUB_ORGANIZATION_NAME"))
    github_api_url: str = "https://api.github.com"
    github_private_repos: bool = True

    # Who publishes a successful run. "github": this service pushes to GitHub and deploys (GITHUB_PUSH, DEPLOY).
    # "project": the WebBlock platform does (Project reads the run, commits to Gitea; Provisioning builds and deploys),
    # so both steps are skipped and the run ends at the completion event.
    publish_target: Literal["github", "project"] = "github"
    # Signs the download URLs that GET /internal/v1/generation-runs/{id} hands to Project (HMAC-SHA256), valid for
    # artifact_url_ttl_seconds. Empty: the runs carry no download URL. artifact_base_url is how Project reaches us.
    artifact_url_secret: str = ""
    artifact_base_url: str = "http://localhost:8000"
    artifact_url_ttl_seconds: int = Field(default=600, ge=30, le=600)

    # Deployment provider. "webhook" POSTs to deployment_webhook_url (works with Vercel/Netlify/Render deploy hooks).
    deployment_provider: Literal["none", "webhook"] = "none"
    deployment_webhook_url: str = ""
    deployment_webhook_secret: str = ""
    # Public URL of a live site, e.g. https://{subdomain}.example.com (empty: the API reports no site URL).
    site_url_template: str = ""

    # Build runner. "docker" is the isolated runner; "local" runs npm on the host (trusted dev only); "skip" disables builds.
    build_runner: Literal["docker", "local", "skip"] = "docker"
    build_image: str = "node:22-alpine"
    build_timeout_seconds: int = 600
    build_memory: str = "2g"
    build_cpus: str = "2"

    # Background worker.
    run_worker: bool = True
    worker_poll_seconds: float = 2.0
    run_lease_seconds: int = 1200
    run_max_attempts: int = 3

    # When true, a version is promoted to baseline only if build, push and deploy all actually ran (none SKIPPED).
    strict_promotion: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def sqlalchemy_database_url(self) -> URL:
        """DATABASE_URL with USER_NAME/DB_PASSWORD applied, as a URL object so a password with @, : or / needs no
        escaping. Accepts postgres://, postgresql:// and jdbc:postgresql:// URLs as well as postgresql+psycopg://."""
        raw = self.database_url.strip()
        for scheme in POSTGRES_SCHEMES:
            if raw.startswith(scheme):
                raw = "postgresql+psycopg://" + raw[len(scheme):]
                break
        url = make_url(raw)
        if self.user_name:
            url = url.set(username=self.user_name)
        if self.db_password:
            url = url.set(password=self.db_password)
        return url

settings = Settings()

"""Generated sites' data: the shared catalog schema in this service's database (sql/007_catalog.sql).

Every website's business profile, categories, products and product images live in the shared catalog tables, keyed by
website_id (the project id), as the team's database design describes. Each site's backend logs in as its own role,
site_<project id hex> (catalog.websites.db_role_name), and row-level security maps that role to its website, so a site
reads and writes only its own rows. The role's password is an HMAC of the project id under settings.site_db_secret:
every deploy hands the site the same password, which is stored nowhere, and Postgres only ever receives its SCRAM
verifier, so the password itself never appears in a statement or a server log.

The DEPLOY step calls prepare_site (idempotent): it creates or re-keys the site's role, registers the website, applies
the site's db/seed.sql, and returns the settings for the site's environment (site_env): the libpq variables and, when
object storage is configured, the S3 settings the site stores product images and its logo with.
"""
import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from typing import Any
from uuid import UUID
from sqlalchemy import text
from app.core.config import settings
from app.db import SessionLocal

GROUP_ROLE = "webblock_site"  # sql/007_catalog.sql grants the catalog privileges to it
WEBSITE_SETTING = "webblock.website_id"  # the seed script reads the website id from it (seed_generator)
CONNECTION_LIMIT = 3  # the site's pool holds at most 2 (lib/db.ts), plus one for a deploy that overlaps
DEFAULT_BUSINESS_TYPE = "everyday_store"
SCRAM_ITERATIONS = 4096
# prepare_site runs as the RLS-bypassing owner on a database where site roles run their own code, so before anything
# else it resolves names in pg_catalog only (a site can't shadow a function or operator the seed uses, such as
# =(varchar, text), from public) and turns on standard_conforming_strings in its own statement: a SET inside the seed
# can't change how that same query string is read, and seed_generator.literal() relies on it.
SESSION_GUARDS = ("SET LOCAL search_path = pg_catalog, pg_temp", "SET LOCAL standard_conforming_strings = on")


class SiteCatalogError(Exception):
    pass


@dataclass
class PreparedSite:
    env: dict[str, str]
    status: str  # catalog.website_status: only an "active" website's role sees any rows


def is_configured() -> bool:
    return bool(settings.site_db_secret)


def storage_configured() -> bool:
    return bool(settings.site_storage_endpoint and settings.site_storage_bucket and settings.site_storage_access_key_id
                and settings.site_storage_secret_access_key)


def role_name(project_id: Any) -> str:
    """The site's login role: site_ plus the project id's 32 hex digits (37 characters, under Postgres's 63)."""
    return "site_" + UUID(str(project_id)).hex


def role_password(project_id: Any) -> str:
    """Stable per project, unguessable without SITE_DB_SECRET."""
    message = f"webblock-site-role:{UUID(str(project_id))}".encode()
    return hmac.new(settings.site_db_secret.encode(), message, hashlib.sha256).hexdigest()


def scram_verifier(password: str, salt: bytes | None = None, iterations: int = SCRAM_ITERATIONS) -> str:
    """The SCRAM-SHA-256 verifier Postgres stores for a password (RFC 5802/7677), so CREATE/ALTER ROLE can send it in
    place of the password. The password is ASCII hex, which SASLprep leaves unchanged."""
    salt = salt or secrets.token_bytes(16)
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    stored_key = hashlib.sha256(hmac.new(salted, b"Client Key", hashlib.sha256).digest()).digest()
    server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    b64 = lambda raw: base64.b64encode(raw).decode()  # noqa: E731
    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


def site_env(project_id: Any) -> dict[str, str]:
    """The libpq variables the site connects with (Provisioning's site contract), plus object storage when set."""
    url = settings.sqlalchemy_database_url
    env = {
        "PGHOST": settings.site_db_host or url.host or "localhost",
        "PGPORT": str(settings.site_db_port or url.port or 5432),
        "PGDATABASE": settings.site_db_name or url.database or "",
        "PGUSER": role_name(project_id),
        "PGPASSWORD": role_password(project_id),
        "PGSSLMODE": settings.site_db_sslmode,
    }
    if storage_configured():
        env.update({
            "STORAGE_ENDPOINT": settings.site_storage_endpoint.rstrip("/"),
            "STORAGE_REGION": settings.site_storage_region,
            "STORAGE_BUCKET": settings.site_storage_bucket,
            "STORAGE_ACCESS_KEY_ID": settings.site_storage_access_key_id,
            "STORAGE_SECRET_ACCESS_KEY": settings.site_storage_secret_access_key,
        })
    return env


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def _ensure_role(db, role: str, verifier: str) -> None:
    """Create the site's login role in the group, or re-key it. Runs as SITE_ROLE_ADMIN when set."""
    driver = db.connection().connection.driver_connection
    if settings.site_role_admin:
        driver.execute(f"SET LOCAL ROLE {_quote(settings.site_role_admin)}")
    exists = db.execute(text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}).first()
    # Role names and the verifier are generated here (hex, base64), never user input; DDL takes no parameters.
    options = f"LOGIN PASSWORD '{verifier}' CONNECTION LIMIT {CONNECTION_LIMIT}"
    if exists:
        driver.execute(f"ALTER ROLE {_quote(role)} WITH {options}")
        member = db.execute(text("SELECT pg_has_role(:r, :g, 'MEMBER')"), {"r": role, "g": GROUP_ROLE}).scalar()
        if not member:
            driver.execute(f"GRANT {_quote(GROUP_ROLE)} TO {_quote(role)}")
    else:
        driver.execute(f"CREATE ROLE {_quote(role)} WITH {options} IN ROLE {_quote(GROUP_ROLE)}")
    if settings.site_role_admin:
        driver.execute("RESET ROLE")


def _business_type_code(business_type: str | None) -> str:
    return (business_type or DEFAULT_BUSINESS_TYPE).strip().lower().replace(" ", "_").replace("-", "_")[:50]


def prepare_site(project: dict[str, Any], seed_sql: str | None, business_type: str | None = None) -> PreparedSite:
    """Create or re-key the site's role, register its website and business profile, and apply its seed.

    The website's business type is the project's business_type_id when the catalog has it, else the onboarding
    business type's code, else everyday_store. An existing website keeps its status and limits. seed_sql is the site's
    db/seed.sql (seed_generator.render_seed_sql), which inserts only while the website has no profile or products."""
    project_id = str(UUID(str(project["project_id"])))
    role = role_name(project_id)
    try:
        with SessionLocal() as db:
            for guard in SESSION_GUARDS:
                db.execute(text(guard))
            _ensure_role(db, role, scram_verifier(role_password(project_id)))
            status = db.execute(text("""
              INSERT INTO catalog.websites (website_id, business_type_id, db_role_name, storage_limit_bytes, max_image_bytes)
              VALUES (:id, COALESCE(
                  (SELECT business_type_id FROM catalog.business_types WHERE business_type_id = CAST(:project_type AS uuid)),
                  (SELECT business_type_id FROM catalog.business_types WHERE code = :code AND is_active),
                  (SELECT business_type_id FROM catalog.business_types WHERE code = :default_code)),
                :role, :limit, :max_image)
              ON CONFLICT (website_id) DO UPDATE SET db_role_name = EXCLUDED.db_role_name
              RETURNING status
            """), {"id": project_id, "project_type": str(project["business_type_id"]) if project.get("business_type_id") else None,
                   "code": _business_type_code(business_type), "default_code": DEFAULT_BUSINESS_TYPE, "role": role,
                   "limit": settings.site_storage_limit_bytes, "max_image": settings.site_max_image_bytes}).scalar_one()
            if seed_sql and seed_sql.strip():
                db.execute(text("SELECT set_config(:name, :id, true)"), {"name": WEBSITE_SETTING, "id": project_id})
                # One simple query, as migrations/sql_files.run_sql_file sends a file (no parameter parsing).
                db.connection().connection.driver_connection.execute(seed_sql)
            # Every website has a profile, so the site only ever updates it (sites may not insert one).
            db.execute(text("""
              INSERT INTO catalog.business_profiles (website_id, business_name, timezone)
              VALUES (:id, :name, :tz) ON CONFLICT (website_id) DO NOTHING
            """), {"id": project_id, "name": (project.get("name") or "My business")[:255], "tz": settings.site_timezone})
            db.commit()
    except Exception as e:  # noqa: BLE001 - any database failure ends the deploy with a readable message
        raise SiteCatalogError(f"Site catalog setup failed: {type(e).__name__}: {str(e).splitlines()[0][:300]}") from e
    return PreparedSite(site_env(project_id), str(status))

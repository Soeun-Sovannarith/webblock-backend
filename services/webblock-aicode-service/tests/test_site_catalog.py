"""Generated sites' data in the shared catalog (sql/007_catalog.sql): per-site roles and passwords, the site's
settings, registering and seeding a website, the DEPLOY step's handling, and the migration against the team's
database design. No database."""
import base64
import hashlib
import hmac
import json
import re
from pathlib import Path
import httpx
import pytest
from app.core.config import settings
from app.services import deployment, graph_pipeline, site_catalog, store
from app.services.scaffold import TEMPLATE_DIR, catalog_schema, refresh_templates

PROJECT = "11111111-1111-1111-1111-111111111111"
SQL = "\n".join(p.read_text(encoding="utf-8")
                for p in sorted((Path(__file__).resolve().parent.parent / "sql").glob("*.sql")) if p.name >= "007")

# The team's "Database Design" page, section "Shared catalog": every table and column, in order.
DESIGN = {
    "business_types": ["business_type_id", "code", "name", "is_active", "created_at"],
    "product_types": ["product_type_id", "business_type_id", "code", "name", "is_active", "created_at"],
    "product_type_schemas": ["product_type_schema_id", "product_type_id", "version_number", "field_schema", "is_active",
                             "created_at"],
    "websites": ["website_id", "business_type_id", "db_role_name", "status", "storage_limit_bytes", "max_image_bytes",
                 "storage_used_bytes", "created_at", "updated_at"],
    "business_profiles": ["website_id", "business_name", "description", "logo_storage_key", "logo_byte_size", "phone",
                          "email", "address", "timezone", "opening_hours", "created_at", "updated_at"],
    "categories": ["category_id", "website_id", "name", "slug", "sort_order", "created_at", "updated_at"],
    "products": ["product_id", "website_id", "product_type_schema_id", "category_id", "name", "description", "price",
                 "attributes", "status", "sort_order", "row_version", "created_at", "updated_at"],
    "product_images": ["product_image_id", "website_id", "product_id", "storage_key", "byte_size", "alt_text",
                       "sort_order", "created_at"],
}
# WebBlock's documented additions for the owner portal.
EXTENSIONS = {"business_profiles": ["maps_url", "portal_edited_at"],
              "portal_revoked_sessions": ["website_id", "sid", "revoked_at"]}
WEBSITE_SCOPED = ["business_profiles", "categories", "products", "product_images", "portal_revoked_sessions"]


def _configure(monkeypatch, **overrides):
    values = {"database_url": "postgresql+psycopg://harness:pw@db.internal:6543/ai_codegen", "user_name": "",
              "db_password": "", "site_db_host": "", "site_db_port": 0, "site_db_name": "", "site_db_sslmode": "require",
              "site_db_secret": "s3cret", "site_role_admin": "", "site_storage_limit_bytes": 1000,
              "site_max_image_bytes": 600, "site_timezone": "Asia/Phnom_Penh", "site_storage_endpoint": "",
              "site_storage_region": "us-east-1", "site_storage_bucket": "", "site_storage_access_key_id": "",
              "site_storage_secret_access_key": "", **overrides}
    for name, value in values.items():
        monkeypatch.setattr(settings, name, value)


def _columns(table: str) -> list[str]:
    body = re.search(rf"CREATE TABLE IF NOT EXISTS catalog\.{table} \((.*?)\n\);", SQL, re.S).group(1)
    names = [line.split()[0] for line in body.splitlines() if line.strip()]
    return [n for n in names if n not in ("UNIQUE", "FOREIGN", "PRIMARY", "CONSTRAINT") and not n.startswith("REFERENCES")]


def test_the_catalog_has_exactly_the_designed_tables_and_columns():
    tables = re.findall(r"CREATE TABLE IF NOT EXISTS catalog\.(\w+)", SQL)
    assert set(tables) == set(DESIGN) | {"portal_revoked_sessions"}
    for table, columns in DESIGN.items():
        actual = _columns(table)
        extensions = EXTENSIONS.get(table, [])
        assert [c for c in actual if c not in extensions] == columns, table  # the design's columns, in its order
        assert sorted(set(actual) - set(columns)) == sorted(extensions), table  # plus only the documented extensions
    assert _columns("portal_revoked_sessions") == EXTENSIONS["portal_revoked_sessions"]


def test_the_designed_status_values_are_used():
    assert "catalog.website_status AS ENUM ('active','suspended','archived')" in SQL
    assert "catalog.product_status AS ENUM ('draft','published','archived')" in SQL


def test_every_website_table_is_isolated_and_defaults_to_the_sites_website():
    policed = {t for block in re.findall(r"ARRAY\[([^\]]*)\] LOOP\s+EXECUTE format\('ALTER TABLE catalog\.%I ENABLE", SQL)
               for t in re.findall(r"'(\w+)'", block)}
    assert policed == set(WEBSITE_SCOPED)
    for table in WEBSITE_SCOPED:
        body = re.search(rf"CREATE TABLE IF NOT EXISTS catalog\.{table} \((.*?)\n\);", SQL, re.S).group(1)
        assert re.search(r"website_id uuid (PRIMARY KEY |NOT NULL DEFAULT catalog\.current_website_id\(\) )REFERENCES catalog\.websites", body), table
    assert "USING (website_id = (SELECT catalog.current_website_id()))" in SQL
    assert "CREATE POLICY website_own_row ON catalog.websites FOR SELECT USING (db_role_name = current_user)" in SQL
    # Children reference their parent together with website_id, so no row can point into another website.
    assert "FOREIGN KEY (website_id, category_id) REFERENCES catalog.categories(website_id, category_id)" in SQL
    assert "FOREIGN KEY (website_id, product_id) REFERENCES catalog.products(website_id, product_id)" in SQL


def test_sites_cannot_write_websites_or_create_profiles():
    grants = re.search(r"IF EXISTS \(SELECT 1 FROM pg_roles WHERE rolname = 'webblock_site'\) THEN(.*?)END IF;", SQL, re.S).group(1)
    assert "GRANT SELECT ON catalog.business_types, catalog.product_types, catalog.product_type_schemas, catalog.websites" in grants
    assert "GRANT SELECT, UPDATE ON catalog.business_profiles" in grants
    assert not re.search(r"(INSERT|UPDATE|DELETE)[^;]*catalog\.websites", grants)


def test_every_table_the_site_schema_declares_exists_in_the_catalog():
    declared = set(re.findall(r'catalog\.table\(\s*"(\w+)"', catalog_schema()))
    assert declared == set(DESIGN) | {"portal_revoked_sessions"}


def test_role_name_and_password_are_stable_per_project(monkeypatch):
    _configure(monkeypatch)
    assert site_catalog.role_name(PROJECT) == "site_11111111111111111111111111111111"
    password = site_catalog.role_password(PROJECT)
    assert re.fullmatch(r"[0-9a-f]{64}", password) and password == site_catalog.role_password(PROJECT.upper())
    assert password != site_catalog.role_password("22222222-2222-2222-2222-222222222222")
    monkeypatch.setattr(settings, "site_db_secret", "other")
    assert site_catalog.role_password(PROJECT) != password


def test_scram_verifier_matches_postgres_format():
    salt = b"0123456789abcdef"
    verifier = site_catalog.scram_verifier("pencil", salt=salt)
    iterations, rest = verifier.removeprefix("SCRAM-SHA-256$").split(":", 1)
    salt_b64, keys = rest.split("$")
    stored_b64, server_b64 = keys.split(":")
    assert iterations == "4096" and base64.b64decode(salt_b64) == salt
    salted = hashlib.pbkdf2_hmac("sha256", b"pencil", salt, 4096)
    assert base64.b64decode(stored_b64) == hashlib.sha256(hmac.new(salted, b"Client Key", hashlib.sha256).digest()).digest()
    assert base64.b64decode(server_b64) == hmac.new(salted, b"Server Key", hashlib.sha256).digest()
    assert "pencil" not in verifier and site_catalog.scram_verifier("pencil") != site_catalog.scram_verifier("pencil")


def test_site_env_uses_the_sites_own_role_and_adds_storage_when_set(monkeypatch):
    _configure(monkeypatch)
    env = site_catalog.site_env(PROJECT)
    assert env == {"PGHOST": "db.internal", "PGPORT": "6543", "PGDATABASE": "ai_codegen",
                   "PGUSER": site_catalog.role_name(PROJECT), "PGPASSWORD": site_catalog.role_password(PROJECT),
                   "PGSSLMODE": "require"}
    _configure(monkeypatch, site_db_host="10.0.0.5", site_storage_endpoint="https://minio.example.com/",
               site_storage_bucket="sites", site_storage_access_key_id="AK", site_storage_secret_access_key="SK")
    env = site_catalog.site_env(PROJECT)
    assert env["PGHOST"] == "10.0.0.5" and env["STORAGE_ENDPOINT"] == "https://minio.example.com"
    assert {env["STORAGE_BUCKET"], env["STORAGE_ACCESS_KEY_ID"], env["STORAGE_SECRET_ACCESS_KEY"]} == {"sites", "AK", "SK"}


class _Session:
    """Records the statements prepare_site sends; pg_roles answers `role_exists`."""

    def __init__(self, log, *, role_exists=False, member=True, fail=False):
        self.log, self.role_exists, self.member, self.fail = log, role_exists, member, fail

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, statement, params=None):
        sql = str(statement)
        if self.fail:
            raise RuntimeError("permission denied to create role\nDETAIL: more")
        self.log.append(("sql", sql, params))
        result = type("R", (), {})()
        result.first = lambda: (1,) if self.role_exists else None
        result.scalar = lambda: self.member
        result.scalar_one = lambda: "active"
        return result

    def connection(self):
        log = self.log

        class Driver:
            def execute(self, sql):
                log.append(("driver", sql, None))
        return type("C", (), {"connection": type("D", (), {"driver_connection": Driver()})()})()

    def commit(self):
        self.log.append(("commit", None, None))


def test_prepare_site_creates_the_role_registers_the_website_and_seeds_it(monkeypatch):
    _configure(monkeypatch)
    log = []
    monkeypatch.setattr(site_catalog, "SessionLocal", lambda: _Session(log))
    prepared = site_catalog.prepare_site({"project_id": PROJECT, "name": "Sok Coffee", "business_type_id": None},
                                         "INSERT INTO catalog.products SELECT 1;", "Cafe")
    role = site_catalog.role_name(PROJECT)
    driver = [entry[1] for entry in log if entry[0] == "driver"]
    assert driver[0].startswith(f'CREATE ROLE "{role}" WITH LOGIN PASSWORD \'SCRAM-SHA-256$4096:')
    assert driver[0].endswith('CONNECTION LIMIT 3 IN ROLE "webblock_site"')
    assert site_catalog.role_password(PROJECT) not in "".join(e[1] or "" for e in log)  # only the verifier is sent
    website = next(e for e in log if e[0] == "sql" and "INSERT INTO catalog.websites" in e[1])
    assert website[2]["code"] == "cafe" and website[2]["role"] == role and website[2]["limit"] == 1000
    seed_index = next(i for i, e in enumerate(log) if e[0] == "sql" and "set_config" in e[1])
    assert log[seed_index][2] == {"name": "webblock.website_id", "id": PROJECT}
    assert log[seed_index + 1] == ("driver", "INSERT INTO catalog.products SELECT 1;", None)
    profile = next(i for i, e in enumerate(log) if e[0] == "sql" and "INSERT INTO catalog.business_profiles" in e[1])
    assert profile > seed_index + 1  # the onboarding profile from the seed wins over the fallback
    assert log[-1][0] == "commit" and prepared.status == "active" and prepared.env["PGUSER"] == role


def test_prepare_site_pins_the_search_path_and_string_rules_before_anything_else(monkeypatch):
    # A site role could plant public.=(varchar, text) for the seed's slug join, which runs as the RLS-bypassing owner,
    # and a SET inside the seed string can't change how that string's literals are read.
    _configure(monkeypatch)
    log = []
    monkeypatch.setattr(site_catalog, "SessionLocal", lambda: _Session(log))
    site_catalog.prepare_site({"project_id": PROJECT}, "SELECT 1;")
    assert [e[1] for e in log[:2]] == ["SET LOCAL search_path = pg_catalog, pg_temp",
                                       "SET LOCAL standard_conforming_strings = on"]
    assert all(e[0] == "sql" for e in log[:2])


def test_no_role_may_create_in_public_and_the_group_role_cannot_log_in():
    assert "REVOKE CREATE ON SCHEMA public FROM PUBLIC;" in SQL
    assert "ALTER ROLE webblock_site NOLOGIN;" in SQL


def test_a_deploy_error_never_echoes_the_site_env_secrets(monkeypatch):
    monkeypatch.setattr(settings, "deployment_provider", "webhook")
    monkeypatch.setattr(settings, "deployment_webhook_url", "https://deploy.test/hook")
    env = {"PGUSER": "site_abc", "PGPASSWORD": "a" * 64, "STORAGE_SECRET_ACCESS_KEY": "wJal/rXUtn+FEMI/K7MDENG",
           "KEYCLOAK_CLIENT_SECRET": "kc-secret-value", "PGSSLMODE": "require"}

    def post(url, content, headers, timeout):  # a receiver that answers 422 with the request it got, JSON-escaped
        echo = json.dumps({"detail": [{"msg": "Field required", "input": json.loads(content)}]}).replace("/", "\\/")
        return httpx.Response(422, text=echo, request=httpx.Request("POST", url))
    monkeypatch.setattr(deployment.httpx, "post", post)
    with pytest.raises(deployment.DeploymentError) as error:
        deployment.deploy({"project_id": PROJECT}, "rel-1", "abc", None, site_env=env)
    message = str(error.value)
    assert "HTTP 422" in message and "[redacted]" in message and "Field required" in message
    for secret in ("a" * 16, "wJal", "kc-secret-value"):
        assert secret not in message
    assert "site_abc" in message  # names and non-secret settings stay readable


def test_prepare_site_rekeys_an_existing_role_as_the_role_admin(monkeypatch):
    _configure(monkeypatch, site_role_admin="webblock_role_admin")
    log = []
    monkeypatch.setattr(site_catalog, "SessionLocal", lambda: _Session(log, role_exists=True, member=False))
    site_catalog.prepare_site({"project_id": PROJECT}, None)
    driver = [entry[1] for entry in log if entry[0] == "driver"]
    role = site_catalog.role_name(PROJECT)
    assert driver[0] == 'SET LOCAL ROLE "webblock_role_admin"'
    assert driver[1].startswith(f'ALTER ROLE "{role}" WITH LOGIN PASSWORD')
    assert driver[2] == f'GRANT "webblock_site" TO "{role}"' and driver[3] == "RESET ROLE"
    assert not any("set_config" in (e[1] or "") for e in log)  # no seed, no seeding


def test_prepare_site_reports_a_database_failure_in_one_line(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(site_catalog, "SessionLocal", lambda: _Session([], fail=True))
    with pytest.raises(site_catalog.SiteCatalogError, match=r"^Site catalog setup failed: RuntimeError: permission denied to create role$"):
        site_catalog.prepare_site({"project_id": PROJECT}, None)


def test_deploy_skips_the_catalog_when_not_configured(monkeypatch):
    _configure(monkeypatch, site_db_secret="")
    lines = []
    monkeypatch.setattr(store, "append_log", lambda run_id, line: lines.append(line))
    assert graph_pipeline._prepare_site_catalog("run", {"project_id": PROJECT}, {}, None) == {}
    assert lines == ["SKIPPED: [DEPLOY] site catalog (SITE_DB_SECRET is not set); the site deploys without a database."]


def test_deploy_fails_when_the_catalog_setup_fails(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(store, "append_log", lambda run_id, line: None)

    def fail(project, seed_sql, business_type):
        raise site_catalog.SiteCatalogError("Site catalog setup failed: OperationalError: timeout")
    monkeypatch.setattr(site_catalog, "prepare_site", fail)
    with pytest.raises(deployment.DeploymentError, match="Site catalog setup failed"):
        graph_pipeline._prepare_site_catalog("run", {"project_id": PROJECT}, {"db/seed.sql": "SELECT 1;"}, None)


def test_deploy_passes_the_seed_and_business_type_and_logs_no_secret(monkeypatch):
    _configure(monkeypatch, site_storage_endpoint="https://s3.example.com", site_storage_bucket="b",
               site_storage_access_key_id="AK", site_storage_secret_access_key="storage-secret")
    lines, seen = [], {}
    monkeypatch.setattr(store, "append_log", lambda run_id, line: lines.append(line))

    def prepare(project, seed_sql, business_type):
        seen.update(seed=seed_sql, business_type=business_type)
        return site_catalog.PreparedSite(site_catalog.site_env(project["project_id"]), "suspended")
    monkeypatch.setattr(site_catalog, "prepare_site", prepare)
    env = graph_pipeline._prepare_site_catalog("run", {"project_id": PROJECT}, {"db/seed.sql": "SELECT 1;"},
                                               {"business": {"business_type": "restaurant"}, "products": []})
    assert seen == {"seed": "SELECT 1;", "business_type": "restaurant"}
    assert any(line.startswith("WARNING: [DEPLOY] the website is suspended") for line in lines)
    assert not any(env["PGPASSWORD"] in line or "storage-secret" in line for line in lines)


def test_deploy_release_reads_the_seed_from_the_captured_input(monkeypatch):
    # The input version keeps the seed under captured_input; reading target["seed"] registered every onboarded
    # website as everyday_store (found by the e2e run of 31 Oct 2026).
    seen = {}
    for name in ("create_release", "start_release_step"):
        monkeypatch.setattr(store, name, lambda *a, **k: "id-1")
    for name in ("finish_release_step", "finish_release", "append_log"):
        monkeypatch.setattr(store, name, lambda *a, **k: None)
    monkeypatch.setattr(graph_pipeline.artifacts, "append_text", lambda *a, **k: None)
    monkeypatch.setattr(graph_pipeline, "_ensure_portal_sso", lambda *a, **k: None)
    monkeypatch.setattr(graph_pipeline, "_prepare_site_catalog", lambda run, project, files, seed: seen.update(seed=seed) or {})
    monkeypatch.setattr(deployment, "deploy", lambda *a, **k: deployment.DeployResult("SKIPPED", message="off"))
    seed = {"business": {"business_type": "cafe"}, "products": []}
    graph_pipeline._deploy_release({"run_id": "r1", "project": {"project_id": PROJECT}, "files": {},
                                    "request": {"generation_request_id": "q1"}, "target": {"captured_input": {"seed": seed}}})
    assert seen["seed"] == seed


def test_refresh_removes_the_per_site_database_setup():
    files = {"drizzle.config.ts": "old", "db/seed.mjs": "old", "db/schema.ts": "old"}
    removed = refresh_templates(files)
    assert {"drizzle.config.ts", "db/seed.mjs"} <= set(removed)
    assert "drizzle.config.ts" not in files and files["db/schema.ts"] == (TEMPLATE_DIR / "db" / "schema.ts").read_text()

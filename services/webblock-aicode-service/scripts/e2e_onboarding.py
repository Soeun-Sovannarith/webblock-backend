"""End-to-end check of business onboarding -> generated Next.js + Drizzle site, against a running API.

    python scripts/e2e_onboarding.py                 # onboard, show schema.ts + seed.sql, generate, download the site
    python scripts/e2e_onboarding.py --serve         # ...then run the site on the shared catalog (Docker)
    python scripts/e2e_onboarding.py --mode llm      # let GLM propose the products and write the code
    python scripts/e2e_onboarding.py --business my_business.json

Needs the API on --api (`uvicorn app.main:app`) with its worker. --serve also needs Docker and the compose Postgres
at migration 007 (`docker compose up -d`, `docker compose up migrate`): it builds the site's image from its Dockerfile
(as the platform's Jenkins does), gives the site its role and website in the compose database's shared catalog and
applies its db/seed.sql (site_catalog.prepare_site, as the DEPLOY step does), then runs the image as Provisioning
does: read-only root, only /tmp writable, UID 65532, port 8080, the PG* variables, logged in as the site's own role so
row-level security applies. Generated code never runs on the host. It uses the compose database only, whatever
DATABASE_URL the API uses.
Everything is written under var/e2e/<project_id>/.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import UUID

SAMPLE_BUSINESS = {
    "business_name": "Sok Coffee",
    "business_type": "cafe",
    "business_description": "A small specialty coffee shop in Phnom Penh serving Cambodian-grown beans and fresh pastries.",
    "phone_number": "012 345 678",
    "email": "hello@sokcoffee.example",
    "address": "St 240, Phnom Penh",
    "opening_hours": [{"day": "Monday", "is_open": True, "opens": "7:00 AM", "closes": "6:00 PM"},
                      {"day": "Sunday", "is_open": False}],
    "theme": "warm",
    "accent": "#a4532b",
    "font": "sans",
}
DB_USER = DB_PASSWORD = "webblock"
DB_PORT = 5433
# Derives the site roles' passwords on the compose database only (SITE_DB_SECRET).
LOCAL_SITE_DB_SECRET = "e2e-local-only"
CONTAINER = "wb-e2e-site"


def banner(text: str) -> None:
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


def call(api: str, method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(api + path, method=method, headers={"content-type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as e:
        sys.exit(f"{method} {path} -> {e.code}: {e.read().decode()[:2000]}")
    except urllib.error.URLError as e:
        sys.exit(f"Cannot reach the API at {api} ({e.reason}). Start it with: uvicorn app.main:app")


def onboard(api: str, business: dict, out: Path) -> dict:
    banner("1. POST /v1/onboardings")
    res = call(api, "POST", "/v1/onboardings", business)
    out = out / res["project_id"]
    (out / "onboarding").mkdir(parents=True, exist_ok=True)
    (out / "onboarding" / "response.json").write_text(json.dumps(res, indent=2, ensure_ascii=False))
    (out / "onboarding" / "schema.ts").write_text(res["drizzle_schema_preview"])
    (out / "onboarding" / "seed.sql").write_text(res["seed_sql_preview"])
    print(f"project_id: {res['project_id']}\nrun_id:     {res['run_id']}\nstatus:     {res['status']}\n"
          f"products:   from {res['seed_source']}")
    banner("db/schema.ts (drizzle_schema_preview)")
    print(res["drizzle_schema_preview"])
    banner("db/seed.sql (seed_sql_preview)")
    print(res["seed_sql_preview"])
    res["_out"] = out
    return res


def wait_for_run(api: str, run_id: str, timeout: int) -> dict:
    banner(f"2. Generation run {run_id} (GET /v1/generation-runs/{{id}})")
    printed, deadline = 0, time.time() + timeout
    while True:
        run = call(api, "GET", f"/v1/generation-runs/{run_id}")
        lines = (run.get("log") or "").splitlines()
        for line in lines[printed:]:
            if line.startswith(("[", "SKIPPED:", "WARNING:", "Portal SSO", "Build", "GLM")) or "error" in line.lower():
                print("  " + line[:200])
        printed = len(lines)
        if run["status"] in ("SUCCEEDED", "FAILED"):
            print(f"\nstatus: {run['status']}  step: {run.get('step')}  error_code: {run.get('error_code')}")
            print(f"github: {run.get('repository_url') or '(not pushed)'}  commit: {run.get('commit_sha') or '-'}")
            return run
        if time.time() > deadline:
            sys.exit(f"Run still {run['status']} after {timeout}s")
        time.sleep(3)


def download_site(api: str, run_id: str, onboarding: dict, out: Path) -> Path:
    banner("3. Generated source (GET /v1/generation-runs/{id}/files)")
    files = call(api, "GET", f"/v1/generation-runs/{run_id}/files")
    if not files:
        sys.exit("The run produced no SOURCE_ARCHIVE.")
    site = out / "site"
    for path, content in files.items():
        target = (site / path).resolve()
        if site.resolve() not in target.parents:
            sys.exit(f"Refusing to write outside the site directory: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)
    for path in sorted(files):
        print(f"  {path:<55} {len(files[path]):>7} bytes")
    print(f"\n{len(files)} files written to {site}")
    same_seed = files.get("db/seed.sql") == onboarding["seed_sql_preview"]
    tables = [line.split('"')[1] for line in files.get("db/schema.ts", "").splitlines() if "pgTable(" in line]
    print(f"db/seed.sql identical to the onboarding preview: {same_seed}")
    print(f"db/schema.ts tables: {', '.join(tables)}")
    return site


def psql(*args: str, script: str | None = None) -> str:
    return subprocess.run(["docker", "compose", "exec", "-T", "db", "psql", "-U", DB_USER, "-d", "webblock", "-v",
                           "ON_ERROR_STOP=1", *args], input=script, check=True, capture_output=True, text=True).stdout


def prepare_site_catalog(site: Path, project_id: str, name: str, business_type: str | None) -> dict[str, str]:
    """site_catalog.prepare_site against the compose database (as its superuser, which may create roles): the site's
    role, its website and its seed. Returns the site's database settings."""
    if psql("-Atc", "SELECT to_regclass('catalog.websites') IS NOT NULL").strip() != "t":
        sys.exit("The compose database has no catalog schema yet. Run: docker compose up migrate")
    os.environ.update({"DATABASE_URL": f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@localhost:{DB_PORT}/webblock",
                       "USER_NAME": "", "DB_PASSWORD": "", "SITE_DB_SECRET": LOCAL_SITE_DB_SECRET, "SITE_ROLE_ADMIN": "",
                       "SITE_DB_SSLMODE": "disable"})
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from app.services import site_catalog  # imported here, after the settings above point it at compose
    project = {"project_id": str(UUID(project_id)), "name": name, "business_type_id": None}
    return site_catalog.prepare_site(project, (site / "db" / "seed.sql").read_text(encoding="utf-8"), business_type).env


def serve(site: Path, project_id: str, port: int, business: dict) -> None:
    banner(f"4. Run the site on the shared catalog (container {CONTAINER}, http://localhost:{port})")
    image = f"wb-e2e-site:{project_id[:8]}"
    print(f"docker build {image} (the site's Dockerfile, linux/amd64) ...")
    subprocess.run(["docker", "build", "--platform", "linux/amd64", "-q", "-t", image, str(site)], check=True,
                   capture_output=True)
    pg = {**prepare_site_catalog(site, project_id, business.get("business_name", "project"), business.get("business_type")),
          "PGHOST": "host.docker.internal"}
    print(f"catalog website {project_id} ready as role {pg['PGUSER']} (compose Postgres, localhost:{DB_PORT})")
    env = [arg for key, value in pg.items() for arg in ("-e", f"{key}={value}")]
    subprocess.run(["docker", "rm", "-f", CONTAINER], capture_output=True)
    subprocess.run(["docker", "run", "-d", "--name", CONTAINER, "--platform", "linux/amd64", "--read-only",
                    "--tmpfs", "/tmp:size=64m", "--user", "65532:65532", "--cap-drop", "ALL",
                    "--security-opt", "no-new-privileges", "-p", f"{port}:8080", *env,
                    "-e", f"SITE_URL=http://localhost:{port}", image], check=True, capture_output=True)
    deadline = time.time() + 120
    while time.time() < deadline:
        state = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", CONTAINER],
                               capture_output=True, text=True).stdout.strip()
        if state != "true":
            print(subprocess.run(["docker", "logs", "--tail", "60", CONTAINER], capture_output=True, text=True).stdout)
            sys.exit("The site container stopped.")
        try:
            with urllib.request.urlopen(f"http://localhost:{port}/", timeout=10) as resp:
                home = resp.read().decode()
                break
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(2)
    else:
        sys.exit("The site did not start within 2 minutes.")
    for path in ("/healthz", "/readyz"):
        try:
            with urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=10) as resp:
                print(f"GET {path:<9} {resp.status}")
        except urllib.error.HTTPError as e:
            print(f"GET {path:<9} {e.code}")

    project = str(UUID(project_id))
    banner("The site's rows in the shared catalog")
    print(psql("-c", r"\dt catalog.*"))
    print(psql("-c", "SELECT bp.business_name, bt.code AS business_type, bp.phone, bp.address FROM catalog.business_profiles bp "
                     "JOIN catalog.websites w USING (website_id) JOIN catalog.business_types bt USING (business_type_id) "
                     f"WHERE bp.website_id = '{project}'"))
    print(psql("-c", "SELECT p.name, p.price, c.name AS category FROM catalog.products p LEFT JOIN catalog.categories c "
                     f"USING (category_id) WHERE p.website_id = '{project}' ORDER BY p.sort_order"))

    banner("Pages served by the running site")
    first = psql("-Atc", f"SELECT name FROM catalog.products WHERE website_id = '{project}' ORDER BY sort_order LIMIT 1").strip()
    print(f"GET /           200, {len(home)} bytes, shows first product from the database: {first in home}")
    links = sorted({part.split('"')[0] for part in home.split('href="/products/')[1:]})
    for path in [f"/products/{links[0]}"] if links else []:
        with urllib.request.urlopen(f"http://localhost:{port}{path}", timeout=30) as resp:
            print(f"GET {path}  {resp.status}")
    with urllib.request.urlopen(f"http://localhost:{port}/portal", timeout=30) as resp:
        print(f"GET /portal     {resp.status} (sign-in needs Keycloak settings; without them it says so)")
    print(f"\nOpen http://localhost:{port} in a browser. Stop it with: docker rm -f {CONTAINER}")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--api", default="http://localhost:8000")
    p.add_argument("--business", type=Path, help="JSON file with a BusinessOnboardingRequest (default: a sample cafe)")
    p.add_argument("--mode", choices=["hybrid", "llm"], help="codegen_mode for this request")
    p.add_argument("--out", type=Path, default=Path("var/e2e"))
    p.add_argument("--serve", action="store_true", help="run the generated site in Docker on the compose catalog")
    p.add_argument("--port", type=int, default=3100)
    p.add_argument("--timeout", type=int, default=1800, help="seconds to wait for the generation run")
    args = p.parse_args()

    business = json.loads(args.business.read_text()) if args.business else dict(SAMPLE_BUSINESS)
    if args.mode:
        business["codegen_mode"] = args.mode
    onboarding = onboard(args.api.rstrip("/"), business, args.out)
    if not onboarding.get("run_id"):
        sys.exit("No run was queued (identical input to the baseline, or another run is active).")
    run = wait_for_run(args.api.rstrip("/"), onboarding["run_id"], args.timeout)
    if run["status"] != "SUCCEEDED":
        sys.exit(f"Run failed: {run.get('error_code')}. Full log: GET /v1/generation-runs/{onboarding['run_id']}")
    site = download_site(args.api.rstrip("/"), onboarding["run_id"], onboarding, onboarding["_out"])
    if args.serve:
        serve(site, onboarding["project_id"], args.port, business)


if __name__ == "__main__":
    main()

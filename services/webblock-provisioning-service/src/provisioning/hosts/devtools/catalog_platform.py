"""STAND-IN for Project's side of the shared catalog, prototype only (decision 0014 section 14).

The design (Notion 6 · Shared catalog) gives Project the catalog schema and the first rows of
each website; this tool plays that part on the prototype stack, and the part of a website's CMS that
fills it with content:

    python -m provisioning.hosts.devtools.catalog_platform register --project <id> \
        --business-type clothing_shop
    python -m provisioning.hosts.devtools.catalog_platform seed --project <id> --file <seed.json>

- ``register`` inserts the ``catalog.websites`` row (``db_role_name = site_<hex>``, status
  ``active``) and the first ``catalog.business_profiles`` row, logged in as
  ``project_catalog_platform`` (a member of ``catalog_platform``). Running it again changes nothing.
  The business type's ID is looked up by its code as the catalog owner (``catalog_platform`` has no
  privilege on ``catalog.business_types`` in the design).
- ``seed`` loads a seed file (format: deploy/prototype/demo-sites/clothing-shop/seed/README.md)
  **as the website's own role** ``site_<hex>``, through row-level security, the way the site's CMS
  would: the profile is updated, then categories, products and their images are inserted. The role's
  credential is read from the Secret ``catalog-runtime`` that access_setup wrote, with the stack's
  admin kubeconfig; it is never printed. A website that already has categories is refused.

Addresses and credentials come from ``bin/kind-state/proto/stack.env`` and the ``catalog-admin.env``
it names (``make proto-up``). Every value is a bound parameter; nothing is built into SQL text.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from provisioning.infrastructure.catalog import site_role_name

DEFAULT_STACK_ENV = Path("bin/kind-state/proto/stack.env")
SEED_FORMAT_VERSION = 1
CONNECT_TIMEOUT_SECONDS = 10
APPLICATION_NAME = "catalog-platform-standin"
STATEMENT_OPTIONS = "-c statement_timeout=30000 -c lock_timeout=5000"
KUBE_FIELD_MANAGER = "webblock-catalog-platform-tool"
_POISONED_KUBECONFIG = "/nonexistent/webblock-devtools-never-read-a-default-kubeconfig"

# --- statements: module-level constants only ------------------------------------------------------

BUSINESS_TYPE_ID = "SELECT business_type_id FROM catalog.business_types WHERE code = %s"
INSERT_WEBSITE = """
INSERT INTO catalog.websites (website_id, business_type_id, db_role_name, status,
                              storage_limit_bytes, max_image_bytes, storage_used_bytes,
                              created_at, updated_at)
VALUES (%s, %s, %s, 'active', %s, %s, 0, now(), now())
ON CONFLICT DO NOTHING
"""
INSERT_FIRST_PROFILE = """
INSERT INTO catalog.business_profiles (website_id, business_name, timezone, opening_hours,
                                       created_at, updated_at)
VALUES (%s, %s, %s, '[]', now(), now())
ON CONFLICT DO NOTHING
"""
CURRENT_WEBSITE = "SELECT catalog.current_website_id()"
CATEGORY_COUNT = "SELECT count(*) FROM catalog.categories"
UPDATE_PROFILE = """
UPDATE catalog.business_profiles
SET business_name = %s, description = %s, phone = %s, email = %s, address = %s, timezone = %s,
    opening_hours = %s, updated_at = now()
WHERE website_id = %s
"""
INSERT_PROFILE = """
INSERT INTO catalog.business_profiles (website_id, business_name, description, phone, email,
                                       address, timezone, opening_hours, created_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now(), now())
"""
INSERT_CATEGORY = """
INSERT INTO catalog.categories (category_id, website_id, name, slug, sort_order, created_at,
                                updated_at)
VALUES (%s, %s, %s, %s, %s, now(), now())
"""
ACTIVE_SCHEMA = """
SELECT s.product_type_schema_id, s.field_schema
FROM catalog.product_type_schemas s
JOIN catalog.product_types t ON t.product_type_id = s.product_type_id
JOIN catalog.business_types b ON b.business_type_id = t.business_type_id
WHERE b.code = %s AND t.code = %s AND s.is_active
ORDER BY s.version_number DESC
LIMIT 1
"""
INSERT_PRODUCT = """
INSERT INTO catalog.products (product_id, website_id, product_type_schema_id, category_id, name,
                              description, price, attributes, status, sort_order, row_version,
                              created_at, updated_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 1, now(), now())
"""
INSERT_IMAGE = """
INSERT INTO catalog.product_images (product_image_id, website_id, product_id, storage_key,
                                    byte_size, alt_text, sort_order, created_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, now())
"""


class ToolError(Exception):
    """A refusal with a message for the person running the tool; never a credential."""


# --- the seed file --------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SeedImage:
    file: str
    byte_size: int
    alt_text: str | None
    sort_order: int


@dataclass(frozen=True, slots=True)
class SeedProduct:
    product_type: str
    category_slug: str | None
    name: str
    description: str | None
    price: Decimal | None
    attributes: dict[str, Any]
    status: str
    sort_order: int
    images: tuple[SeedImage, ...]


@dataclass(frozen=True, slots=True)
class SeedCategory:
    slug: str
    name: str
    sort_order: int


@dataclass(frozen=True, slots=True)
class SeedProfile:
    business_name: str
    description: str | None
    phone: str | None
    email: str | None
    address: str | None
    timezone: str
    opening_hours: list[Any]


@dataclass(frozen=True, slots=True)
class Seed:
    business_type: str
    profile: SeedProfile | None
    categories: tuple[SeedCategory, ...]
    products: tuple[SeedProduct, ...]


def _field(obj: Mapping[str, Any], key: str, kind: type | tuple[type, ...], where: str) -> Any:
    if key not in obj:
        raise ToolError(f"{where}: {key} is missing")
    value = obj[key]
    if not isinstance(value, kind) or (isinstance(value, bool) and kind is int):
        raise ToolError(f"{where}: {key} has the wrong type")
    return value


def _optional(obj: Mapping[str, Any], key: str, where: str) -> str | None:
    value = obj.get(key)
    if value is not None and not isinstance(value, str):
        raise ToolError(f"{where}: {key} must be text or null")
    return value


def parse_seed(data: Any) -> Seed:
    """Validate a seed document (format version 1) and return it typed. Refusals name the field,
    never put a value into SQL."""
    if not isinstance(data, dict):
        raise ToolError("the seed must be a JSON object")
    if data.get("format_version") != SEED_FORMAT_VERSION:
        raise ToolError(f"format_version must be {SEED_FORMAT_VERSION}")
    business_type = _field(data, "business_type", str, "seed")
    profile = None
    if data.get("business_profile") is not None:
        p = _field(data, "business_profile", dict, "seed")
        hours = p.get("opening_hours", [])
        if not isinstance(hours, list):
            raise ToolError("business_profile: opening_hours must be an array")
        profile = SeedProfile(
            business_name=_field(p, "business_name", str, "business_profile"),
            description=_optional(p, "description", "business_profile"),
            phone=_optional(p, "phone", "business_profile"),
            email=_optional(p, "email", "business_profile"),
            address=_optional(p, "address", "business_profile"),
            timezone=_field(p, "timezone", str, "business_profile"),
            opening_hours=hours,
        )
    categories = tuple(
        SeedCategory(
            slug=_field(c, "slug", str, f"categories[{i}]"),
            name=_field(c, "name", str, f"categories[{i}]"),
            sort_order=_field(c, "sort_order", int, f"categories[{i}]"),
        )
        for i, c in enumerate(_objects(data, "categories", "seed"))
    )
    slugs = [c.slug for c in categories]
    if len(set(slugs)) != len(slugs):
        raise ToolError("categories: slugs must be unique")
    products = tuple(
        _product(p, i, set(slugs)) for i, p in enumerate(_objects(data, "products", "seed"))
    )
    return Seed(business_type, profile, categories, products)


def _objects(obj: Mapping[str, Any], key: str, where: str) -> list[dict[str, Any]]:
    items = _field(obj, key, list, where)
    objects = [item for item in items if isinstance(item, dict)]
    if len(objects) != len(items):
        raise ToolError(f"{where}: every entry of {key} must be an object")
    return objects


def _product(p: dict[str, Any], i: int, slugs: set[str]) -> SeedProduct:
    where = f"products[{i}]"
    slug = _optional(p, "category_slug", where)
    if slug is not None and slug not in slugs:
        raise ToolError(f"{where}: category_slug names no category of the seed")
    raw_price = p.get("price")
    price: Decimal | None = None
    if raw_price is not None:
        if not isinstance(raw_price, str):
            raise ToolError(f"{where}: price must be a decimal string or null")
        try:
            price = Decimal(raw_price)
        except InvalidOperation:
            raise ToolError(f"{where}: price is not a decimal") from None
        if not price.is_finite() or price < 0 or price != price.quantize(Decimal("0.01")):
            raise ToolError(f"{where}: price must be zero or more with at most two decimals")
    status = _field(p, "status", str, where)
    if status not in ("draft", "published", "archived"):
        raise ToolError(f"{where}: status must be draft, published or archived")
    images = tuple(
        SeedImage(
            file=_field(m, "file", str, f"{where}.images[{j}]"),
            byte_size=_field(m, "byte_size", int, f"{where}.images[{j}]"),
            alt_text=_optional(m, "alt_text", f"{where}.images[{j}]"),
            sort_order=_field(m, "sort_order", int, f"{where}.images[{j}]"),
        )
        for j, m in enumerate(_objects(p, "images", where))
    )
    for j, image in enumerate(images):
        if image.file.startswith("/") or ".." in image.file.split("/") or not image.file:
            raise ToolError(f"{where}.images[{j}]: file must be a relative path inside the shop")
        if image.byte_size <= 0:
            raise ToolError(f"{where}.images[{j}]: byte_size must be positive")
    return SeedProduct(
        product_type=_field(p, "product_type", str, where),
        category_slug=slug,
        name=_field(p, "name", str, where),
        description=_optional(p, "description", where),
        price=price,
        attributes=_field(p, "attributes", dict, where),
        status=status,
        sort_order=_field(p, "sort_order", int, where),
        images=images,
    )


def check_attributes(attributes: Mapping[str, Any], field_schema: Mapping[str, Any]) -> None:
    """The attribute keys the pinned schema allows (a schema with ``additionalProperties: false``
    allows only its ``properties``). Values are not validated here."""
    allowed = field_schema.get("properties")
    if field_schema.get("additionalProperties") is False and isinstance(allowed, dict):
        unknown = sorted(set(attributes) - set(allowed))
        if unknown:
            raise ToolError("attributes not in the product type's schema: " + ", ".join(unknown))


# --- the stack ------------------------------------------------------------------------------------


def read_env_file(path: Path) -> dict[str, str]:
    """KEY=VALUE lines; comments and blank lines are skipped. Values are never printed."""
    values = {}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            raise ToolError(f"{path.name}: a line is not KEY=VALUE")
        values[key.strip()] = value
    return values


@dataclass(frozen=True, slots=True)
class Stack:
    host: str
    port: int
    database: str
    credentials: dict[str, str]
    admin_kubeconfig: Path

    @classmethod
    def load(cls, stack_env: Path) -> Stack:
        if not stack_env.is_file():
            raise ToolError(f"{stack_env} does not exist; run make proto-up")
        env = read_env_file(stack_env)
        needed = (
            "PROTO_CATALOG_HOST_ON_HOST",
            "PROTO_CATALOG_PORT_ON_HOST",
            "PROTO_CATALOG_DATABASE",
            "PROTO_CATALOG_ENV",
            "PROTO_KUBECONFIG_ADMIN",
        )
        missing = [k for k in needed if not env.get(k)]
        if missing:
            raise ToolError(f"{stack_env.name} lacks " + ", ".join(missing))
        return cls(
            host=env["PROTO_CATALOG_HOST_ON_HOST"],
            port=int(env["PROTO_CATALOG_PORT_ON_HOST"]),
            database=env["PROTO_CATALOG_DATABASE"],
            credentials=read_env_file(Path(env["PROTO_CATALOG_ENV"])),
            admin_kubeconfig=Path(env["PROTO_KUBECONFIG_ADMIN"]),
        )

    def login(self, name: str) -> tuple[str, str]:
        user = self.credentials.get(f"CATALOG_{name}_USER")
        password = self.credentials.get(f"CATALOG_{name}_PASSWORD")
        if not user or not password:
            raise ToolError(f"catalog-admin.env lacks the {name.lower()} login")
        return user, password

    async def connect(self, user: str, password: str) -> psycopg.AsyncConnection[tuple[Any, ...]]:
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT_SECONDS + 1):
                return await psycopg.AsyncConnection.connect(
                    host=self.host,
                    port=self.port,
                    dbname=self.database,
                    user=user,
                    password=password,
                    sslmode="disable",
                    connect_timeout=CONNECT_TIMEOUT_SECONDS,
                    application_name=APPLICATION_NAME,
                    options=STATEMENT_OPTIONS,
                    require_auth="scram-sha-256",
                )
        except (psycopg.Error, OSError, TimeoutError) as e:
            raise ToolError(f"cannot log in to the catalog as {user} ({_sqlstate(e)})") from None


def _sqlstate(error: BaseException) -> str:
    code = getattr(error, "sqlstate", None)
    return f"SQLSTATE {code}" if isinstance(code, str) else type(error).__name__


# --- register -------------------------------------------------------------------------------------


async def register(
    stack: Stack,
    project_id: UUID,
    business_type: str,
    *,
    name: str,
    timezone: str,
    storage_limit_bytes: int,
    max_image_bytes: int,
) -> bool:
    """Insert the website and its first profile; True when the website row was new."""
    owner = await stack.connect(*stack.login("MIGRATOR"))
    async with owner:
        cursor = await owner.execute(BUSINESS_TYPE_ID, (business_type,))
        row = await cursor.fetchone()
    if row is None:
        raise ToolError(f"no business type with code {business_type!r}")
    platform = await stack.connect(*stack.login("PLATFORM"))
    async with platform, platform.transaction():
        cursor = await platform.execute(
            INSERT_WEBSITE,
            (project_id, row[0], site_role_name(project_id), storage_limit_bytes, max_image_bytes),
        )
        created = cursor.rowcount == 1
        await platform.execute(INSERT_FIRST_PROFILE, (project_id, name, timezone))
    return created


# --- seed -----------------------------------------------------------------------------------------


async def site_credential(stack: Stack, project_id: UUID) -> tuple[str, str]:
    """PGUSER and PGPASSWORD from the Secret catalog-runtime, read with the admin kubeconfig."""
    # Imported here, after main() has poisoned KUBECONFIG: the library fixes its default path when
    # it is first imported, and that default is never to be the owner's kubeconfig.
    from provisioning.infrastructure.kubernetes import Resource, connect_kubeconfig

    api = await connect_kubeconfig(
        stack.admin_kubeconfig.resolve(),
        field_manager=KUBE_FIELD_MANAGER,
        request_timeout_seconds=10.0,
    )
    async with api:
        secret = await api.read(Resource.SECRET, "catalog-runtime", f"wb-{project_id}")
    if secret is None:
        raise ToolError("the Secret catalog-runtime does not exist yet: run access_setup first")
    data = secret.get("data") or {}
    try:
        user = base64.b64decode(data["PGUSER"]).decode()
        password = base64.b64decode(data["PGPASSWORD"]).decode()
    except KeyError, ValueError:
        raise ToolError("the Secret catalog-runtime lacks PGUSER or PGPASSWORD") from None
    if user != site_role_name(project_id):
        raise ToolError("the Secret's PGUSER is not this project's site role")
    return user, password


@dataclass(frozen=True, slots=True)
class SeedResult:
    categories: int
    products: int
    images: int


async def load_seed(
    connection: psycopg.AsyncConnection[tuple[Any, ...]], project_id: UUID, seed: Seed
) -> SeedResult:
    """Load ``seed`` in one transaction on a connection logged in as the website's own role."""
    images = 0
    async with connection.transaction():
        cursor = await connection.execute(CURRENT_WEBSITE)
        row = await cursor.fetchone()
        website = row[0] if row else None
        if website is None:
            raise ToolError("the catalog maps this role to no active website: register it first")
        if website != project_id:
            raise ToolError("the catalog maps this role to another website")
        cursor = await connection.execute(CATEGORY_COUNT)
        row = await cursor.fetchone()
        if row and row[0]:
            raise ToolError("the website already has categories; seed refuses to load twice")
        if seed.profile is not None:
            p = seed.profile
            values = (
                p.business_name,
                p.description,
                p.phone,
                p.email,
                p.address,
                p.timezone,
                Jsonb(p.opening_hours),
            )
            cursor = await connection.execute(UPDATE_PROFILE, (*values, website))
            if cursor.rowcount == 0:
                await connection.execute(INSERT_PROFILE, (website, *values))
        category_ids: dict[str, UUID] = {}
        for c in seed.categories:
            category_ids[c.slug] = uuid.uuid4()
            await connection.execute(
                INSERT_CATEGORY, (category_ids[c.slug], website, c.name, c.slug, c.sort_order)
            )
        schemas: dict[str, tuple[UUID, dict[str, Any]]] = {}
        for product in seed.products:
            if product.product_type not in schemas:
                cursor = await connection.execute(
                    ACTIVE_SCHEMA, (seed.business_type, product.product_type)
                )
                found = await cursor.fetchone()
                if found is None:
                    raise ToolError(f"no active schema for product type {product.product_type!r}")
                schemas[product.product_type] = (found[0], found[1])
            schema_id, field_schema = schemas[product.product_type]
            check_attributes(product.attributes, field_schema)
            product_id = uuid.uuid4()
            await connection.execute(
                INSERT_PRODUCT,
                (
                    product_id,
                    website,
                    schema_id,
                    category_ids[product.category_slug] if product.category_slug else None,
                    product.name,
                    product.description,
                    product.price,
                    Jsonb(product.attributes),
                    product.status,
                    product.sort_order,
                ),
            )
            for m in product.images:
                await connection.execute(
                    INSERT_IMAGE,
                    (
                        uuid.uuid4(),
                        website,
                        product_id,
                        f"{website}/{m.file}",
                        m.byte_size,
                        m.alt_text,
                        m.sort_order,
                    ),
                )
                images += 1
    return SeedResult(len(seed.categories), len(seed.products), images)


async def seed_website(stack: Stack, project_id: UUID, seed: Seed) -> SeedResult:
    connection = await stack.connect(*await site_credential(stack, project_id))
    async with connection:
        try:
            return await load_seed(connection, project_id, seed)
        except psycopg.Error as e:
            raise ToolError(f"the catalog refused the seed ({_sqlstate(e)})") from None


# --- command line ---------------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m provisioning.hosts.devtools.catalog_platform",
        description="Stand-in for Project's catalog writes on the prototype stack (decision 0014).",
    )
    parser.add_argument("--stack-env", type=Path, default=DEFAULT_STACK_ENV)
    commands = parser.add_subparsers(dest="command", required=True)
    reg = commands.add_parser("register", help="insert the website and its first profile")
    reg.add_argument("--project", type=UUID, required=True)
    reg.add_argument("--business-type", required=True)
    reg.add_argument("--name", default=None, help="business name of the first profile")
    reg.add_argument("--timezone", default="UTC")
    reg.add_argument("--storage-limit-bytes", type=int, default=100 * 1024 * 1024)
    reg.add_argument("--max-image-bytes", type=int, default=2 * 1024 * 1024)
    seed = commands.add_parser("seed", help="load a seed file as the website's own role")
    seed.add_argument("--project", type=UUID, required=True)
    seed.add_argument("--file", type=Path, required=True)
    return parser


async def run(argv: Sequence[str]) -> str:
    args = _parser().parse_args(argv)
    stack = Stack.load(args.stack_env)
    if args.command == "register":
        created = await register(
            stack,
            args.project,
            args.business_type,
            name=args.name or f"Website {args.project}",
            timezone=args.timezone,
            storage_limit_bytes=args.storage_limit_bytes,
            max_image_bytes=args.max_image_bytes,
        )
        verb = "registered" if created else "was already registered"
        return f"website {args.project} {verb} (role {site_role_name(args.project)})"
    try:
        document = json.loads(args.file.read_text())
    except OSError, ValueError:
        raise ToolError(f"cannot read {args.file} as JSON") from None
    result = await seed_website(stack, args.project, parse_seed(document))
    return (
        f"website {args.project} seeded: {result.categories} categories, "
        f"{result.products} products, {result.images} images"
    )


def main(argv: Sequence[str] | None = None) -> int:
    # The Kubernetes client is only ever given the stack's admin kubeconfig by explicit path; this
    # makes sure no default can name another one (CLAUDE.md section 0).
    os.environ["KUBECONFIG"] = _POISONED_KUBECONFIG
    try:
        print(asyncio.run(run(sys.argv[1:] if argv is None else argv)))
    except ToolError as e:
        print(f"catalog_platform: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

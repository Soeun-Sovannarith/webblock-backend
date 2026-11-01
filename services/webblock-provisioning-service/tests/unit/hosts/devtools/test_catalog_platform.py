"""The catalog_platform stand-in tool without a database: the seed format of task S
(deploy/prototype/demo-sites/clothing-shop/seed/README.md), the attribute check against a field
schema, and the stack files it reads. Its writes are proven live by tests/integration/catalog."""

import copy
import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from provisioning.hosts.devtools.catalog_platform import (
    Stack,
    ToolError,
    check_attributes,
    main,
    parse_seed,
    read_env_file,
)

SEED: dict[str, Any] = {
    "format_version": 1,
    "business_type": "clothing_shop",
    "business_profile": {
        "business_name": "Juniper & Thread",
        "description": "Everyday clothing.",
        "phone": None,
        "email": "hello@shop.example",
        "address": None,
        "timezone": "America/Los_Angeles",
        "opening_hours": [
            {
                "from_day": "monday",
                "to_day": "friday",
                "opens_at": "10:00",
                "closes_at": "19:00",
                "closes_next_day": False,
            }
        ],
    },
    "categories": [
        {"slug": "tops", "name": "Tops", "sort_order": 1},
        {"slug": "trousers", "name": "Trousers", "sort_order": 2},
    ],
    "products": [
        {
            "product_type": "top",
            "category_slug": "tops",
            "name": "Crew Tee",
            "description": None,
            "price": "28.00",
            "attributes": {"size": ["S", "M"], "color": ["Black"]},
            "status": "published",
            "sort_order": 10,
            "images": [
                {
                    "file": "products/tee.svg",
                    "byte_size": 1844,
                    "alt_text": "A tee",
                    "sort_order": 0,
                }
            ],
        },
        {
            "product_type": "trousers",
            "category_slug": None,
            "name": "Chinos",
            "description": "Cotton twill.",
            "price": None,
            "attributes": {},
            "status": "draft",
            "sort_order": 20,
            "images": [],
        },
    ],
}


def test_the_demo_shop_seed_names_the_catalog_business_type_clothing_shop() -> None:
    # The Project service's reference data names the business type clothing_shop (decision 0017
    # section 1); a seed of another type is refused by the loader on the live catalog.
    path = Path(__file__).resolve().parents[4] / (
        "deploy/prototype/demo-sites/clothing-shop/seed/seed.json"
    )

    seed = parse_seed(json.loads(path.read_text()))

    assert seed.business_type == "clothing_shop"
    assert {p.product_type for p in seed.products} <= {"top", "outerwear", "trousers", "accessory"}


def test_a_valid_seed_is_read_into_typed_rows() -> None:
    seed = parse_seed(SEED)

    assert seed.business_type == "clothing_shop"
    assert seed.profile is not None
    assert seed.profile.business_name == "Juniper & Thread"
    assert seed.profile.opening_hours[0]["from_day"] == "monday"
    assert [c.slug for c in seed.categories] == ["tops", "trousers"]
    tee, chinos = seed.products
    assert tee.price == Decimal("28.00")
    assert tee.images[0].file == "products/tee.svg"
    assert tee.images[0].byte_size == 1844
    assert chinos.price is None
    assert chinos.category_slug is None
    assert chinos.status == "draft"


def _changed(path: tuple[Any, ...], value: Any) -> dict[str, Any]:
    document = copy.deepcopy(SEED)
    target: Any = document
    for key in path[:-1]:
        target = target[key]
    if value is KeyError:
        del target[path[-1]]
    else:
        target[path[-1]] = value
    return document


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("format_version",), 2),
        (("business_type",), KeyError),
        (("business_profile", "business_name"), KeyError),
        (("business_profile", "opening_hours"), {"monday": "10-19"}),
        (("categories", 1, "slug"), "tops"),
        (("categories", 0, "sort_order"), "1"),
        (("products", 0, "category_slug"), "shoes"),
        (("products", 0, "price"), 28.0),
        (("products", 0, "price"), "-1.00"),
        (("products", 0, "price"), "1.005"),
        (("products", 0, "price"), "NaN"),
        (("products", 0, "status"), "hidden"),
        (("products", 0, "attributes"), ["size"]),
        (("products", 0, "sort_order"), True),
        (("products", 0, "images", 0, "byte_size"), 0),
        (("products", 0, "images", 0, "file"), "../../etc/passwd"),
        (("products", 0, "images", 0, "file"), "/products/tee.svg"),
    ],
)
def test_a_seed_that_breaks_the_format_is_refused(path: tuple[Any, ...], value: Any) -> None:
    with pytest.raises(ToolError):
        parse_seed(_changed(path, value))


def test_attributes_outside_a_closed_schema_are_refused() -> None:
    schema = {
        "type": "object",
        "properties": {"size": {}, "color": {}},
        "additionalProperties": False,
    }
    check_attributes({"size": ["S"], "color": ["Red"]}, schema)

    with pytest.raises(ToolError, match="ram_gb"):
        check_attributes({"size": ["S"], "ram_gb": 8}, schema)


def test_an_open_schema_allows_any_attribute() -> None:
    check_attributes({"anything": 1}, {"type": "object", "properties": {"size": {}}})


def test_the_env_files_are_key_value_lines(tmp_path: Path) -> None:
    path = tmp_path / "x.env"
    path.write_text("# comment\n\nA=1\nB=x=y\n")

    assert read_env_file(path) == {"A": "1", "B": "x=y"}


def test_a_stack_env_without_the_catalog_keys_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "stack.env"
    path.write_text("PROTO_CLUSTER=webblock-proto\n")

    with pytest.raises(ToolError, match="PROTO_CATALOG_PORT_ON_HOST"):
        Stack.load(path)


def test_the_command_line_reports_a_missing_stack_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("KUBECONFIG", "/nonexistent/restored-after-the-test")  # main() poisons it
    project = "0b7c2f43-5d5e-4f1e-9a43-0e1d5c1c7a11"
    argv = ["--stack-env", str(tmp_path / "absent.env"), "seed", "--project", project]
    code = main([*argv, "--file", str(tmp_path / "seed.json")])

    assert code == 1
    assert "make proto-up" in capsys.readouterr().err

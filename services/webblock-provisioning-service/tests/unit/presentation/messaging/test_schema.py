"""The JSON Schema subset the message codecs validate with (decision 0005 section 6, OD-27).

The validator interprets the schema documents under src/contracts/events directly, so that a codec
validates against the contract and not against a hand copy of it. It knows only the keywords those
documents use and refuses a schema with any other keyword at construction, so a contract change
that needs a new keyword fails loudly instead of being ignored.
"""

import pytest

from provisioning.presentation.messaging import JsonSchema, UnsupportedSchema, parse_json

UUID_PATTERN = "^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"

OBJECT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://webblock.invalid/test.schema.json",
    "title": "t",
    "description": "d",
    "type": "object",
    "additionalProperties": False,
    "required": ["id", "n", "kind"],
    "properties": {
        "v": {"const": 1},
        "id": {
            "type": "string",
            "format": "uuid",
            "minLength": 36,
            "maxLength": 36,
            "pattern": UUID_PATTERN,
            "not": {"enum": ["00000000-0000-0000-0000-000000000000"]},
        },
        "n": {"type": "integer", "format": "int64", "minimum": 1, "maximum": 10},
        "kind": {"type": "string", "enum": ["a", "b", "h"]},
        "at": {"type": "string", "format": "date-time", "maxLength": 40},
        "rel": {"type": "string"},
    },
    "if": {"required": ["kind"], "properties": {"kind": {"enum": ["h"]}}},
    "then": {"not": {"required": ["rel"]}},
    "else": {"required": ["rel"]},
}

GOOD = {
    "v": 1,
    "id": "0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e01",
    "n": 3,
    "kind": "a",
    "rel": "x",
    "at": "2026-10-08T09:30:00.5Z",
}


def failures(instance: object) -> tuple[str, ...]:
    return JsonSchema(OBJECT_SCHEMA).failures(instance)


def test_a_valid_instance_has_no_failures() -> None:
    assert failures(GOOD) == ()
    hosting = {k: v for k, v in GOOD.items() if k != "rel"} | {"kind": "h"}
    assert failures(hosting) == ()


@pytest.mark.parametrize(
    ("change", "location"),
    [
        ({"v": 2}, "/properties/v/const"),
        ({"v": True}, "/properties/v/const"),
        ({"id": "0199C4A1-7C1E-7D4B-9A3E-5B2F8C6D1E01"}, "/properties/id/pattern"),
        ({"id": "00000000-0000-0000-0000-000000000000"}, "/properties/id/not"),
        ({"id": "0199c4a1-7c1e-7d4b-9a3e-5b2f8c6d1e0"}, "/properties/id/minLength"),
        ({"id": 7}, "/properties/id/type"),
        ({"n": 0}, "/properties/n/minimum"),
        ({"n": 11}, "/properties/n/maximum"),
        ({"n": 2.0}, "/properties/n/type"),
        ({"n": True}, "/properties/n/type"),
        ({"n": "3"}, "/properties/n/type"),
        ({"kind": "z"}, "/properties/kind/enum"),
        ({"at": "2026-10-08 09:30:00Z"}, "/properties/at/format"),
        ({"at": "2026-13-08T09:30:00Z"}, "/properties/at/format"),
        ({"at": "2026-10-08T09:30:00"}, "/properties/at/format"),
        ({"at": "2026-10-08T25:30:00Z"}, "/properties/at/format"),
        ({"at": "2026-10-08T09:30:00.123456789+02:00" + "0" * 10}, "/properties/at/maxLength"),
        ({"extra": 1}, "/additionalProperties"),
        ({"kind": "h"}, "/then/not"),
    ],
)
def test_each_keyword_reports_its_location(change: dict[str, object], location: str) -> None:
    assert location in failures({**GOOD, **change})


def test_a_missing_required_property_is_reported() -> None:
    assert "/required" in failures({k: v for k, v in GOOD.items() if k != "n"})
    assert "/else/required" in failures({k: v for k, v in GOOD.items() if k != "rel"})


def test_a_non_object_is_a_type_failure() -> None:
    # if/then/else applies to any instance (JSON Schema semantics), so only /type is asserted.
    instances: tuple[object, ...] = ([], "x", 1, None, True)
    for instance in instances:
        assert "/type" in failures(instance)


def test_failures_are_sorted_and_distinct() -> None:
    got = failures({**GOOD, "n": 0, "kind": "z", "extra": 1, "another": 2})
    assert got == tuple(sorted(set(got)))
    assert got == ("/additionalProperties", "/properties/kind/enum", "/properties/n/minimum")


def test_rfc3339_offsets_and_fractions_are_accepted() -> None:
    for at in (
        "2026-10-08T09:30:00Z",
        "2026-10-08t09:30:00z",
        "2026-10-08T09:30:00.000001+02:00",
        "2026-10-08T09:30:00-11:30",
    ):
        assert failures({**GOOD, "at": at}) == (), at


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "patternProperties": {}},
        {"type": "array"},
        {"type": "object", "properties": {"a": {"type": "string", "format": "email"}}},
        {"type": "object", "additionalProperties": {"type": "string"}},
        {"type": "object", "properties": {"a": {"$ref": "#/x"}}},
        {"type": "object", "required": "a"},
    ],
)
def test_a_schema_outside_the_subset_is_refused(schema: dict[str, object]) -> None:
    with pytest.raises(UnsupportedSchema):
        JsonSchema(schema)


@pytest.mark.parametrize("node_type", [["object"], 1, None])
def test_tr24_a_schema_type_that_is_no_string_is_refused(node_type: object) -> None:
    """TR-24: a ``type`` keyword that is not one of the subset's type names, a list or a number
    included, refuses the schema with ``UnsupportedSchema``."""
    with pytest.raises(UnsupportedSchema):
        JsonSchema({"type": node_type})


def test_parse_json_accepts_one_value() -> None:
    assert parse_json(b'{"a": [1, "x", null, true]}') == {"a": [1, "x", None, True]}


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"{",
        b'{"a": 1} {"b": 2}',
        b'{"a": 1, "a": 2}',
        b'{"a": NaN}',
        b'{"a": Infinity}',
        b'{"a": "\xff"}',
        b"\xef\xbb\xbf{}",
    ],
)
def test_parse_json_refuses_anything_else(body: bytes) -> None:
    with pytest.raises(ValueError) as caught:
        parse_json(body)
    # The error names the problem, never a piece of the body.
    assert str(caught.value) in {
        "not one JSON value",
        "duplicate object key",
        "not a finite number",
        "not UTF-8",
    }
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None or caught.value.__suppress_context__

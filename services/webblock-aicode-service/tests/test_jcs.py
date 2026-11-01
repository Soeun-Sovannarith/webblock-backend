import hashlib
import pytest
from app.services import jcs


# Number serialization samples from RFC 8785 appendix B and ECMAScript's Number.prototype.toString.
@pytest.mark.parametrize("value, text", [
    (0, "0"), (-0.0, "0"), (5e-324, "5e-324"), (1.7976931348623157e308, "1.7976931348623157e+308"),
    (9007199254740992.0, "9007199254740992"), (9007199254740994, "9007199254740994"), (295147905179352830000.0, "295147905179352830000"),
    (1e21, "1e+21"), (1e-7, "1e-7"), (0.000001, "0.000001"), (333333333.3333333, "333333333.3333333"),
    (4.5, "4.5"), (0.002, "0.002"), (1e23, "1e+23"), (-1.5, "-1.5"), (5.0, "5"), (123, "123"), (-42, "-42"),
])
def test_numbers_are_written_like_ecmascript(value, text):
    assert jcs.canonicalize(value) == text


def test_object_members_are_sorted_by_utf16_code_units_without_whitespace():
    # U+1F600 (a surrogate pair, D83D...) sorts before U+FB33 in UTF-16 although it is the larger code point.
    value = {"דּ": 1, "\U0001f600": 2, "b": [True, None, "x\n\"y"], "a": {"z": 1, "y": 2.0}}
    assert jcs.canonicalize(value) == '{"a":{"y":2,"z":1},"b":[true,null,"x\\n\\"y"],"\U0001f600":2,"דּ":1}'


def test_hash_is_sha256_of_the_utf8_canonical_form():
    value = {"pages": [{"name": "Café", "path": "/"}], "componentCatalogVersion": "0.1.0"}
    expected = hashlib.sha256('{"componentCatalogVersion":"0.1.0","pages":[{"name":"Café","path":"/"}]}'.encode()).hexdigest()
    assert jcs.sha256_hex(value) == expected


def test_non_finite_numbers_are_refused():
    with pytest.raises(ValueError):
        jcs.canonicalize(float("nan"))

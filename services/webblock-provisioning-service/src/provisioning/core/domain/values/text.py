"""Text rules of decision 0001 sections 4 and 9, decision 0008 section 4 and decision 0014.

Codes, failure summaries, idempotency keys, requesters, log references, and the sub-domain rule
(decision 0025 CQ1). No rule rewrites the text, and no error
contains it.
"""

from __future__ import annotations

import re
import string
import unicodedata

from provisioning.core.domain.errors import (
    InvalidFailureCode,
    InvalidFailureSummary,
    InvalidIdempotencyKey,
    InvalidLogRef,
    InvalidRequester,
    InvalidSubdomain,
    MissingFailureCode,
)

# The width of release_steps.failure_code and releases.failure_code.
MAX_CODE_LEN = 100
# The character limit of a failure summary, the bound the REST contract publishes.
MAX_FAILURE_SUMMARY_LEN = 500
# The byte limit of an idempotency key, and of a requester.
MAX_IDEMPOTENCY_KEY_LEN = 255
# The width of release_steps.log_ref in bytes; the reference is ASCII.
MAX_LOG_REF_LEN = 500


def validate_code(code: str) -> None:
    """A machine-readable failure or reason code: a lowercase letter followed by up to 99 lowercase
    letters, digits or underscores. Empty or blank is ``MissingFailureCode``; any other violation
    is ``InvalidFailureCode``."""
    if code.strip() == "":
        raise MissingFailureCode("failure code required")
    if len(code) > MAX_CODE_LEN:
        raise InvalidFailureCode(f"longer than {MAX_CODE_LEN} characters")
    for i, c in enumerate(code):
        if "a" <= c <= "z" or (i > 0 and ("0" <= c <= "9" or c == "_")):
            continue
        raise InvalidFailureCode(
            "expected a lowercase letter then lowercase letters, digits or underscores"
        )


def validate_failure_summary(summary: str) -> None:
    """Accept an empty (absent) summary or up to 500 characters of valid UTF-8 without control
    characters (Cc), line or paragraph separators (Zl, Zp) or bidirectional controls, and not only
    white space (``InvalidFailureSummary``)."""
    if not _valid_utf8(summary):
        raise InvalidFailureSummary("not valid UTF-8")
    if len(summary) > MAX_FAILURE_SUMMARY_LEN:
        raise InvalidFailureSummary(f"longer than {MAX_FAILURE_SUMMARY_LEN} characters")
    if any(_forbidden_char(c) for c in summary):
        raise InvalidFailureSummary("control, separator or bidirectional control character")
    if summary != "" and summary.strip() == "":
        raise InvalidFailureSummary("blank")


def validate_idempotency_key(key: str) -> None:
    """1 to 255 bytes of valid UTF-8, none of the characters a failure summary refuses, no leading
    or trailing white space (``InvalidIdempotencyKey``)."""
    reason = _key_text_problem(key)
    if reason:
        raise InvalidIdempotencyKey(reason)


def validate_requester(subject: str) -> None:
    """The idempotency key's character rule applied to a token subject (``InvalidRequester``)."""
    reason = _key_text_problem(subject)
    if reason:
        raise InvalidRequester(reason)


def validate_log_ref(ref: str) -> None:
    """A step's ``log_ref`` (``InvalidLogRef``; decision 0008 section 4, revision 19): an opaque
    identifier of 1 to 500 bytes of letters, digits, ``.``, ``_``, ``-`` and ``/``, in
    ``/``-separated segments none of which is empty, ``.`` or ``..``. Never a URL, a path with a
    scheme or free text; the build system's handle is such a reference (decision 0014)."""
    if not ref.isascii() or not 1 <= len(ref) <= MAX_LOG_REF_LEN:
        raise InvalidLogRef(f"expected 1 to {MAX_LOG_REF_LEN} ASCII bytes")
    if any(c not in _OPAQUE_CHARS for c in ref):
        raise InvalidLogRef("expected letters, digits, '.', '_', '-' or '/'")
    if any(segment in ("", ".", "..") for segment in ref.split("/")):
        raise InvalidLogRef("an empty, '.' or '..' segment")


# The bidirectional controls decision 0001 section 9 forbids, written out so that the list changes
# only with the decision record.
_BIDI_CONTROLS = frozenset(
    "\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069"
)
_OPAQUE_CHARS = frozenset(string.ascii_letters + string.digits + "._-/")


def _valid_utf8(text: str) -> bool:
    """A Python str holds invalid UTF-8 only as lone surrogates (for example from
    ``surrogateescape``); those do not encode."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _forbidden_char(c: str) -> bool:
    """Control characters (Cc), line and paragraph separators (Zl, Zp), the bidi controls."""
    return unicodedata.category(c) in ("Cc", "Zl", "Zp") or c in _BIDI_CONTROLS


def _key_text_problem(text: str) -> str:
    """The rule shared by idempotency keys and requesters; a reason, or "" when it holds. The
    reason never contains the text."""
    if not _valid_utf8(text):
        return "length or encoding"
    if text == "" or len(text.encode("utf-8")) > MAX_IDEMPOTENCY_KEY_LEN:
        return "length or encoding"
    if any(_forbidden_char(c) for c in text):
        return "control, separator or bidirectional control character"
    if text[0].isspace() or text[-1].isspace():
        return "leading or trailing whitespace"
    return ""


# A DNS label in lower case: 1 to 63 letters, digits and hyphens, no hyphen at either end (Notion
# "5 · Provisioning": project_hosting.subdomain VARCHAR(63); migration 00020's check).
_SUBDOMAIN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.ASCII)


def validate_subdomain(value: str) -> None:
    """Accept a DNS label: 1 to 63 of ``a`` to ``z``, ``0`` to ``9`` and ``-``, starting and
    ending with a letter or digit (``InvalidSubdomain``; the message never holds the value)."""
    if not isinstance(value, str) or _SUBDOMAIN.fullmatch(value) is None:
        raise InvalidSubdomain(
            "expected 1 to 63 lower-case letters, digits or '-', not starting or ending with '-'"
        )


# FR-20, FR-55 (PREVIEW1): a project's preview host is ``<subdomain>-preview.<site domain>``, one
# DNS label under the site domain. A sub-domain the platform hosts therefore has at most 63 - 8 = 55
# characters, and never ends in ``-preview``: its site host would be another project's preview host.
# The database keeps its own check of 1 to 63 characters (Notion "5 · Provisioning"); the bootstrap
# refuses the rest in code.
PREVIEW_LABEL_SUFFIX = "-preview"
MAX_HOSTED_SUBDOMAIN_LEN = 55


def preview_label(subdomain: str) -> str:
    """``<subdomain>-preview``: the one label of a project's preview host (FR-20)."""
    return subdomain + PREVIEW_LABEL_SUFFIX


def subdomain_refusal(subdomain: str) -> str | None:
    """Why the platform hosts no site on ``subdomain`` (FR-55), or None: ``preview_suffix`` when it
    ends in ``-preview`` (``preview`` alone is allowed), ``subdomain_too_long`` when it has more
    than 55 characters. The reason never holds the sub-domain."""
    if subdomain.endswith(PREVIEW_LABEL_SUFFIX):
        return "preview_suffix"
    if len(subdomain) > MAX_HOSTED_SUBDOMAIN_LEN:
        return "subdomain_too_long"
    return None

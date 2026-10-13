"""Path normalization: turn concrete observed paths into route templates.

Rules (docs/11-TEST-PLAN.md section 4):

- ``/api/customer/100`` and ``/api/customer/200`` both become
  ``/api/customer/{id}``;
- ``/api/v1/customer`` and ``/api/v2/customer`` stay separate (version
  segments are literal);
- ``/api/users/search`` stays literal - only identifier-shaped segments
  (numeric, UUID, hash-like, mixed alphanumerics with digits) are templated;
- ``POST /graphql`` operations are separated by ``operationName``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
_NUMERIC_PATTERN = re.compile(r"^\d+$")
_HEX_HASH_PATTERN = re.compile(r"^[0-9a-fA-F]{16,}$")
_MIXED_ID_PATTERN = re.compile(r"^(?=.*\d)[A-Za-z0-9_-]{6,}$")
_VERSION_PATTERN = re.compile(r"^v\d+(\.\d+)?$")

# route words that must never be treated as identifiers even when they
# contain digits (none today, reserved for tuning)
LITERAL_WORDS: frozenset[str] = frozenset({"graphql"})


@dataclass
class NormalizedPath:
    template: str
    param_names: list[str] = field(default_factory=list)
    # set when the same position showed conflicting value shapes across
    # observations (int vs string id) -> operation must go to needs_review
    type_conflict: bool = False


def classify_segment(segment: str) -> str | None:
    """Return a template name if the segment looks like an identifier."""
    if not segment:
        return None
    lower = segment.lower()
    if lower in LITERAL_WORDS:
        return None
    if _VERSION_PATTERN.match(segment):
        return None
    if _NUMERIC_PATTERN.match(segment):
        return "{id}"
    if _UUID_PATTERN.match(segment):
        return "{uuid}"
    if _HEX_HASH_PATTERN.match(segment):
        return "{hash}"
    if _MIXED_ID_PATTERN.match(segment):
        return "{id}"
    return None


def normalize_path(method: str, path: str, *,
    graphql_operation: str | None = None) -> NormalizedPath:
    """Normalize one observed path into a route template.

    ``path`` must not contain the query string. For GraphQL endpoints pass
    the request ``operationName`` so different operations dedupe apart.
    """
    clean = path.split("?", 1)[0].strip()
    segments = [s for s in clean.split("/") if s != ""]

    if segments and segments[-1].lower() == "graphql" and graphql_operation:
        return NormalizedPath(
            template=f"/{'/'.join(segments)}#{graphql_operation}",
            param_names=[],
        )

    param_names: list[str] = []
    rendered: list[str] = []
    seen: set[str] = set()
    for segment in segments:
        template = classify_segment(segment)
        if template is None:
            rendered.append(segment)
            continue
        name = template
        if name in seen:
            # two identifier-ish segments on one path: give positional names
            idx = 2
            while f"{name.rstrip('}')}_{idx}}}" in seen:
                idx += 1
            name = f"{name.rstrip('}')}_{idx}}}"
        seen.add(name)
        param_names.append(name.strip("{}"))
        rendered.append(name)
    return NormalizedPath(template="/" + "/".join(rendered), param_names=param_names)

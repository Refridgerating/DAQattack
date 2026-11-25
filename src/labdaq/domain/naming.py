"""Identifier and Windows-safe slug validation."""

from __future__ import annotations

import re
import unicodedata
from typing import Final

IDENTIFIER_PATTERN: Final = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
SLUG_PATTERN: Final = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_SLUG_LENGTH: Final = 64
_WINDOWS_RESERVED: Final = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{number}" for number in range(1, 10)}
    | {f"lpt{number}" for number in range(1, 10)}
)


def validate_identifier(value: str, *, field: str = "identifier") -> None:
    """Reject identifiers that are not stable lowercase ASCII tokens."""
    if not isinstance(value, str) or IDENTIFIER_PATTERN.fullmatch(value) is None or ".." in value:
        raise ValueError(
            f"{field} must use lowercase ASCII letters, digits, and single-purpose hyphens"
        )


def validate_slug(value: str) -> None:
    """Validate a supplied filename component without modifying it."""
    if not isinstance(value, str) or not value:
        raise ValueError("experiment_slug must not be empty")
    if len(value) > MAX_SLUG_LENGTH:
        raise ValueError(f"experiment_slug must be at most {MAX_SLUG_LENGTH} characters")
    if value != value.strip() or value.endswith((".", " ")):
        raise ValueError("experiment_slug must not have surrounding whitespace or trailing dots")
    if any(ord(character) < 32 for character in value):
        raise ValueError("experiment_slug must not contain control characters")
    if "/" in value or "\\" in value or ".." in value:
        raise ValueError("experiment_slug must not contain path separators or traversal")
    if SLUG_PATTERN.fullmatch(value) is None:
        raise ValueError(
            "experiment_slug must contain lowercase letters, digits, and single hyphens"
        )
    if value.casefold() in _WINDOWS_RESERVED:
        raise ValueError("experiment_slug must not be a Windows reserved name")


def make_slug(display_name: str) -> str:
    """Explicitly construct a safe slug from a human-readable name."""
    ascii_name = (
        unicodedata.normalize("NFKD", display_name).encode("ascii", "ignore").decode("ascii")
    )
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_name.casefold()).strip("-")
    slug = slug[:MAX_SLUG_LENGTH].rstrip("-")
    validate_slug(slug)
    return slug

"""Report Command validation failures without echoing the rejected input.

The Daemon validates every Command, and the same details reach the caller in the
``invalid_arguments`` error, whichever client sent it.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

MAX_VALIDATION_ERRORS = 8
MAX_VALIDATION_MESSAGE_CHARACTERS = 256
MAX_VALIDATION_LOCATION_CHARACTERS = 64
# Pydantic messages interpolate only their context; these context keys come from the schema,
# never from the rejected input, so messages built from them are safe to report.
_SCHEMA_CONTEXT_KEYS = frozenset(
    {
        "actual_length",
        "class",
        "class_name",
        "discriminator",
        "expected",
        "expected_plural",
        "expected_tags",
        "field_type",
        "ge",
        "gt",
        "le",
        "lt",
        "max_length",
        "min_length",
        "multiple_of",
        "pattern",
    }
)


def _validation_message(item: Any) -> str:
    context = item.get("ctx") or {}
    if set(context) <= _SCHEMA_CONTEXT_KEYS:
        return str(item["msg"])
    if item["type"] == "value_error" and type(context.get("error")) is ValueError:
        return str(item["msg"])  # raised by a Command catalog validator with fixed text
    if item["type"] == "union_tag_invalid":
        return (
            f"Input tag found using {context['discriminator']} does not match any of the "
            f"expected tags: {context['expected_tags']}"
        )
    return f"Input failed {item['type']} validation"


def _validation_location(location: tuple[int | str, ...]) -> list[int | str]:
    return [
        part[:MAX_VALIDATION_LOCATION_CHARACTERS] if isinstance(part, str) else part
        for part in location
    ]


def validation_details(error: ValidationError) -> dict[str, Any]:
    """Name each violated field and its limit, at most ``MAX_VALIDATION_ERRORS`` of them."""
    errors = error.errors(include_url=False, include_input=False)
    return {
        "validation_errors": [
            {
                "location": _validation_location(item["loc"]),
                "type": item["type"],
                "message": _validation_message(item)[:MAX_VALIDATION_MESSAGE_CHARACTERS],
            }
            for item in errors[:MAX_VALIDATION_ERRORS]
        ],
        "validation_errors_truncated": len(errors) > MAX_VALIDATION_ERRORS,
    }

"""What the Daemon does to a Command before admission, for client tests without a Daemon."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from td_cli.client import ClientError
from td_cli.protocol import Command, CommandPlan


def accepted_command(command: dict[str, Any]) -> dict[str, Any]:
    """Validate and normalize a Command the way ``POST /v3/requests`` does."""
    try:
        return Command.model_validate(command).model_dump(mode="json")
    except ValidationError as error:
        raise ClientError.invalid_arguments(error) from error


def validated_plan(document: bytes) -> list[dict[str, Any]]:
    """Validate a plan document the way ``POST /v3/command-plans/validate`` does."""
    try:
        return CommandPlan.model_validate_json(document).model_dump(mode="json")["commands"]
    except ValidationError as error:
        raise ClientError.invalid_arguments(error) from error

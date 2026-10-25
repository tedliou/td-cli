"""Resolve an Instance selector against the Daemon's Instance list.

A selector is a prefix of an Instance's short selector or of its Instance ID. Without a
selector, the only online Instance is chosen.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any


class SelectionError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def select_instance(
    instances: Sequence[dict[str, Any]], selector: str | None, *, online_only: bool
) -> dict[str, Any]:
    if selector is None:
        online = [item for item in instances if item["status"] == "online"]
        if len(online) != 1:
            raise SelectionError(
                "instance_not_found" if not online else "instance_selector_ambiguous"
            )
        return online[0]
    matches = [
        item
        for item in instances
        if item["selector"].startswith(selector) or item["instance_id"].startswith(selector)
    ]
    if not matches:
        raise SelectionError("instance_not_found")
    if len(matches) != 1:
        raise SelectionError("instance_selector_ambiguous")
    instance = matches[0]
    if online_only and instance["status"] != "online":
        raise SelectionError(
            {
                "offline": "instance_offline",
                "draining": "instance_draining",
                "synchronizing": "instance_synchronizing",
            }[instance["status"]]
        )
    return instance

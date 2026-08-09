from __future__ import annotations

import typer

from td_cli import __version__


def print_version(value: bool) -> None:
    if value:
        typer.echo(__version__)
        raise typer.Exit()

from __future__ import annotations

from typing import Annotated

import typer

from td_cli.cli_support import print_version

app = typer.Typer(no_args_is_help=True)


@app.callback()
def main(
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=print_version, is_eager=True),
    ] = None,
) -> None:
    """Control supported TouchDesigner Instances through the local Daemon."""


if __name__ == "__main__":
    app()

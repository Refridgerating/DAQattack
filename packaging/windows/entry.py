"""Shared frozen entry point for the windowed and console executables."""

from __future__ import annotations

import sys
from pathlib import Path


def main() -> int:
    if Path(sys.executable).stem.casefold() == "labdaq-cli":
        from labdaq.cli.main import main as cli_main

        return cli_main()
    from labdaq.gui.__main__ import main as gui_main

    return gui_main(
        _windowed_arguments(sys.argv[1:]),
        require_preview_acknowledgement=False,
    )


def _windowed_arguments(arguments: list[str]) -> list[str]:
    """Force the packaged desktop into the NI functional workflow."""
    if "--feedback" in arguments:
        return ["--feedback"]
    return [
        "--daq",
        "ni",
        "--mode",
        "accelerometer_commissioning",
        "--functional-preview",
    ]


if __name__ == "__main__":
    raise SystemExit(main())

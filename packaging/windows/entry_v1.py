"""Frozen launchers for the LabDAQ laboratory release."""

from __future__ import annotations

import sys
from pathlib import Path


def _windowed_arguments(arguments: list[str]) -> list[str]:
    """Default to NI hardware while allowing an explicit simulator launch."""
    if "--feedback" in arguments:
        return ["--feedback"]
    return list(arguments) if "--daq" in arguments else ["--daq", "ni", *arguments]


def main() -> int:
    if Path(sys.executable).stem.casefold() == "labdaq-cli":
        from labdaq.cli.main import main as cli_main

        return cli_main()
    from labdaq.gui.__main__ import main as gui_main

    return gui_main(_windowed_arguments(sys.argv[1:]), require_preview_acknowledgement=False)


if __name__ == "__main__":
    raise SystemExit(main())

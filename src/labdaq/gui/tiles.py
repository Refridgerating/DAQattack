"""Shared styling helpers for resizable GUI tiles."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtWidgets import QSplitter, QWidget

TILE_PROPERTY = "labdaqTile"

_TILE_BORDER_STYLESHEET = f"""
*[{TILE_PROPERTY}="true"] {{
    border: 1px solid palette(mid);
}}
"""


def add_splitter_tile(splitter: QSplitter, widget: QWidget) -> None:
    """Add one bordered tile to a splitter."""
    widget.setProperty(TILE_PROPERTY, True)
    splitter.addWidget(widget)


def style_splitter_tiles(splitter: QSplitter) -> None:
    """Mark existing splitter panes and install their scoped border rule."""
    for index in range(splitter.count()):
        widget = splitter.widget(index)
        if widget is not None:
            widget.setProperty(TILE_PROPERTY, True)
    splitter.setStyleSheet(_TILE_BORDER_STYLESHEET)

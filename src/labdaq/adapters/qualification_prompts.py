"""Headless operator-confirmation adapters for qualification phases."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from labdaq.domain.qualification import ConfirmationDocument, OperatorConfirmation

if TYPE_CHECKING:
    from collections.abc import Callable


class ConfirmationFilePrompt:
    """Return signed entries from a versioned, preloaded confirmation document."""

    def __init__(self, document: ConfirmationDocument | None = None) -> None:
        self._by_id = {
            item.confirmation_id: item for item in (document.confirmations if document else ())
        }

    def obtain(
        self,
        confirmation_id: str,
        statement: str,
        *,
        requested_at: datetime,
    ) -> OperatorConfirmation | None:
        del statement, requested_at
        return self._by_id.get(confirmation_id)


class InteractiveOperatorPrompt:
    """Collect explicit yes/no evidence without coupling application services to a console."""

    def __init__(
        self,
        *,
        operator: str,
        input_function: Callable[[str], str] = input,
        output_function: Callable[[str], None] = print,
    ) -> None:
        self._operator = operator
        self._input = input_function
        self._output = output_function

    def obtain(
        self,
        confirmation_id: str,
        statement: str,
        *,
        requested_at: datetime,
    ) -> OperatorConfirmation | None:
        self._output(statement)
        answer = self._input(f"Confirm {confirmation_id}? [y/N]: ").strip().casefold()
        if answer not in {"y", "yes"}:
            return None
        return OperatorConfirmation(
            confirmation_id=confirmation_id,
            confirmed=True,
            confirmed_at=datetime.now(UTC),
            operator=self._operator,
            statement=statement,
        )

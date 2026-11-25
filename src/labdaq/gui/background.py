"""Keyed Qt background execution with stale-result suppression."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot

if TYPE_CHECKING:
    from collections.abc import Callable


class _TaskSignals(QObject):
    succeeded = Signal(str, int, object)
    failed = Signal(str, int, object)
    finished = Signal(str, int)


class _Task(QRunnable):
    def __init__(self, key: str, generation: int, operation: Callable[[], object]) -> None:
        super().__init__()
        self.key = key
        self.generation = generation
        self.operation = operation
        self.signals = _TaskSignals()
        self.setAutoDelete(True)

    @Slot()
    def run(self) -> None:
        try:
            value = self.operation()
        except BaseException as error:
            self.signals.failed.emit(self.key, self.generation, error)
        else:
            self.signals.succeeded.emit(self.key, self.generation, value)
        finally:
            self.signals.finished.emit(self.key, self.generation)


class KeyedBackgroundRunner(QObject):
    """Run one current generation per key and ignore every superseded response."""

    busyChanged = Signal(str, bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._generations: dict[str, int] = {}
        self._active: set[tuple[str, int]] = set()
        self._tasks: dict[tuple[str, int], _Task] = {}
        self._closing = False

    def submit(
        self,
        key: str,
        operation: Callable[[], object],
        *,
        on_success: Callable[[Any], None],
        on_failure: Callable[[BaseException], None],
        cancel_previous: Callable[[], None] | None = None,
    ) -> int:
        if self._closing:
            raise RuntimeError("Background runner is shutting down")
        if self.busy(key) and cancel_previous is not None:
            cancel_previous()
        generation = self._generations.get(key, 0) + 1
        self._generations[key] = generation
        task = _Task(key, generation, operation)
        self._active.add((key, generation))
        self._tasks[(key, generation)] = task
        task.signals.succeeded.connect(
            lambda task_key, token, value: self._deliver_success(task_key, token, value, on_success)
        )
        task.signals.failed.connect(
            lambda task_key, token, error: self._deliver_failure(task_key, token, error, on_failure)
        )
        task.signals.finished.connect(self._finished)
        self.busyChanged.emit(key, True)
        self._pool.start(task)
        return generation

    def busy(self, key: str) -> bool:
        generation = self._generations.get(key)
        return generation is not None and (key, generation) in self._active

    def invalidate(self, key: str, *, cancel: Callable[[], None] | None = None) -> None:
        self._generations[key] = self._generations.get(key, 0) + 1
        if cancel is not None:
            cancel()
        self.busyChanged.emit(key, False)

    def shutdown(
        self,
        *,
        cancel: Callable[[], None] | None = None,
        timeout_ms: int = 10_000,
    ) -> bool:
        if self._closing:
            return self._pool.waitForDone(timeout_ms)
        self._closing = True
        for key in tuple(self._generations):
            self._generations[key] += 1
            self.busyChanged.emit(key, False)
        if cancel is not None:
            cancel()
        self._pool.clear()
        return self._pool.waitForDone(timeout_ms)

    def _is_current(self, key: str, generation: int) -> bool:
        return not self._closing and self._generations.get(key) == generation

    @Slot(str, int, object)
    def _deliver_success(
        self,
        key: str,
        generation: int,
        value: object,
        callback: Callable[[Any], None],
    ) -> None:
        if self._is_current(key, generation):
            callback(value)

    @Slot(str, int, object)
    def _deliver_failure(
        self,
        key: str,
        generation: int,
        error: BaseException,
        callback: Callable[[BaseException], None],
    ) -> None:
        if self._is_current(key, generation):
            callback(error)

    @Slot(str, int)
    def _finished(self, key: str, generation: int) -> None:
        self._active.discard((key, generation))
        self._tasks.pop((key, generation), None)
        if self._generations.get(key) == generation:
            self.busyChanged.emit(key, False)

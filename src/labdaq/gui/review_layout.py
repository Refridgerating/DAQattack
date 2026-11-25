"""In-memory window controls for the nested session-review splitters."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class ReviewPanel(QWidget):
    """Keep a content widget alive inside a titled, controllable panel."""

    def __init__(self, title: str, content: QWidget) -> None:
        super().__init__()
        self.title = title
        self.setObjectName(f"reviewPanel{content.objectName() or title.replace(' ', '')}")
        self.content_layout = QVBoxLayout(self)
        # Keep supporting panes resizable on the existing 776-pixel-high window.
        # Their content can be enlarged with the splitter or maximize control.
        self.content_layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.content_layout.setContentsMargins(2, 2, 2, 2)
        self.content_layout.setSpacing(2)
        header = QHBoxLayout()
        header.addWidget(QLabel(title), 1)
        self.minimize_button = QToolButton()
        self.minimize_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TitleBarMinButton)
        )
        self.minimize_button.setAccessibleName(f"Minimize {title}")
        self.minimize_button.setToolTip(f"Minimize {title}")
        self.maximize_button = QToolButton()
        header.addWidget(self.minimize_button)
        header.addWidget(self.maximize_button)
        self.content_layout.addLayout(header)
        self.content_layout.addWidget(content, 1)
        self.set_maximized(False)

    def minimumSizeHint(self) -> QSize:
        hint = super().minimumSizeHint()
        return QSize(hint.width(), self.minimize_button.sizeHint().height() + 32)

    def set_maximized(self, maximized: bool) -> None:
        action = "Restore" if maximized else "Maximize"
        icon = (
            QStyle.StandardPixmap.SP_TitleBarNormalButton
            if maximized
            else QStyle.StandardPixmap.SP_TitleBarMaxButton
        )
        self.maximize_button.setIcon(self.style().standardIcon(icon))
        self.maximize_button.setAccessibleName(f"{action} {self.title}")
        self.maximize_button.setToolTip(f"{action} {self.title}")


class ReviewLayoutController:
    """Change visibility only; preserve widget ownership and normal proportions."""

    def __init__(
        self,
        page: QWidget,
        root: QSplitter,
        panels: dict[str, ReviewPanel],
        defaults: dict[QSplitter, tuple[int, ...]],
    ) -> None:
        self.root = root
        self.panels = panels
        self.minimized: set[str] = set()
        self.maximized: str | None = None
        self._defaults = defaults
        self._weights = {splitter: list(map(float, sizes)) for splitter, sizes in defaults.items()}
        self._paths: dict[str, list[QWidget]] = {}
        for key, panel in panels.items():
            path: list[QWidget] = []
            branch: QWidget | None = panel
            while branch is not None and branch is not root:
                path.append(branch)
                branch = branch.parentWidget()
            if branch is None:
                raise ValueError("Review panel must belong to the workspace")
            self._paths[key] = path
            panel.minimize_button.clicked.connect(
                lambda _checked=False, key=key: self.minimize(key)
            )
            panel.maximize_button.clicked.connect(
                lambda _checked=False, key=key: self.toggle_maximize(key)
            )

        self.toolbar = QWidget(page)
        toolbar_layout = QHBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(0, 0, 0, 0)
        self.restore_scroll = QScrollArea()
        self.restore_scroll.setWidgetResizable(True)
        self.restore_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.restore_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.restore_scroll.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        restore_content = QWidget()
        restore_layout = QHBoxLayout(restore_content)
        restore_layout.setContentsMargins(2, 2, 2, 2)
        self.restore_buttons: dict[str, QPushButton] = {}
        for key, panel in panels.items():
            button = QPushButton(f"Restore {panel.title}")
            button.clicked.connect(lambda _checked=False, key=key: self.restore(key))
            button.hide()
            restore_layout.addWidget(button)
            self.restore_buttons[key] = button
        restore_layout.addStretch(1)
        self.restore_scroll.setWidget(restore_content)
        button_height = next(iter(self.restore_buttons.values())).sizeHint().height()
        self.restore_scroll.setFixedHeight(
            button_height + self.restore_scroll.horizontalScrollBar().sizeHint().height() + 10
        )
        toolbar_layout.addWidget(self.restore_scroll, 1)
        self.reset_button = QPushButton("Reset layout")
        self.reset_button.clicked.connect(self.reset)
        toolbar_layout.addWidget(self.reset_button)
        self.escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), page)
        self.escape_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.escape_shortcut.activated.connect(self.restore_maximized)
        self.escape_shortcut.setEnabled(False)

    def _remember_sizes(self) -> None:
        if self.maximized is not None:
            return
        for splitter, weights in self._weights.items():
            if not splitter.isVisible():
                continue
            sizes = splitter.sizes()
            visible = [
                i
                for i, size in enumerate(sizes)
                if size > 0 and (widget := splitter.widget(i)) is not None and not widget.isHidden()
            ]
            total = sum(sizes[i] for i in visible)
            weight = sum(weights[i] for i in visible)
            for i in visible:
                weights[i] = weight * sizes[i] / total

    def _apply(self) -> None:
        visible = {
            branch
            for key, path in self._paths.items()
            if key not in self.minimized and (self.maximized is None or key == self.maximized)
            for branch in path
        }
        branches = {branch for path in self._paths.values() for branch in path}
        self.root.setUpdatesEnabled(False)
        try:
            for branch in branches:
                branch.setVisible(branch in visible)
            for splitter, weights in self._weights.items():
                splitter.setSizes([max(1, round(weight * 1000)) for weight in weights])
            for key, panel in self.panels.items():
                panel.set_maximized(key == self.maximized)
                self.restore_buttons[key].setVisible(key in self.minimized)
            self.escape_shortcut.setEnabled(self.maximized is not None)
        finally:
            self.root.setUpdatesEnabled(True)

    def minimize(self, key: str) -> None:
        self._remember_sizes()
        self.maximized = None
        self.minimized.add(key)
        self._apply()
        self.restore_buttons[key].setFocus()

    def restore(self, key: str) -> None:
        self._remember_sizes()
        self.maximized = None
        self.minimized.discard(key)
        self._apply()
        self.panels[key].maximize_button.setFocus()

    def toggle_maximize(self, key: str) -> None:
        self._remember_sizes()
        self.minimized.discard(key)
        self.maximized = None if self.maximized == key else key
        self._apply()
        self.panels[key].maximize_button.setFocus()

    def restore_maximized(self) -> None:
        if self.maximized is not None:
            self.restore(self.maximized)

    def reset(self) -> None:
        self.minimized.clear()
        self.maximized = None
        self._weights = {
            splitter: list(map(float, sizes)) for splitter, sizes in self._defaults.items()
        }
        self._apply()

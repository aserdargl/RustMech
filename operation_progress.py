"""Compact per-workspace progress, driven by the existing operation lifecycle."""
from contextlib import contextmanager

from PyQt6.QtCore import QEventLoop, Qt, QTimer
from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QProgressBar, QSizePolicy, QWidget

from studio_style import CONTROL_HEIGHT


class OperationProgress(QWidget):
    """Show a real count when available; otherwise use an indeterminate bar.

    The provider returns (active, step[, completed, total]) on the GUI thread.
    Keeping lifecycle ownership in the workspace also covers failure and cancel.
    """

    def __init__(self, parent=None, provider=None):
        super().__init__(parent)
        self.setObjectName("operationProgress")
        policy = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        # A running operation must not resize splitters or change saved bounds.
        policy.setRetainSizeWhenHidden(True)
        self.setSizePolicy(policy)
        self.setFixedHeight(CONTROL_HEIGHT + 4)
        row = QHBoxLayout(self)
        row.setContentsMargins(4, 2, 4, 2)
        row.setSpacing(8)
        self.bar = QProgressBar(self)
        self.bar.setObjectName("operationProgressBar")
        self.bar.setAccessibleName("Operation progress")
        self.bar.setFixedSize(160, CONTROL_HEIGHT - 7)
        self.label = QLabel(self)
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.label.setMinimumWidth(0)
        self.label.setFixedHeight(CONTROL_HEIGHT)
        row.addWidget(self.bar)
        row.addWidget(self.label, 1)
        self.hide()
        self._provider = provider
        self._manual_steps = []
        self._timer = QTimer(self, interval=80)
        self._timer.timeout.connect(self.refresh)
        if provider is not None:
            self._timer.start()

    def refresh(self):
        if self._manual_steps:
            self.update_step(True, self._manual_steps[-1])
            return
        if self._provider is None:
            return
        active, message, *counts = self._provider()
        self.update_step(active, message, *counts)

    def update_step(self, active, message, completed=None, total=None):
        if active:
            self.bar.show()
            from ui_language import translate
            message = translate(str(message))
            self.label.setText(message.replace("\n", " · "))
            self.label.setToolTip(message)
            self.label.setAccessibleName(message)
            if total is not None and total > 0:
                self.bar.setRange(0, total)
                self.bar.setValue(max(0, min(completed or 0, total)))
                self.bar.setFormat("%v / %m")
                self.bar.setTextVisible(True)
            else:
                self.bar.setRange(0, 0)
                self.bar.setTextVisible(False)
        self.setVisible(bool(active))

    def begin_step(self, message):
        """Paint before a synchronous stage without accepting another action."""
        self.update_step(True, message)
        QApplication.processEvents(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)

    @contextmanager
    def step(self, message):
        """Show synchronous import/export work and restore any background stage."""
        self._manual_steps.append(message)
        try:
            self.begin_step(message)
            yield
        finally:
            self._manual_steps.pop()
            if self._provider is not None or self._manual_steps:
                self.refresh()
            else:
                self.update_step(False, "")

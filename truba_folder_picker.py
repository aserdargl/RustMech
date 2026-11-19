"""Select an existing destination through the connected TRUBA SFTP session."""

from pathlib import PurePosixPath

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget, QPushButton, QVBoxLayout

from studio_style import CONTROL_HEIGHT, STUDIO_STYLESHEET
from truba_files import list_batch_folders, project_root
from ui_language import translate


class TrubaFolderPicker(QDialog):
    def __init__(self, parent, auth, executor, initial):
        super().__init__(parent)
        self.setObjectName('trubaBatchFolderPicker')
        self.setWindowTitle(translate('Choose TRUBA folder'))
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint
                            | Qt.WindowType.WindowMaximizeButtonHint)
        self.setStyleSheet(STUDIO_STYLESHEET)
        self.resize(570, 370)
        self.setSizeGripEnabled(True)
        self.auth = auth
        self.executor = executor
        self.directory = PurePosixPath()
        self.future = None
        self.loaded = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(7)
        navigation = QHBoxLayout()
        self.up = self._button('Up', self._up)
        navigation.addWidget(self.up)
        self.address = QLineEdit()
        self.address.setReadOnly(True)
        self.address.setFixedHeight(CONTROL_HEIGHT)
        self.address.setAccessibleName(translate('TRUBA folder path'))
        navigation.addWidget(self.address, 1)
        layout.addLayout(navigation)
        self.folders = QListWidget()
        self.folders.setAccessibleName(translate('TRUBA folders'))
        self.folders.setProperty('preserveItemText', True)
        self.folders.itemActivated.connect(lambda item: self.load(self.directory / item.text()))
        layout.addWidget(self.folders, 1)
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        actions = QHBoxLayout()
        actions.addStretch()
        cancel = self._button('Cancel', self.reject)
        self.use = self._button('Use this folder', self._accept_loaded)
        self.use.setProperty('primary', True)
        width = max(cancel.sizeHint().width(), self.use.sizeHint().width())
        for button in (cancel, self.use):
            button.setMinimumWidth(width)
            actions.addWidget(button)
        layout.addLayout(actions)

        self.timer = QTimer(self, interval=100)
        self.timer.timeout.connect(self._poll)
        self.finished.connect(self._stop)
        self.load(initial)

    def _button(self, text, action):
        button = QPushButton(translate(text), self)
        button.setAutoDefault(False)
        button.clicked.connect(action)
        return button

    def load(self, directory):
        self.loading = directory
        self.future = self.executor.submit(list_batch_folders, self.auth, directory)
        self.address.setText(str(project_root(self.auth.username) / directory))
        self.folders.setEnabled(False)
        self.up.setEnabled(False)
        self.use.setEnabled(False)
        self.status.setText(translate('Loading TRUBA folders…'))
        self.timer.start()

    def _up(self):
        self.load(self.directory.parent)

    def _accept_loaded(self):
        if self.loaded and self.future is None:
            self.accept()

    def _poll(self):
        if not self.future.done():
            return
        future, self.future = self.future, None
        self.timer.stop()
        try:
            folders = future.result()
        except Exception as error:
            if not self.loaded and self.loading.parts:
                self.load(PurePosixPath())
                return
            self.address.setText(str(project_root(self.auth.username) / self.directory))
            self.status.setText(f"{translate('Could not load TRUBA folders.')} {error}")
        else:
            self.directory = self.loading
            self.loaded = True
            self.folders.clear()
            self.folders.addItems(folders)
            self.status.clear()
        self.folders.setEnabled(self.loaded)
        self.up.setEnabled(self.loaded and bool(self.directory.parts))
        self.use.setEnabled(self.loaded)

    def _stop(self):
        self.timer.stop()
        if self.future is not None:
            self.future.cancel()

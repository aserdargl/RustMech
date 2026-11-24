"""Explicit password-authenticated TRUBA connection with per-user credentials."""
from __future__ import annotations

from PySide6.QtCore import QRectF, QThread, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
                               QLabel, QLayout, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSplitter,
                               QStyle, QStyleOptionTab, QTabBar, QTabWidget, QVBoxLayout, QWidget)

from studio_style import CONTROL_HEIGHT, STUDIO_STYLESHEET
from truba_client import TrubaSession
from truba_credentials import CredentialStore, state_directory
from truba_desktop import DesktopSessionMonitor, DesktopStatus
from truba_ssh import HOST


class TrubaTabBar(QTabBar):
    """Paint GraphCalc's browser tab contour over Qt's native tab controls."""

    def __init__(self):
        super().__init__()
        self.setObjectName("trubaTabBar")
        self.setDrawBase(False)
        self.setExpanding(False)
        self.setElideMode(Qt.TextElideMode.ElideNone)
        self.setUsesScrollButtons(True)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#E8EDF4"))
        for index in range(self.count()):
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            selected = bool(option.state & QStyle.StateFlag.State_Selected)
            enabled = bool(option.state & QStyle.StateFlag.State_Enabled)
            focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
            hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
            bounds = QRectF(option.rect).adjusted(0.5, 0.5, -0.5, -0.5)
            if selected:
                left, top, right, bottom = bounds.getCoords()
                path = QPainterPath()
                path.moveTo(left, bottom)
                path.quadTo(left + 5, bottom, left + 5, bottom - 5)
                path.lineTo(left + 5, top + 8)
                path.quadTo(left + 5, top, left + 13, top)
                path.lineTo(right - 13, top)
                path.quadTo(right - 5, top, right - 5, top + 8)
                path.lineTo(right - 5, bottom - 5)
                path.quadTo(right - 5, bottom, right, bottom)
                path.closeSubpath()
                painter.setBrush(QColor("#BFDBFE" if enabled else "#E5E7EB"))
                painter.setPen(QColor("#2563EB" if enabled else "#D5DBE2"))
                painter.drawPath(path)
            elif hovered and enabled:
                painter.setBrush(QColor("#F1F5F9"))
                painter.setPen(QColor("#93C5FD"))
                painter.drawRoundedRect(bounds.adjusted(4, 2, -4, -2), 5, 5)
            painter.setPen(QColor("#0F172A" if enabled else "#64748B"))
            painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextShowMnemonic, option.text)
            if focused and enabled:
                painter.setPen(QColor("#1D4ED8"))
                edge = option.rect.adjusted(15, 0, -15, -1)
                painter.drawLine(edge.bottomLeft(), edge.bottomRight())


class ConnectionWorker(QThread):
    state_changed = Signal(str)
    host_key_required = Signal(str)
    failure_detail = Signal(str)

    def __init__(self, username, password, directory, parent=None):
        super().__init__(parent)
        self._session = TrubaSession(
            username, password, directory,
            on_state=self.state_changed.emit,
            on_host_key=self.host_key_required.emit,
            on_failure=self._report_failure,
        )

    def _report_failure(self, category):
        self.failure_detail.emit(
            f"SSH negotiation failed ({category}). Try the same host in WinSCP / SFTP to compare."
        )

    def run(self):
        self._session.run()

    def stop(self):
        self._session.stop()

    @property
    def auth(self):
        return self._session.auth

    def decide_host_key(self, accepted):
        self._session.decide_host_key(accepted)


class TrubaConnectionDialog(QDialog):
    connection_changed = Signal(bool)
    connection_ui_changed = Signal()
    disconnect_requested = Signal()
    open_desktop_requested = Signal()

    def analysis_auth(self):
        if self.desktop_monitor is not None:
            return self.desktop_status.session
        worker = self.worker
        if self.connected and worker is not None:
            return worker.auth
        return None

    def __init__(self, parent=None, *, credential_store=None, use_desktop_session=False):
        super().__init__(parent)
        self.setWindowTitle("TRUBA connection")
        self.setObjectName("trubaConnectionDialog")
        self.setStyleSheet(STUDIO_STYLESHEET)
        self.setMinimumSize(360, 320)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        from ui_language import install_language
        preferences = getattr(getattr(parent, 'solver_profile_store', None), 'settings', None)
        self.language_manager = install_language(QApplication.instance(), preferences)
        self.process = None  # Used by workspace no-startup-connection checks.
        self.worker = None
        self.connected = False
        self.stopping = False
        self.desktop_status = DesktopStatus()
        self.desktop_monitor = DesktopSessionMonitor(self) if use_desktop_session else None
        self.state_dir = state_directory()
        self.credentials = None if use_desktop_session else (
            credential_store if credential_store is not None else CredentialStore())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(6)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("trubaTabs")
        self.tabs.setTabBar(TrubaTabBar())
        self.connection_badge = QLabel("Disconnected")
        self.connection_badge.setObjectName("trubaConnectionBadge")
        self.connection_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.connection_badge.setProperty("state", "disconnected")
        self.tabs.setCornerWidget(self.connection_badge, Qt.Corner.TopRightCorner)
        self.jobs_splitter = QSplitter(Qt.Orientation.Vertical)
        self.jobs_splitter.setObjectName('trubaJobsSplitter')
        self.jobs_splitter.setChildrenCollapsible(False)
        self.jobs_splitter.setHandleWidth(8)
        self.jobs_splitter.addWidget(self.tabs)
        layout.addWidget(self.jobs_splitter, 1)
        self.settings_scroll = QScrollArea()
        self.settings_scroll.setWidgetResizable(False)
        self.settings_scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.tabs.addTab(self.settings_scroll, 'Connection')
        self.settings_content = QWidget()
        content = QVBoxLayout(self.settings_content)
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(4)
        self.settings_layout = content
        self.settings_columns = QHBoxLayout()
        self.settings_columns.setSpacing(7)

        server = QFrame()
        server_layout = QVBoxLayout(server)
        server_layout.setContentsMargins(8, 2, 8, 2)
        server_layout.setSpacing(0)
        server_layout.addWidget(QLabel(f"TRUBA · {HOST} · VPN required"))
        content.addWidget(server)
        content.setAlignment(Qt.AlignmentFlag.AlignTop)

        account = QGroupBox("Credentials and password storage")
        account_layout = QVBoxLayout(account)
        account_layout.setContentsMargins(8, 4, 8, 6)
        account_layout.setSpacing(4)
        account_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        fields = QFormLayout()
        fields.setContentsMargins(0, 0, 0, 0)
        fields.setHorizontalSpacing(8)
        fields.setVerticalSpacing(4)
        fields.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        self.username = QLineEdit()
        self.username.setFixedSize(200, CONTROL_HEIGHT)
        self.username.setPlaceholderText("TRUBA username")
        self.password = QLineEdit()
        self.password.setFixedSize(200, CONTROL_HEIGHT)
        self.password.setEchoMode(QLineEdit.EchoMode.Password)
        self.show_password = QCheckBox("Show password")
        self.show_password.toggled.connect(self._set_password_visible)
        password_row = QHBoxLayout()
        password_row.setContentsMargins(0, 0, 0, 0)
        password_row.setSpacing(6)
        password_row.addWidget(self.password)
        password_row.addWidget(self.show_password)
        password_row.addStretch()
        fields.addRow("Username", self.username)
        fields.addRow("Password", password_row)
        account_layout.addLayout(fields)
        self.remember = QCheckBox("Remember password for this Windows user")
        self.remember.toggled.connect(self._remember_changed)
        account_layout.addWidget(self.remember)
        hint = QLabel("Saved credentials belong to this Windows account. Use separate Windows accounts on shared computers.")
        hint.setObjectName("mutedHintLabel")
        hint.setWordWrap(True)
        account_layout.addWidget(hint)
        self.status = QLabel("Disconnected")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        # Reserve room for longer connection messages without moving actions.
        self.status.setFixedHeight(self.status.fontMetrics().lineSpacing() * 2 + 6)
        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("primaryLaunchBtn")
        self.disconnect_button = QPushButton("Disconnect")
        self.disconnect_button.setObjectName("cancelBtn")
        for button in (self.connect_button, self.disconnect_button):
            button.setAutoDefault(False)
            button.setDefault(False)
            buttons.addWidget(button)
        buttons.addStretch()
        account_layout.addLayout(buttons)
        account_layout.addWidget(self.status)
        self.credentials_group = account
        self.settings_columns.addWidget(account, alignment=Qt.AlignmentFlag.AlignTop)
        content.addLayout(self.settings_columns)
        # Keep the entire form anchored and unchanged on resize. Smaller
        # viewports scroll instead of squeezing, wrapping or relocating fields.
        self.settings_scroll.setWidget(self.settings_content)
        self._remote_sections = []
        self.resize(940, 590)
        if use_desktop_session:
            for index in range(fields.rowCount()):
                fields.setRowVisible(index, False)
            self.remember.hide()
            hint.hide()
            self.disconnect_button.hide()
            account.setTitle("TRUBA Desktop session")
            self.connect_button.setText("Open TRUBA")
            self.connect_button.clicked.connect(self.open_desktop_requested)
            self._desktop_status_changed(self.desktop_status)
        else:
            self.connect_button.clicked.connect(self.start_connection)
            self.disconnect_button.clicked.connect(self.stop_connection)
            self._restore_saved_credentials()
            self._update_controls()
        self.fit_settings_content()
        self.language_manager.changed.connect(self._language_changed)
        from truba_job_list import TrubaJobList
        from truba_active_jobs import TrubaActiveJobs
        from truba_queue_status import TrubaQueueStatus
        self.job_list = TrubaJobList(self)
        self.active_jobs = TrubaActiveJobs(self)
        self.queue_status = TrubaQueueStatus(self)
        self.tabs.addTab(self.active_jobs, 'Active jobs')

        self.job_tables_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.job_tables_splitter.setObjectName('trubaJobTablesSplitter')
        self.job_tables_splitter.setChildrenCollapsible(False)
        self.job_tables_splitter.setHandleWidth(8)
        self.queue_status_group = QGroupBox('Queue status')
        queue_group_layout = QVBoxLayout(self.queue_status_group)
        queue_group_layout.setContentsMargins(0, 0, 0, 0)
        queue_group_layout.addWidget(self.queue_status)
        self.job_tables_splitter.addWidget(self.job_list)
        self.job_tables_splitter.addWidget(self.queue_status_group)
        self.jobs_splitter.addWidget(self.job_tables_splitter)
        self.job_tables_splitter.setSizes([360, 540])
        self.tabs.currentChanged.connect(self._update_queue_status_visibility)
        self.jobs_splitter.setStretchFactor(0, 1)
        self.jobs_splitter.setStretchFactor(1, 1)
        # Initial proportions only; WindowStateStore restores the named splitter.
        self.jobs_splitter.setSizes([310, 190])
        from operation_progress import OperationProgress
        self.operation_progress = OperationProgress(self, provider=self._operation_state)
        layout.addWidget(self.operation_progress)

    def start_desktop_monitor(self):
        if self.desktop_monitor is not None:
            self.desktop_monitor.updated.connect(self._desktop_status_changed)
            self.desktop_monitor.start()

    def _desktop_status_changed(self, status):
        self.desktop_status = status
        self.connected = status.session is not None
        self._set_desktop_status_text()
        self.connection_changed.emit(self.connected)
        self.connection_ui_changed.emit()

    def _set_desktop_status_text(self):
        from ui_language import translate

        status = self.desktop_status
        if self.connected:
            self.status.setText(translate("Shared TRUBA ARF session · {username}").format(
                username=status.session.username))
            self._set_connection_badge(translate("Connected"), "connected")
        else:
            message = ("RustMech analyses use the TRUBA ARF cluster." if status.other_cluster else
                       "Connect to TRUBA ARF in the TRUBA application first.")
            self.status.setText(translate(message))
            self._set_connection_badge(translate("Disconnected"), "disconnected")

    def _update_queue_status_visibility(self, _index):
        self.queue_status_group.setVisible(self.tabs.currentWidget() is self.settings_scroll)

    def _operation_state(self):
        controller = getattr(self.parent(), "remote_controller", None)
        if controller is not None:
            if controller.closing or controller.future is not None:
                return True, controller.status.text()
            if controller.limits_future is not None:
                return True, "Checking TRUBA resource limits…"
            if controller.release_future is not None:
                return True, controller.session_status.text()
            if controller.session_future is not None:
                return True, controller.session_status.text()
        if self.job_list.workers:
            return True, self.job_list.hint.text()
        return self.worker is not None and (not self.connected or self.stopping), self.status.text()

    def install_remote_sections(self, resources, advanced):
        # The runtime controller owns the controls; this dialog owns presentation.
        for previous in self._remote_sections:
            self.settings_columns.removeWidget(previous)
            previous.hide()
            previous.setParent(self)
        self._remote_sections = [resources, advanced]
        for section in self._remote_sections:
            self.settings_columns.addWidget(section, alignment=Qt.AlignmentFlag.AlignTop)
            section.show()
        self.fit_settings_content()

    def _language_changed(self, *_):
        self.language_manager.localize_tree(self)
        if self.desktop_monitor is not None:
            self._set_desktop_status_text()
        self.fit_settings_content()

    def fit_settings_content(self):
        """Size the shared, non-reflowing canvas when sections are installed."""
        self.settings_content.ensurePolished()
        # Resolve compact action sizes before calculating the form minimum.
        buttons = (self.connect_button,) if self.desktop_monitor is not None else (
            self.connect_button, self.disconnect_button)
        button_width = max(132, *(button.fontMetrics().horizontalAdvance(button.text()) + 28 for button in buttons))
        for button in buttons:
            button.setFixedSize(button_width, CONTROL_HEIGHT)
        sections = (self.credentials_group, *self._remote_sections)
        for section in sections:
            section.ensurePolished()
            for layout in section.findChildren(QLayout):
                layout.invalidate()
        width = max(460, *(section.minimumSizeHint().width() for section in sections))
        heights = []
        for section in sections:
            section.setFixedWidth(width)
            height = section.heightForWidth(width)
            heights.append(height if height >= 0 else section.sizeHint().height())
        height = max(heights)
        for section in sections:
            section.setFixedHeight(height)
        self.settings_columns.invalidate()
        self.settings_layout.invalidate()
        self.settings_content.setFixedSize(self.settings_layout.sizeHint())

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
        else:
            super().keyPressEvent(event)

    def _remember_changed(self, checked):
        if not checked:
            try:
                self.credentials.forget()
            except OSError:
                QMessageBox.warning(self, "TRUBA", "Saved credentials could not be removed.")

    def _set_password_visible(self, visible):
        self.password.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)

    def hideEvent(self, event):
        self.show_password.setChecked(False)
        super().hideEvent(event)

    def _restore_saved_credentials(self, *, required=False):
        try:
            saved = self.credentials.load()
        except (OSError, ValueError):
            self.status.setText("Saved credentials cannot be read by this Windows user. Enter your credentials again.")
            return False
        if not saved or not saved[0].strip() or not saved[1]:
            if required:
                self.status.setText("No saved TRUBA credentials. Open TRUBA to enter and remember your credentials.")
            return False
        self.username.setText(saved[0])
        self.password.setText(saved[1])
        self.remember.setChecked(True)
        return True

    def start_saved_connection(self):
        if self.desktop_monitor is not None:
            return False
        if self.worker is not None or self.connected:
            return False
        if not self._restore_saved_credentials(required=True):
            self.connection_ui_changed.emit()
            return False
        self.start_connection()
        return True

    def start_connection(self):
        if self.desktop_monitor is not None or self.worker is not None:
            return
        username, password = self.username.text().strip(), self.password.text()
        if not username or not password:
            self.status.setText("Enter your TRUBA username and password.")
            (self.username if not username else self.password).setFocus()
            return
        self.stopping = False
        self.show_password.setChecked(False)
        self.worker = ConnectionWorker(username, password, self.state_dir, self)
        self.worker.state_changed.connect(self._state_changed)
        self.worker.host_key_required.connect(self._confirm_host_key)
        self.worker.failure_detail.connect(self._show_failure_detail)
        self.worker.finished.connect(self._finished)
        self._state_changed("connecting")
        self.worker.start()

    def _confirm_host_key(self, fingerprint):
        worker = self.worker
        if worker is None or self.stopping:
            return
        owner = self if self.isVisible() else self.parentWidget()
        answer = QMessageBox.question(
            owner, "Verify TRUBA server identity",
            "First connection to this server. Verify this fingerprint with TRUBA before accepting:\n\n" + fingerprint,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        worker.decide_host_key(answer == QMessageBox.StandardButton.Yes)

    def _state_changed(self, state):
        if self.stopping:
            return
        self.connected = state == "connected"
        self.connection_changed.emit(self.connected)
        messages = {
            "connecting": "Connecting...", "connected": "Connected to TRUBA",
            "host_key_confirmation": "SSH server reached. Waiting for you to verify the server fingerprint in the confirmation window.",
            "authenticating": "Server identity verified. Checking username and password...",
            "host_key_declined": "Server identity was not approved. Click Connect to review the fingerprint again.",
            "password_auth_unavailable": "This server does not accept password login for this account. Check the login method used in WinSCP.",
            "connection_timeout": "SSH connection timed out. Check VPN and retry; the server may be busy.",
            "reconnecting": "Connection lost; waiting for VPN / reconnecting...",
            "waiting_for_network": "Waiting for VPN / TRUBA network access...",
            "authentication_failed": "Username or password was rejected. Check your credentials and click Connect.",
            "host_key_changed": "Server identity changed. Connection refused; contact TRUBA support.",
            "ssh_failed": "SSH connection could not be established. Check the server identity and VPN, then retry.",
            "local_error": "Local connection settings could not be read or written.",
        }
        self.status.setText(messages.get(state, "Disconnected"))
        if self.connected:
            self._set_connection_badge("Connected", "connected")
        elif state in ("connecting", "authenticating", "reconnecting"):
            self._set_connection_badge("Connecting...", "pending")
        else:
            self._set_connection_badge("Disconnected", "disconnected")
        if self.connected and self.remember.isChecked():
            try:
                self.credentials.save(self.username.text().strip(), self.password.text())
            except OSError:
                self.status.setText("Connected to TRUBA. Password could not be saved for this Windows user.")
        self._update_controls()

    def _show_failure_detail(self, message):
        if not self.stopping:
            self.status.setText(message)
            self.connection_ui_changed.emit()

    def _set_connection_badge(self, text, state):
        self.connection_badge.setText(text)
        self.connection_badge.setProperty("state", state)
        self.connection_badge.style().unpolish(self.connection_badge)
        self.connection_badge.style().polish(self.connection_badge)

    def _update_controls(self):
        running = self.worker is not None
        self.connect_button.setEnabled(not running)
        self.connect_button.setText("Connected" if self.connected else "Connect")
        self.connect_button.setProperty("trubaConnected", self.connected)
        self.connect_button.style().unpolish(self.connect_button)
        self.connect_button.style().polish(self.connect_button)
        self.disconnect_button.setEnabled(running and not self.stopping)
        for field in (self.username, self.password, self.remember):
            field.setEnabled(not running)
        self.connection_ui_changed.emit()

    def stop_connection(self):
        if self.worker is not None:
            self.stopping = True
            self.connected = False
            self.connection_changed.emit(False)
            self.disconnect_requested.emit()
            self.status.setText("Disconnecting...")
            self._set_connection_badge("Disconnecting...", "pending")
            self.worker.stop()
            self._update_controls()

    def _finished(self):
        worker = self.worker
        self.worker = None
        self.connected = False
        self._set_connection_badge("Disconnected", "disconnected")
        self.connection_changed.emit(False)
        if self.stopping:
            self.status.setText("Disconnected")
        if not self.remember.isChecked():
            self.password.clear()
        self._update_controls()
        if worker is not None:
            worker.deleteLater()

    def shutdown(self):
        if self.desktop_monitor is not None:
            self.desktop_monitor.stop()
        self.job_list.shutdown()
        self.stop_connection()
        self.active_jobs.shutdown()
        self.queue_status.shutdown()
        if self.worker is not None:
            self.worker.wait()

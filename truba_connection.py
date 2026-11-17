"""Explicit password-authenticated TRUBA connection with per-user credentials."""
from __future__ import annotations

import base64
import hashlib
import socket
from threading import Event

import paramiko
from PySide6.QtCore import QThread, Qt, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFormLayout, QFrame, QGroupBox, QHBoxLayout,
                               QLabel, QLineEdit, QMessageBox, QPushButton, QScrollArea, QSplitter,
                               QTabWidget, QVBoxLayout, QWidget)

from studio_style import CONTROL_HEIGHT, STUDIO_STYLESHEET
from truba_credentials import CredentialStore, state_directory

HOST = "172.16.6.15"


class HostKeyDeclined(paramiko.SSHException):
    """The user did not approve the server identity."""


class ConnectionWorker(QThread):
    state_changed = Signal(str)
    host_key_required = Signal(str)
    failure_detail = Signal(str)

    def __init__(self, username, password, directory, parent=None):
        super().__init__(parent)
        self.username, self.password = username, password
        self.directory = directory
        self.stop_requested = Event()
        self.host_decided = Event()
        self.host_accepted = False
        self.client = None

    def missing_host_key(self, client, hostname, key):
        fingerprint = base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        self.host_decided.clear()
        self.host_accepted = False
        self.state_changed.emit("host_key_confirmation")
        self.host_key_required.emit(f"{hostname}\n{key.get_name()}\nSHA256:{fingerprint}")
        while not self.host_decided.wait(0.1):
            if self.stop_requested.is_set():
                raise paramiko.SSHException("Cancelled")
        if not self.host_accepted or self.stop_requested.is_set():
            raise HostKeyDeclined("Host identity was not accepted")
        client.get_host_keys().add(hostname, key.get_name(), key)
        client.save_host_keys(str(self.directory / "known_hosts"))
        self.state_changed.emit("authenticating")

    def stop(self):
        self.stop_requested.set()
        self.host_decided.set()
        client = self.client
        if client is not None:
            client.close()

    def run(self):
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            while not self.stop_requested.is_set():
                self.state_changed.emit("connecting")
                client = self.client = paramiko.SSHClient()
                try:
                    known_hosts = self.directory / "known_hosts"
                    if known_hosts.exists():
                        client.load_host_keys(str(known_hosts))
                    client.set_missing_host_key_policy(self)
                    # Never use the developer's SSH agent, private keys or account.
                    client.connect(HOST, username=self.username, password=self.password,
                                   allow_agent=False, look_for_keys=False,
                                   timeout=15, banner_timeout=30, auth_timeout=30)
                    if self.stop_requested.is_set():
                        break
                    transport = client.get_transport()
                    if transport is None or not transport.is_authenticated():
                        raise paramiko.AuthenticationException()
                    transport.set_keepalive(15)
                    from truba_ssh import register_session
                    register_session(self.username, self.password, client)
                    self.state_changed.emit("connected")
                    while not self.stop_requested.wait(1):
                        if not transport.is_active() or not transport.is_authenticated():
                            break
                except paramiko.BadAuthenticationType:
                    self.state_changed.emit("password_auth_unavailable")
                    break
                except paramiko.AuthenticationException:
                    self.state_changed.emit("authentication_failed")
                    break
                except paramiko.BadHostKeyException:
                    self.state_changed.emit("host_key_changed")
                    break
                except HostKeyDeclined:
                    self.state_changed.emit("host_key_declined")
                    break
                except (paramiko.SSHException, EOFError) as exc:
                    self.state_changed.emit("ssh_failed")
                    # Report the category only: exception text can contain server
                    # content or authentication data and must not reach the UI.
                    self.failure_detail.emit(f"SSH negotiation failed ({type(exc).__name__}). Try the same host in WinSCP / SFTP to compare.")
                    break
                except socket.timeout:
                    self.state_changed.emit("connection_timeout")
                    break
                except OSError:
                    self.state_changed.emit("waiting_for_network")
                    if self.stop_requested.wait(10):
                        break
                    continue
                finally:
                    from truba_ssh import unregister_session
                    unregister_session(self.username, client)
                    client.close()
                    self.client = None
                if self.stop_requested.is_set():
                    break
                self.state_changed.emit("reconnecting")
                if self.stop_requested.wait(10):
                    break
        except (OSError, ValueError):
            self.state_changed.emit("local_error")
        finally:
            self.password = ""


class TrubaConnectionDialog(QDialog):
    connection_changed = Signal(bool)
    disconnect_requested = Signal()

    def analysis_auth(self):
        worker=self.worker
        if self.connected and worker is not None and worker.client is not None:
            from truba_jobs import RemoteAuth
            return RemoteAuth(worker.username,worker.password)
        return None

    def __init__(self, parent=None, *, credential_store=None):
        super().__init__(parent)
        self.setWindowTitle("TRUBA connection")
        self.setObjectName("trubaConnectionDialog")
        self.setStyleSheet(STUDIO_STYLESHEET)
        self.setMinimumSize(360, 320)
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.setSizeGripEnabled(True)
        from ui_language import LanguageSelector, install_language
        preferences = getattr(getattr(parent, 'solver_profile_store', None), 'settings', None)
        self.language_manager = install_language(QApplication.instance(), preferences)
        self.process = None  # Used by workspace no-startup-connection checks.
        self.worker = None
        self.connected = False
        self.stopping = False
        self.state_dir = state_directory()
        self.credentials = credential_store if credential_store is not None else CredentialStore()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 6)
        layout.setSpacing(6)
        header = QFrame()
        header.setObjectName('trubaHeader')
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 5, 8, 5)
        header_layout.setSpacing(8)
        heading = QVBoxLayout()
        heading.setSpacing(0)
        title = QLabel('TRUBA')
        title.setObjectName('trubaTitle')
        heading.addWidget(title)
        subtitle = QLabel('Remote compute')
        subtitle.setObjectName('mutedHintLabel')
        heading.addWidget(subtitle)
        header_layout.addLayout(heading)
        header_layout.addStretch()
        self.connection_badge = QLabel('Disconnected')
        self.connection_badge.setObjectName('trubaConnectionBadge')
        header_layout.addWidget(self.connection_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        self.language_selector = LanguageSelector(self.language_manager, self)
        header_layout.addWidget(self.language_selector)
        layout.addWidget(header)
        self.tabs = QTabWidget()
        self.tabs.tabBar().setExpanding(False)
        self.jobs_splitter = QSplitter(Qt.Orientation.Vertical)
        self.jobs_splitter.setObjectName('trubaJobsSplitter')
        self.jobs_splitter.setChildrenCollapsible(False)
        self.jobs_splitter.setHandleWidth(6)
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
        self.forget_button = QPushButton("Forget saved password")
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
        for button in (self.connect_button, self.disconnect_button, self.forget_button):
            button.setAutoDefault(False)
            button.setDefault(False)
        for button in (self.connect_button, self.disconnect_button):
            buttons.addWidget(button)
        buttons.addWidget(self.forget_button)
        buttons.addStretch()
        account_layout.addLayout(buttons)
        account_layout.addWidget(self.status)
        content.addWidget(account)
        # Keep the entire form anchored and unchanged on resize. Smaller
        # viewports scroll instead of squeezing, wrapping or relocating fields.
        self.settings_scroll.setWidget(self.settings_content)
        self._remote_sections = []
        self.fit_settings_content()
        self.resize(500, 540)
        self.connect_button.clicked.connect(self.start_connection)
        self.disconnect_button.clicked.connect(self.stop_connection)
        self.forget_button.clicked.connect(self.forget_password)
        try:
            saved = self.credentials.load()
            if saved:
                self.username.setText(saved[0])
                self.password.setText(saved[1])
                self.remember.setChecked(True)
        except (OSError, ValueError):
            self.status.setText("Saved credentials cannot be read by this Windows user. Enter your credentials again.")
        self._update_controls()
        self.language_manager.changed.connect(self._language_changed)
        from truba_job_list import TrubaJobList
        self.job_list = TrubaJobList(self)
        self.jobs_splitter.addWidget(self.job_list)
        self.jobs_splitter.setStretchFactor(0, 1)
        self.jobs_splitter.setStretchFactor(1, 1)
        # Initial proportions only; WindowStateStore restores the named splitter.
        self.jobs_splitter.setSizes([310, 140])
        from operation_progress import OperationProgress
        self.operation_progress = OperationProgress(self, provider=self._operation_state)
        layout.addWidget(self.operation_progress)

    def _operation_state(self):
        controller = getattr(self.parent(), "remote_controller", None)
        if controller is not None:
            if controller.close_future is not None or controller.future is not None:
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
        while self.tabs.count() > 1:
            previous = self.tabs.widget(1)
            self.tabs.removeTab(1)
            previous.hide()
            previous.setParent(self)
        self._remote_sections = [resources, advanced]
        for section, title in ((resources, 'Resources'), (advanced, 'Advanced')):
            scroll = QScrollArea()
            scroll.setWidgetResizable(False)
            scroll.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
            scroll.setWidget(section)
            self.tabs.addTab(scroll, title)
        self.fit_settings_content()

    def _language_changed(self, *_):
        self.language_manager.localize_tree(self)
        self.fit_settings_content()

    def fit_settings_content(self):
        """Size the shared, non-reflowing canvas when sections are installed."""
        self.settings_content.ensurePolished()
        # Resolve compact action sizes before calculating the form minimum.
        buttons = (self.connect_button, self.disconnect_button)
        button_width = max(132, *(button.fontMetrics().horizontalAdvance(button.text()) + 28 for button in buttons))
        for button in buttons:
            button.setFixedSize(button_width, CONTROL_HEIGHT)
        self.forget_button.setFixedSize(self.forget_button.fontMetrics().horizontalAdvance(self.forget_button.text()) + 24, CONTROL_HEIGHT)
        width = max(460, self.settings_layout.minimumSize().width())
        self.settings_content.setFixedWidth(width)
        for index in range(self.settings_layout.count()):
            section = self.settings_layout.itemAt(index).widget()
            section.setFixedWidth(width)
            section.ensurePolished()
            section.layout().invalidate()
            height = section.heightForWidth(width)
            section.setFixedHeight(height if height >= 0 else section.sizeHint().height())
        self.settings_layout.invalidate()
        self.settings_content.setFixedHeight(self.settings_layout.sizeHint().height())
        for section in self._remote_sections:
            # Long advanced paths must not widen the connection form.
            section.setFixedWidth(max(width, section.layout().minimumSize().width()))
            section.layout().invalidate()
            section.setFixedHeight(section.heightForWidth(section.width()) if section.hasHeightForWidth() else section.sizeHint().height())

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

    def forget_password(self):
        self._remember_changed(False)
        self.remember.setChecked(False)
        self.password.clear()

    def start_connection(self):
        if self.worker is not None:
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
        answer = QMessageBox.question(
            self, "Verify TRUBA server identity",
            "First connection to this server. Verify this fingerprint with TRUBA before accepting:\n\n" + fingerprint,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
        worker.host_accepted = answer == QMessageBox.StandardButton.Yes
        worker.host_decided.set()

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
        self.connection_badge.setText('Connected' if self.connected else
                                      'Connecting...' if state in ('connecting', 'authenticating', 'reconnecting') else 'Disconnected')
        self.connection_badge.setProperty('connected', self.connected)
        self.connection_badge.style().unpolish(self.connection_badge)
        self.connection_badge.style().polish(self.connection_badge)
        if self.connected and self.remember.isChecked():
            try:
                self.credentials.save(self.username.text().strip(), self.password.text())
            except OSError:
                self.status.setText("Connected to TRUBA. Password could not be saved for this Windows user.")
        self._update_controls()

    def _show_failure_detail(self, message):
        if not self.stopping:
            self.status.setText(message)

    def _update_controls(self):
        running = self.worker is not None
        self.connection_badge.setProperty('connected', self.connected)
        self.connection_badge.style().unpolish(self.connection_badge)
        self.connection_badge.style().polish(self.connection_badge)
        self.connect_button.setEnabled(not running)
        self.connect_button.setText("Connected" if self.connected else "Connect")
        self.connect_button.setProperty("trubaConnected", self.connected)
        self.connect_button.style().unpolish(self.connect_button)
        self.connect_button.style().polish(self.connect_button)
        self.disconnect_button.setEnabled(running and not self.stopping)
        for field in (self.username, self.password, self.remember, self.forget_button):
            field.setEnabled(not running)

    def stop_connection(self):
        if self.worker is not None:
            self.stopping = True
            self.connected = False
            self.connection_changed.emit(False)
            self.disconnect_requested.emit()
            self.status.setText("Disconnecting...")
            self.connection_badge.setText('Disconnecting...')
            self.worker.stop()
            self._update_controls()

    def _finished(self):
        worker = self.worker
        self.worker = None
        self.connected = False
        self.connection_badge.setText('Disconnected')
        self.connection_changed.emit(False)
        if self.stopping:
            self.status.setText("Disconnected")
        if not self.remember.isChecked():
            self.password.clear()
        self._update_controls()
        if worker is not None:
            worker.deleteLater()

    def shutdown(self):
        self.job_list.shutdown()
        self.stop_connection()
        if self.worker is not None:
            self.worker.wait()

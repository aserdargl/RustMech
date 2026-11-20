"""Session-scoped TRUBA mode and asynchronous compute-node capability checks."""
from concurrent.futures import ThreadPoolExecutor, CancelledError
from dataclasses import asdict, replace
import json
from threading import Event

from PySide6.QtCore import QObject,QTimer,Signal,Qt
from PySide6.QtWidgets import (QCheckBox,QFormLayout,QGridLayout,QGroupBox,QHBoxLayout,QLabel,
                               QLineEdit,QPushButton,QVBoxLayout)

from truba_remote import RemoteContext,run_remote_job
from studio_style import CompactSpinBox, configure_form, CompactComboBox, CompactDoubleSpinBox, CONTROL_HEIGHT
from truba_session import saved_session, create_session, refresh_session, release_session
from truba_resources import (BASELINES, AUTO_QUEUES, candidates, fetch_catalog,
                             allocate_with_limits, automatic_context, convergence_context)


DEFAULT_SESSION_WALLTIME = '04:00:00'


def ensure_remote_runtime(context, auth, *, cancelled, on_progress=None, prepare_damask=False, detach_requested=None):
    """Reuse a verified runtime; repair the app-owned environment once if needed."""
    result, directory = run_remote_job(dict(operation='probe'), context, auth,
                                     cancelled=cancelled, on_progress=on_progress, detach_requested=detach_requested)
    if result is None or (detach_requested is not None and detach_requested.is_set()):
        return result, directory
    capabilities = result['capabilities']
    required = ('mesh', 'damask') if prepare_damask else ('mesh', 'netgen', 'kratos')
    if all(capabilities.get(key) for key in required):
        return result, directory
    if cancelled.is_set():
        raise CancelledError
    # Custom interpreters belong to their owner. The installer also validates
    # resolved remote paths before changing anything.
    if not context.runtime_python.startswith('~/.local/share/RustMech/remote-'):
        return result, directory
    request = dict(operation='prepare')
    if prepare_damask:
        request['prepare_damask'] = True
    return run_remote_job(request, context, auth,
                          cancelled=cancelled, on_progress=on_progress, detach_requested=detach_requested)


def release_while_busy(session, pending, allocating, auth):
    """Send scancel independently of runtime work and queue-refresh requests."""
    if allocating and pending is not None:
        try:
            session = pending.result()
        except Exception:
            session = saved_session() or session
    if session is None:
        raise RuntimeError('No submitted resource session could be found. Refresh and retry.')
    session = release_session(session, auth)
    # An older refresh can still write RUNNING. Let it finish, then retain the
    # confirmed release as the final durable state.
    if pending is not None and not allocating:
        try:
            pending.result()
        except Exception:
            pass
        from truba_session import save_released_session
        session = save_released_session(session)
    return session


class RemoteController(QObject):
    changed = Signal()
    mode_changed = Signal(bool)
    close_ready = Signal()
    runtime_progress = Signal(str)

    def __init__(self,window,preferences):
        super().__init__(window)
        self.window,self.preferences = window,preferences
        self.requested = False
        self.capabilities = None
        self.runtime_stage = ''
        self.runtime_progress.connect(self._runtime_progress)
        self.owner = None
        self.future = None
        self.cancelled = Event()
        self.closing = False
        self.close_confirmed = False
        self.close_pending = ()
        self.runtime_detach = Event()
        self._auto_probe_session = None
        self.executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix="truba-runtime")
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.poll)
        self.controls = {}
        self.resource_min = {}
        self.resource_max = {}
        self.queue_supported = True
        self._last_partition = None
        self.auto_preview = None
        self.queue_catalog = dict(BASELINES)
        self.limits_future = None
        try: self.session = saved_session()
        except (OSError, ValueError): self.session = None
        self.session_future = None
        self.release_future = None
        self.release_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='truba-release')
        self.session_action = ""
        self.session_watch = False
        self.session_executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix="truba-session")
        defaults = RemoteContext(partition='auto', walltime=DEFAULT_SESSION_WALLTIME)
        try: defaults = RemoteContext(**json.loads(preferences.value("remote/context",json.dumps(asdict(defaults)))))
        except (ValueError,TypeError): pass
        defaults = replace(defaults, walltime=DEFAULT_SESSION_WALLTIME)
        preferred_partition = defaults.partition
        if self.session and self.session.get("state") not in ("released","ended"):
            defaults = RemoteContext(**self.session["context"])
        panel = QGroupBox("CPU, RAM and resource session")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(6,5,6,5)
        layout.setSpacing(3)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.toggle = QCheckBox("Run mesh generation, convergence and analyses on TRUBA")
        self.toggle.setChecked(False)  # Connection/preferences never activate computations on launch.
        self.toggle.toggled.connect(self.set_enabled)
        layout.addWidget(self.toggle)
        form = QFormLayout()
        configure_form(form)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        advanced = QGroupBox("Advanced · remote runtime")
        advanced_layout = QVBoxLayout(advanced)
        advanced_layout.setContentsMargins(6, 5, 6, 5)
        advanced_layout.setSpacing(3)
        advanced_form = QFormLayout()
        configure_form(advanced_form)
        advanced_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.DontWrapRows)
        advanced_layout.addLayout(advanced_form)
        for name,label in (("partition","Queue"),("account","Account"),
                           ("walltime","Wall time"),("cores","CPU cores"),("gpus","GPUs"),("memory_mb","RAM / GiB"),
                           ("parallel_jobs","Parallel analyses"),
                           ("runtime_python","Runtime Python"),("python_module","Python module"),("foam_module","OpenCFD module"),
                           ("winscp_site","WinSCP site (optional)")):
            if name in ("memory_mb", "cores", "gpus", "parallel_jobs"):
                control = CompactDoubleSpinBox() if name == 'memory_mb' else CompactSpinBox()
                control.setFixedWidth(88)
                control.setRange(1 / 1024, 4096) if name == 'memory_mb' else control.setRange(0 if name == 'gpus' else 1, 8 if name == 'gpus' else 224)
                control.setValue(getattr(defaults, name) / 1024 if name == 'memory_mb' else getattr(defaults, name))
                control.setKeyboardTracking(False)
                control.valueChanged.connect(self.configuration_changed)
            elif name == 'partition':
                control = CompactComboBox()
                control.setFixedWidth(190)
                control.addItem('Auto · program chooses resources', 'auto')
                for key, limit in BASELINES.items():
                    control.addItem(limit.label, key)
                index = control.findData('auto' if ',' in preferred_partition else preferred_partition)
                if index < 0:
                    control.addItem(preferred_partition, preferred_partition)
                    index = control.count() - 1
                control.setCurrentIndex(index)
                control.currentIndexChanged.connect(self.configuration_changed)
            else:
                control = QLineEdit(str(getattr(defaults,name)))
                control.setFixedWidth(120 if name == 'walltime' else 360 if name == 'runtime_python' else 240)
                control.editingFinished.connect(self.configuration_changed)
            self.controls[name] = control
            if name == 'parallel_jobs':
                control.setToolTip('Maximum simultaneous DoE / analysis queue jobs. CPU cores and RAM are divided equally between jobs; each job has separate results. Limited by the allocated cores and memory. Set 1 for a single large analysis.')
            if name in ('cores', 'gpus', 'memory_mb'):
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(8)
                row.addWidget(control)
                for labels in (self.resource_min, self.resource_max):
                    info = QLabel()
                    info.setTextFormat(Qt.TextFormat.PlainText)
                    info.setFixedWidth(120)
                    labels[name] = info
                    row.addWidget(info)
                row.addStretch()
                form.addRow(label, row)
            else:
                target = advanced_form if name in ('runtime_python', 'python_module', 'foam_module', 'winscp_site') else form
                target.addRow(label,control)
        self.transport = defaults.transport
        layout.addLayout(form)
        self.auto_renew = QCheckBox('Prepare replacement resources near the time limit')
        self.auto_renew.setChecked(defaults.auto_renew)
        self.auto_renew.setToolTip('Batch queues request replacement resources in the last hour or the final 10%, whichever is shorter. New analyses wait for them. Running analyses remain subject to the original Slurm time limit.')
        self.auto_renew.toggled.connect(self.configuration_changed)
        layout.addWidget(self.auto_renew)
        self.queue_hint = QLabel()
        self.queue_hint.setWordWrap(True)
        self.queue_hint.setObjectName('mutedHintLabel')
        layout.addWidget(self.queue_hint)
        self.limits_button = QPushButton('Refresh queue limits')
        self.limits_button.setObjectName('secondaryActionBtn')
        self.limits_button.setAutoDefault(False)
        self.limits_button.setDefault(False)
        self.limits_button.clicked.connect(self.refresh_limits)
        layout.addWidget(self.limits_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.session_state = QLabel()
        self.session_state.setObjectName('trubaAllocationState')
        self.session_state.setTextFormat(Qt.TextFormat.PlainText)
        self.session_state.setWordWrap(True)
        layout.addWidget(self.session_state)
        self.session_status = QLabel("No resource session. Wall time includes idle time and limits all jobs in the allocation.")
        self.session_status.setTextFormat(Qt.TextFormat.PlainText)
        self.session_status.setWordWrap(True)
        layout.addWidget(self.session_status)
        session_buttons = QHBoxLayout()
        self.allocate = QPushButton("Allocate resources")
        self.session_refresh = QPushButton("Refresh session")
        self.session_release = QPushButton("Release resources")
        for button in (self.allocate,self.session_refresh,self.session_release):
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setObjectName("secondaryActionBtn")
            session_buttons.addWidget(button)
        session_buttons.addStretch()
        self.allocate.clicked.connect(lambda:self.session_command("allocate"))
        self.session_refresh.clicked.connect(lambda:self.session_command("refresh"))
        self.session_release.clicked.connect(lambda:self.session_command("release"))
        layout.addLayout(session_buttons)
        hint = QLabel("Slurm stops running analyses when the allocation reaches its wall time.")
        hint.setWordWrap(True)
        hint.setObjectName("mutedHintLabel")
        layout.addWidget(hint)
        # Keep the session explanation available without pushing actions below
        # the standard window's fold.
        self.toggle.setToolTip(hint.text())
        self.controls['walltime'].setToolTip(hint.text())
        hint.hide()
        buttons = QGridLayout()
        buttons.setHorizontalSpacing(6)
        buttons.setVerticalSpacing(4)
        self.check = QPushButton("Check remote capabilities")
        self.prepare = QPushButton("Prepare remote runtime")
        self.prepare.setToolTip("Repair the dedicated per-user Python/Rust environment manually. Resources automatically check and prepare this environment when RUNNING.")
        self.recover = QPushButton("Retrieve remote job…")
        self.stop = QPushButton("Stop remote check/setup")
        self.stop.setEnabled(False)
        for index, button in enumerate((self.check, self.prepare, self.recover, self.stop)):
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setObjectName("secondaryActionBtn")
            buttons.addWidget(button, index // 2, index % 2, Qt.AlignmentFlag.AlignLeft)
        buttons.setColumnStretch(2, 1)
        self.check.clicked.connect(lambda:self.refresh(False))
        self.prepare.clicked.connect(lambda:self.refresh(True))
        self.recover.clicked.connect(window.retrieve_truba_job)
        self.stop.clicked.connect(self.cancelled_check)
        layout.insertLayout(1, buttons)
        self.status = QLabel("Local mode. Connecting alone does not move computations to TRUBA.")
        self.status.setWordWrap(True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setFixedHeight(64)
        self.status.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.insertWidget(2, self.status)
        self.scroll = panel
        panel.setObjectName("trubaRemoteControls")
        self.resource_panel = panel
        self.advanced_panel = advanced
        window.truba_dialog.install_remote_sections(panel, advanced)
        window.truba_dialog.connection_changed.connect(self.connection_changed)
        window.truba_dialog.disconnect_requested.connect(self.disconnect)
        window.truba_dialog.job_list.release_requested.connect(lambda: self.session_command('release'))
        self.session_timer = QTimer(self)
        self.session_timer.setInterval(250)
        self.session_timer.timeout.connect(self.poll_session)
        self.session_timer.start()
        self.session_next_refresh = 0
        self.apply_queue_limits()
        self.update_session_controls()
        self.queue_hint.setFixedHeight(44)
        self.queue_hint.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.session_status.setFixedHeight(36)
        self.session_status.setAlignment(Qt.AlignmentFlag.AlignTop)
        window.truba_dialog.language_manager.changed.connect(self.fit_action_buttons)
        self.fit_action_buttons()
        dialog = window.truba_dialog
        if not dialog.property('preferencesRestored'):
            area = dialog.screen().availableGeometry()
            content = dialog.settings_content.size()
            dialog.resize(min(content.width() + 48, area.width() - 16),
                          min(max(760, content.height() + 265), area.height() - 32))
            dialog.jobs_splitter.setSizes([content.height() + 32, 135])

    def fit_action_buttons(self, *_):
        dialog = self.window.truba_dialog
        dialog.language_manager.localize_tree(dialog)
        for group in ((self.allocate, self.session_refresh, self.session_release),
                      (self.check, self.prepare, self.recover, self.stop), (self.limits_button,)):
            for button in group:
                button.ensurePolished()
            width = max(button.fontMetrics().horizontalAdvance(button.text()) + 24 for button in group)
            for button in group:
                button.setFixedSize(width, CONTROL_HEIGHT)
        dialog.fit_settings_content()

    @property
    def ready(self):
        if not self.session or self.session.get("state") != "RUNNING": return False
        if not self.session_watch or not self.session.get("ready"): return False
        return self.capabilities is not None and bool(self.capabilities.get("mesh"))

    def context(self, *, use_session=True):
        if self.controls['partition'].currentData() == 'auto' and not (self.session and self.session.get('state') not in ('released', 'ended')):
            self.apply_queue_limits()
        values = {key:control.currentData() if key == 'partition' else control.value() if key in ('cores', 'gpus', 'memory_mb', 'parallel_jobs') else control.text().strip() for key,control in self.controls.items()}
        values['auto_renew'] = self.auto_renew.isChecked()
        values['memory_mb'] = round(values['memory_mb'] * 1024)
        active = use_session and self.session and (self.session.get("state") not in ("released","ended") or
                                   self.session.get("state") == "ended" and self.session.get("job_id"))
        if active and 'context' in self.session:
            values.update({key: self.session['context'].get(key, values[key]) for key in values})
        context = RemoteContext(**values,transport=self.transport,
                                session_id=str(self.session.get("job_id", "")) if active else "")
        if active and not context.session_id:
            raise RuntimeError("Resource submission is unconfirmed. Refresh the saved session first.")
        context.validate()
        return context

    def apply_queue_limits(self):
        active = self.session and self.session.get('state') not in ('released', 'ended')
        name = self.controls['partition'].currentData()
        if active:
            context = self.session.get('context', {})
            name = context.get('partition', name).split(',')[0]
        self.queue_supported = True
        self.auto_preview = None
        if name == 'auto':
            try:
                required = self.window.controls['cores'].value() if 'cores' in self.window.controls else 1
                proposal = automatic_context(RemoteContext(walltime=self.controls['walltime'].text().strip()), self.queue_catalog, min_cpus=required)
                self.auto_preview = proposal
                limit = self.queue_catalog[proposal.partition]
            except (ValueError, KeyError) as error:
                self.queue_supported = False
                self.queue_hint.setText(str(error))
                return
        else:
            limit = self.queue_catalog.get(name)
            if not limit or not limit.available:
                self.queue_supported = False
                self.queue_hint.setText('Queue unavailable. Connect and refresh limits or choose another queue.')
                for key in self.resource_min:
                    self.resource_min[key].setText('Min: unavailable')
                    self.resource_max[key].setText('Max: unavailable')
                return
        changed_queue = self._last_partition is not None and name != self._last_partition
        self._last_partition = name
        cpu, gpu, ram = (self.controls[k] for k in ('cores', 'gpus', 'memory_mb'))
        for control in (cpu, gpu, ram):
            control.blockSignals(True)
        try:
            if active:
                cpu.setRange(1, 224)
                gpu.setRange(0, 8)
                ram.setRange(1 / 1024, 4096)
                cpu.setValue(context.get('cores', cpu.value()))
                gpu.setValue(context.get('gpus', 0))
                ram.setValue(context.get('memory_mb', round(ram.value() * 1024)) / 1024)
            else:
                cpu.setRange(limit.min_cpus, limit.max_cpus)
                gpu.setRange(limit.min_gpus, limit.max_gpus)
                if name == 'auto':
                    cpu.setValue(proposal.cores)
                    gpu.setValue(proposal.gpus)
                elif changed_queue:
                    cpu.setValue(limit.min_cpus)
                    gpu.setValue(limit.min_gpus)
                ram.setRange((limit.min_memory_mb or 1) / 1024, max(1, limit.memory_ceiling(cpu.value())) / 1024)
                if name == 'auto':
                    ram.setValue(proposal.memory_mb / 1024)
                elif changed_queue:
                    ram.setValue(limit.suggested_memory(cpu.value()) / 1024)
        finally:
            for control in (cpu, gpu, ram):
                control.blockSignals(False)
        self.resource_min['cores'].setText(f'Min: {limit.min_cpus}')
        self.resource_max['cores'].setText(f'Max: {limit.max_cpus}')
        self.resource_min['gpus'].setText(f'Min: {limit.min_gpus}')
        self.resource_max['gpus'].setText(f'Max: {limit.max_gpus}')
        self.resource_min['memory_mb'].setText('Min: not specified' if limit.min_memory_mb is None else f'Min: {limit.min_memory_mb / 1024:g} GiB')
        self.resource_max['memory_mb'].setText(f'Max: {limit.memory_ceiling(cpu.value()) / 1024:.3f} GiB')
        ram.setToolTip('No site minimum RAM is published. Enter a positive amount; the maximum depends on CPU count. Slurm defaults are not minimum requirements.')
        source = 'Live Slurm limits' if limit.verified else 'TRUBA reference · refresh to verify'
        detail = f'Auto: {limit.label} · {cpu.value()} CPUs · {ram.value():.3f} GiB. ' if name == 'auto' else ''
        if name == 'auto':
            detail += 'Preview only; Allocate compares CPU queue start estimates. '
        if limit.min_gpus or gpu.value():
            self.queue_supported = False
            detail += 'GPU queue: limits can be inspected and edited; allocation is unavailable because current solvers use CPUs. '
        if name == 'smp':
            detail += 'Orkinos may require a TRUBA reservation. '
        if active:
            detail += 'Existing allocation restored unchanged. '
        self.queue_hint.setText(source + '. ' + detail + 'RAM maximum follows CPU count; account/QOS and job-submission rules are checked by Slurm.')
        if not active:
            from truba_resources import seconds
            walltime = self.controls['walltime']
            try:
                if seconds(walltime.text()) > limit.max_seconds:
                    duration = limit.max_seconds
                    walltime.setText(f'{duration // 3600:02d}:{duration % 3600 // 60:02d}:{duration % 60:02d}')
            except ValueError:
                pass

    def refresh_limits(self):
        if self.limits_future is not None or self.future is not None or self.session_future is not None:
            return
        if self.session and self.session.get('state') not in ('released', 'ended'):
            return
        try:
            self.limits_future = self.session_executor.submit(fetch_catalog, self.context(), self.auth())
            self.queue_hint.setText('Reading queue and memory limits from Slurm…')
            self.update_session_controls()
        except Exception as error:
            self.queue_hint.setText(str(error))

    def update_session_controls(self):
        active = bool(self.session and self.session.get("state") not in ("released","ended"))
        busy = self.closing or self.release_future is not None or self.session_future is not None or self.limits_future is not None
        self.allocate.setEnabled(not active and not busy and self.future is None and self.queue_supported)
        self.session_refresh.setEnabled(active and not busy)
        allocating = self.session_future is not None and self.session_action == 'allocate'
        self.session_release.setEnabled(bool(active or allocating) and not self.closing and self.release_future is None)
        for control in self.controls.values(): control.setEnabled(not active and not busy and self.future is None)
        self.auto_renew.setEnabled(not active and not busy and self.future is None)
        if self.controls['partition'].currentData() == 'auto':
            for key in ('cores', 'gpus', 'memory_mb'):
                self.controls[key].setEnabled(False)
        self.limits_button.setEnabled(not active and not busy and self.future is None)
        state = str((self.session or {}).get('state', 'NONE')).upper()
        explanations = {
            'NONE': 'No resource allocation.',
            'PENDING': 'Queued; resources have not been allocated yet.',
            'RUNNING': 'Resources allocated; session worker is starting.',
            'CONFIGURING': 'Slurm is preparing the allocated resources.',
            'COMPLETING': 'Job is finishing and resources are being released.',
            'SUSPENDED': 'Job is suspended.',
            'RELEASED': 'Resources released.',
            'ENDED': 'Session ended; no active allocation.',
            'SUBMITTING': 'Submission is unconfirmed; refresh to verify.',
        }
        explanation = explanations.get(state, 'Slurm job state.')
        if active and not self.session_watch:
            explanation = 'Saved state; refresh the session to verify with Slurm.'
        elif state == 'RUNNING' and self.session.get('ready'):
            explanation = 'Resources allocated; session worker is ready.'
        if self.session_future is not None and self.session_action == 'allocate':
            state, explanation = 'SUBMITTING', 'Checking the request and submitting to Slurm…'
        self.session_state.setText('Slurm: ' + state + ' · ' + explanation)
        if self.session:
            replacement = ''
            try:
                from truba_handover import successor_path
                if successor_path().exists():
                    successor = json.loads(successor_path().read_text(encoding='utf-8'))
                    if (successor.get('renewal_parent_id') == self.session.get('job_id')
                            and successor.get('username') == self.session.get('username')):
                        replacement = ' · replacement: ' + str(successor.get('job_id', 'submitting')) + ' · ' + successor.get('state', 'unknown')
            except (OSError, ValueError):
                pass
            self.session_status.setText("Resource session " + str(self.session.get("job_id", "unconfirmed")) +
                " · " + self.session.get("state", "unknown") +
                (" · ready" if self.session.get("ready") and self.session_watch else "") +
                (" · " + self.session['allocated_partition'] if self.session.get('allocated_partition') else '') +
                (" · " + self.session['allocated_nodes'] if self.session.get('allocated_nodes') else '') +
                " · remaining: " + (self.session.get("remaining") or "—") +
                replacement +
                (" · " + self.session["reason"] if self.session.get("reason") else "") +
                (" · refresh to verify" if active and not self.session_watch else ""))
        self.session_status.setToolTip(self.session_status.text())

    def session_command(self, action):
        if self.closing: return
        if action == 'release':
            self.release_resources()
            return
        if self.release_future is not None: return
        if self.session_future is not None or self.limits_future is not None: return
        if self.future is not None or self._physics_busy() or self.window.future is not None:
            self.session_status.setText("Finish or stop current computations before changing the resource session.")
            return
        try:
            auth = self.auth()
            if action == "allocate":
                from dataclasses import replace
                context = replace(self.context(), session_id='')
                if not self.queue_supported:
                    raise ValueError('Select a supported CPU queue or Auto before allocating resources.')
                if context.partition != 'auto':
                    candidates(context.partition, context.cores, context.memory_mb, context.walltime, self.queue_catalog, gpus=context.gpus)
                self.session_future = self.session_executor.submit(allocate_with_limits, context, auth)
            else:
                if not self.session: return
                self.session_future = self.session_executor.submit(
                    refresh_session, self.session, auth)
            self.session_action = action
            self.session_watch = True
            self.capabilities = None
            if action == 'allocate':
                self._auto_probe_session = None
                if not self.requested:
                    self.requested = True
                    self.toggle.blockSignals(True)
                    self.toggle.setChecked(True)
                    self.toggle.blockSignals(False)
                    self.mode_changed.emit(True)
            self.changed.emit()
            self.update_session_controls()
            self.session_status.setText("Resource session: " + action + "…")
        except Exception as error:
            self.session_status.setText(str(error))

    def release_resources(self):
        if self.closing or self.release_future is not None:
            return
        pending = self.session_future
        allocating = pending is not None and self.session_action == 'allocate'
        if not self.session and not allocating:
            return
        self.session_watch = False
        self.cancelled.set()
        if getattr(self.window, '_preview_is_remote', False):
            self.window.mesh_cancelled.set()
        self.capabilities = None
        message = 'Cancelling resource session; waiting for Slurm to release resources…'
        try:
            self.release_future = self.release_executor.submit(
                release_while_busy, self.session, pending, allocating, self.auth())
            self.window.truba_dialog.job_list.set_session_release_state(True, message)
            self.update_session_controls()
            self.session_status.setText(message)
            self.changed.emit()
        except Exception as error:
            self.session_status.setText('Resource cancellation failed: ' + str(error))

    def poll_session(self):
        if self.closing:
            self.poll_close()
            return
        import time
        if self.release_future is not None:
            if not self.release_future.done():
                return
            future, self.release_future = self.release_future, None
            try:
                self.session = future.result()
                self.session_future = None
                message = 'Resources released.'
            except Exception as error:
                message = 'Resource cancellation not confirmed: ' + str(error) + '. Reconnect and retry.'
            self.update_session_controls()
            self.session_status.setText(message)
            self.window.truba_dialog.job_list.set_session_release_state(False, message)
            self.changed.emit()
            return
        if self.limits_future is not None and self.limits_future.done():
            future, self.limits_future = self.limits_future, None
            try:
                self.queue_catalog = future.result()
                self.apply_queue_limits()
                self.configuration_changed()
            except Exception as error:
                self.queue_hint.setText('Queue refresh failed: ' + str(error))
            self.update_session_controls()
        if self.session_future is not None:
            if not self.session_future.done(): return
            future,self.session_future = self.session_future,None
            try:
                self.session = future.result()
                self.apply_queue_limits()
                self.update_session_controls()
                if self.session_action == "allocate":
                    self.status.setText("Resource request submitted. Waiting for Slurm; runtime will be verified when ready.")
            except Exception as error:
                try: self.session = saved_session() or self.session
                except (OSError,ValueError): pass
                self.session_watch = bool(self.session_action == 'watch' and self.session_watch and self.session)
                if self.session_watch:
                    self.session = dict(self.session, ready=False)
                self.update_session_controls()
                self.session_status.setText("Resource session: " + str(error))
            self.session_next_refresh = time.monotonic() + 5
            self.window.truba_dialog.job_list.refresh()
            self.changed.emit()
        if (self.requested and self.session_watch and self.session and self.session.get('ready')
                and self.session.get('state') == 'RUNNING' and self.future is None
                and self.capabilities is None
                and self._auto_probe_session != self.session.get('job_id') and not self._physics_busy()):
            if self.refresh(False, automatic=True):
                self._auto_probe_session = self.session.get('job_id')
        if (self.session_watch and self.session and self.session.get("state") not in ("released","ended")
                and time.monotonic() >= self.session_next_refresh):
            self.session_future = self.session_executor.submit(refresh_session,self.session,self.auth())
            self.session_action = "watch"

    def auth(self): return self.window.truba_dialog.analysis_auth()

    def _save_context_preferences(self, context):
        # A running session has a concrete Slurm partition even when Auto was selected.
        preferences = replace(context, partition=self.controls['partition'].currentData(), session_id='')
        self.preferences.setValue('remote/context', json.dumps(asdict(preferences)))
        self.preferences.sync()

    def configuration_changed(self,*_):
        if self.closing: return
        if self.future is not None: return
        self.apply_queue_limits()
        self.update_session_controls()
        try:
            self._save_context_preferences(self.context())
        except (ValueError, RuntimeError) as error:
            self.status.setText(str(error))
        self.capabilities = None
        self._auto_probe_session = None
        self.changed.emit()
        if self.requested: self.status.setText("Configuration changed. Runtime will be checked and prepared automatically.")

    def _physics_busy(self):
        window = self.window
        convergence = window._mesh_convergence_dialog
        return bool(window.analysis_job or getattr(window.doe_studio,"_active_future",None) or
                    getattr(getattr(window, 'analysis_queue_widget', None), '_active_future', None) or
                    window.flow_workspace.job or window.thermal_workspace.job or
                    (convergence is not None and convergence.study.future is not None))

    def set_enabled(self,enabled, *, force=False):
        if self.closing: return
        if not force and self._physics_busy():
            self.toggle.blockSignals(True)
            self.toggle.setChecked(self.requested)
            self.toggle.blockSignals(False)
            self.status.setText("Finish or stop current computations before changing execution mode.")
            return
        self.requested = bool(enabled)
        self.capabilities = None
        self._auto_probe_session = None
        self.mode_changed.emit(self.requested)
        self.changed.emit()
        if enabled:
            if self.session_watch and self.session and self.session.get('ready'):
                self.refresh(False, automatic=True)
            else:
                self.status.setText("Allocate resources once, or refresh a saved session. All computations use that session.")
        else: self.status.setText("Local mode. Remote jobs already submitted can be retrieved using their records.")

    def disconnect(self):
        if self.closing: return
        # An explicit disconnect selects local execution for subsequent work.
        # Keep submitted job/session records available for explicit retrieval.
        self.session_watch = False
        self._auto_probe_session = None
        self.runtime_detach.set()
        if self.requested:
            self.toggle.blockSignals(True)
            self.toggle.setChecked(False)
            self.toggle.blockSignals(False)
            self.set_enabled(False, force=True)
        else:
            self.capabilities = None
            self.changed.emit()
            self.status.setText("Local mode. Remote jobs already submitted can be retrieved using their records.")
        self.update_session_controls()

    def connection_changed(self,connected):
        if self.closing:
            return
        self.session_watch = False
        if connected:
            auth = self.auth()
            if (auth is not None and self.session and self.session.get('username') == auth.username
                    and self.session.get('state') not in ('released', 'ended')):
                self.session_watch = True
                self.session = dict(self.session, ready=False)
                self.session_next_refresh = 0
        else:
            self.runtime_detach.set()
        self.update_session_controls()
        if self.requested:
            self.capabilities = None
            self._auto_probe_session = None
            self.changed.emit()
            self.status.setText("Connection changed. Runtime will be checked and prepared automatically.")

    def refresh(self,prepare=False, *, automatic=False):
        if self.closing: return
        if self.future is not None:
            return
        if any(future is not None for future in (self.session_future, self.limits_future, self.release_future)):
            if not automatic:
                self.status.setText("Wait for the current TRUBA resource operation to finish, then retry.")
            return
        if self._physics_busy():
            self.status.setText("Finish or stop current computations before refreshing the remote runtime.")
            return
        if (prepare or automatic) and self.requested and self.window.future is not None:
            self.status.setText("Finish or stop the remote mesh job before modifying its runtime.")
            return
        if (not self.session or self.session.get('state') != 'RUNNING'
                or not self.session_watch or not self.session.get('ready')):
            self.status.setText("Allocate resources and wait for a ready session before checking or preparing the runtime.")
            return
        try:
            context,auth = self.context(),self.auth()
            self._save_context_preferences(context)
            self.cancelled = Event()
            self.runtime_detach = Event()
            self.capabilities = None
            fft_selected = self.window.setup_tabs.currentIndex() == self.window._fft_method_index
            if automatic:
                self.future = self.executor.submit(ensure_remote_runtime, context, auth,
                    cancelled=self.cancelled, on_progress=self.runtime_progress.emit, prepare_damask=fft_selected,
                    detach_requested=self.runtime_detach)
            else:
                request = dict(operation="prepare" if prepare else "probe")
                if prepare and fft_selected:
                    request['prepare_damask'] = True
                self.future = self.executor.submit(run_remote_job,request,context,auth,
                    cancelled=self.cancelled, on_progress=self.runtime_progress.emit, detach_requested=self.runtime_detach)
            self._auto_probe_session = self.session.get('job_id')
        except Exception as error:
            self.status.setText(str(error))
            return
        self.check.setEnabled(False)
        self.prepare.setEnabled(False)
        self.recover.setEnabled(False)
        self.stop.setEnabled(True)
        for control in self.controls.values(): control.setEnabled(False)
        self.status.setText("SLURM job: preparing the per-user remote runtime…" if prepare else "SLURM job: checking runtime and supported solvers on a compute node…")
        self.runtime_stage = 'preparing runtime' if prepare else 'checking runtime'
        self.timer.start()
        self.changed.emit()
        return True

    def _runtime_progress(self, message):
        if self.future is not None and not self.cancelled.is_set() and not self.closing:
            self.runtime_stage = message
            self.status.setText(message)
            self.changed.emit()

    def poll(self):
        if self.closing: return
        if self.future is None or not self.future.done(): return
        future,self.future = self.future,None
        self.timer.stop()
        self.check.setEnabled(True)
        self.prepare.setEnabled(True)
        self.recover.setEnabled(True)
        self.stop.setEnabled(False)
        self.update_session_controls()
        if self.cancelled.is_set() or self.runtime_detach.is_set():
            self.capabilities = None
            self.changed.emit()
            return
        try:
            result,directory = future.result()
            self.capabilities = result["capabilities"]
            import json
            self.owner = json.loads((directory/"remote_job.json").read_text())["username"]
            active = [label for key,label in (("mesh","Mesh"),("kratos","Kratos mechanics"),("damask","DAMASK FFT"),("flow","OpenFOAM CFD"),("thermal","OpenFOAM CHT")) if self.capabilities.get(key)]
            reasons = self.capabilities.get("reasons",{})
            self.status.setText("Available: "+(", ".join(active) if active else "none")+".\n"+
                                "\n".join(f"{key}: {value}" for key,value in reasons.items() if not self.capabilities.get(key)))
        except Exception as error:
            self.capabilities = None
            self.status.setText("Remote runtime check/setup failed: "+str(error))
        self.changed.emit()

    def cancelled_check(self):
        self.cancelled.set()
        self.stop.setEnabled(False)
        self.status.setText("Cancelling the remote check/setup job…")

    def require(self,capability,settings=None, *, allow_handover=False):
        return self._require(capability, settings, allow_handover=allow_handover)

    def require_convergence(self, settings, *, max_jobs):
        """Validate the resource request for an independent Slurm submission."""
        if self.closing:
            raise RuntimeError("RustMech is closing.")
        if not self.requested or not self.convergence_solver_enabled(settings.solver_backend):
            raise ValueError("Select Kratos and enable TRUBA for mesh convergence.")
        context = convergence_context(self.context(use_session=False), self.queue_catalog, settings.cores, max_jobs)
        self._validate_kratos_request(settings, context)
        study_name = self.window.truba_dialog.active_jobs.study_name.text().strip()
        return replace(context, study_name=study_name), self.auth()

    def require_batch_queue(self, partition, settings):
        return self._require(settings.solver_backend, settings, partition=partition)

    def _require(self, capability, settings, *, allow_handover=False, partition=None):
        if self.closing: raise RuntimeError('RustMech is closing.')
        can_renew = (allow_handover and self.session and self.session.get('state') == 'ended'
                     and self.session.get('context', {}).get('auto_renew', True)
                     and not self.session.get('renewal_disabled'))
        if (not self.session or not self.session_watch or
                (not can_renew and (self.session.get("state") != "RUNNING" or not self.session.get("ready")))):
            raise RuntimeError("Allocate or refresh the resource session and wait until it is ready before computing.")
        if not self.requested: raise ValueError("TRUBA computation mode is not enabled.")
        if not self.ready:
            if self.future is not None or self._auto_probe_session is None:
                raise RuntimeError("TRUBA runtime is being checked and prepared automatically. Wait until READY before computing.\n" + self.status.text())
            raise RuntimeError("TRUBA runtime preparation did not complete.\n" + self.status.text())
        if not self.capabilities.get(capability):
            raise RuntimeError(self.capabilities.get("reasons",{}).get(capability,f"{capability} is unavailable or unsupported on TRUBA."))
        if settings is not None and capability in ("mesh","kratos") and settings.mesh_type not in self.capabilities.get("mesh_types",[]):
            raise RuntimeError(f"The remote runtime does not support {settings.mesh_type} meshing.")
        context = self.context()
        if partition is not None:
            from truba_remote import request_resources

            baseline = BASELINES[partition]
            live_limits = self.queue_catalog.get(partition, baseline)
            minimum_cores = max(settings.cores, baseline.min_cpus, live_limits.min_cpus)
            maximum_cores = min(baseline.max_cpus, live_limits.max_cpus)
            _, memory = request_resources(context, 'doe_solve')
            memory = max(memory, live_limits.min_memory_mb or 1)
            cores = next((count for count in range(minimum_cores, maximum_cores + 1)
                          if memory <= min(baseline.memory_ceiling(count), live_limits.memory_ceiling(count))),
                         minimum_cores)
            candidates(partition, cores, memory, context.walltime)
            candidates(partition, cores, memory, context.walltime, self.queue_catalog)
            context = replace(context, partition=partition, cores=cores, memory_mb=memory, gpus=0, session_id='')
        if settings is not None and capability=="kratos":
            if settings.cores > context.cores:
                raise RuntimeError("Solver CPU count exceeds the TRUBA allocation. Increase remote CPU cores or reduce solver cores.")
            self._validate_kratos_request(settings, context)
        study_name = self.window.truba_dialog.active_jobs.study_name.text().strip()
        return replace(context, study_name=study_name), self.auth()

    @staticmethod
    def _validate_kratos_request(settings, context):
        from solver_api import KRATOS_CAPABILITIES
        effective = replace(settings, truba_partition=context.partition, truba_account=context.account,
                            truba_walltime=context.walltime, truba_memory_mb=context.memory_mb)
        errors = KRATOS_CAPABILITIES.validate(effective)
        if errors:
            raise RuntimeError("\n".join(errors))

    def solver_enabled(self,identifier):
        return self.ready and bool(self.capabilities.get(identifier))

    def convergence_solver_enabled(self, identifier):
        return identifier == "kratos" and (not self.ready or self.solver_enabled(identifier))

    def apply_solver_combo(self,combo, *, allow_direct_convergence=False):
        from solver_api import get_solver_capabilities,SOLVER_REGISTRY
        remote_enabled = self.convergence_solver_enabled if allow_direct_convergence else self.solver_enabled
        for i in range(combo.count()):
            identifier = combo.itemData(i)
            capabilities = get_solver_capabilities(identifier) if identifier in SOLVER_REGISTRY else None
            enabled = remote_enabled(identifier) if self.requested else capabilities.implemented if capabilities is not None else combo.model().item(i).isEnabled()
            combo.model().item(i).setEnabled(enabled)
            if self.requested and not enabled:
                combo.setItemData(i,(self.capabilities or {}).get("reasons",{}).get(identifier,"Not verified as available/supported on TRUBA."),Qt.ItemDataRole.ToolTipRole)
            elif capabilities is not None:
                combo.setItemData(i,capabilities.unavailable_reason or capabilities.display_name,Qt.ItemDataRole.ToolTipRole)
        if self.requested and not remote_enabled(combo.currentData()):
            for i in range(combo.count()):
                if combo.model().item(i).isEnabled():
                    combo.setCurrentIndex(i)
                    break

    def request_close(self, detached_jobs=()):
        if self.close_confirmed:
            return True
        if self.closing:
            return False
        preview = self.window.future if getattr(self.window, '_preview_is_remote', False) else None
        self.runtime_detach.set()
        if preview is not None:
            self.window._preview_detach.set()
        self.close_pending = tuple(future for future in (
            *detached_jobs, self.session_future, self.release_future, self.future, preview
        ) if future is not None and not future.done())
        if not self.close_pending:
            return True
        self.closing = True
        self.window.debounce.stop()
        self.window._analysis_after_mesh = None
        message = 'Saving TRUBA work before closing…'
        self.status.setText(message)
        self.window.status.setText(message)
        self.update_session_controls()
        for button in (self.check, self.prepare, self.recover, self.stop):
            button.setEnabled(False)
        self.changed.emit()
        return False

    def poll_close(self):
        if any(not future.done() for future in self.close_pending):
            return
        self.close_pending = ()
        self.close_confirmed = True
        self.close_ready.emit()

    def shutdown(self):
        self.runtime_detach.set()
        self.timer.stop()
        self.session_timer.stop()
        self.session_executor.shutdown(wait=False,cancel_futures=True)
        self.release_executor.shutdown(wait=False,cancel_futures=True)
        self.executor.shutdown(wait=False,cancel_futures=True)

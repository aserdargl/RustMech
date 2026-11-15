"""Session-scoped TRUBA mode and asynchronous compute-node capability checks."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import json
from threading import Event

from PySide6.QtCore import QObject,QTimer,Signal,Qt
from PySide6.QtWidgets import (QCheckBox,QFormLayout,QGroupBox,QHBoxLayout,QLabel,
                               QLineEdit,QPushButton,QSpinBox,QVBoxLayout,QWidget)

from truba_remote import RemoteContext,run_remote_job
from studio_style import configure_form, CompactComboBox, CompactDoubleSpinBox
from truba_session import saved_session, create_session, refresh_session, release_session
from truba_resources import BASELINES, AUTO_QUEUES, candidates, fetch_catalog, allocate_with_limits, automatic_context


class RemoteController(QObject):
    changed = Signal()
    mode_changed = Signal(bool)

    def __init__(self,window,preferences):
        super().__init__(window)
        self.window,self.preferences = window,preferences
        self.requested = False
        self.capabilities = None
        self.owner = None
        self.future = None
        self.cancelled = Event()
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
        self.session_action = ""
        self.session_watch = False
        self.session_executor = ThreadPoolExecutor(max_workers=1,thread_name_prefix="truba-session")
        defaults = RemoteContext(partition='auto')
        try: defaults = RemoteContext(**json.loads(preferences.value("remote/context",json.dumps(asdict(defaults)))))
        except (ValueError,TypeError): pass
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
                           ("runtime_python","Runtime Python"),("python_module","Python module"),("foam_module","OpenCFD module"),
                           ("winscp_site","WinSCP site (optional)")):
            if name in ("memory_mb", "cores", "gpus"):
                control = CompactDoubleSpinBox() if name == 'memory_mb' else QSpinBox()
                control.setFixedWidth(88)
                control.setRange(1 / 1024, 4096) if name == 'memory_mb' else control.setRange(0 if name == 'gpus' else 1, 8 if name == 'gpus' else 224)
                control.setValue(getattr(defaults, name) / 1024 if name == 'memory_mb' else getattr(defaults, name))
                control.setKeyboardTracking(False)
                control.valueChanged.connect(self.configuration_changed)
            elif name == 'partition':
                control = CompactComboBox()
                control.addItem('Auto · program chooses resources', 'auto')
                for key, limit in BASELINES.items():
                    control.addItem(limit.label, key)
                index = control.findData('auto' if ',' in defaults.partition else defaults.partition)
                if index < 0:
                    control.addItem(defaults.partition, defaults.partition)
                    index = control.count() - 1
                control.setCurrentIndex(index)
                control.currentIndexChanged.connect(self.configuration_changed)
            else:
                control = QLineEdit(str(getattr(defaults,name)))
                control.editingFinished.connect(self.configuration_changed)
            self.controls[name] = control
            if name in ('cores', 'gpus', 'memory_mb'):
                row = QHBoxLayout()
                row.setContentsMargins(0, 0, 0, 0)
                row.setSpacing(8)
                row.addWidget(control)
                for labels in (self.resource_min, self.resource_max):
                    info = QLabel()
                    info.setTextFormat(Qt.TextFormat.PlainText)
                    info.setMinimumWidth(120)
                    labels[name] = info
                    row.addWidget(info, 1)
                form.addRow(label, row)
            else:
                target = advanced_form if name in ('runtime_python', 'python_module', 'foam_module', 'winscp_site') else form
                target.addRow(label,control)
        self.transport = defaults.transport
        layout.addLayout(form)
        self.queue_hint = QLabel()
        self.queue_hint.setWordWrap(True)
        self.queue_hint.setObjectName('mutedHintLabel')
        layout.addWidget(self.queue_hint)
        self.limits_button = QPushButton('Refresh queue limits')
        self.limits_button.setObjectName('secondaryActionBtn')
        self.limits_button.setAutoDefault(False)
        self.limits_button.setDefault(False)
        self.limits_button.clicked.connect(self.refresh_limits)
        layout.addWidget(self.limits_button)
        self.session_status = QLabel("No resource session. Wall time sets the requested session duration (72:00:00 = 3 days).")
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
        self.allocate.clicked.connect(lambda:self.session_command("allocate"))
        self.session_refresh.clicked.connect(lambda:self.session_command("refresh"))
        self.session_release.clicked.connect(lambda:self.session_command("release"))
        layout.addLayout(session_buttons)
        hint = QLabel("The first allocation waits in Slurm. Ready sessions run analyses sequentially on the same resources. Idle time counts. Closing RustMech keeps the allocation until its time limit; use Release resources to end it early.")
        hint.setWordWrap(True)
        hint.setObjectName("mutedHintLabel")
        layout.addWidget(hint)
        self.status = QLabel("Local mode. Connecting alone does not move computations to TRUBA.")
        self.status.setWordWrap(True)
        self.status.setFixedHeight(64)
        self.status.setAlignment(Qt.AlignmentFlag.AlignTop)
        advanced_layout.addWidget(self.status)
        buttons = QHBoxLayout()
        self.check = QPushButton("Check remote capabilities")
        self.prepare = QPushButton("Prepare remote runtime")
        self.prepare.setToolTip("Explicitly install a dedicated per-user Python/Rust computation environment on a SLURM node. Does not change system Python or shell startup files.")
        self.recover = QPushButton("Retrieve remote job…")
        for button in (self.check,self.prepare,self.recover):
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setObjectName("secondaryActionBtn")
            buttons.addWidget(button)
        self.check.clicked.connect(lambda:self.refresh(False))
        self.prepare.clicked.connect(lambda:self.refresh(True))
        self.recover.clicked.connect(window.retrieve_truba_job)
        advanced_layout.addLayout(buttons)
        self.stop = QPushButton("Stop remote check/setup")
        self.stop.setAutoDefault(False)
        self.stop.setDefault(False)
        self.stop.setObjectName("secondaryActionBtn")
        self.stop.setEnabled(False)
        self.stop.clicked.connect(self.cancelled_check)
        advanced_layout.addWidget(self.stop)
        # One fixed canvas and one scrollbar for credentials, resources and
        # advanced setup; resizing never redistributes space between panels.
        self.scroll = panel
        panel.setObjectName("trubaRemoteControls")
        self.resource_panel = panel
        self.advanced_panel = advanced
        window.truba_dialog.settings_layout.addWidget(panel)
        window.truba_dialog.settings_layout.addWidget(advanced)
        window.truba_dialog.connection_changed.connect(self.connection_changed)
        self.session_timer = QTimer(self)
        self.session_timer.setInterval(250)
        self.session_timer.timeout.connect(self.poll_session)
        self.session_timer.start()
        self.session_next_refresh = 0
        self.apply_queue_limits()
        self.update_session_controls()
        self.queue_hint.setFixedHeight(56)
        self.queue_hint.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.session_status.setFixedHeight(48)
        self.session_status.setAlignment(Qt.AlignmentFlag.AlignTop)
        window.truba_dialog.fit_settings_content()
        if not window.truba_dialog.property('preferencesRestored'):
            window.truba_dialog.resize(window.truba_dialog.settings_content.width() + 36, 820)

    @property
    def ready(self):
        if self.session and self.session.get("state") != "released":
            if not self.session_watch or not self.session.get("ready"): return False
        return self.capabilities is not None and bool(self.capabilities.get("mesh"))

    def context(self):
        if self.controls['partition'].currentData() == 'auto' and not (self.session and self.session.get('state') not in ('released', 'ended')):
            self.apply_queue_limits()
        values = {key:control.currentData() if key == 'partition' else control.value() if key in ('cores', 'gpus', 'memory_mb') else control.text().strip() for key,control in self.controls.items()}
        values['memory_mb'] = round(values['memory_mb'] * 1024)
        active = self.session and (self.session.get("state") not in ("released","ended") or
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
        busy = self.session_future is not None or self.limits_future is not None
        self.allocate.setEnabled(not active and not busy and self.future is None and self.queue_supported)
        self.session_refresh.setEnabled(active and not busy)
        self.session_release.setEnabled(active and not busy)
        for control in self.controls.values(): control.setEnabled(not active and not busy and self.future is None)
        if self.controls['partition'].currentData() == 'auto':
            for key in ('cores', 'gpus', 'memory_mb'):
                self.controls[key].setEnabled(False)
        self.limits_button.setEnabled(not active and not busy and self.future is None)
        if self.session:
            self.session_status.setText("Resource session " + str(self.session.get("job_id", "unconfirmed")) +
                " · " + self.session.get("state", "unknown") +
                (" · ready" if self.session.get("ready") and self.session_watch else "") +
                (" · " + self.session['allocated_partition'] if self.session.get('allocated_partition') else '') +
                (" · " + self.session['allocated_nodes'] if self.session.get('allocated_nodes') else '') +
                " · remaining: " + (self.session.get("remaining") or "—") +
                (" · " + self.session["reason"] if self.session.get("reason") else "") +
                (" · refresh to verify" if active and not self.session_watch else ""))

    def session_command(self, action):
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
                    release_session if action == "release" else refresh_session, self.session, auth)
            self.session_action = action
            self.session_watch = True
            self.capabilities = None
            self.changed.emit()
            self.update_session_controls()
            self.session_status.setText("Resource session: " + action + "…")
        except Exception as error:
            self.session_status.setText(str(error))

    def poll_session(self):
        import time
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
                    self.status.setText("Resource request submitted. Once ready, enable TRUBA mode or check remote capabilities.")
            except Exception as error:
                try: self.session = saved_session()
                except (OSError,ValueError): pass
                self.session_watch = False
                self.update_session_controls()
                self.session_status.setText("Resource session: " + str(error))
            self.session_next_refresh = time.monotonic() + 5
            self.changed.emit()
        if (self.session_watch and self.session and self.session.get("state") not in ("released","ended")
                and time.monotonic() >= self.session_next_refresh):
            self.session_future = self.session_executor.submit(refresh_session,self.session,self.auth())
            self.session_action = "watch"

    def auth(self): return self.window.truba_dialog.analysis_auth()

    def configuration_changed(self,*_):
        if self.future is not None: return
        self.apply_queue_limits()
        self.update_session_controls()
        try:
            context = self.context()
            self.preferences.setValue("remote/context",json.dumps({**asdict(context), "session_id": ""}))
            self.preferences.sync()
        except (ValueError, RuntimeError) as error:
            self.status.setText(str(error))
        self.capabilities = None
        self.changed.emit()
        if self.requested: self.status.setText("Configuration changed. Check remote capabilities before computing.")

    def _physics_busy(self):
        window = self.window
        return bool(window.analysis_job or getattr(window.doe_studio,"_active_future",None) or
                    window.flow_workspace.job or window.thermal_workspace.job or
                    any(getattr(child,"future",None) is not None for child in window.findChildren(QWidget) if type(child).__name__=="MeshConvergenceWidget"))

    def set_enabled(self,enabled):
        if self._physics_busy():
            self.toggle.blockSignals(True)
            self.toggle.setChecked(self.requested)
            self.toggle.blockSignals(False)
            self.status.setText("Finish or stop current computations before changing execution mode.")
            return
        self.requested = bool(enabled)
        self.capabilities = None
        self.mode_changed.emit(self.requested)
        self.changed.emit()
        if enabled: self.refresh(False)
        else: self.status.setText("Local mode. Remote jobs already submitted can be retrieved using their records.")

    def connection_changed(self,connected):
        if self.requested:
            self.capabilities = None
            self.changed.emit()
            self.status.setText("Connection changed. Check remote capabilities again; local fallback is disabled.")

    def refresh(self,prepare=False):
        if self.future is not None or self.session_future is not None or self.limits_future is not None: return
        if self._physics_busy():
            self.status.setText("Finish or stop current computations before refreshing the remote runtime.")
            return
        if prepare and self.requested and self.window.future is not None:
            self.status.setText("Finish or stop the remote mesh job before modifying its runtime.")
            return
        try:
            context,auth = self.context(),self.auth()
            self.preferences.setValue("remote/context",json.dumps({**asdict(context), "session_id": ""}))
            self.preferences.sync()
            self.cancelled = Event()
            self.capabilities = None
            self.future = self.executor.submit(run_remote_job,dict(operation="prepare" if prepare else "probe"),context,auth,cancelled=self.cancelled)
        except Exception as error:
            self.status.setText(str(error))
            return
        self.check.setEnabled(False)
        self.prepare.setEnabled(False)
        self.recover.setEnabled(False)
        self.stop.setEnabled(True)
        for control in self.controls.values(): control.setEnabled(False)
        self.status.setText("SLURM job: preparing the per-user remote runtime…" if prepare else "SLURM job: checking runtime and supported solvers on a compute node…")
        self.timer.start()
        self.changed.emit()

    def poll(self):
        if self.future is None or not self.future.done(): return
        future,self.future = self.future,None
        self.timer.stop()
        self.check.setEnabled(True)
        self.prepare.setEnabled(True)
        self.recover.setEnabled(True)
        self.stop.setEnabled(False)
        self.update_session_controls()
        try:
            result,directory = future.result()
            self.capabilities = result["capabilities"]
            import json
            self.owner = json.loads((directory/"remote_job.json").read_text())["username"]
            active = [label for key,label in (("mesh","Mesh"),("kratos","Kratos mechanics"),("flow","OpenFOAM CFD"),("thermal","OpenFOAM CHT")) if self.capabilities.get(key)]
            reasons = self.capabilities.get("reasons",{})
            self.status.setText("Available: "+(", ".join(active) if active else "none")+".\n"+
                                "\n".join(f"{key}: {value}" for key,value in reasons.items() if not self.capabilities.get(key)))
        except Exception as error:
            self.capabilities = None
            self.status.setText("Remote check failed: "+str(error)+"\nUse Connect or select a working saved WinSCP site. No local fallback.")
        self.changed.emit()

    def cancelled_check(self):
        self.cancelled.set()
        self.stop.setEnabled(False)
        self.status.setText("Cancelling the remote check/setup job…")

    def require(self,capability,settings=None):
        if self.session and self.session.get("state") != "released":
            if not self.session_watch or not self.session.get("ready"):
                raise RuntimeError("Refresh the resource session and wait until it is ready before computing.")
        if not self.requested: raise ValueError("TRUBA computation mode is not enabled.")
        if not self.ready: raise RuntimeError("TRUBA runtime is not ready. Check capabilities or prepare the remote runtime; local computation is disabled.")
        if not self.capabilities.get(capability):
            raise RuntimeError(self.capabilities.get("reasons",{}).get(capability,f"{capability} is unavailable or unsupported on TRUBA."))
        if settings is not None and capability in ("mesh","kratos") and settings.mesh_type not in self.capabilities.get("mesh_types",[]):
            raise RuntimeError(f"The remote runtime does not support {settings.mesh_type} meshing.")
        if settings is not None and capability=="kratos" and settings.cores>self.context().cores:
            raise RuntimeError("Solver CPU count exceeds the TRUBA allocation. Increase remote CPU cores or reduce solver cores.")
        if settings is not None and capability=="kratos":
            from dataclasses import replace
            from solver_api import KRATOS_CAPABILITIES
            context=self.context()
            effective=replace(settings,truba_partition=context.partition,truba_account=context.account,
                              truba_walltime=context.walltime,truba_memory_mb=context.memory_mb)
            errors=KRATOS_CAPABILITIES.validate(effective)
            if errors: raise RuntimeError("\n".join(errors))
        return self.context(),self.auth()

    def solver_enabled(self,identifier):
        return self.ready and bool(self.capabilities.get(identifier))

    def apply_solver_combo(self,combo):
        from solver_api import get_solver_capabilities,SOLVER_REGISTRY
        for i in range(combo.count()):
            identifier = combo.itemData(i)
            capabilities = get_solver_capabilities(identifier) if identifier in SOLVER_REGISTRY else None
            enabled = self.solver_enabled(identifier) if self.requested else capabilities.implemented if capabilities is not None else combo.model().item(i).isEnabled()
            combo.model().item(i).setEnabled(enabled)
            if self.requested and not enabled:
                combo.setItemData(i,(self.capabilities or {}).get("reasons",{}).get(identifier,"Not verified as available/supported on TRUBA."),Qt.ItemDataRole.ToolTipRole)
            elif capabilities is not None:
                combo.setItemData(i,capabilities.unavailable_reason or capabilities.display_name,Qt.ItemDataRole.ToolTipRole)
        if self.requested and not self.solver_enabled(combo.currentData()):
            for i in range(combo.count()):
                if combo.model().item(i).isEnabled():
                    combo.setCurrentIndex(i)
                    break

    def shutdown(self):
        self.cancelled.set()
        self.timer.stop()
        self.session_timer.stop()
        self.session_executor.shutdown(wait=False,cancel_futures=True)
        self.executor.shutdown(wait=False,cancel_futures=True)

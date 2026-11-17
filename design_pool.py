"""Incoming Gitterburg designs, validation and explicit analysis preparation."""
from concurrent.futures import ThreadPoolExecutor
import json

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog,
                              QHeaderView, QLabel, QMessageBox, QPushButton,
                              QTableWidget, QTableWidgetItem, QVBoxLayout)

from design_exchange import list_designs, pool_directory, read_design
from imported_geometry import read_surface, store_surface, prepare_general_surface
from studio_style import ActionFlowLayout, STUDIO_STYLESHEET
from window_state import install_window_state


def load_pool_surface(identifier, require_single=True):
    data, path = read_design(identifier, verify=True)
    return data, read_surface(path, require_single=require_single)


class DesignPoolDialog(QDialog):
    geometry_prepared = Signal(str, float, str)
    flow_geometry_prepared = Signal(str, float, str)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        install_window_state(QApplication.instance())
        self.setObjectName("designPoolWindow")
        self.setWindowTitle("Design Pool - Gitterburg / RustMech")
        self.setStyleSheet(STUDIO_STYLESHEET)
        self.resize(880, 570)
        self.setMinimumSize(560, 360)
        self.setSizeGripEnabled(True)
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowMinimizeButtonHint | Qt.WindowType.WindowMaximizeButtonHint)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="design-pool")
        self.future = None
        self.pending = []
        self.preparation_mode = "mechanical"
        self.entries = []
        self._listing = None
        layout = QVBoxLayout(self)
        hint = QLabel("Select Gitterburg designs. Configure support/loading faces, then material, mesh and solver in the main workspace. Add each configured model to the analysis queue.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Design", "Sent (UTC)", "Source"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.itemSelectionChanged.connect(self._selection_changed)
        layout.addWidget(self.table, 1)
        self.details = QLabel("No design selected.")
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        actions = ActionFlowLayout()
        self.prepare = QPushButton("Configure selected...")
        self.prepare.clicked.connect(self.prepare_selected)
        self.prepare_flow = QPushButton("Use for flow / thermal")
        self.prepare_flow.clicked.connect(self.prepare_flow_selected)
        self.refresh_button = QPushButton("Refresh")
        self.refresh_button.clicked.connect(self.refresh)
        close = QPushButton("Close")
        close.clicked.connect(self.close)
        for button in (self.prepare, self.prepare_flow, self.refresh_button, close):
            button.setAutoDefault(False)
            button.setDefault(False)
            actions.addWidget(button)
        layout.addLayout(actions)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        from operation_progress import OperationProgress
        self.operation_progress = OperationProgress(self, provider=lambda: (
            self.future is not None, self.status.text(),
        ))
        self.location = QLabel("Shared pool: " + str(pool_directory()))
        self.location.setWordWrap(True)
        self.location.setObjectName("mutedHintLabel")
        layout.addWidget(self.location)
        layout.addWidget(self.operation_progress)
        self.timer = QTimer(self)
        self.timer.setInterval(150)
        self.timer.timeout.connect(self._poll)
        self.timer.start()
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(2500)
        self.refresh_timer.timeout.connect(self._refresh_visible)
        self.refresh_timer.start()
        self.refresh()

    def _refresh_visible(self):
        if self.isVisible() and self.future is None:
            self.refresh()

    def refresh(self):
        try:
            entries, errors = list_designs()
        except OSError as error:
            self.status.setText(str(error))
            return
        listing = json.dumps((entries, errors), sort_keys=True)
        if listing == self._listing:
            return
        selected = set(self.selected_ids())
        self._listing = listing
        self.entries = entries
        self.table.setRowCount(len(entries))
        for index, entry in enumerate(entries):
            for col, value in enumerate((entry["name"], entry["created_at"][:19].replace("T", " "),
                                         entry.get("parameters", {}).get("source_mode", "Gitterburg"))):
                item = QTableWidgetItem(str(value))
                item.setToolTip(f"{entry['name']}\n{entry['id']}\nCoordinates: mm")
                self.table.setItem(index, col, item)
                item.setSelected(entry["id"] in selected)
        self.status.setText("\n".join(errors) if errors else f"{len(entries)} designs available. Sending never starts analysis.")
        self._selection_changed()

    def selected_ids(self):
        return [self.entries[index]["id"] for index in sorted({item.row() for item in self.table.selectedIndexes()})
                if index < len(self.entries)]

    def _selection_changed(self):
        selected = self.selected_ids()
        self.prepare.setEnabled(bool(selected) and self.future is None)
        self.prepare_flow.setEnabled(len(selected) == 1 and self.future is None)
        self.details.setText(f"{len(selected)} selected. Each design is configured separately; the next opens after adding the current one to the queue."
                             if selected else "No design selected.")

    def prepare_selected(self):
        if self.future is not None:
            return
        self.pending = self.selected_ids()
        self.preparation_mode = "mechanical"
        self.prepare_next()

    def prepare_flow_selected(self):
        selected = self.selected_ids()
        if self.future is not None or len(selected) != 1:
            return
        self.pending.clear()
        self.preparation_mode = "flow"
        self.status.setText("Checking geometry and transfer integrity...")
        self.future = self.executor.submit(load_pool_surface, selected[0], False)
        self._selection_changed()

    def prepare_next(self):
        if not self.pending or self.future is not None:
            return
        identifier = self.pending.pop(0)
        self.status.setText("Checking geometry and transfer integrity...")
        self.prepare.setEnabled(False)
        self.prepare_flow.setEnabled(False)
        self.future = self.executor.submit(load_pool_surface, identifier)

    def _poll(self):
        if self.future is None or not self.future.done():
            return
        future, self.future = self.future, None
        try:
            data, surface = future.result()
            if self.preparation_mode == "flow":
                surface, size = prepare_general_surface(surface)
                path = store_surface(surface)
                self.hide()
                self.flow_geometry_prepared.emit(path, size, data["name"])
                return
            from geometry_import_dialog import GeometryImportDialog
            picker = GeometryImportDialog(surface, data["name"], self)
            picker.setStyleSheet(STUDIO_STYLESHEET)
            try:
                if not picker.exec():
                    self.pending.clear()
                    self.status.setText("Preparation cancelled. No analysis was queued.")
                    return
                aligned, height = picker.result_surface
                path = store_surface(aligned)
            finally:
                picker.plotter.close()
                picker.deleteLater()
            self.hide()
            self.geometry_prepared.emit(path, height, data["name"])
        except Exception as error:
            self.pending.clear()
            self.status.setText("Design cannot be prepared: " + str(error))
            self.show()
            QMessageBox.warning(self, "Design Pool", str(error))
        finally:
            self._selection_changed()

    def open_pool(self):
        from window_state import show_restored_window
        self.refresh()
        show_restored_window(self)

    def closeEvent(self, event):
        self.pending.clear()
        if self.future is not None:
            self.future.cancel()
            self.future = None
        super().closeEvent(event)

    def dispose(self):
        self.timer.stop()
        self.refresh_timer.stop()
        self.pending.clear()
        if self.future is not None:
            self.future.cancel()
            self.future = None
        self.executor.shutdown(wait=False, cancel_futures=True)

"""PySide6 views of the same saved mechanical tables used by the Excel export."""

import csv
import io
import json
from numbers import Real

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QHeaderView, QLabel, QMessageBox, QPushButton,
    QStackedWidget, QTableView, QVBoxLayout, QWidget,
)

from mechanical_result_report import WORKBOOK_NAME, axis_label, publish_mechanical_workbook
from studio_style import CONTROL_HEIGHT, CompactComboBox
from ui_language import translate


class _ResultTableModel(QAbstractTableModel):
    def __init__(self, sheet, parent):
        super().__init__(parent)
        self.sheet = sheet

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.sheet.rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.sheet.headers)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return translate(self.sheet.headers[section])
        return super().headerData(section, orientation, role)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        value = self.sheet.rows[index.row()][index.column()]
        if role == Qt.ItemDataRole.TextAlignmentRole and isinstance(value, Real):
            return Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        if role == Qt.ItemDataRole.DisplayRole:
            if isinstance(value, float):
                return format(value, ".12g")
            return "" if value is None else str(value)
        return None


class ResultDataSheet(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        tools = QHBoxLayout()
        self.sheet = CompactComboBox()
        self.sheet.setAccessibleName("DataSheet")
        self.sheet.currentIndexChanged.connect(self._show_sheet)
        tools.addWidget(self.sheet)
        self.excel = QPushButton("Open Excel workbook")
        self.excel.setFixedHeight(CONTROL_HEIGHT)
        self.excel.setMinimumWidth(max(self.excel.fontMetrics().horizontalAdvance(label)
                                       for label in (self.excel.text(), translate(self.excel.text()))) + 20)
        self.excel.setAutoDefault(False)
        self.excel.clicked.connect(self._open_workbook)
        tools.addWidget(self.excel)
        tools.addStretch()
        layout.addLayout(tools)
        self.table = QTableView()
        self.table.setAccessibleName("DataSheet")
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(CONTROL_HEIGHT)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.setProperty("preserveItemText", True)
        layout.addWidget(self.table, 1)
        self.copy = QShortcut(QKeySequence.StandardKey.Copy, self.table)
        self.copy.activated.connect(self._copy_selection)

    def load(self, folder, sheets):
        self.folder = folder
        self.sheet.blockSignals(True)
        self.sheet.clear()
        for sheet in sheets:
            self.sheet.addItem(sheet.name, sheet)
        self.sheet.blockSignals(False)
        self._show_sheet(self.sheet.currentIndex())

    def _show_sheet(self, index):
        previous = self.table.model()
        sheet = self.sheet.itemData(index)
        self.table.setModel(_ResultTableModel(sheet, self.table) if sheet else None)
        if previous is not None:
            previous.deleteLater()

    def _copy_selection(self):
        selection = self.table.selectedIndexes()
        if not selection:
            return
        columns = sorted({index.column() for index in selection})
        rows = sorted({index.row() for index in selection})
        values = {(index.row(), index.column()): index.data() for index in selection}
        clipboard = io.StringIO()
        writer = csv.writer(clipboard, delimiter="\t", lineterminator="\n")
        writer.writerows([values.get((row, column), "") for column in columns] for row in rows)
        QApplication.clipboard().setText(clipboard.getvalue())

    def _open_workbook(self):
        path = self.folder / WORKBOOK_NAME
        if path.is_file() or publish_mechanical_workbook(self.folder) is not None:
            if QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.absolute()))):
                return
        QMessageBox.warning(self, translate("Results"),
                            translate("Cannot open saved result: {path}").format(path=path))


def _apply_carbonplot_axes(axis, x, y):
    """Use the standalone CarbonPlot axis engine when it is installed."""
    try:
        from carbonplot_native import CarbonPlotLayout, carbonplot_abi_version
    except ImportError:
        return
    if carbonplot_abi_version() != 1:
        return
    request = {"chart_type": "line_markers", "x_is_numeric": True, "nice_x_ticks": True,
               "series": [{"name": "Result", "points": [
                   {"x": x_value, "y": y_value, "col_index": index}
                   for index, (x_value, y_value) in enumerate(zip(x, y))]}]}
    plan = json.loads(CarbonPlotLayout().axis_plan_json(json.dumps(request)))
    axis.set_xlim(plan["x_domain"])
    axis.set_ylim(plan["y_domain"])


class ResultCarbonPlot(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.series = CompactComboBox()
        self.series.setAccessibleName("CarbonPlot")
        self.series.currentIndexChanged.connect(self._draw)
        controls.addWidget(self.series)
        controls.addStretch()
        layout.addLayout(controls)
        self.pages = QStackedWidget()
        message = QLabel("No saved curves for this run.")
        message.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pages.addWidget(message)
        layout.addWidget(self.pages, 1)
        self.canvas = None

    def load(self, curves):
        self.series.blockSignals(True)
        self.series.clear()
        for curve in curves:
            self.series.addItem(curve.name, curve)
        self.series.setEnabled(bool(curves))
        self.series.blockSignals(False)
        self._draw(self.series.currentIndex())

    def _create_plot(self):
        from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg, NavigationToolbar2QT
        from matplotlib.figure import Figure

        class Toolbar(NavigationToolbar2QT):
            toolitems = tuple(item for item in NavigationToolbar2QT.toolitems
                              if item[0] in ("Home", "Back", "Forward", "Pan", "Zoom"))

        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        figure = Figure(layout="constrained")
        self.canvas = FigureCanvasQTAgg(figure)
        self.axis = figure.add_subplot()
        self.toolbar = Toolbar(self.canvas, page, coordinates=True)
        layout.addWidget(self.toolbar)
        layout.addWidget(self.canvas, 1)
        self.pages.addWidget(page)

    def _draw(self, index):
        curve = self.series.itemData(index)
        if curve is None:
            self.pages.setCurrentIndex(0)
            return
        if self.canvas is None:
            self._create_plot()
        x, y = zip(*((row[curve.x], row[curve.y]) for row in curve.sheet.rows))
        self.axis.clear()
        self.axis.plot([value if isinstance(value, Real) else float("nan") for value in x],
                       [value if isinstance(value, Real) else float("nan") for value in y],
                       color="#247FA6", marker=".", linewidth=1.5)
        self.axis.set_xlabel(translate(axis_label(curve.sheet.headers[curve.x])))
        self.axis.set_ylabel(translate(axis_label(curve.sheet.headers[curve.y])))
        self.axis.grid(alpha=0.25)
        points = [(a, b) for a, b in zip(x, y) if isinstance(a, Real) and isinstance(b, Real)]
        if points:
            try:
                _apply_carbonplot_axes(self.axis, *zip(*points))
            except (ValueError, KeyError):
                self.axis.autoscale()
        self.toolbar.update()
        self.pages.setCurrentIndex(1)
        self.canvas.draw_idle()

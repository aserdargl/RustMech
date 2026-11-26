"""Language changes update presentation, preserve inputs, and never connect/solve."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtCore import QCoreApplication, QEvent, QLocale, QSettings, QTimer, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QLabel, QLineEdit, QPushButton, QWidget

from file_dialog import QFileDialog
from model import Settings
from solver_profiles import SolverProfileStore
from studio_style import CompactComboBox
from studio_style import help_tooltip
from setup_help import GEOMETRY_HELP
from ui_language import LanguageManager, LanguageSelector, translate
from window_state import WindowStateStore


class LanguageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.preferences = QSettings(str(Path(self.temp.name) / 'language.ini'), QSettings.Format.IniFormat)
        self.previous = getattr(self.app, '_rustmech_language', None)
        if self.previous is not None:
            self.app.removeEventFilter(self.previous)
            self.app.removeTranslator(self.previous.qt_translator)
        self.manager = LanguageManager(self.app, self.preferences)
        self.app._rustmech_language = self.manager
        self.windows = []
        self.previous_store = getattr(self.app, '_rustmech_window_state', None)
        if self.previous_store is not None:
            self.app.removeEventFilter(self.previous_store)
        self.store = WindowStateStore(self.app, self.preferences)
        self.app._rustmech_window_state = self.store

    def tearDown(self):
        for window in self.windows:
            window.close()
            if hasattr(window, 'dispose'):
                window.dispose()
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.removeEventFilter(self.manager)
        self.app.removeTranslator(self.manager.qt_translator)
        if self.previous is not None:
            self.app._rustmech_language = self.previous
            self.app.installTranslator(self.previous.qt_translator)
            self.app.installEventFilter(self.previous)
        else:
            del self.app._rustmech_language
        self.manager.deleteLater()
        self.app.removeEventFilter(self.store)
        if self.previous_store is not None:
            self.app._rustmech_window_state = self.previous_store
            self.app.installEventFilter(self.previous_store)
        else:
            del self.app._rustmech_window_state
        self.temp.cleanup()

    def test_live_roundtrip_preserves_identifiers_input_and_selector_sync(self):
        root = QWidget()
        self.windows.append(root)
        button = QPushButton('Run Analysis', root)
        label = QLabel('Connecting...', root)
        password = QLineEdit('MyPassword-and-file-path', root)
        password.setEchoMode(QLineEdit.EchoMode.Password)
        combo = CompactComboBox(root)
        combo.addItem('Frictionless', 'frictionless')
        first = LanguageSelector(self.manager, root)
        second = LanguageSelector(self.manager, root)
        self.manager.set_language('tr')
        self.assertEqual(button.text(), 'Analizi çalıştır')
        self.assertEqual(label.text(), 'Bağlanıyor...')
        self.assertEqual(combo.currentText(), 'Frictionless')
        self.assertEqual(combo.currentData(), 'frictionless')
        self.assertEqual(combo.itemDelegate().displayText('Frictionless', QLocale()), 'Sürtünmesiz')
        self.assertEqual(password.text(), 'MyPassword-and-file-path')
        self.assertEqual(first.combo.currentData(), 'tr')
        self.assertEqual(second.combo.currentData(), 'tr')
        self.manager.set_language('en')
        self.assertEqual(button.text(), 'Run Analysis')
        self.assertEqual(label.text(), 'Connecting...')
        self.assertEqual(first.combo.currentData(), 'en')

    def test_existing_turkish_help_and_standard_dialog_buttons_are_bilingual(self):
        label = QLabel('Geometry')
        self.windows.append(label)
        label.setToolTip(help_tooltip(GEOMETRY_HELP['size']))
        self.manager.localize(label)
        self.assertIn('Length of one edge', label.toolTip())
        self.manager.set_language('tr')
        self.assertEqual(label.text(), 'Geometri')
        self.assertIn('Küp numunenin', label.toolTip())
        self.assertEqual(QCoreApplication.translate('QDialogButtonBox', 'Cancel'), 'İptal')
        self.manager.set_language('en')
        self.assertIn('Length of one edge', label.toolTip())

    def test_folder_picker_keeps_untranslated_qt_text(self):
        owner = QWidget()
        self.windows.append(owner)
        labels = []

        def read_labels():
            picker = self.app.activeModalWidget()
            try:
                box = picker.findChild(QDialogButtonBox)
                labels.append((box.button(QDialogButtonBox.StandardButton.Open).text().replace('&', ''),
                               box.button(QDialogButtonBox.StandardButton.Cancel).text().replace('&', ''),
                               picker.findChild(QLabel, 'fileNameLabel').text().replace('&', '')))
            finally:
                picker.reject()

        for language in ('en', 'tr'):
            self.manager.set_language(language)
            QTimer.singleShot(0, read_labels)
            QFileDialog.getExistingDirectory(owner, 'Specimen folder', self.temp.name)

        self.assertEqual(labels, [('Choose', 'Cancel', 'Directory:'), ('Choose', 'İptal', 'Directory:')])

    def test_preference_is_read_by_a_fresh_process(self):
        self.manager.set_language('tr')
        script = "from PySide6.QtCore import QSettings; from PySide6.QtWidgets import QApplication; from ui_language import install_language; import sys; app=QApplication([]); print(install_language(app,QSettings(sys.argv[1],QSettings.Format.IniFormat)).language)"
        result = subprocess.run([sys.executable, '-c', script, self.preferences.fileName()],
                                cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'tr')

    def test_main_and_truba_change_language_without_changing_plan_or_starting_jobs(self):
        from nonlinear_compression import RustMechDialog
        with patch.object(RustMechDialog, 'start_preview', lambda self: None), \
                patch('nonlinear_compression.create_preview_viewport',
                      side_effect=lambda *a, **kw: MagicMock(interactor=QWidget())), \
                patch('remote_controller.saved_session', return_value=None):
            window = RustMechDialog(Settings(), solver_profile_store=SolverProfileStore(self.preferences))
        self.windows.append(window)
        before = window.collect()
        context = window.remote_controller.context()
        plan = window.doe_studio.capture_plan()
        controller = window.remote_controller
        panel = controller.resource_window
        controller.study_name.setText('sample-study')
        with patch('nonlinear_compression.open_truba_desktop') as worker:
            window.show()
            self.app.processEvents()
            mesh_size = window.controls['mesh_resolution']
            mesh_size.setLocale(QLocale(QLocale.Language.English))
            self.assertEqual(window.mesh_precision.value(), 3)
            self.assertEqual(mesh_size.text(), f'{before.mesh_resolution:.3f}')
            self.assertEqual(mesh_size.parentWidget().layout().itemAt(1).widget().text(), 'mm')
            QTest.mouseClick(window.mesh_precision.btn_up, Qt.MouseButton.LeftButton)
            self.assertEqual(mesh_size.text(), f'{before.mesh_resolution:.4f}')
            self.assertEqual(self.preferences.value('ui/meshDisplayDecimals', type=int), 4)
            normal_bounds = window.geometry()
            commands = (window.project_button, window.designs_button, window.workspace_button,
                        window.sidebar_toggle,
                        window.esen_button, window.result_history_button, window.truba_button,
                        window.truba_connect_button, window.sync_button, window.about_button)
            experiment_buttons = (window.doe_button, window.deep_learning_button)

            def assert_toolbar_layout():
                self.assertEqual(len({button.size().toTuple() for button in commands}), 1)
                self.assertEqual(window.doe_button.size(), window.deep_learning_button.size())
                self.assertEqual(window.deep_learning_button.height(), window.project_button.height())
                for button in (*commands, *experiment_buttons):
                    if button.isVisible():
                        self.assertTrue(button.parentWidget().rect().contains(button.geometry()))
                self.assertLess(window.esen_button.geometry().right(), window.about_button.x())

            assert_toolbar_layout()
            self.manager.set_language('tr')
            self.app.processEvents()
            self.assertEqual(window._mesh_form.labelForField(window.mesh_precision).text(), 'Ağ ondalık hane sayısı')
            assert_toolbar_layout()
            self.assertEqual(window.about_button.text(), 'Hakkında')
            methods = window.sidebar_toggle.menu().actions()
            self.assertEqual(
                [method.text() for method in methods],
                ['Sonlu Eleman', 'FFT homojenleştirme', 'Malzeme noktası yöntemi', 'Peridinamik'],
            )
            finite_element, fft, mpm, peridynamics = methods
            self.assertEqual(window.sidebar_toggle.text(), finite_element.text())
            original_size = window.size()
            for width in (900, 1440):
                window.resize(width, original_size.height())
                self.app.processEvents()
                toolbar_widgets = (window.brand_title_label, *commands)
                bounds = [widget.geometry() for widget in toolbar_widgets]
                for action in (fft, peridynamics, mpm, window.workspace_actions.actions()[2],
                               window.workspace_actions.actions()[3], finite_element):
                    with self.subTest(width=width, selection=action.text()):
                        action.trigger()
                        self.app.processEvents()
                        self.assertEqual([widget.geometry() for widget in toolbar_widgets], bounds)
                        self.assertLessEqual(window.brand_title_label.sizeHint().width(),
                                             window.brand_title_label.width())
            window.resize(original_size)
            self.app.processEvents()
            mpm.trigger()
            self.assertFalse(window.apply_button.isEnabled())
            with patch.object(window, '_start_peridynamic_compression') as start_peridynamics:
                peridynamics.trigger()
                self.assertEqual(window.setup_tabs.currentIndex(), peridynamics.data())
                self.assertEqual(window.sidebar_toggle.accessibleName(), peridynamics.text())
                self.assertTrue(window.apply_button.isEnabled())
                start_peridynamics.assert_not_called()
            fft.trigger()
            self.assertEqual(window.sidebar_toggle.text(), 'FFT')
            self.assertEqual(window.sidebar_toggle.accessibleName(), fft.text())
            assert_toolbar_layout()
            self.assertTrue(window.apply_button.isEnabled())
            folder = Path("Named FFT results")
            with patch.object(window, '_start_fft') as start_fft, patch(
                "nonlinear_compression.choose_analysis_results_folder", return_value=folder
            ):
                window.apply_button.click()
                start_fft.assert_called_once_with(window._fft_settings(before), folder)
            panel.show()
            self.app.processEvents()
            self.assertEqual(window.apply_button.text(), 'Analizi çalıştır')
            self.assertEqual(panel.windowTitle(), 'Hesaplama kaynakları')
            self.assertEqual(controller.resource_panel.title(), 'CPU, RAM ve kaynak oturumu')
            advanced = controller.advanced_panel
            self.assertEqual(advanced.title(), 'Gelişmiş · Uzak çalışma ortamı')
            self.assertTrue(advanced.rect().contains(controller.controls['runtime_python'].geometry()))
            window.sync_button.click()
            sync = window.sync_window
            self.assertEqual(sync.job_table.horizontalHeaderItem(2).text(), 'Analiz')
            self.assertEqual(sync.input_path.parentWidget().title(), 'Yerel girdiler')
            self.assertFalse(window.remote_controller.requested)
            worker.assert_not_called()
            self.assertEqual(window.collect(), before)
            self.assertEqual(window.remote_controller.context(), context)
            self.assertEqual(window.geometry(), normal_bounds)
            self.assertEqual(window.doe_studio.capture_plan(), plan)
            self.manager.set_language('en')
            self.assertEqual(window.sidebar_toggle.text(), 'FFT')
            finite_element.trigger()
            self.assertEqual(window.sidebar_toggle.text(), 'Finite Element')
            self.assertEqual(panel.windowTitle(), 'Compute resources')
            self.assertEqual(sync.input_path.parentWidget().title(), 'Local inputs')
            self.assertEqual(controller.study_name.text(), 'sample-study')

    def test_dynamic_translations_preserve_values_and_paths(self):
        self.manager.set_language('tr')
        self.assertEqual(translate('12 designs'), '12 tasarım')
        self.assertEqual(translate('2 reference configurations · 25 experiments'),
                         '2 referans yapılandırma · 25 deney')
        self.assertEqual(translate('Analysis completed. Results: D:/My Folder/test-42'),
                         'Analiz tamamlandı. Sonuçlar: D:/My Folder/test-42')
        self.assertEqual(translate('HEX8'), 'HEX8')
        self.assertEqual(translate('Kratos Multiphysics'), 'Kratos Multiphysics')

    def test_existing_plot_labels_switch_without_rebuilding_results(self):
        from types import SimpleNamespace
        from vtkmodules.vtkRenderingAnnotation import vtkScalarBarActor
        from matplotlib.figure import Figure
        actor = vtkScalarBarActor()
        actor.SetTitle('Temperature / °C')
        render = MagicMock()
        plotter = SimpleNamespace(scalar_bars={'temperature': actor}, render=render)
        figure = Figure()
        axis = figure.add_subplot(111)
        axis.plot([0, 1], [20, 30], label='Water mean')
        axis.set_xlabel('Time / s')
        axis.set_ylabel('Temperature / °C')
        axis.legend()
        self.manager.set_language('tr')
        self.manager.localize_plotter(plotter)
        self.manager.localize_figure(figure)
        self.assertEqual(actor.GetTitle(), 'Sıcaklık / °C')
        self.assertEqual(axis.get_xlabel(), 'Zaman / s')
        self.assertEqual(axis.get_legend().get_texts()[0].get_text(), 'Ortalama su sıcaklığı')
        self.assertEqual(list(axis.lines[0].get_ydata()), [20, 30])
        self.manager.set_language('en')
        self.manager.localize_plotter(plotter)
        self.manager.localize_figure(figure)
        self.assertEqual(actor.GetTitle(), 'Temperature / °C')
        self.assertEqual(axis.get_xlabel(), 'Time / s')
        self.assertEqual(render.call_count, 2)

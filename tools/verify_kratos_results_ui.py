"""Check real downloaded-format fields, exports and native window persistence."""
import json
import argparse
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from kratos_backend import KratosResults
from kratos_results_view import KratosResultsDialog
from nonlinear_compression import extract_curve, export_graphs, export_field_results
from model import settings_from_dict
from tools.verify_kratos_pipeline import cube_mesh
from window_state import WindowStateStore
from types import SimpleNamespace
import pyvista as pv

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--report',type=Path)
args=parser.parse_args()
if args.report:
    folder=args.report
    saved=json.loads((folder/'summary.json').read_text())
    raw=Path(saved['solver_diagnostics']['local_directory'])
    mesh=SimpleNamespace(grid=pv.read(raw/'submitted_mesh.vtu'))
    reader=KratosResults(raw/'results',mesh)
    graphs,fields,reactions=(saved[key] for key in ('graphs','fields','reactions'))
else:
    raw=root/'build/kratos-cases/elastic'
    folder=root/'build/kratos-ui-check'
    folder.mkdir(exist_ok=True)
    for name in ('03_Results','04_Graphs'):(folder/name).mkdir(exist_ok=True)
    settings=settings_from_dict(json.loads((raw/'input.json').read_text())['settings'])
    mesh=cube_mesh()
    reader=KratosResults(raw/'results',mesh)
    history,reactions=extract_curve(reader)
    graphs=export_graphs(history,settings,folder/'04_Graphs',mesh=mesh,images=False)
    fields=export_field_results(reader,mesh,folder/'03_Results',images=False)
    (folder/'summary.json').write_text(json.dumps({'solver_diagnostics':{'local_directory':str(raw)},'graphs':graphs,'fields':fields}))
app=QApplication.instance() or QApplication([])
with TemporaryDirectory() as temp:
    store=WindowStateStore(app,QSettings(str(Path(temp)/'ui.ini'),QSettings.Format.IniFormat))
    first=KratosResultsDialog(folder)
    first.show();app.processEvents()
    first.resize(910,620);first.move(35,45);app.processEvents()
    normal=first.geometry()
    first.field.setCurrentIndex(1);first.scale.setValue(2.)
    QTest.qWait(500)
    first.plotter.screenshot(str(folder/'result-field.png'))
    if first.reader.has_self_contact:
        first.field.setCurrentIndex(2)
        app.processEvents();QTest.qWait(100)
        assert first.reader.contact_pressure(first.reader.nsets-1,first.mesh.point_data['ansys_node_num']).max()>0
        assert 'Cannot display' not in first.status.text()
        first.plotter.screenshot(str(folder/'self-contact-pressure.png'))
        first.field.setCurrentIndex(3);app.processEvents()
        assert 'Cannot display' not in first.status.text()
        first.field.setCurrentIndex(2)
    first.grab().save(str(folder/'viewer.png'))
    first.close();app.processEvents()
    second=KratosResultsDialog(folder)
    second.show();app.processEvents()
    assert second.geometry()==normal,(second.geometry(),normal)
    second.showMaximized();app.processEvents();second.close();app.processEvents()
    third=KratosResultsDialog(folder)
    third.show();app.processEvents()
    assert third.isMaximized()
    third.showNormal();app.processEvents()
    assert third.size()==normal.size()
    third.close();app.processEvents()
assert graphs['completed']
assert not reactions['force_balance_warning']
report={'passed':True,'steps':reader.nsets,'exports':fields,'window_geometry_restored':True,'maximized_state_restored':True}
(folder/('viewer-verification.json' if args.report else 'verification.json')).write_text(json.dumps(report,indent=2))
print(json.dumps(report))

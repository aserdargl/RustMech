"""Real OpenCFD fitted meshes: duct reference, insert, layer reporting and y+."""
import argparse
from dataclasses import replace
import json
import math
from pathlib import Path
import sys
from uuid import uuid4

import pyvista as pv

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from model import ROOT,Settings
from flow_analysis import FlowSettings,execute_flow_case


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root",type=Path,default=ROOT/"build")
    args = parser.parse_args()
    root = args.output_root.resolve()/("fitted-validation-"+uuid4().hex[:8])
    root.mkdir(parents=True)
    spec = Settings(size=2,cell_size=1,compression=.1)
    flow = FlowSettings(mesh_method="snappy",channel_shape="rectangular",diameter_mm=2,width_mm=2,height_mm=2,
                        cells_across=8,inlet_length_mm=3,outlet_length_mm=3,empty_pipe=True,inlet_speed=.001,iterations=1000)
    report = {"cases":{},"checks":{},"scope":"Laminar duct reference and mesh/SST output smoke checks; not experimental or turbulence-model validation."}
    correction = 1-192/math.pi**5*sum(math.tanh(n*math.pi/2)/n**5 for n in range(1,400,2))
    errors = []
    for count in (8,16):
        case = f"square{count}"
        summary = execute_flow_case(spec,replace(flow,cells_across=count),root/case)
        length = (summary["specimen_pressure_planes_mm"][1]-summary["specimen_pressure_planes_mm"][0])*.001
        expected = 12*flow.dynamic_viscosity*flow.inlet_speed*length/(.002**2*correction)
        error = abs(summary["pressure_drop_specimen_Pa"]/expected-1)
        errors.append(error)
        report["cases"][case] = {"summary":summary,"analytical_pressure_Pa":expected,"relative_error":error}
        report["checks"][case+"_converged"] = summary["status"]=="converged"
        report["checks"][case+"_mesh_ok"] = summary["mesh"]["mesh_check"]=="Mesh OK"
        print(f"{case}: pressure error={error:.6g}, cells={summary['mesh']['fluid_cells']}",flush=True)
    report["checks"]["square_refinement_improves"] = errors[1]<errors[0]
    report["checks"]["square_fine_error_below_5_percent"] = errors[1]<.05
    cylinder = execute_flow_case(spec,replace(flow,channel_shape="cylinder"),root/"cylinder")
    length = (cylinder["specimen_pressure_planes_mm"][1]-cylinder["specimen_pressure_planes_mm"][0])*.001
    expected = 32*flow.dynamic_viscosity*flow.inlet_speed*length/.002**2
    error = abs(cylinder["pressure_drop_specimen_Pa"]/expected-1)
    report["cases"]["cylinder"] = {"summary":cylinder,"relative_error":error}
    report["checks"]["cylinder_converged"] = cylinder["status"]=="converged"
    report["checks"]["cylinder_error_below_5_percent"] = error<.05
    print(f"cylinder: pressure error={error:.6g}",flush=True)
    surface = root/"insert.vtp"
    pv.Box(bounds=(.75,1.25,.75,1.25,0,2)).triangulate().save(surface)
    imported = replace(spec,imported_geometry_path=str(surface),imported_geometry_role="general")
    insert = execute_flow_case(imported,replace(flow,empty_pipe=False),root/"insert")
    report["cases"]["insert"] = {"summary":insert}
    report["checks"]["insert_converged"] = insert["status"]=="converged"
    report["checks"]["insert_adds_pressure_loss"] = insert["pressure_drop_specimen_Pa"]>report["cases"]["square8"]["summary"]["pressure_drop_specimen_Pa"]
    report["checks"]["insert_refinement_verified"] = insert["mesh"]["surface_boundary_min_refinement"]>=flow.surface_refinement
    report["checks"]["actual_insert_layer_coverage_reported"] = "insert" in insert["mesh"]["layers"]["patches"]
    print("insert: "+insert["status"],flush=True)
    sst = execute_flow_case(spec,replace(flow,turbulence="kOmegaSST",inlet_speed=.01),root/"sst-smoke")
    report["cases"]["sst-smoke"] = {"summary":sst}
    report["checks"]["sst_converged"] = sst["status"]=="converged"
    report["checks"]["y_plus_reported"] = bool(sst["y_plus"].get("walls"))
    report["passed"] = all(report["checks"].values())
    output = root/"verification.json"
    output.write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
    print(json.dumps({"passed":report["passed"],"checks":report["checks"],"report":str(output)},indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__": raise SystemExit(main())

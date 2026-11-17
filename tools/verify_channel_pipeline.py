"""Real square-duct reference and non-TPMS CHT smoke verification in WSL."""
from dataclasses import replace
import json
import math
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flow_analysis import FlowSettings, execute_flow_case
from model import ROOT, Settings
from thermal_analysis import ThermalSettings, execute_thermal_case


def main():
    root = ROOT/"build"/("channel-validation-"+uuid4().hex[:8])
    root.mkdir(parents=True)
    spec = Settings(size=2, cell_size=1, compression=.1)
    base = FlowSettings(channel_shape="rectangular", width_mm=2, height_mm=2,
                        diameter_mm=2, inlet_speed=.001, inlet_length_mm=3,
                        outlet_length_mm=3, iterations=1000, empty_pipe=True)
    report = {"cases": {}, "checks": {}}
    if "--imported" in sys.argv:
        import pyvista as pv
        from imported_geometry import prepare_general_surface
        surface, size = prepare_general_surface(pv.Box(bounds=(.75,1.25,.75,1.25,0,2)).triangulate())
        path = root/"imported.vtp"
        surface.save(path)
        settings = replace(spec,size=size,imported_geometry_path=str(path),imported_geometry_role="general")
        flow = replace(base,empty_pipe=False,cells_across=12,inlet_length_mm=1,outlet_length_mm=1)
        cfd = execute_flow_case(settings,flow,root/"imported-flow")
        print(f"imported flow: {cfd['status']}",flush=True)
        thermal = ThermalSettings(inlet_temperature_c=40,initial_water_c=40,initial_solid_c=40,
                                   duration_s=.05,time_step_s=.01,output_interval_s=.05)
        cht = execute_thermal_case(settings,flow,thermal,root/"imported-cht")
        error = max(abs(row[key]-40) for row in cht["history"] for key in
                    ("water_min_C","water_max_C","tpms_min_C","tpms_max_C"))
        report.update(cases={"flow":cfd,"cht":cht},checks={"flow_converged":cfd["status"]=="converged",
                      "cht_completed":cht["status"]=="completed","equilibrium_preserved":error<.001})
        report["passed"] = all(report["checks"].values())
        output = root/"verification.json"
        output.write_text(json.dumps(report,indent=2,allow_nan=False),encoding="utf-8")
        print(json.dumps({"passed":report["passed"],"checks":report["checks"],"equilibrium_max_error_C":error,"report":str(output)},indent=2))
        return 0 if report["passed"] else 1
    errors = []
    # Fully developed rectangular-duct Fourier series (square in these cases).
    correction = 1-192/math.pi**5*sum(math.tanh(n*math.pi/2)/n**5 for n in range(1,400,2))
    for count in (8,16):
        summary = execute_flow_case(spec, replace(base, cells_across=count), root/f"square{count}")
        length = (summary["specimen_pressure_planes_mm"][1]-summary["specimen_pressure_planes_mm"][0])*.001
        expected = 12*base.dynamic_viscosity*base.inlet_speed*length/(.002**2*correction)
        error = abs(summary["pressure_drop_specimen_Pa"]/expected-1)
        errors.append(error)
        report["cases"][f"square{count}"] = {"summary":summary, "analytical_pressure_Pa":expected, "relative_error":error}
        print(f"square{count}: error={error:.6g}, status={summary['status']}", flush=True)
    report["checks"].update(square_refinement_improves=errors[1]<errors[0], square_pressure_within_5_percent=errors[1]<.05)
    thermal = ThermalSettings(inlet_temperature_c=40, initial_water_c=40, initial_solid_c=40,
                              duration_s=.05, time_step_s=.01, output_interval_s=.05)
    for family, name, shape in (("lattice","BCC","rectangular"), ("porous","Gaussian Random Field","cylinder")):
        case = f"{family}_{shape}"
        settings = replace(spec, structure_family=family, structure_name=name, solid_fraction=15)
        flow = replace(base, channel_shape=shape, empty_pipe=False, cells_across=12,
                       inlet_length_mm=1, outlet_length_mm=1)
        summary = execute_thermal_case(settings, flow, thermal, root/case)
        error = max(abs(row[key]-40) for row in summary["history"] for key in
                    ("water_min_C", "water_max_C", "tpms_min_C", "tpms_max_C"))
        report["cases"][case] = {"summary": summary, "equilibrium_max_error_C": error}
        report["checks"][case+"_equilibrium"] = error < .001
        report["checks"][case+"_completed"] = summary["status"] == "completed"
        print(f"{case}: equilibrium error={error:.6g}, status={summary['status']}", flush=True)
    report["checks"]["all_square_flows_converged"] = all(report["cases"][f"square{n}"]["summary"]["status"]=="converged" for n in (8,16))
    report["passed"] = all(report["checks"].values())
    path = root/"verification.json"
    path.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"passed":report["passed"],"checks":report["checks"],"report":str(path)}, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Run real OpenFOAM cases and check laminar pipe physics and TPMS pressure loss.

Uses WSL OpenCFD simpleFoam; no licensed mechanical solver is launched.
"""
from dataclasses import replace
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flow_analysis import FlowSettings, execute_flow_case
from model import ROOT, Settings


def main():
    directory = ROOT/"build"/("flow-validation-"+uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    specimen = Settings()
    flow = FlowSettings(inlet_speed=.001,inlet_length_mm=30,outlet_length_mm=30,
                        cells_across=12,iterations=800,empty_pipe=True)
    report = {"cases":{},"checks":{},"directory":str(directory)}
    errors = []
    for n in (12,24):
        summary = execute_flow_case(specimen,replace(flow,cells_across=n),directory/f"empty{n}")
        length = (summary["specimen_pressure_planes_mm"][1]-summary["specimen_pressure_planes_mm"][0])*.001
        expected = 32*flow.dynamic_viscosity*flow.inlet_speed*length/(flow.diameter_mm*.001)**2
        error = abs(summary["pressure_drop_specimen_Pa"]/expected-1)
        errors.append(error)
        report["cases"][f"empty{n}"] = dict(summary=summary,poiseuille_section_Pa=expected,relative_error=error)
    report["checks"]["empty_pipe_refinement_improves_pressure"] = errors[1] < errors[0]
    report["checks"]["empty_pipe_pressure_within_10_percent"] = errors[1] < .10
    report["checks"]["empty_pipe_velocity_is_parabolic"] = 1.8 < report["cases"]["empty24"]["summary"]["maximum_speed_m_s"]/flow.inlet_speed < 2.1
    tpms = execute_flow_case(specimen,replace(flow,cells_across=24,empty_pipe=False),directory/"tpms24")
    report["cases"]["tpms24"] = tpms
    report["checks"]["tpms_increases_pressure_loss"] = tpms["pressure_drop_specimen_Pa"] > report["cases"]["empty24"]["summary"]["pressure_drop_specimen_Pa"]
    summaries = [report["cases"][f"empty{n}"]["summary"] for n in (12,24)]+[tpms]
    report["checks"]["all_solutions_converged"] = all(s["status"]=="converged" for s in summaries)
    report["checks"]["mass_imbalance_below_0_1_percent"] = all(s["mass_imbalance_fraction"]<.001 for s in summaries)
    report["passed"] = all(report["checks"].values())
    (directory/"verification.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"passed":report["passed"],"checks":report["checks"],"pipe_errors":errors,"report":str(directory/"verification.json")},indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

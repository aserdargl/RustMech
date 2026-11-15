"""Real OpenFOAM transient CHT verification: equilibrium, heating and time refinement."""
from dataclasses import replace
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from flow_analysis import FlowSettings
from model import ROOT,Settings
from thermal_analysis import ThermalSettings,execute_thermal_case


def main():
    directory = ROOT/"build"/("thermal-validation-"+uuid.uuid4().hex[:8])
    directory.mkdir(parents=True)
    flow = FlowSettings(cells_across=12,inlet_length_mm=1,outlet_length_mm=1)
    thermal = ThermalSettings(duration_s=.5,time_step_s=.01,output_interval_s=.05)
    results = {}
    for name,settings in (("equilibrium",replace(thermal,inlet_temperature_c=40,initial_water_c=40,initial_solid_c=40,duration_s=.1)),
                          ("heating",thermal),("half_time_step",replace(thermal,time_step_s=.005))):
        results[name] = execute_thermal_case(Settings(),flow,settings,directory/name)
    equilibrium = results["equilibrium"]["history"]
    heating = results["heating"]["history"]
    fine = results["half_time_step"]["history"][-1]
    last = heating[-1]
    time_error = abs(last["tpms_mean_C"]-fine["tpms_mean_C"])/abs(fine["tpms_mean_C"]-20)
    equilibrium_error = max(abs(r[key]-40) for r in equilibrium for key in ("water_min_C","water_max_C","tpms_min_C","tpms_max_C"))
    checks = dict(equilibrium_preserved=equilibrium_error<.001,
                  water_and_solid_both_warm=last["water_mean_C"]>20.5 and last["tpms_mean_C"]>20.5,
                  outlet_between_initial_and_inlet=20<last["outlet_bulk_C"]<80,
                  temperature_bounds_preserved=all(19.999<r[key]<80.001 for r in heating for key in ("water_min_C","water_max_C","tpms_min_C","tpms_max_C")),
                  time_refinement_solid_heating_within_5_percent=time_error<.05,
                  interface_heat_balance=all(r.get("maximum_interface_relative_heat_mismatch",0)<.01 for r in results.values()),
                  interface_temperature_continuity=all(r.get("maximum_interface_temperature_jump_K",0)<.1 for r in results.values()),
                  saved_initial_and_final_times=all(r["frames"][0]["time_s"]==0 and abs(r["frames"][-1]["time_s"]-r["duration_s"])<1e-8 for r in results.values()))
    report = dict(passed=all(checks.values()),checks=checks,equilibrium_max_error_C=equilibrium_error,
                  solid_heating_time_refinement_relative_change=time_error,results=results)
    (directory/"verification.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"passed":report["passed"],"checks":checks,"equilibrium_max_error_C":equilibrium_error,"time_refinement_change":time_error,"report":str(directory/"verification.json")},indent=2))
    return 0 if report["passed"] else 1


if __name__=="__main__": raise SystemExit(main())

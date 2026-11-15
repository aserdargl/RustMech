"""Verify cancellation kills the owned Linux solver group as well as its Windows worker."""
from pathlib import Path
import json
import sys
import time
import uuid

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from analysis_process import AnalysisProcess
from flow_analysis import FlowSettings,execute_flow_case,stop_flow_case,_wsl
from model import ROOT,Settings


def main():
    thermal = "--thermal" in sys.argv
    directory = ROOT/"build"/(("thermal-cancel-" if thermal else "flow-cancel-")+uuid.uuid4().hex[:8])
    flow = FlowSettings(cells_across=16,iterations=20000,turbulence="kOmegaSST",inlet_speed=.1)
    log_name = "log.simpleFoam"
    if thermal:
        from thermal_analysis import ThermalSettings,execute_thermal_case
        flow = FlowSettings(cells_across=12)
        job = AnalysisProcess(execute_thermal_case,Settings(),flow,ThermalSettings(),str(directory))
        log_name = "log.chtMultiRegionFoam"
    else:
        job = AnalysisProcess(execute_flow_case,Settings(),flow,str(directory))
    start = time.monotonic()
    pid = None
    try:
        while time.monotonic()-start<45:
            if (directory/log_name).exists() and (directory/".runner.pid").exists():
                pid = int((directory/".runner.pid").read_text().strip())
                break
            if job.done(): raise RuntimeError("Flow job ended before cancellation could be exercised.")
            time.sleep(.1)
        if pid is None: raise RuntimeError("Solver did not start in time.")
        stop_flow_case(flow,directory)
        job.force_stop()
        for _ in range(20):
            result = _wsl(flow,f"kill -0 -- -{pid}",capture_output=True,timeout=10)
            if result.returncode: break
            time.sleep(.1)
        passed = result.returncode!=0
        report = dict(passed=passed,linux_group=pid,case=str(directory),cancel_request=(directory/".cancel-request").exists())
        (directory/"verification.json").write_text(json.dumps(report,indent=2))
        print(json.dumps(report,indent=2))
        return 0 if passed else 1
    except BaseException:
        stop_flow_case(flow,directory)
        try: job.force_stop()
        except (ValueError,AttributeError): pass
        raise


if __name__ == "__main__":
    import multiprocessing
    multiprocessing.freeze_support()
    raise SystemExit(main())

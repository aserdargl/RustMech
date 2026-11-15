"""RustMech test grubu calistiricisi.

Kullanim:  python tools/run_tests.py [core|gui|all] [--timeout SANIYE] [--python YOL]

core: ekran/OpenGL gerektirmeyen testler (Qt 'offscreen' modunda calisir).
gui : VTK/OpenGL penceresi acan testler; gercek bir masaustu oturumu gerekir.
Her test dosyasi ayri surecte, sure sinirlariyla calisir; cikis kodu 0 yalniz
tum dosyalar basariliysa. RUSTMECH_OFFLINE=1 ag/loopback testlerini atlar.
Not: gui listesi 2026-11-15'te 'offscreen' modda VTK pixel-format hatasiyla
dusen dosyalardan turetilmistir; listede olmayan yeni dosyalar core sayilir.
"""
import argparse, os, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUI = {
    "test_dialog_keyboard", "test_flow_workspace", "test_forced_stop_ui",
    "test_geometry_import_ui", "test_loading_contact", "test_standard_tests",
    "test_thermal_workspace", "test_viewport_orientation", "test_remote_mode_ui",
    "test_doe_window",
}


def select(group):
    names = sorted(p.stem for p in (ROOT / "tests").glob("test_*.py"))
    if group == "gui":
        return [n for n in names if n in GUI]
    if group == "core":
        return [n for n in names if n not in GUI]
    return names


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("group", nargs="?", default="core", choices=["core", "gui", "all"])
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--python", default=sys.executable)
    a = ap.parse_args()
    failed = 0
    for name in select(a.group):
        env = dict(os.environ, RUSTMECH_OFFLINE=os.environ.get("RUSTMECH_OFFLINE", "1"))
        if name in GUI:
            env.pop("QT_QPA_PLATFORM", None)
        else:
            env.setdefault("QT_QPA_PLATFORM", "offscreen")
        t0 = time.time()
        try:
            r = subprocess.run([a.python, "-m", "unittest", f"tests.{name}"], cwd=ROOT, env=env,
                               capture_output=True, text=True, timeout=a.timeout)
            tail = [l for l in r.stderr.splitlines() if l.startswith(("Ran ", "OK", "FAILED"))]
            ok = r.returncode == 0
            state = "OK" if ok else "FAIL"
            info = " | ".join(tail) or "ozet yok (surec beklenmedik bicimde bitti)"
        except subprocess.TimeoutExpired:
            ok, state, info = False, "TIMEOUT", f">{a.timeout}s"
        failed += not ok
        print(f"{state:7} {name:34} {time.time() - t0:6.1f}s  {info}", flush=True)
    print(f"\n{'TUMU BASARILI' if not failed else str(failed) + ' dosya basarisiz'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

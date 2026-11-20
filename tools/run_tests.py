"""RustMech test grubu calistiricisi.

Kullanim:  python tools/run_tests.py [core|gui|all] [--timeout SANIYE] [--python YOL]
           python tools/run_tests.py core -k test_ui_language   # tek dosya

core: ekran/OpenGL gerektirmeyen testler (Qt 'offscreen' modunda calisir).
gui : VTK/OpenGL penceresi acan veya gercek konsol isteyen testler; gercek bir
      masaustu oturumu gerekir.
Her test dosyasi ayri surecte pytest ile calisir (unittest ve pytest stili
testlerin ikisi de toplanir). Hic test toplanmayan dosya basarisiz sayilir.
Cikis kodu 0 yalniz tum dosyalar basariliysa. RUSTMECH_OFFLINE=1 ag/loopback
testlerini atlar.
Not: gui listesi 2026-11-15'te 'offscreen' modda VTK pixel-format hatasiyla
dusen dosyalardan turetilmistir; listede olmayan yeni dosyalar core sayilir.
"""
import argparse, os, re, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GUI = {
    "test_dialog_keyboard", "test_flow_workspace", "test_forced_stop_ui",
    "test_geometry_import_ui", "test_loading_contact", "test_standard_tests",
    "test_thermal_workspace", "test_viewport_orientation", "test_remote_mode_ui",
    "test_doe_window", "test_doe_sample_settings", "test_design_pool",
    "test_viewport_lifecycle",
    # Yuz secici gercek QtInteractor acar; offscreen'de VTK aralikli coker.
    "test_folder_batch",
    # Konsola gercek Ctrl+C gonderir; konsolu olmayan surecte takilir.
    "test_desktop_interrupt",
}
# Varsayilan offscreen ekrani 800x800; pencere geri yukleme testleri ekrana
# sigmayan pencereleri kirpar. 1920x1080 tanimli ekran kullan. Qt secenekleri
# ':' ile ayirdigindan yol, ROOT'a gore goreli olmali (surucu harfi olmamali).
OFFSCREEN = "offscreen:configfile=tests/offscreen_screen.json"
# pytest cikis kodlari: 0 basarili, 5 hic test toplanmadi.
NO_TESTS = 5
SUMMARY = re.compile(r"\b\d+ (passed|failed|error|errors|skipped|deselected)\b")


def select(group, only=None):
    names = sorted(p.stem for p in (ROOT / "tests").glob("test_*.py"))
    if only:
        names = [n for n in names if only in n]
    if group == "gui":
        return [n for n in names if n in GUI]
    if group == "core":
        return [n for n in names if n not in GUI]
    return names


def summary(output):
    lines = [l.strip(" =") for l in output.splitlines() if SUMMARY.search(l)]
    return lines[-1] if lines else ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("group", nargs="?", default="core", choices=["core", "gui", "all"])
    ap.add_argument("--timeout", type=int, default=240)
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("-k", dest="only", help="yalniz adinda bu metin gecen dosyalar")
    a = ap.parse_args()
    failed = 0
    with tempfile.TemporaryDirectory(prefix="rustmech-tests-") as scratch:
        for name in select(a.group, a.only):
            env = dict(os.environ, RUSTMECH_OFFLINE=os.environ.get("RUSTMECH_OFFLINE", "1"))
            if name in GUI:
                env.pop("QT_QPA_PLATFORM", None)
            else:
                env.setdefault("QT_QPA_PLATFORM", OFFSCREEN)
            command = [a.python, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                       "--basetemp", str(Path(scratch) / name), f"tests/{name}.py"]
            t0 = time.time()
            try:
                r = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                   text=True, timeout=a.timeout)
                ok = r.returncode == 0
                info = summary(r.stdout + r.stderr)
                if r.returncode == NO_TESTS:
                    state, info = "FAIL", "hic test toplanmadi"
                else:
                    state = "OK" if ok else "FAIL"
                    info = info or "ozet yok (surec beklenmedik bicimde bitti)"
            except subprocess.TimeoutExpired:
                ok, state, info = False, "TIMEOUT", f">{a.timeout}s"
            failed += not ok
            print(f"{state:7} {name:34} {time.time() - t0:6.1f}s  {info}", flush=True)
    print(f"\n{'TUMU BASARILI' if not failed else str(failed) + ' dosya basarisiz'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

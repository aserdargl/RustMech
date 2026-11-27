"""Generate a LAMMPS input for independent tests on one equilibrated BCC cell."""

from dataclasses import asdict, dataclass
import json
from math import isfinite
import re

from esen_md import PLAN_METADATA, read_plan
from esen_md_protocol import CNA_CUTOFF


ENTRY = "in.md-tests"
MANIFEST = "tests.json"
TESTS = {
    "tension": "Tensile Test",
    "compression": "Compression Test",
    "shear": "Simple Shear Test",
    "elastic": "Elastic Constants",
    "relaxation": "Stress Relaxation",
    "creep": "Creep Test",
    "notch": "Notched Tensile Test",
}
SAMPLE = 100
INPUTS = (PLAN_METADATA, MANIFEST, "structure.lmp", "meam_kutuphane.meam", "meam_parametreler.meam")


@dataclass(frozen=True)
class TestSelection:
    tests: tuple[str, ...] = ("tension",)
    strain_percent: float = 50
    rate_per_second: float = 1e10
    relaxation_percent: float = 2
    creep_gpa: float = 1
    hold_ps: float = 50

    def validate(self):
        if not self.tests:
            raise ValueError("Select at least one MD test.")
        if len(set(self.tests)) != len(self.tests) or any(test not in TESTS for test in self.tests):
            raise ValueError("Invalid MD test selection.")
        for value, low, high in ((self.strain_percent, 0.1, 50), (self.rate_per_second, 1e8, 1e11),
                                 (self.relaxation_percent, 0.1, 20), (self.creep_gpa, 0.01, 100),
                                 (self.hold_ps, 1, 1000)):
            if not isfinite(value) or not low <= value <= high:
                raise ValueError("Invalid MD test settings.")


def _forces(package):
    """Reuse the validated map and neighbor skin from an E20 preparation."""
    with (package / "analiz.inc").open(encoding="utf-8") as source:
        contents = source.read(65_537)
    pair = re.search(r"^pair_coeff \* \* meam_kutuphane\.meam(?: [A-Za-z][A-Za-z0-9_+\-]*)+"
                     r" meam_parametreler\.meam(?: [A-Za-z][A-Za-z0-9_+\-]*)+$", contents, re.MULTILINE)
    skin = re.search(r"^neighbor [0-9]+(?:\.[0-9]+)? bin$", contents, re.MULTILINE)
    if pair is None or skin is None:
        raise ValueError("Prepare the MD package again to use the selected tests.")
    return f"pair_style meam\n{pair[0]}\n{skin[0]}\ntimestep 0.001\nthermo 1000\n"


def _start_test(name, forces):
    return f'''clear
print "ESEN_STEP={name}"
read_restart equilibrated.restart
{forces}reset_timestep 0
'''


def _sample_curve(name, strain, *, duration, stress_scale="1"):
    return f'''variable strain equal {strain}
variable elapsed equal step*dt
variable sx equal -pxx/10000*{stress_scale}
variable sy equal -pyy/10000*{stress_scale}
variable sz equal -pzz/10000*{stress_scale}
variable sxy equal -pxy/10000*{stress_scale}
variable tk equal temp
fix curve all print {SAMPLE} "${{elapsed}},${{strain}},${{sx}},${{sy}},${{sz}},${{sxy}},${{tk}}" &
    file {name}.csv screen no title "time_ps,strain,stress_x_GPa,stress_y_GPa,stress_z_GPa,stress_xy_GPa,temperature_K"
thermo_style custom step v_elapsed v_strain v_sx v_sy v_sz v_sxy temp press
run {duration}
'''


def _deformation(test, settings, elements, temperature):
    shear = test == "shear"
    notch = test == "notch"
    relaxation = test == "relaxation"
    target = (settings.relaxation_percent if relaxation else settings.strain_percent) / 100
    if test == "compression":
        target = -target
    steps = max(SAMPLE, round(abs(target) / (settings.rate_per_second * 1e-15 * SAMPLE)) * SAMPLE)
    t_control = f"temp {temperature} {temperature} 0.5"
    prep = ""
    if shear:
        prep = "change_box all triclinic\n"
        reference = "variable ref equal $(ly)\nvariable offset equal $(xy)\n"
        integration = f"nvt {t_control}"
        axis, strain = "xy", "(xy-v_offset)/v_ref"
    else:
        reference = "variable ref equal $(lx)\n"
        integration = f"npt {t_control} y 0 0 1 z 0 0 1 drag 1"
        axis, strain = "x", "(lx-v_ref)/v_ref"
    if notch:
        # Edge notch through Z, with free Y surfaces and periodic X and Z.
        prep = f'''variable width equal $(ly)
variable left equal $(xlo+0.45*lx)
variable right equal $(xlo+0.55*lx)
variable tip equal $(ylo+0.25*ly)
change_box all y delta -10 10 boundary p m p units box
variable before equal $(atoms)
region slit block ${{left}} ${{right}} INF ${{tip}} INF INF units box
delete_atoms region slit
if "$(atoms) == ${{before}} || $(atoms) == 0" then "print 'The notch requires a larger cell.'" "quit 1"
fix surface all nvt {t_control}
min_style cg
minimize 1e-10 1e-10 2000 4000
run 10000
unfix surface
reset_timestep 0
'''
        integration = f"npt {t_control} z 0 0 1 drag 1"
    rate = target / (0.001 * steps)
    script = prep + reference + f'''fix integrate all {integration}
fix deform_box all deform 1 {axis} erate {rate:.16g} units box remap x flip no
'''
    if not relaxation:
        script += f'''compute cna all cna/atom {CNA_CUTOFF}
compute csym all centro/atom bcc
dump trajectory all custom 250 {test}.lammpstrj id type element xs ys zs c_cna c_csym
dump_modify trajectory sort id element {" ".join(elements)}
'''
    script += _sample_curve(test, strain, duration=steps, stress_scale="(ly/v_width)" if notch else "1")
    if relaxation:
        hold = round(settings.hold_ps * 1000 / SAMPLE) * SAMPLE
        script += f"unfix deform_box\nunfix integrate\nfix hold all nvt {t_control}\nrun {hold}\n"
    return script


def _creep(settings, temperature):
    pressure = -settings.creep_gpa * 10_000  # metal units use bar and positive compression
    hold = round(settings.hold_ps * 1000 / SAMPLE) * SAMPLE
    control = f"npt temp {temperature} {temperature} 0.5 y 0 0 1 z 0 0 1"
    return (f"variable ref equal $(lx)\nfix load all {control} x 0 {pressure:.12g} 1\n"
            + _sample_curve("creep", "(lx-v_ref)/v_ref", duration=5000)
            + f"unfix load\nfix hold all {control} x {pressure:.12g} {pressure:.12g} 1\nrun {hold}\n")


def _elastic(forces, temperature):
    # Voigt order uses engineering shear (tilt / perpendicular box length).
    axes = ("x", "y", "z", "yz", "xz", "xy")
    pressures = ("pxx", "pyy", "pzz", "pyz", "pxz", "pxy")
    delta = 0.005
    columns = []
    for column, axis in enumerate(axes, 1):
        for tag, strain in (("minus", -delta), ("plus", delta)):
            sample = f"{axis} {tag}"
            if len(axis) == 1:
                deformation = f"{axis} scale {1 + strain}"
            else:
                deformation = f"triclinic {axis} delta $({strain}*l{axis[-1]})"
            columns.append(_start_test(f"Elastic: {sample}", forces) + f'''change_box all {deformation} remap units box
fix integrate all nvt temp {temperature} {temperature} 0.5
run 10000
''')
            columns.extend(f"variable sigma{i} equal {-1 / 10000:.4f}*{pressure}\n"
                           for i, pressure in enumerate(pressures, 1))
            columns.append("fix averaged all ave/time 100 100 10000 "
                           + " ".join(f"v_sigma{i}" for i in range(1, 7)) + " ave running\nrun 20000\n")
            # Capture the two block averages before clear discards the fix.
            columns.extend(f"variable {tag}{i} equal $(f_averaged[{i}])\n" for i in range(1, 7))
        columns.extend(f"variable C{i}{column} equal (v_plus{i}-v_minus{i})/{2 * delta}\n"
                       f"variable C{i}{column} equal $(v_C{i}{column})\n"
                       for i in range(1, 7))
    matrix = "elastic-stiffness-GPa.csv"
    columns.append(f'print "stress_component,{",".join(axes)}" file {matrix} screen no\n')
    for i, axis in enumerate(axes, 1):
        values = ",".join(f"${{C{i}{j}}}" for j in range(1, 7))
        columns.append(f'print "{axis},{values}" append {matrix} screen no\n')
    columns.append('''variable cubic11 equal (v_C11+v_C22+v_C33)/3
variable cubic12 equal (v_C12+v_C13+v_C21+v_C23+v_C31+v_C32)/6
variable cubic44 equal (v_C44+v_C55+v_C66)/3
print "C11_GPa,C12_GPa,C44_GPa" file elastic-cubic-projection.csv screen no
print "${cubic11},${cubic12},${cubic44}" append elastic-cubic-projection.csv screen no
''')
    return "".join(columns)


def write_run_input(folder, package, settings):
    settings.validate()
    model = read_plan(package)
    forces = _forces(package)
    temperature, seed = model["temperature"], model.get("seed")
    if (not 1 <= temperature <= 10_000 or type(seed) is not int or not 1 <= seed <= 900_000_000):
        raise ValueError("Prepare the MD package again to use the selected tests.")
    sections = [f'''print "ESEN_STEP=Minimization and equilibration"
units metal
boundary p p p
atom_style atomic
read_data structure.lmp
{forces}min_style cg
minimize 1e-25 1e-25 5000 10000
reset_timestep 0
velocity all create {temperature} {seed} mom yes rot no
fix equilibrate all npt temp {temperature} {temperature} 0.5 iso 0 0 1 drag 1
run 50000
unfix equilibrate
write_restart equilibrated.restart
''']
    for test in settings.tests:
        if test == "elastic":
            sections.append(_elastic(forces, temperature))
            continue
        sections.append(_start_test(TESTS[test], forces))
        sections.append(_creep(settings, temperature) if test == "creep" else
                        _deformation(test, settings, model["elements"], temperature))
    sections.append('print "ESEN_STEP=Tamamlandi"\n')
    (folder / ENTRY).write_text("\n".join(sections), encoding="ascii", newline="\n")
    (folder / MANIFEST).write_text(json.dumps({"version": 1, **asdict(settings)}, indent=2) + "\n", encoding="ascii")

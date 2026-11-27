"""LAMMPS protocol adapted from the MolecularDynamic E20 single-crystal run."""

ENTRY = "in.md-bcc"
RESTART = "devam.in"
CNA_CUTOFF = 3.9

INITIAL = """include kosu.inc
print "ESEN_STEP=Model yukleniyor" screen yes
units metal
boundary p p p
atom_style atomic
read_data structure.lmp
variable ilk index 1
include analiz.inc
"""

RESUME = """print "ESEN_STEP=Ara kayit yukleniyor" screen yes
read_restart ${esen_checkpoint}
include kosu.inc
variable ilk index 0
include analiz.inc
"""

ANALYSIS = """\
# Shared physics and absolute timestep ranges for initial and resumed segments.
variable denge_son equal 50000
variable cekme_son equal 100000
variable kayit_araligi equal 10000
variable srate equal 1.0e10
variable srate1 equal v_srate/1.0e12

pair_style meam
pair_coeff * * meam_kutuphane.meam @MEAM_ORDER@ meam_parametreler.meam @MEAM_MAP@
# The preparation validates the MEAM cutoff and sets a sufficient neighbor skin.
neighbor @NEIGHBOR_SKIN@ bin
neigh_modify delay 20
timestep 0.001
thermo 100
thermo_style custom step temp pe lx ly lz press pxx pyy pzz

if "${ilk} == 0" then "jump SELF dengeleme"
print "ESEN_STEP=Minimizasyon" screen yes
reset_timestep 0
min_style cg
minimize 1e-25 1e-25 5000 10000
reset_timestep 0
write_restart ${esen_checkpoint_dir}/step.0

label dengeleme
restart ${kayit_araligi} ${esen_checkpoint_dir}/step.*
if "$(step) >= ${denge_son}" then "jump SELF cekme"
print "ESEN_STEP=Dengeleme" screen yes
if "$(step) == 0" then "velocity all create ${T} ${tohum} mom yes rot no"
fix denge_npt all npt temp ${T} ${T} 0.5 iso 0 0 1 drag 1
run ${denge_son} upto start 0 stop ${denge_son}
unfix denge_npt

label cekme
if "$(step) == ${cekme_son}" then "jump SELF tamamlandi"
if "$(step) > ${cekme_son}" then "print 'Ara kayit hedef adimin otesinde.'" "quit 1"
print "ESEN_STEP=Cekme ve CNA" screen yes

# erate is engineering strain per ps. Invert LX = L0 * (1 + erate * dt * n)
# at the loaded absolute step to recover the same reference without a sidecar.
variable L0 equal $(lx/(1.0+v_srate1*dt*(step-v_denge_son)):%.17g)
print "Initial Length, L0: ${L0}" screen yes
fix cekme_npt all npt temp ${T} ${T} 0.5 y 0 0 1 z 0 0 1 drag 1
fix cekme_deform all deform 1 x erate ${srate1} units box remap x

compute csym all centro/atom bcc
compute cna all cna/atom @CNA_CUTOFF@
compute peratom all pe/atom
variable strain equal (lx-v_L0)/v_L0
variable sx equal -pxx/10000
variable sy equal -pyy/10000
variable sz equal -pzz/10000
variable adim equal step-v_denge_son
fix csv all print 100 "${adim},${strain},${sx},${sy},${sz}" &
    file ${cikti_oneki}.${esen_segment}_08-gerilme-gerinim.csv screen no &
    title "adim,gerinim,gerilme_X_GPa,gerilme_Y_GPa,gerilme_Z_GPa"

dump atoms all custom 250 ${cikti_oneki}.${esen_segment}_08-atom-yorsungesi.lammpstrj id type element xs ys zs c_cna c_csym c_peratom fx fy fz
dump_modify atoms sort id element @ATOM_TYPES@
thermo 1000
thermo_style custom step v_strain temp v_sx v_sy v_sz ke pe press
run ${cekme_son} upto start ${denge_son} stop ${cekme_son}
undump atoms
unfix csv

label tamamlandi
print "ESEN_STEP=Tamamlandi" screen yes
print "All done" screen yes
""".replace("@CNA_CUTOFF@", str(CNA_CUTOFF))

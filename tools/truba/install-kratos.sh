#!/usr/bin/env bash
# Per-user, versioned installation; never changes system Python or shell startup.
set -euo pipefail
export TERM=dumb
export OMP_NUM_THREADS=1
export OPENBLAS_NUM_THREADS=1
module load comp/python/3.12.0
environment="$HOME/.local/share/RustMech/kratos-10.4.4"
python3 -m venv "$environment"
"$environment/bin/python" -m pip install --disable-pip-version-check --only-binary=:all: --timeout 25 --retries 1 \
  'KratosMultiphysics==10.4.4' 'KratosStructuralMechanicsApplication==10.4.4' \
  'KratosContactStructuralMechanicsApplication==10.4.4' \
  'KratosConstitutiveLawsApplication==10.4.4' 'KratosLinearSolversApplication==10.4.4'
"$environment/bin/python" -m pip check
"$environment/bin/python" - <<'PY'
import json
from pathlib import Path
import sys
import KratosMultiphysics as KM
import KratosMultiphysics.StructuralMechanicsApplication
import KratosMultiphysics.ContactStructuralMechanicsApplication as Contact
import KratosMultiphysics.ConstitutiveLawsApplication
import KratosMultiphysics.LinearSolversApplication
result = {
    "installed": True,
    "version": KM.KratosGlobals.Kernel.Version(),
    "python": sys.executable,
    "applications": ["StructuralMechanicsApplication", "ContactStructuralMechanicsApplication",
                     "ConstitutiveLawsApplication", "LinearSolversApplication"],
    "self_contact_utilities": hasattr(Contact, "SelfContactUtilities"),
    "analysis_executed": False,
}
Path(sys.prefix, "rustmech-installation.json").write_text(json.dumps(result, indent=2) + "\n")
print("RUSTMECH_KRATOS_INSTALLATION=" + json.dumps(result))
PY

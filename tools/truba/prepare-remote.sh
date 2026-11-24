#!/bin/bash
# Invoked automatically for an incomplete runtime, or by manual repair, on a SLURM node.
set -euo pipefail
runtime_python="$1"
native_sha="$2"
stage() { printf '%s\n' "$1" > progress.txt; printf '%s\n' "$1"; }
stage 'Preparing private Python environment'
environment="$(dirname "$(dirname "$runtime_python")")"
python3 - "$environment" <<'PY'
from pathlib import Path
import sys
target=Path(sys.argv[1]).resolve()
base=Path.home()/'.local/share/RustMech'
if not target.is_relative_to(base) or not target.name.startswith('remote-'):
    raise SystemExit('Prepare only supports dedicated ~/.local/share/RustMech/remote-* environments. Existing custom runtimes can be probed without modifying them.')
PY
if python3 -c 'import ctypes, ssl; import sys; assert sys.version_info[:2] == (3, 12)' 2>/dev/null; then
  python3 -m venv "$environment"
else
  # Some cluster Python builds omit _ctypes. A venv cannot repair its base
  # interpreter; install a complete private CPython without changing modules,
  # system Python, PATH, or shell startup files.
  bootstrap="$environment/python-bootstrap"
  mkdir -p "$bootstrap"
  if [ ! -x "$bootstrap/uv-x86_64-unknown-linux-gnu/uv" ]; then
    curl --fail --location --proto '=https' --tlsv1.2 \
      https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-unknown-linux-gnu.tar.gz \
      -o "$bootstrap/uv.tar.gz"
    tar -xzf "$bootstrap/uv.tar.gz" -C "$bootstrap"
  fi
  export UV_PYTHON_INSTALL_DIR="$bootstrap/pythons" UV_PYTHON_BIN_DIR="$bootstrap/bin" UV_CACHE_DIR="$bootstrap/cache"
  uv="$bootstrap/uv-x86_64-unknown-linux-gnu/uv"
  "$uv" python install 3.12
  bootstrap_python="$("$uv" python find --managed-python 3.12)"
  # Remove only the old venv's interpreter symlinks, never their shared targets.
  python3 - "$environment" <<'PY'
from pathlib import Path
import sys
environment = Path(sys.argv[1])
for name in ('python', 'python3', 'python3.12'):
    path = environment / 'bin' / name
    if path.is_symlink():
        path.unlink()
    elif path.exists():
        raise SystemExit('Refusing to replace a non-symlink Python executable: ' + str(path))
PY
  "$bootstrap_python" -m venv "$environment"
fi
"$runtime_python" -c 'import ctypes, ssl; assert hasattr(ctypes, "CDLL")'
stage 'Installing Python and solver packages (first setup may take several minutes)'
"$runtime_python" -m pip install --disable-pip-version-check --only-binary=:all: --timeout 30 --retries 1 \
  'numpy>=2.0,<3' 'scipy>=1.14,<2' 'scikit-image>=0.25,<0.27' \
  'pyvista>=0.46,<0.48' 'vtk>=9.4,<9.7' 'netgen-mesher>=6.2.2506' \
  'matplotlib>=3.9,<4' 'PySide6>=6.8,<7' 'pyvistaqt>=0.11,<0.12' 'paramiko>=4,<5' \
  'KratosMultiphysics==10.4.4' 'KratosStructuralMechanicsApplication==10.4.4' \
  'KratosContactStructuralMechanicsApplication==10.4.4' \
  'KratosConstitutiveLawsApplication==10.4.4' 'KratosLinearSolversApplication==10.4.4' \
  'KratosMPMApplication==10.4.4'
export CARGO_HOME="$environment/cargo" RUSTUP_HOME="$environment/rustup"
stage 'Preparing Rust compiler'
if [ ! -x "$CARGO_HOME/bin/cargo" ]; then
  curl --fail --location --proto '=https' --tlsv1.2 https://sh.rustup.rs -o "$environment/rustup-init.sh"
  sh "$environment/rustup-init.sh" -y --profile minimal --default-toolchain 1.85.1 --no-modify-path
fi
export CARGO_BUILD_JOBS="${SLURM_CPUS_PER_TASK:-1}"
kernel_target="$environment/kernel-build/$native_sha"
stage 'Building remote mesh kernel'
"$CARGO_HOME/bin/cargo" build --release --locked --manifest-path engine/native/mesh_kernel/Cargo.toml --target-dir "$kernel_target"
mkdir -p "$environment/native"
cp "$kernel_target/release/librustmech_core.so" "$environment/native/librustmech_core.so.new"
mv "$environment/native/librustmech_core.so.new" "$environment/native/librustmech_core.so"
"$runtime_python" - "$environment/native/librustmech_core.json" "$native_sha" <<'PY'
import json,sys
from pathlib import Path
Path(sys.argv[1]).write_text(json.dumps(dict(native_sha256=sys.argv[2])))
PY
stage 'Verifying remote runtime dependencies'
"$runtime_python" -m pip check

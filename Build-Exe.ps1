param([string]$Python)

$ErrorActionPreference = 'Stop'
if (-not $Python) {
    $candidates = @(
        (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
        (Join-Path (Split-Path $PSScriptRoot -Parent) 'Gitterburg\.venv\Scripts\python.exe')
    )
    $Python = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $Python) { $Python = (Get-Command python).Source }
}
# Hidden imports do not install missing packages; fail before building a broken EXE.
& $Python -c 'from ansys.mapdl.core import launch_mapdl; from ansys.mapdl.reader import read_binary'
if ($LASTEXITCODE -ne 0) {
    throw "ANSYS Python dependencies are missing or broken. Run: & '$Python' -m pip install -r '$PSScriptRoot\requirements-mapdl.txt'"
}
& $Python -c 'import paramiko'
if ($LASTEXITCODE -ne 0) {
    throw "TRUBA dependencies are missing. Run: & '$Python' -m pip install -r '$PSScriptRoot\requirements-truba.txt'"
}
& (Join-Path $PSScriptRoot 'Build-MeshKernel.ps1') -Check
if ($LASTEXITCODE -ne 0) { throw 'RustMech native build failed.' }
$arguments = @(
    '-m', 'PyInstaller', '--noconfirm', '--onedir', '--windowed',
    '--name', 'RustMech', '--distpath', (Join-Path $PSScriptRoot 'dist'),
    '--workpath', (Join-Path $PSScriptRoot 'build'),
    '--specpath', (Join-Path $PSScriptRoot 'packaging'),
    '--version-file', (Join-Path $PSScriptRoot 'packaging\version_info.txt'),
    '--add-data', ((Join-Path $PSScriptRoot 'assets\ui') + ';assets/ui'),
    '--add-data', ((Join-Path $PSScriptRoot 'assets\help') + ';assets/help'),
    '--add-data', ((Join-Path $PSScriptRoot '*.py') + ';remote_sources'),
    '--add-data', ((Join-Path $PSScriptRoot 'assets\ui') + ';remote_sources/assets/ui'),
    '--add-data', ((Join-Path $PSScriptRoot 'assets\help') + ';remote_sources/assets/help'),
    '--add-data', ((Join-Path $PSScriptRoot 'tools\truba') + ';remote_sources/tools/truba'),
    '--add-data', ((Join-Path $PSScriptRoot 'native\mesh_kernel\src') + ';remote_sources/native/mesh_kernel/src'),
    '--add-data', ((Join-Path $PSScriptRoot 'native\mesh_kernel\Cargo.toml') + ';remote_sources/native/mesh_kernel'),
    '--add-data', ((Join-Path $PSScriptRoot 'native\mesh_kernel\Cargo.lock') + ';remote_sources/native/mesh_kernel'),
      '--add-data', ((Join-Path $PSScriptRoot 'tools\truba') + ';tools/truba'),
    '--add-binary', ((Join-Path $PSScriptRoot 'native\bin\rustmech_core.dll') + ';native/bin'),
    '--collect-all', 'netgen', '--collect-all', 'pyngcore',
    '--hidden-import', 'ansys.mapdl.reader', '--hidden-import', 'ansys.mapdl.core',
    '--exclude-module', 'PyQt5', '--exclude-module', 'PyQt6', '--exclude-module', 'PySide2',
    '--exclude-module', 'torch', '--exclude-module', 'tensorflow', '--exclude-module', 'numba',
    '--exclude-module', 'pytest', '--exclude-module', 'IPython', '--exclude-module', 'jupyter',
    '--exclude-module', 'tkinter', '--exclude-module', '_tkinter',
    (Join-Path $PSScriptRoot 'desktop_entry.py')
)
& $Python @arguments
if ($LASTEXITCODE -ne 0) { throw 'RustMech executable build failed.' }
# PySide6 expects the unversioned Windows ICU API. The build host's Poppler
# PATH can contribute an incompatible ICU 78 DLL with version-suffixed exports.
# Let Windows supply its ICU DLL instead of shipping that unrelated dependency.
$packagedIcu = Join-Path $PSScriptRoot 'dist\RustMech\_internal\icuuc.dll'
if (Test-Path -LiteralPath $packagedIcu) {
    Rename-Item -LiteralPath $packagedIcu -NewName 'icuuc-poppler-unused.dll'
}
& $Python (Join-Path $PSScriptRoot 'tools\finalize_distribution.py') (Join-Path $PSScriptRoot 'dist\RustMech')
if ($LASTEXITCODE -ne 0) { throw 'RustMech Netgen DLL collection failed.' }
$settings = Join-Path $PSScriptRoot 'settings.json'
$destination = Join-Path $PSScriptRoot 'dist\RustMech\settings.json'
if ((Test-Path -LiteralPath $settings) -and -not (Test-Path -LiteralPath $destination)) {
    Copy-Item -LiteralPath $settings -Destination $destination
}
Write-Output ('RustMech ready: ' + (Join-Path $PSScriptRoot 'dist\RustMech\RustMech.exe'))

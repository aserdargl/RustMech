param([int]$Jobs = 8, [switch]$Install, [switch]$Check, [string]$Python)
$ErrorActionPreference = 'Stop'
if (-not $Python) {
    $pythonCandidates = @((Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
        (Join-Path (Split-Path $PSScriptRoot -Parent) 'Gitterburg\.venv\Scripts\python.exe'))
    $Python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $Python) { $Python = (Get-Command python).Source }
}
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$visualStudio = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $visualStudio) { throw 'Visual Studio C++ build tools are required.' }
$vcvars = Join-Path $visualStudio 'VC\Auxiliary\Build\vcvars64.bat'
& $env:ComSpec /d /c "`"$vcvars`" >nul && set" | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
}
if ($LASTEXITCODE -ne 0) { throw 'Cannot initialize C++ build tools.' }
$cmake = Join-Path $visualStudio 'Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin\cmake.exe'
$ninja = Join-Path $visualStudio 'Common7\IDE\CommonExtensions\Microsoft\CMake\Ninja\ninja.exe'
$source = Join-Path $PSScriptRoot 'native\netgen_kernel'
$build = Join-Path $source 'build'
foreach ($dependency in @(
    @{ Folder='upstream'; Repository='https://github.com/NGSolve/netgen.git'; Tag='v6.2.2608'; Commit='96e5682f6ea43ba77ba3bb2ae4bb4bd1791f506e' },
    @{ Folder='zlib'; Repository='https://github.com/madler/zlib.git'; Tag='v1.3.1'; Commit='51b7f2abdade71cd9bb0e7a373ef2610ec6f9daf' }
)) {
    $dependencyPath = Join-Path $source $dependency.Folder
    if (-not (Test-Path -LiteralPath $dependencyPath)) {
        & git clone --depth 1 --branch $dependency.Tag $dependency.Repository $dependencyPath
        if ($LASTEXITCODE -ne 0) { throw "Cannot obtain $($dependency.Folder)." }
    }
    $revision = & git -C $dependencyPath rev-parse HEAD
    if ($LASTEXITCODE -ne 0 -or $revision -ne $dependency.Commit) { throw "Unexpected $($dependency.Folder) source revision." }
}
& $Python (Join-Path $source 'patch_netgen.py')
if ($LASTEXITCODE -ne 0) { throw 'Cannot apply the pinned Netgen optimizations.' }
& $cmake -S $source -B $build -G Ninja "-DCMAKE_MAKE_PROGRAM=$ninja"
if ($LASTEXITCODE -ne 0) { throw 'Netgen kernel configuration failed.' }
& $cmake --build $build --target nglib --parallel $Jobs
if ($LASTEXITCODE -ne 0) { throw 'Netgen kernel build failed.' }
if ($Check) {
    & $cmake --build $build --target point_tree_tests --parallel $Jobs
    if ($LASTEXITCODE -ne 0) { throw 'Point index test build failed.' }
    & (Join-Path $build 'runtime\point_tree_tests.exe')
    if ($LASTEXITCODE -ne 0) { throw 'Point index regression tests failed.' }
}
if ($Install) {
    $destination = Join-Path $PSScriptRoot 'native\bin\netgen_kernel'
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    foreach ($filename in @('rustmech_netgen.dll','rustmech_ngcore.dll')) {
        Copy-Item -LiteralPath (Join-Path $build "runtime\$filename") -Destination $destination -Force
    }
    Copy-Item -LiteralPath (Join-Path $source 'LICENSE.Netgen'),(Join-Path $source 'LICENSE.zlib') -Destination $destination -Force
    & $Python -c 'import hashlib,json,sys; from pathlib import Path; folder=Path(sys.argv[1]); data=dict(revision="96e5682f-rustmech-atlas-5",upstream_commit="96e5682f6ea43ba77ba3bb2ae4bb4bd1791f506e",sha256={name:hashlib.sha256((folder/name).read_bytes()).hexdigest() for name in ("rustmech_netgen.dll","rustmech_ngcore.dll")}); (folder/"build.json").write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")' $destination
    if ($LASTEXITCODE -ne 0) { throw 'Cannot write Netgen kernel metadata.' }
}

param([string]$Python)
$ErrorActionPreference = 'Stop'
if (-not $Python) {
    $pythonCandidates = @(
        (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
        (Join-Path (Split-Path $PSScriptRoot -Parent) 'Gitterburg\.venv\Scripts\python.exe')
    )
    $Python = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $Python) { $Python = (Get-Command python).Source }
}
$config = & $Python -c 'import json, netgen, sys, sysconfig; from pathlib import Path; print(json.dumps(dict(netgen=str(Path(netgen.__file__).parent), include=sysconfig.get_paths()["include"], libs=sysconfig.get_config_var("LIBDIR"), pythonlib="python"+str(sys.version_info.major)+str(sys.version_info.minor)+".lib")))' | ConvertFrom-Json
if ($LASTEXITCODE -ne 0) { throw 'Cannot locate Netgen and Python development files.' }
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$visualStudio = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $visualStudio) { throw 'Visual Studio C++ build tools are required.' }
$vcvars = Join-Path $visualStudio 'VC\Auxiliary\Build\vcvars64.bat'
& $env:ComSpec /d /c "`"$vcvars`" >nul && set" | ForEach-Object {
    if ($_ -match '^([^=]+)=(.*)$') { [Environment]::SetEnvironmentVariable($matches[1], $matches[2], 'Process') }
}
if ($LASTEXITCODE -ne 0) { throw 'Cannot initialize Visual Studio C++ build tools.' }
$outputFolder = Join-Path $PSScriptRoot 'native\bin'
$buildFolder = Join-Path $PSScriptRoot 'native\netgen_bridge\build'
New-Item -ItemType Directory -Path $outputFolder -Force | Out-Null
New-Item -ItemType Directory -Path $buildFolder -Force | Out-Null
$netgenInclude = Join-Path $config.netgen 'include'
$arguments = @('/nologo', '/O2', '/MD', '/EHsc', '/std:c++17', '/LD', '/DNOMINMAX', '/DWIN32',
    "/I$($config.include)", "/I$netgenInclude", "/I$netgenInclude\include",
    (Join-Path $PSScriptRoot 'native\netgen_bridge\bridge.cpp'),
    "/Fo$buildFolder\netgen_bridge.obj", '/link', "/LIBPATH:$($config.libs)",
    "/LIBPATH:$($config.netgen)\lib", $config.pythonlib, 'nglib.lib', 'ngcore.lib',
    "/IMPLIB:$buildFolder\netgen_bridge.lib", "/OUT:$outputFolder\_rustmech_netgen.pyd")
& cl.exe @arguments
if ($LASTEXITCODE -ne 0) { throw 'Netgen array bridge build failed.' }
& $Python -c 'import hashlib,json,netgen,sys; from pathlib import Path; from importlib.metadata import version; folder=Path(sys.argv[1]); library=Path(netgen.__file__).parent/"nglib.dll"; data=dict(netgen_version=version("netgen-mesher"),python_cache_tag=sys.implementation.cache_tag,netgen_library_sha256=hashlib.sha256(library.read_bytes()).hexdigest()); (folder/"netgen_bridge.json").write_text(json.dumps(data,indent=2)+"\n",encoding="utf-8")' $outputFolder
if ($LASTEXITCODE -ne 0) { throw 'Cannot record Netgen bridge compatibility metadata.' }
Write-Output "Netgen array bridge built in $outputFolder"

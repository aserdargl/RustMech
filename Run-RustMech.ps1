$ErrorActionPreference = 'Stop'
$pythonCandidates = @(
    (Join-Path $PSScriptRoot '.venv\Scripts\python.exe'),
    (Join-Path (Split-Path $PSScriptRoot -Parent) 'Gitterburg\.venv\Scripts\python.exe')
)
$pythonExecutable = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $pythonExecutable) { $pythonExecutable = (Get-Command python).Source }
& $pythonExecutable (Join-Path $PSScriptRoot 'desktop_entry.py') @args
exit $LASTEXITCODE

param([switch]$Check)

$ErrorActionPreference = 'Stop'
$manifest = Join-Path $PSScriptRoot 'native\mesh_kernel\Cargo.toml'
if ($Check) {
    & cargo test --manifest-path $manifest
    if ($LASTEXITCODE -ne 0) { throw 'Rust mesh tests failed.' }
}
& cargo build --release --manifest-path $manifest
if ($LASTEXITCODE -ne 0) { throw 'Rust mesh build failed.' }
$destination = Join-Path $PSScriptRoot 'native\bin'
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$built = Join-Path $PSScriptRoot 'native\mesh_kernel\target\release\rustmech_core.dll'
$installed = Join-Path $destination 'rustmech_core.dll'
$sameBinary = (Test-Path -LiteralPath $installed) -and (
    (Get-FileHash -LiteralPath $built).Hash -eq (Get-FileHash -LiteralPath $installed).Hash
)
if (-not $sameBinary) {
    try {
        Copy-Item -LiteralPath $built -Destination $installed -Force
    } catch {
        throw "Could not install the mesh DLL. Close running RustMech and mesh workers, then retry. $($_.Exception.Message)"
    }
}
Write-Output "Mesh kernel built: $destination\rustmech_core.dll"

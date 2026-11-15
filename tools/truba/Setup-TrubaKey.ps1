param([Parameter(Mandatory=$true)][ValidatePattern('^[a-zA-Z0-9_.-]+$')][string]$Username)
$ErrorActionPreference = 'Stop'
$key = Join-Path $env:USERPROFILE ".ssh\rustmech_truba_${Username}_ed25519"
New-Item -ItemType Directory -Path (Split-Path $key) -Force | Out-Null
if (-not (Test-Path -LiteralPath $key)) {
    Write-Host 'Create a key passphrase when prompted. Keep the passphrase private.'
    & ssh-keygen -t ed25519 -f $key -C 'RustMech-TRUBA'
    if ($LASTEXITCODE -ne 0) { throw 'Key generation failed.' }
}
if ((Get-Service ssh-agent).Status -ne 'Running') {
    Write-Host 'Windows may ask for administrator permission to enable SSH Agent.'
    $serviceSetup = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList '-NoProfile -Command "Set-Service ssh-agent -StartupType Automatic; Start-Service ssh-agent; if ((Get-Service ssh-agent).Status -ne ''Running'') { exit 1 }"'
    if ($serviceSetup.ExitCode -ne 0) { throw 'SSH Agent could not be enabled.' }
}
& ssh-add $key
if ($LASTEXITCODE -ne 0) { throw 'Could not load the SSH key.' }
Write-Host 'VPN must be connected. Verify the host fingerprint if prompted, then enter your TRUBA password here.'
Get-Content -LiteralPath ($key + '.pub') | & ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=ask "${Username}@172.16.6.15" 'umask 077; mkdir -p .ssh; cat >> .ssh/authorized_keys; chmod 700 .ssh; chmod 600 .ssh/authorized_keys'
if ($LASTEXITCODE -ne 0) { throw 'Public key installation failed; check VPN and SSH access.' }
& (Join-Path $PSScriptRoot 'Watch-Truba.ps1') -Once -Username $Username
Get-Content -LiteralPath (Join-Path $env:LOCALAPPDATA 'RustMech\TRUBA\status.json')

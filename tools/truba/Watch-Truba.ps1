param(
    [Parameter(Mandatory=$true)][ValidatePattern('^[a-zA-Z0-9_.-]+$')][string]$Username,
    [switch]$Once, [int]$ParentPid = 0, [string]$StopFile = ''
)
$ErrorActionPreference = 'Stop'
$stateDir = Join-Path $env:LOCALAPPDATA 'RustMech\TRUBA'
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$identity = Join-Path $env:USERPROFILE ".ssh\rustmech_truba_${Username}_ed25519"
$target = "${Username}@172.16.6.15"
$ssh = Join-Path $env:WINDIR 'System32\OpenSSH\ssh.exe'
$options = @('-p', '22', '-i', $identity, '-o', 'IdentitiesOnly=yes', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3')
$connection = $null
$lock = $null
function Stop-Requested {
    if ($StopFile -and (Test-Path -LiteralPath $StopFile)) { return $true }
    if ($ParentPid -and -not (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue)) { return $true }
    return $false
}
function Write-State($state, $detail) {
    $record = [ordered]@{ state=$state; detail=$detail; host='172.16.6.15'; user=$Username; updated=(Get-Date).ToString('o'); monitorPid=$PID }
    $temp = Join-Path $stateDir "status.$PID.tmp"
    $record | ConvertTo-Json | Set-Content -LiteralPath $temp -Encoding UTF8
    Move-Item -LiteralPath $temp -Destination (Join-Path $stateDir 'status.json') -Force
}
try {
    # An exclusive file handle prevents duplicate monitors, including at login.
    try { $lock = [IO.File]::Open((Join-Path $stateDir 'monitor.lock'), 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { exit 0 }
    do {
        if (Stop-Requested) { break }
        if (-not (Test-Path -LiteralPath $identity)) {
            Write-State 'setup_required' 'Run Setup-TrubaKey.ps1 once.'
        } else {
            $tcp = New-Object Net.Sockets.TcpClient
            try {
                $pending = $tcp.ConnectAsync('172.16.6.15', 22)
                $reachable = $pending.Wait(3000) -and $tcp.Connected
            } catch { $reachable = $false }
            finally { $tcp.Dispose() }
            if (-not $reachable) {
                Write-State 'waiting_for_network' 'TRUBA SSH is unreachable; waiting for VPN/network.'
            } else {
                # Probe authentication before declaring the persistent session connected.
                $probeError = Join-Path $stateDir 'last-ssh-error.log'
                $ErrorActionPreference = 'Continue'
                try {
                    $probe = & $ssh @options $target 'printf RUSTMECH_SSH_OK' 2> $probeError
                    $probeExit = $LASTEXITCODE
                } finally { $ErrorActionPreference = 'Stop' }
                if ($probeExit -ne 0 -or ($probe -join '') -ne 'RUSTMECH_SSH_OK') {
                    Write-State 'authentication_or_host_key_required' 'See last-ssh-error.log; run Setup-TrubaKey.ps1 or unlock your key with ssh-add.'
                } elseif ($Once) {
                    Write-State 'verified' 'Network, host key and public-key authentication verified.'
                } else {
                    $processOptions = $options.Clone()
                    $processOptions[3] = '"' + $identity + '"'
                    $connection = Start-Process -FilePath $ssh -ArgumentList ($processOptions + @('-N', $target)) -WindowStyle Hidden -PassThru -RedirectStandardError (Join-Path $stateDir 'connection-error.log')
                    Start-Sleep -Seconds 2
                    if (-not $connection.HasExited) {
                        Write-State 'connected' 'Authenticated SSH keepalive session is active.'
                        while (-not $connection.WaitForExit(1000)) {
                            if (Stop-Requested) { break }
                            Write-State 'connected' 'Authenticated SSH keepalive session is active.'
                        }
                    }
                    Write-State 'reconnecting' 'SSH session ended; retrying.'
                    if (-not $connection.HasExited) { $connection.Kill(); $connection.WaitForExit() }
                    $connection.Dispose()
                    $connection = $null
                }
            }
        }
        if (-not $Once) {
            for ($delay = 0; $delay -lt 30; $delay++) {
                if (Stop-Requested) { break }
                Start-Sleep -Seconds 1
            }
        }
    } while (-not $Once)
} finally {
    if ($connection -and -not $connection.HasExited) { $connection.Kill() }
    if (-not $Once -and $lock) { Write-State 'stopped' 'Connection monitor stopped.' }
    if ($lock) { $lock.Dispose() }
}

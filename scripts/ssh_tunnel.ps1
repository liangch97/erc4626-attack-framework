# SSH Tunnel - System-level OpenSSH (replaces paramiko Python script)
# Usage:
#   powershell -ExecutionPolicy Bypass -File scripts\ssh_tunnel.ps1 -SetupKey
#   powershell -ExecutionPolicy Bypass -File scripts\ssh_tunnel.ps1

param(
    [switch]$SetupKey
)

$SSH_HOST = "172.16.108.231"
$SSH_PORT = 22
$SSH_USER = "flash_swap_test"
$SSH_PASS = "flash_swap"
$LOCAL_PORT = 18545
$REMOTE_HOST = "127.0.0.1"
$REMOTE_PORT = 18545

if ($SetupKey) {
    $keyPath = "$env:USERPROFILE\.ssh\id_rsa"
    if (-not (Test-Path $keyPath)) {
        Write-Host "[1/3] Generating SSH key..."
        ssh-keygen -t rsa -b 4096 -f $keyPath -N '""' -q
    } else {
        Write-Host "[1/3] SSH key exists: $keyPath"
    }

    Write-Host "[2/3] Uploading public key to ${SSH_USER}@${SSH_HOST}..."
    Write-Host "  Password: $SSH_PASS"
    $pubKey = Get-Content "$keyPath.pub"
    echo $pubKey | ssh -p $SSH_PORT "${SSH_USER}@${SSH_HOST}" "mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys && chmod 700 ~/.ssh"

    Write-Host "[3/3] Testing passwordless login..."
    ssh -p $SSH_PORT -o BatchMode=yes -o ConnectTimeout=5 "${SSH_USER}@${SSH_HOST}" "echo OK"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Key setup OK! You can now run the tunnel."
    } else {
        Write-Host "Key setup may have failed. Please check manually."
    }
    exit
}

Write-Host "========================================="
Write-Host "  SSH Tunnel (System OpenSSH)"
Write-Host "  Local  127.0.0.1:${LOCAL_PORT}"
Write-Host "  Remote ${REMOTE_HOST}:${REMOTE_PORT}"
Write-Host "  Via    ${SSH_USER}@${SSH_HOST}:${SSH_PORT}"
Write-Host "  Press Ctrl+C to stop"
Write-Host "========================================="

$listener = Get-NetTCPConnection -LocalPort $LOCAL_PORT -ErrorAction SilentlyContinue
if ($listener) {
    Write-Host "Port ${LOCAL_PORT} is in use (PID: $($listener.OwningProcess)). Please close it first."
    exit 1
}

while ($true) {
    $ts = Get-Date -Format "HH:mm:ss"
    Write-Host "[$ts] Starting SSH tunnel..."

    $sshArgs = @(
        "-N",
        "-L", "${LOCAL_PORT}:${REMOTE_HOST}:${REMOTE_PORT}",
        "-p", "$SSH_PORT",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=3",
        "-o", "TCPKeepAlive=yes",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ConnectTimeout=10",
        "-o", "StrictHostKeyChecking=no",
        "${SSH_USER}@${SSH_HOST}"
    )

    $process = Start-Process -FilePath "ssh" -ArgumentList $sshArgs -NoNewWindow -PassThru
    $process.WaitForExit()
    $exitCode = $process.ExitCode

    $ts = Get-Date -Format "HH:mm:ss"
    Write-Host "[$ts] Tunnel disconnected (exit=$exitCode). Reconnecting in 5s..."
    Start-Sleep -Seconds 5
}

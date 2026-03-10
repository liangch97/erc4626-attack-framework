<#
.SYNOPSIS
    ERC4626 攻击测试 - 一键运行脚本
    
.DESCRIPTION
    自动完成：
    1. 设置环境变量（NO_PROXY 绕过系统代理）
    2. 检查 SSH 隧道是否存活
    3. 检查 FlashSwap API 是否可用
    4. 运行 forge test
    
.PARAMETER CaseFile
    指定要测试的 .sol 文件名（不含路径），例如 "Case_57e69699_22497642.sol"
    不指定则测试 generated/ 下所有文件
    
.PARAMETER Verbose
    forge test 输出详细程度: vv, vvv, vvvv, vvvvv (默认 vvv)
    
.PARAMETER SkipChecks
    跳过 RPC/API 连通性检查，直接运行 forge test
    
.PARAMETER BuildOnly
    只编译不测试

.EXAMPLE
    # 测试单个案例
    .\run_erc4626_test.ps1 -CaseFile "Case_57e69699_22497642.sol"
    
    # 测试所有案例
    .\run_erc4626_test.ps1
    
    # 只编译
    .\run_erc4626_test.ps1 -BuildOnly
#>

param(
    [string]$CaseFile = "",
    [string]$Verbose = "vvv",
    [switch]$SkipChecks,
    [switch]$BuildOnly
)

$ErrorActionPreference = "Stop"

# ============================================================
# 配置
# ============================================================
$FORGE = "C:\Users\Administrator\.foundry\bin\forge.exe"
$FOUNDRY_DIR = "D:\区块链\DeFiHackLabs"
$GENERATED_DIR = "$FOUNDRY_DIR\src\test\2026-erc4626\generated"
$RPC_URL = "http://127.0.0.1:18545"
$API_URL = "http://127.0.0.1:3001/inputdata"
$SSH_TUNNEL_SCRIPT = "D:\区块链\ssh_tunnel.py"

# ============================================================
# 1. 环境变量（绕过系统代理 Clash/V2Ray 7898）
# ============================================================
Write-Host "[1/4] Setting environment..." -ForegroundColor Cyan
$env:NO_PROXY = "127.0.0.1,localhost"
$env:no_proxy = "127.0.0.1,localhost"
Write-Host "  NO_PROXY = $env:NO_PROXY" -ForegroundColor Green

# ============================================================
# 2. 检查 forge
# ============================================================
if (-not (Test-Path $FORGE)) {
    Write-Host "[ERR] forge not found: $FORGE" -ForegroundColor Red
    Write-Host "  Install: curl -L https://foundry.paradigm.xyz | bash; foundryup" -ForegroundColor Yellow
    exit 1
}
Write-Host "  forge: $FORGE" -ForegroundColor Green

# ============================================================
# 3. 连通性检查
# ============================================================
if (-not $SkipChecks) {
    Write-Host "[2/4] Checking connectivity..." -ForegroundColor Cyan
    
    # 检查 RPC
    try {
        $rpcBody = '{"jsonrpc":"2.0","method":"eth_blockNumber","params":[],"id":1}'
        $rpcResp = Invoke-RestMethod -Method Post -Uri $RPC_URL -ContentType "application/json" -Body $rpcBody -TimeoutSec 10
        $blockNum = [Convert]::ToInt64($rpcResp.result, 16)
        Write-Host "  RPC OK: block $blockNum" -ForegroundColor Green
    } catch {
        Write-Host "  RPC FAILED: $($_.Exception.Message)" -ForegroundColor Red
        Write-Host "  Start SSH tunnel: python $SSH_TUNNEL_SCRIPT" -ForegroundColor Yellow
        
        # 检查端口是否监听
        $listener = Get-NetTCPConnection -LocalPort 18545 -ErrorAction SilentlyContinue
        if (-not $listener) {
            Write-Host "  Port 18545 not listening. Starting SSH tunnel..." -ForegroundColor Yellow
            Start-Process python -ArgumentList $SSH_TUNNEL_SCRIPT -WindowStyle Minimized
            Start-Sleep -Seconds 5
            
            # 重试
            try {
                $rpcResp = Invoke-RestMethod -Method Post -Uri $RPC_URL -ContentType "application/json" -Body $rpcBody -TimeoutSec 10
                $blockNum = [Convert]::ToInt64($rpcResp.result, 16)
                Write-Host "  RPC OK after tunnel start: block $blockNum" -ForegroundColor Green
            } catch {
                Write-Host "  RPC still failing after tunnel start. Aborting." -ForegroundColor Red
                exit 1
            }
        } else {
            Write-Host "  Port 18545 is listening but RPC failed. Check SSH tunnel health." -ForegroundColor Red
            exit 1
        }
    }
    
    # 检查 FlashSwap API（可选，不阻塞）
    try {
        $apiBody = '{"token_in":"0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48","token_out":"0xf939E0A03FB07F59A73314E73794Be0E57ac1b4E","amount":"1000000","max_hops":1,"enable_verification":false}'
        $apiResp = Invoke-RestMethod -Method Post -Uri $API_URL -ContentType "application/json" -Body $apiBody -TimeoutSec 10
        Write-Host "  FlashSwap API OK" -ForegroundColor Green
    } catch {
        Write-Host "  FlashSwap API unreachable (non-blocking)" -ForegroundColor Yellow
    }
} else {
    Write-Host "[2/4] Skipping connectivity checks" -ForegroundColor Yellow
}

# ============================================================
# 4. 杀死残留的 forge/solc 进程
# ============================================================
Write-Host "[3/4] Cleaning stale processes..." -ForegroundColor Cyan
$stale = Get-Process -Name forge, solc -ErrorAction SilentlyContinue
if ($stale) {
    $stale | Stop-Process -Force
    Write-Host "  Killed $($stale.Count) stale process(es)" -ForegroundColor Yellow
} else {
    Write-Host "  No stale processes" -ForegroundColor Green
}

# ============================================================
# 5. 编译 / 测试
# ============================================================
Push-Location $FOUNDRY_DIR

if ($BuildOnly) {
    Write-Host "[4/4] Building..." -ForegroundColor Cyan
    & $FORGE build 2>&1 | Tee-Object -Variable buildOutput
    if ($LASTEXITCODE -eq 0) {
        Write-Host "Build SUCCESS" -ForegroundColor Green
    } else {
        Write-Host "Build FAILED" -ForegroundColor Red
    }
    Pop-Location
    exit $LASTEXITCODE
}

Write-Host "[4/4] Running forge test..." -ForegroundColor Cyan

if ($CaseFile) {
    # 单个文件测试
    $matchPath = "src/test/2026-erc4626/generated/$CaseFile"
    $contractName = [System.IO.Path]::GetFileNameWithoutExtension($CaseFile)
    
    Write-Host "  File: $CaseFile" -ForegroundColor White
    Write-Host "  Contract: $contractName" -ForegroundColor White
    
    & $FORGE test --match-path $matchPath --match-contract $contractName "-$Verbose" 2>&1
    $exitCode = $LASTEXITCODE
    
    if ($exitCode -eq 0) {
        Write-Host "`n=== PASS ===" -ForegroundColor Green
    } else {
        Write-Host "`n=== FAIL ===" -ForegroundColor Red
    }
} else {
    # 批量测试 generated/ 下所有文件
    $files = Get-ChildItem "$GENERATED_DIR\*.sol" | Sort-Object Name
    Write-Host "  Found $($files.Count) test files" -ForegroundColor White
    
    $passed = 0
    $failed = 0
    $results = @()
    
    foreach ($f in $files) {
        $matchPath = "src/test/2026-erc4626/generated/$($f.Name)"
        $contractName = $f.BaseName
        
        Write-Host "`n--- $($f.Name) ---" -ForegroundColor Cyan -NoNewline
        
        $output = & $FORGE test --match-path $matchPath --match-contract $contractName -vv 2>&1 | Out-String
        
        if ($LASTEXITCODE -eq 0) {
            Write-Host " PASS" -ForegroundColor Green
            $passed++
            $results += [PSCustomObject]@{ File=$f.Name; Status="PASS" }
        } else {
            # 提取失败原因
            $reason = ""
            if ($output -match "\[FAIL[:\s]*([^\]]+)\]") {
                $reason = $Matches[1].Trim()
            }
            Write-Host " FAIL: $reason" -ForegroundColor Red
            $failed++
            $results += [PSCustomObject]@{ File=$f.Name; Status="FAIL"; Reason=$reason }
        }
    }
    
    Write-Host "`n============================================" -ForegroundColor White
    Write-Host "Results: $passed PASS / $failed FAIL / $($files.Count) total" -ForegroundColor $(if ($failed -eq 0) { "Green" } else { "Yellow" })
    Write-Host "============================================" -ForegroundColor White
    
    # 输出失败详情
    $failedResults = $results | Where-Object Status -eq "FAIL"
    if ($failedResults) {
        Write-Host "`nFailed cases:" -ForegroundColor Red
        $failedResults | Format-Table -AutoSize
    }
}

Pop-Location

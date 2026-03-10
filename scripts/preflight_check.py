#!/usr/bin/env python3
"""
ERC4626 攻击测试 - 环境预检脚本

一键检查所有依赖是否就绪：
  1. forge 可执行文件
  2. SSH 隧道 + RPC 连通性
  3. FlashSwap API
  4. Foundry 项目编译状态
  5. 系统代理状态

用法:
  python preflight_check.py [--fix]  # --fix 会自动尝试修复可修复的问题
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

# ============================================================
# 配置（与 cacd_attack_test.py 保持一致）
# ============================================================
FORGE_PATH = Path.home() / ".foundry" / "bin" / "forge.exe"
FOUNDRY_DIR = Path(r"D:\区块链\DeFiHackLabs")
RPC_URL = "http://127.0.0.1:18545"
API_URL = "http://127.0.0.1:3001/inputdata"
SSH_TUNNEL_SCRIPT = Path(r"D:\区块链\ssh_tunnel.py")
PROXY_PORT = 7898  # Clash/V2Ray 代理端口


def green(s):  return f"\033[92m✓ {s}\033[0m"
def red(s):    return f"\033[91m✗ {s}\033[0m"
def yellow(s): return f"\033[93m⚠ {s}\033[0m"
def cyan(s):   return f"\033[96m  {s}\033[0m"


def check_forge():
    """检查 forge 是否存在"""
    # 先查 PATH
    found = shutil.which("forge")
    if found:
        print(green(f"forge in PATH: {found}"))
        return True
    
    if FORGE_PATH.exists():
        print(green(f"forge found: {FORGE_PATH}"))
        return True
    
    print(red("forge NOT FOUND"))
    print(cyan(f"Expected: {FORGE_PATH}"))
    print(cyan("Install: curl -L https://foundry.paradigm.xyz | bash; foundryup"))
    return False


def check_no_proxy():
    """检查 NO_PROXY 环境变量"""
    no_proxy = os.environ.get("NO_PROXY", "") + os.environ.get("no_proxy", "")
    if "127.0.0.1" in no_proxy:
        print(green(f"NO_PROXY set: {no_proxy}"))
        return True
    
    # 检查代理是否在运行
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", PROXY_PORT))
        sock.close()
        if result == 0:
            print(red(f"System proxy running on port {PROXY_PORT} but NO_PROXY not set!"))
            print(cyan("Fix: $env:NO_PROXY = '127.0.0.1,localhost'"))
            print(cyan("Without this, forge/cast will get 502 errors."))
            return False
        else:
            print(green("No system proxy detected (port 7898 closed)"))
            return True
    except Exception:
        print(yellow("Cannot check proxy port"))
        return True


def check_port_listening(port):
    """检查本地端口是否在监听"""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex(("127.0.0.1", port))
        sock.close()
        return result == 0
    except Exception:
        return False


def check_rpc():
    """检查 RPC 连通性"""
    if not check_port_listening(18545):
        print(red("Port 18545 not listening - SSH tunnel not running"))
        print(cyan(f"Start: python {SSH_TUNNEL_SCRIPT}"))
        return False
    
    try:
        payload = json.dumps({
            "jsonrpc": "2.0",
            "method": "eth_blockNumber",
            "params": [],
            "id": 1
        }).encode()
        req = urllib.request.Request(
            RPC_URL,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        # 绕过代理
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        with opener.open(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            block_hex = result.get("result", "0x0")
            block_num = int(block_hex, 16)
            print(green(f"RPC OK: block {block_num}"))
            return True
    except Exception as e:
        print(red(f"RPC failed: {e}"))
        print(cyan(f"Start SSH tunnel: python {SSH_TUNNEL_SCRIPT}"))
        return False


def check_api():
    """检查 FlashSwap API"""
    if not check_port_listening(3001):
        print(yellow("FlashSwap API not running (port 3001 closed)"))
        print(cyan("This is needed for swap calldata generation."))
        return False
    
    try:
        # 测试 USDC→crvUSD（已知可用的路由）
        payload = json.dumps({
            "token_in": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
            "token_out": "0xf939E0A03FB07F59A73314E73794Be0E57ac1b4E",
            "amount": "1000000",
            "max_hops": 1,
            "enable_verification": False,
        }).encode()
        req = urllib.request.Request(
            API_URL,
            data=payload,
            headers={"Content-Type": "application/json"}
        )
        proxy_handler = urllib.request.ProxyHandler({})
        opener = urllib.request.build_opener(proxy_handler)
        with opener.open(req, timeout=10) as resp:
            result = json.loads(resp.read().decode())
            # 检查是否有路由数据
            steps = result.get("steps", [])
            multicall = result.get("multicall_to", "")
            if steps or multicall:
                print(green("FlashSwap API OK (USDC→crvUSD route works)"))
                return True
            else:
                print(yellow("FlashSwap API responded but no route data"))
                return False
    except urllib.error.HTTPError as e:
        print(yellow(f"FlashSwap API HTTP {e.code}"))
        return False
    except Exception as e:
        print(yellow(f"FlashSwap API error: {e}"))
        return False


def check_foundry_project():
    """检查 Foundry 项目目录"""
    toml = FOUNDRY_DIR / "foundry.toml"
    if not toml.exists():
        print(red(f"foundry.toml not found: {toml}"))
        return False
    
    gen_dir = FOUNDRY_DIR / "src" / "test" / "2026-erc4626" / "generated"
    if gen_dir.exists():
        sol_files = list(gen_dir.glob("*.sol"))
        print(green(f"Foundry project OK: {len(sol_files)} generated test files"))
    else:
        print(yellow("generated/ directory not found (no test files generated yet)"))
    
    # 检查 src/ 下总文件数（如果太多会编译慢）
    all_sol = list((FOUNDRY_DIR / "src").rglob("*.sol"))
    if len(all_sol) > 150:
        print(yellow(f"WARNING: {len(all_sol)} .sol files under src/ — compilation will be slow!"))
        print(cyan("Only keep needed directories. See erc4626-forge-environment.md for cleanup."))
    else:
        print(green(f"{len(all_sol)} .sol files under src/ (compilation ~80-90s)"))
    
    return True


def check_stale_processes():
    """检查残留的 forge/solc 进程"""
    try:
        result = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq forge.exe", "/FI", "IMAGENAME eq solc.exe", "/NH"],
            capture_output=True, text=True, timeout=5
        )
        lines = [l for l in result.stdout.strip().split("\n") if "forge" in l.lower() or "solc" in l.lower()]
        if lines:
            print(yellow(f"{len(lines)} stale forge/solc processes running"))
            print(cyan("Clean: Get-Process forge,solc | Stop-Process -Force"))
            return False
        else:
            print(green("No stale forge/solc processes"))
            return True
    except Exception:
        return True


def auto_fix():
    """尝试自动修复可修复的问题"""
    print("\n--- Auto-fix ---")
    
    # 设置 NO_PROXY
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    os.environ["no_proxy"] = "127.0.0.1,localhost"
    print(green("Set NO_PROXY=127.0.0.1,localhost"))
    
    # 杀残留进程
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "forge.exe"],
            capture_output=True, timeout=5
        )
        subprocess.run(
            ["taskkill", "/F", "/IM", "solc.exe"],
            capture_output=True, timeout=5
        )
        print(green("Killed stale processes"))
    except Exception:
        pass
    
    # 启动 SSH 隧道（如果没运行）
    if not check_port_listening(18545):
        print(cyan("Starting SSH tunnel..."))
        subprocess.Popen(
            [sys.executable, str(SSH_TUNNEL_SCRIPT)],
            creationflags=subprocess.CREATE_NEW_CONSOLE
        )
        import time
        time.sleep(5)
        if check_port_listening(18545):
            print(green("SSH tunnel started"))
        else:
            print(red("SSH tunnel failed to start"))


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ERC4626 测试环境预检")
    parser.add_argument("--fix", action="store_true", help="自动修复可修复的问题")
    args = parser.parse_args()

    print("=" * 50)
    print("ERC4626 Attack Test - Preflight Check")
    print("=" * 50)

    checks = [
        ("Forge binary", check_forge),
        ("NO_PROXY env", check_no_proxy),
        ("RPC (SSH tunnel)", check_rpc),
        ("FlashSwap API", check_api),
        ("Foundry project", check_foundry_project),
        ("Stale processes", check_stale_processes),
    ]

    all_ok = True
    for name, fn in checks:
        print(f"\n[{name}]")
        if not fn():
            all_ok = False

    print("\n" + "=" * 50)
    if all_ok:
        print(green("ALL CHECKS PASSED — ready to run tests"))
        print(cyan(f"Run: .\\run_erc4626_test.ps1 -CaseFile 'Case_57e69699_22497642.sol'"))
    else:
        print(red("SOME CHECKS FAILED"))
        if args.fix:
            auto_fix()
            print(cyan("\nRe-run this script to verify fixes."))
        else:
            print(cyan("Run with --fix to attempt auto-repair."))

    print("=" * 50)


if __name__ == "__main__":
    main()

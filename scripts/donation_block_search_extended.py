#!/usr/bin/env python3
"""
ERC-4626 Donation Attack - Extended Block Search

扩展搜索脚本：使用合约部署区块作为搜索下界，找到真实的 min_attack_block

与原脚本的区别：
1. 只处理 VULNERABLE vault
2. 搜索范围改为 [vault_deploy_block, first_seen_block]
3. 同一 vault 地址只搜索一次，结果复用
4. 结果保存到新的 CSV 文件
"""

import csv
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional, Any
from web3 import Web3, HTTPProvider

# Constants
CSV_PATH = "d:/区块链/calldata_bridge/data/suspicious_contracts.csv"
TEMPLATE_SOL_PATH = "d:/区块链/calldata_bridge/templates/DonationSensitivityTest.sol"
GENERATED_TEST_DIR = "d:/区块链/DeFiHackLabs/src/test/2026-erc4626/generated/"
FORGE_PROJECT_ROOT = "d:/区块链/DeFiHackLabs/"
RESULTS_CSV_PATH = "d:/区块链/calldata_bridge/donation_search_results_extended.csv"
RPC_URL = "https://eth-mainnet.g.alchemy.com/v2/wXOiJF5OutGtBTTDgBM65"

# VULNERABLE vault 的部署区块号（通过 get_deploy_blocks.py 查询得到）
DEPLOY_BLOCKS = {
    "0x356b8d89c1e1239cbbb9de4815c39a1474d5ba7d": 20434756,
    "0x57f5e098cad7a3d1eed53991d4d66c45c9af7812": 18293692,
    "0x5c5b196abe0d54485975d1ec29617d42d9198326": 20319829,
    "0x7751e2f4b8ae93ef6b79d86419d42fe3295a4559": 20829243,
    "0x90d2af7d622ca3141efa4d8f1f24d86e5974cc8f": 21833795,
    "0x9d39a5de30e57443bff2a8307a4256c8797a3497": 18571359,
    "0xd11c452fc99cf405034ee446803b6f6c1f6d5ed8": 20711118,
    # 额外的 VULNERABLE vault（不在原始7个列表中，但也需要处理）
    "0xd9a442856c234a39a81a089c06451ebaa4306a72": None,  # 需要查询
}

# 初始化 Web3
w3 = Web3(HTTPProvider(RPC_URL))


# ============================================================
# Step 1: CSV 解析
# ============================================================

def read_suspicious_contracts(csv_file_path: str) -> List[Dict[str, str]]:
    contracts = []
    with open(csv_file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            contracts.append(row)
    return contracts


# ============================================================
# Step 2: 时间戳 → 区块号（RPC 二分搜索）
# ============================================================

def get_block_timestamp(block_number: int) -> int:
    block = w3.eth.get_block(block_number)
    return block['timestamp']


def find_block_by_timestamp(target_timestamp_utc: datetime,
                            lower_bound_block: int = 0,
                            upper_bound_block: Optional[int] = None) -> Optional[int]:
    target_unix = int(target_timestamp_utc.timestamp())
    if upper_bound_block is None:
        upper_bound_block = w3.eth.block_number

    low, high = lower_bound_block, upper_bound_block
    found_block = None
    print(f"  Searching block for ts={target_unix}  range=[{low}, {high}]")

    for _ in range(200):
        if low > high:
            break
        if low == high:
            ts = get_block_timestamp(low)
            if ts <= target_unix:
                found_block = low
            break
        mid = (low + high) // 2
        if mid == 0:
            mid = 1
        try:
            mid_ts = get_block_timestamp(mid)
        except Exception as e:
            print(f"  RPC error at block {mid}: {e}")
            high = mid - 1
            continue
        if mid_ts < target_unix:
            low = mid + 1
            found_block = mid
        elif mid_ts > target_unix:
            high = mid - 1
        else:
            found_block = mid
            break

    if found_block is not None:
        for blk in range(max(0, found_block - 1), min(upper_bound_block, found_block + 5) + 1):
            try:
                ts = get_block_timestamp(blk)
                if ts >= target_unix:
                    print(f"  => block {blk}  ts={ts}")
                    return blk
            except Exception:
                pass

    print(f"  Could not pinpoint block for ts={target_unix}, best={found_block}")
    return found_block


def timestamp_to_block_number(timestamp_str: str) -> Optional[int]:
    if not timestamp_str or not timestamp_str.strip():
        return None
    try:
        dt = datetime.strptime(timestamp_str.strip(), "%Y-%m-%d %H:%M:%S.000 UTC").replace(tzinfo=timezone.utc)
    except ValueError:
        print(f"  Warning: cannot parse timestamp '{timestamp_str}'")
        return None
    current_block = w3.eth.block_number
    estimated_blocks_ago = (datetime.now(timezone.utc).timestamp() - dt.timestamp()) / 12
    lower = max(0, int(current_block - estimated_blocks_ago - 50000))
    upper = current_block
    return find_block_by_timestamp(dt, lower, upper)


# ============================================================
# Step 3: Solidity 测试文件生成
# ============================================================

def _make_contract_name(vault_address: str, suffix: str) -> str:
    hex_part = vault_address.replace('0x', '')[:8].lower()
    safe_suffix = re.sub(r'[^a-zA-Z0-9_]', '_', suffix)
    return f"DonationTest_{hex_part}_{safe_suffix}"


def generate_solidity_test_file(vault_address: str, fork_block_number: int, suffix: str) -> str:
    os.makedirs(GENERATED_TEST_DIR, exist_ok=True)
    contract_name = _make_contract_name(vault_address, suffix)
    output_file = os.path.join(GENERATED_TEST_DIR, f"{contract_name}.sol")
    with open(TEMPLATE_SOL_PATH, 'r', encoding='utf-8') as f:
        tpl = f.read()
    sol = tpl.replace("{{CONTRACT_NAME}}", contract_name)
    sol = sol.replace("{{VAULT_ADDRESS}}", vault_address)
    sol = sol.replace("{{FORK_BLOCK_NUMBER}}", str(fork_block_number))
    with open(output_file, 'w', encoding='utf-8') as f:
        f.write(sol)
    return output_file


# ============================================================
# Step 4: Forge 测试执行 + 二分搜索
# ============================================================

def find_forge_executable() -> str:
    foundry_path = os.path.expanduser("~/.foundry/bin/forge.exe")
    if os.path.isfile(foundry_path):
        return foundry_path
    win_path = r"C:\Users\Administrator\.foundry\bin\forge.exe"
    if os.path.isfile(win_path):
        return win_path
    found = shutil.which("forge")
    return found if found else "forge"


def run_forge_test(sol_file_path: str, override_block: Optional[int] = None) -> bool:
    """
    运行 Forge 测试。
    如果传了 override_block，通过环境变量 FORK_BLOCK 覆盖 .sol 中的默认区块号。
    """
    forge = find_forge_executable()
    rel_path = os.path.relpath(sol_file_path, FORGE_PROJECT_ROOT).replace("\\", "/")

    cmd = [
        forge, "test",
        "--match-path", rel_path,
        "-vv",
    ]

    env = os.environ.copy()
    env["NO_PROXY"] = "127.0.0.1,localhost"

    if override_block is not None:
        env["FORK_BLOCK"] = str(override_block)

    try:
        result = subprocess.run(
            cmd, cwd=FORGE_PROJECT_ROOT,
            capture_output=True, text=True, env=env, timeout=300,
        )
        passed = result.returncode == 0
        block_str = f" block={override_block}" if override_block else ""
        print(f"    forge {'PASS' if passed else 'FAIL'}{block_str}")
        return passed
    except subprocess.TimeoutExpired:
        print(f"    forge TIMEOUT")
        return False
    except FileNotFoundError:
        print(f"    ERROR: forge not found")
        return False
    except Exception as e:
        print(f"    ERROR: {e}")
        return False


def find_minimum_block_extended(vault_address: str, first_seen_block: int,
                                deploy_block: int) -> Dict[str, Any]:
    """
    扩展搜索：从 deploy_block 到 first_seen_block 进行二分搜索
    
    与原版的区别：
    - 搜索下界是 deploy_block 而不是 first_seen_block - 50000
    - 搜索范围可能很大（几十万区块），但二分搜索 20 次迭代足够
    """
    high = first_seen_block
    low = max(1, deploy_block)
    search_range = high - low

    print(f"  Extended search [{low}, {high}] (range={search_range} blocks) for {vault_address}")

    # 生成一个 .sol 文件（只编译一次）
    sol_file = generate_solidity_test_file(vault_address, high, f"ext_search")

    # 阶段1: 测高端（不传 override，用 .sol 中硬编码的 FORK_BLOCK）
    t0 = time.time()
    passed_high = run_forge_test(sol_file)
    t1 = time.time()
    print(f"    initial test: {t1-t0:.1f}s")

    if not passed_high:
        _safe_remove(sol_file)
        return {"min_attack_block": None, "deploy_block": deploy_block, 
                "status": "NOT_VULNERABLE", "error": "", "search_range": search_range,
                "hit_lower_bound": False}

    # 阶段2: 二分搜索（复用同一个 .sol，用 FORK_BLOCK 环境变量覆盖）
    min_block = high

    for iteration in range(25):  # 增加到25次迭代，覆盖更大范围
        if low > high:
            break
        mid = (low + high) // 2

        passed = run_forge_test(sol_file, override_block=mid)

        if passed:
            min_block = mid
            high = mid - 1
        else:
            low = mid + 1

        print(f"    iter {iteration+1}: mid={mid} {'PASS' if passed else 'FAIL'}  "
              f"window=[{low}, {high}]  best={min_block}")

    _safe_remove(sol_file)
    
    # 检查是否命中下界
    hit_lower_bound = (min_block == deploy_block or min_block == deploy_block + 1)
    
    return {
        "min_attack_block": min_block, 
        "deploy_block": deploy_block,
        "status": "VULNERABLE", 
        "error": "",
        "search_range": search_range,
        "hit_lower_bound": hit_lower_bound
    }


def _safe_remove(path: str):
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


# ============================================================
# Step 5: 查询缺失的部署区块
# ============================================================

def get_contract_deploy_block(address: str) -> int:
    """
    通过二分搜索找到合约部署区块
    """
    address = w3.to_checksum_address(address)
    current_block = w3.eth.block_number
    
    low = 0
    high = current_block
    deploy_block = None
    
    print(f"    Binary searching deploy block in [0, {current_block}]...")
    
    for iteration in range(30):
        if low > high:
            break
        
        mid = (low + high) // 2
        
        try:
            code_at_mid = w3.eth.get_code(address, block_identifier=mid)
            has_code = len(code_at_mid) > 0
            
            if has_code:
                deploy_block = mid
                high = mid - 1
            else:
                low = mid + 1
                
        except Exception as e:
            print(f"    Error at block {mid}: {e}")
            low = mid + 1
    
    return deploy_block


# ============================================================
# Step 6: 主流程 + 结果保存
# ============================================================

def to_checksum_address(address: str) -> str:
    return w3.to_checksum_address(address)


def save_results_to_csv(results: List[Dict[str, Any]], output_path: str):
    fieldnames = [
        "vault", "lending_platform_address", "first_seen_borrow", "first_seen_supply",
        "listing_hint", "project", "version",
        "first_seen_block", "deploy_block", "min_attack_block", "search_range", 
        "hit_lower_bound", "status", "error",
    ]
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)
    print(f"Results saved to {output_path}")


def main_extended():
    """
    扩展搜索主函数：只处理 VULNERABLE vault，使用部署区块作为搜索下界
    """
    print("=" * 70)
    print("ERC-4626 Donation Attack — Extended Block Search")
    print("Using deploy_block as lower bound")
    print("=" * 70)

    contracts = read_suspicious_contracts(CSV_PATH)
    print(f"Loaded {len(contracts)} cases from CSV\n")

    # 清理旧的生成文件
    if os.path.isdir(GENERATED_TEST_DIR):
        for f in os.listdir(GENERATED_TEST_DIR):
            _safe_remove(os.path.join(GENERATED_TEST_DIR, f))

    results: List[Dict[str, Any]] = []
    
    # 缓存：vault_addr -> 搜索结果（同一 vault 只搜索一次）
    vault_search_cache: Dict[str, Dict[str, Any]] = {}
    
    # 首先查询缺失的部署区块
    print("Checking deploy blocks...")
    for vault in DEPLOY_BLOCKS:
        if DEPLOY_BLOCKS[vault] is None:
            print(f"  Querying deploy block for {vault}...")
            deploy_block = get_contract_deploy_block(vault)
            DEPLOY_BLOCKS[vault] = deploy_block
            print(f"    => {deploy_block}")
    print()

    for idx, row in enumerate(contracts):
        vault = row["vault"]
        project = row.get("project", "")
        version = row.get("version", "")
        t_start = time.time()
        print(f"\n[{idx+1}/{len(contracts)}] vault={vault}  project={project} v{version}")

        # 只处理 VULNERABLE vault
        # 从原始 CSV 中筛选出已知 VULNERABLE 的 vault
        vault_lower = vault.lower()
        if vault_lower not in [v.lower() for v in DEPLOY_BLOCKS.keys()]:
            print(f"  SKIP: not in VULNERABLE list")
            continue

        ts_str = row.get("first_seen_supply", "").strip()
        if not ts_str:
            ts_str = row.get("first_seen_borrow", "").strip()

        if not ts_str:
            print("  SKIP: no timestamp")
            result = row.copy()
            result.update({"first_seen_block": None, "deploy_block": None,
                           "min_attack_block": None, "search_range": None,
                           "hit_lower_bound": None,
                           "status": "SKIPPED_NO_TIMESTAMP", "error": ""})
            results.append(result)
            continue

        print(f"  timestamp: {ts_str}")
        first_seen_block = timestamp_to_block_number(ts_str)
        if first_seen_block is None:
            print("  SKIP: block conversion failed")
            result = row.copy()
            result.update({"first_seen_block": None, "deploy_block": None,
                           "min_attack_block": None, "search_range": None,
                           "hit_lower_bound": None,
                           "status": "SKIPPED_BLOCK_FAIL", "error": f"Cannot convert '{ts_str}'"})
            results.append(result)
            continue

        print(f"  first_seen_block: {first_seen_block}")

        try:
            vault_cs = to_checksum_address(vault)
        except Exception as e:
            print(f"  SKIP: invalid address: {e}")
            result = row.copy()
            result.update({"first_seen_block": first_seen_block, "deploy_block": None,
                           "min_attack_block": None, "search_range": None,
                           "hit_lower_bound": None,
                           "status": "SKIPPED_BAD_ADDRESS", "error": str(e)})
            results.append(result)
            continue

        # 获取部署区块
        deploy_block = DEPLOY_BLOCKS.get(vault_lower) or DEPLOY_BLOCKS.get(vault_cs)
        if deploy_block is None:
            print(f"  SKIP: no deploy_block info")
            result = row.copy()
            result.update({"first_seen_block": first_seen_block, "deploy_block": None,
                           "min_attack_block": None, "search_range": None,
                           "hit_lower_bound": None,
                           "status": "SKIPPED_NO_DEPLOY_BLOCK", "error": "No deploy block info"})
            results.append(result)
            continue

        print(f"  deploy_block: {deploy_block}")

        # 检查缓存：同一个 vault 不需要重复搜索
        cache_key = vault_cs.lower()
        if cache_key in vault_search_cache:
            cached = vault_search_cache[cache_key]
            print(f"  CACHED: min_block={cached.get('min_attack_block')} (same vault already searched)")
            result = row.copy()
            result["first_seen_block"] = first_seen_block
            result["deploy_block"] = cached.get("deploy_block")
            result["min_attack_block"] = cached.get("min_attack_block")
            result["search_range"] = cached.get("search_range")
            result["hit_lower_bound"] = cached.get("hit_lower_bound")
            result["status"] = cached.get("status")
            result["error"] = cached.get("error", "")
            results.append(result)
            elapsed = time.time() - t_start
            print(f"  => status={result['status']}  min_block={result.get('min_attack_block')}  ({elapsed:.1f}s)")
            save_results_to_csv(results, RESULTS_CSV_PATH)
            continue

        # 执行扩展搜索
        search_result = find_minimum_block_extended(vault_cs, first_seen_block, deploy_block)

        # 缓存结果
        vault_search_cache[cache_key] = search_result

        result = row.copy()
        result["first_seen_block"] = first_seen_block
        result.update(search_result)
        results.append(result)

        elapsed = time.time() - t_start
        print(f"  => status={search_result['status']}  min_block={search_result.get('min_attack_block')}  "
              f"deploy={search_result.get('deploy_block')}  range={search_result.get('search_range')}  "
              f"hit_bound={search_result.get('hit_lower_bound')}  ({elapsed:.1f}s)")
        save_results_to_csv(results, RESULTS_CSV_PATH)

    print("\n" + "=" * 70)
    print("EXTENDED SEARCH COMPLETE")
    print("=" * 70)

    vulnerable = [r for r in results if r.get("status") == "VULNERABLE"]
    not_vuln = [r for r in results if r.get("status") == "NOT_VULNERABLE"]
    skipped = [r for r in results if r.get("status", "").startswith("SKIPPED")]
    hit_bound = [r for r in vulnerable if r.get("hit_lower_bound")]
    
    print(f"  VULNERABLE: {len(vulnerable)}")
    print(f"  NOT_VULNERABLE: {len(not_vuln)}")
    print(f"  SKIPPED: {len(skipped)}")
    print(f"  Hit lower bound: {len(hit_bound)} / {len(vulnerable)}")
    print(f"  Results: {RESULTS_CSV_PATH}")
    
    # 打印详细结果摘要
    print("\n" + "=" * 70)
    print("DETAILED SUMMARY")
    print("=" * 70)
    print(f"{'Vault':<44} {'Deploy':<12} {'MinAttack':<12} {'Range':<10} {'HitBound':<8}")
    print("-" * 90)
    for r in vulnerable:
        vault = r.get("vault", "")[:42]
        deploy = r.get("deploy_block", "")
        min_blk = r.get("min_attack_block", "")
        range_val = r.get("search_range", "")
        hit = r.get("hit_lower_bound", "")
        print(f"{vault:<44} {str(deploy):<12} {str(min_blk):<12} {str(range_val):<10} {str(hit):<8}")


if __name__ == "__main__":
    main_extended()

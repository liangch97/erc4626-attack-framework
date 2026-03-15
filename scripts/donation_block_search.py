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
RESULTS_CSV_PATH = "d:/区块链/calldata_bridge/donation_search_results.csv"
RPC_URL = "https://eth-mainnet.g.alchemy.com/v2/wXOiJF5OutGtBTTDgBM65"
BLOCK_SEARCH_RANGE = 50000

# Initialize Web3
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
    .sol 模板中 setUp() 使用 vm.envOr("FORK_BLOCK", FORK_BLOCK) 读取。
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

    # 通过环境变量传入 fork 区块号，vm.envOr("FORK_BLOCK", ...) 会读取
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


def find_minimum_block(vault_address: str, first_seen_block: int,
                       search_range: int = BLOCK_SEARCH_RANGE) -> Dict[str, Any]:
    """
    两阶段搜索：
      阶段1: 测 first_seen_block，判断是否 donation 敏感
      阶段2: 若敏感，用同一个 .sol 文件 + --fork-block-number 二分搜索
    """
    high = first_seen_block
    low = max(1, first_seen_block - search_range)

    print(f"  Search [{low}, {high}] for {vault_address}")

    # 生成一个 .sol 文件（只编译一次）
    sol_file = generate_solidity_test_file(vault_address, high, f"search")

    # 阶段1: 测高端（不传 override，用 .sol 中硬编码的 FORK_BLOCK）
    t0 = time.time()
    passed_high = run_forge_test(sol_file)
    t1 = time.time()
    print(f"    initial test: {t1-t0:.1f}s")

    if not passed_high:
        _safe_remove(sol_file)
        return {"min_attack_block": None, "status": "NOT_VULNERABLE", "error": ""}

    # 阶段2: 二分搜索（复用同一个 .sol，用 --fork-block-number 覆盖）
    min_block = high

    for iteration in range(20):
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
    return {"min_attack_block": min_block, "status": "VULNERABLE", "error": ""}


def _safe_remove(path: str):
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass


# ============================================================
# Step 5: 主流程 + 结果保存
# ============================================================

def to_checksum_address(address: str) -> str:
    return w3.to_checksum_address(address)


def save_results_to_csv(results: List[Dict[str, Any]], output_path: str):
    fieldnames = [
        "vault", "lending_platform_address", "first_seen_borrow", "first_seen_supply",
        "listing_hint", "project", "version",
        "first_seen_block", "min_attack_block", "status", "error",
    ]
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)
    print(f"Results saved to {output_path}")


def main():
    print("=" * 70)
    print("ERC-4626 Donation Attack — Minimum Block Batch Search")
    print("=" * 70)

    contracts = read_suspicious_contracts(CSV_PATH)
    print(f"Loaded {len(contracts)} cases from CSV\n")

    # 清理旧的生成文件
    if os.path.isdir(GENERATED_TEST_DIR):
        for f in os.listdir(GENERATED_TEST_DIR):
            _safe_remove(os.path.join(GENERATED_TEST_DIR, f))

    results: List[Dict[str, Any]] = []

    # 去重：同一个 vault 地址只需测一次敏感性
    # 相同 vault 在不同 lending platform 上的 donation 敏感性相同
    vault_sensitivity_cache: Dict[str, str] = {}  # vault_addr -> "VULNERABLE" / "NOT_VULNERABLE"

    for idx, row in enumerate(contracts):
        vault = row["vault"]
        project = row.get("project", "")
        version = row.get("version", "")
        t_start = time.time()
        print(f"\n[{idx+1}/{len(contracts)}] vault={vault}  project={project} v{version}")

        ts_str = row.get("first_seen_supply", "").strip()
        if not ts_str:
            ts_str = row.get("first_seen_borrow", "").strip()

        if not ts_str:
            print("  SKIP: no timestamp")
            result = row.copy()
            result.update({"first_seen_block": None, "min_attack_block": None,
                           "status": "SKIPPED_NO_TIMESTAMP", "error": ""})
            results.append(result)
            continue

        print(f"  timestamp: {ts_str}")
        first_seen_block = timestamp_to_block_number(ts_str)
        if first_seen_block is None:
            print("  SKIP: block conversion failed")
            result = row.copy()
            result.update({"first_seen_block": None, "min_attack_block": None,
                           "status": "SKIPPED_BLOCK_FAIL", "error": f"Cannot convert '{ts_str}'"})
            results.append(result)
            continue

        print(f"  first_seen_block: {first_seen_block}")

        try:
            vault_cs = to_checksum_address(vault)
        except Exception as e:
            print(f"  SKIP: invalid address: {e}")
            result = row.copy()
            result.update({"first_seen_block": first_seen_block, "min_attack_block": None,
                           "status": "SKIPPED_BAD_ADDRESS", "error": str(e)})
            results.append(result)
            continue

        # 检查缓存：同一个 vault 不需要重复测试敏感性
        if vault_cs in vault_sensitivity_cache:
            cached = vault_sensitivity_cache[vault_cs]
            print(f"  CACHED: {cached} (same vault already tested)")
            result = row.copy()
            result["first_seen_block"] = first_seen_block
            if cached == "NOT_VULNERABLE":
                result.update({"min_attack_block": None, "status": "NOT_VULNERABLE", "error": ""})
            else:
                # 敏感的 vault — 对这个 lending platform 的 first_seen_block 做独立二分搜索
                search_result = find_minimum_block(vault_cs, first_seen_block, BLOCK_SEARCH_RANGE)
                result.update(search_result)
            results.append(result)
            elapsed = time.time() - t_start
            print(f"  => status={result['status']}  min_block={result.get('min_attack_block')}  ({elapsed:.1f}s)")
            save_results_to_csv(results, RESULTS_CSV_PATH)
            continue

        # 首次测试该 vault
        search_result = find_minimum_block(vault_cs, first_seen_block, BLOCK_SEARCH_RANGE)

        # 缓存结果
        vault_sensitivity_cache[vault_cs] = search_result["status"]

        result = row.copy()
        result["first_seen_block"] = first_seen_block
        result.update(search_result)
        results.append(result)

        elapsed = time.time() - t_start
        print(f"  => status={search_result['status']}  min_block={search_result.get('min_attack_block')}  ({elapsed:.1f}s)")
        save_results_to_csv(results, RESULTS_CSV_PATH)

    print("\n" + "=" * 70)
    print("ALL DONE")
    print("=" * 70)

    vulnerable = [r for r in results if r.get("status") == "VULNERABLE"]
    not_vuln = [r for r in results if r.get("status") == "NOT_VULNERABLE"]
    skipped = [r for r in results if r.get("status", "").startswith("SKIPPED")]
    print(f"  VULNERABLE: {len(vulnerable)}")
    print(f"  NOT_VULNERABLE: {len(not_vuln)}")
    print(f"  SKIPPED: {len(skipped)}")
    print(f"  Results: {RESULTS_CSV_PATH}")


if __name__ == "__main__":
    main()

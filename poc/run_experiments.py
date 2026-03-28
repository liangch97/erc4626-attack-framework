"""
批量运行 ERC4626 Donation Attack Flash Loan PoC 测试
收集每个测试的编译/运行结果到 experiment_results/
"""
import subprocess, os, sys, time, json, re
from datetime import datetime

FOUNDRY_DIR = r"D:\区块链\DeFiHackLabs"
POC_DIR = os.path.join(FOUNDRY_DIR, "src", "test", "2026-erc4626")
RESULTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "experiment_results")
os.makedirs(RESULTS_DIR, exist_ok=True)

# 找 forge
def find_forge():
    for p in [
        os.path.expanduser("~/.foundry/bin/forge.exe"),
        os.path.expanduser("~/.foundry/bin/forge"),
    ]:
        if os.path.isfile(p):
            return p
    return "forge"  # rely on PATH

FORGE = find_forge()

# 要测试的文件和对应的测试函数
TESTS = [
    # (文件名, 合约名, 测试函数列表, 描述)
    ("Vault_0x5c5b_Compound_Donation.sol", "Vault_0x5c5b_DonationPoC",
     ["testDonationSensitivity", "testFullAttackFlow"], "Compound v3, deploy block 20319829"),
    ("Vault_0x7751_Morpho_Donation.sol", "Vault_0x7751_DonationPoC",
     ["testDonationSensitivity", "testFullAttackFlow"], "Morpho v1, delayed ~9 days, block 20894195"),
    ("Vault_0x90d2_Aave_Donation.sol", "Vault_0x90d2_DonationPoC",
     ["testDonationSensitivity", "testFullAttackFlow"], "Aave v3, deploy block 21833795"),
    ("Vault_0xd11c_Multi_Donation.sol", "Vault_0xd11c_DonationPoC",
     ["testDonationSensitivity", "testFullAttackFlow"], "Aave+Compound multi, deploy block 20711118"),
    ("Vault_0xd9a4_Compound_Donation.sol", "Vault_0xd9a4_DonationPoC",
     ["testDonationSensitivity", "testFullAttackFlow"], "Compound v3, +576 blocks delay, block 19128623"),
    ("Vault_0x57f5_wUSDM_Donation.sol", "Vault_0x57f5_DonationPoC",
     ["testDonationSensitivity", "testFullAttackFlow"], "wUSDM rebasing, Compound+Morpho, block 18293905"),
    ("Vault_0x356b_Aave_Donation.sol", "Vault_0x356b_DonationPoC",
     ["testDonationSensitivity", "testFullAttackFlow"], "Aave v3, USDT (non-standard ERC20), block 20434756"),
    ("sUSDe_Aave_Donation_Attack.sol", "sUSDe_DonationPoC",
     ["testDonationSensitivity", "testFullAttackFlow"], "sUSDe, _checkMinShares protection, block 18571359"),
]

def run_test(sol_file, contract, test_func, desc):
    """运行单个 forge test 并返回结果"""
    match_path = f"src/test/2026-erc4626/{sol_file}"
    cmd = [
        FORGE, "test",
        "--match-path", match_path,
        "--match-contract", contract,
        "--match-test", test_func,
        "-vvvv",
        "--no-match-path", "src/test/2026-erc4626/generated/*",
    ]
    env = os.environ.copy()
    env["NO_PROXY"] = "127.0.0.1,localhost"

    print(f"\n{'='*70}")
    print(f"Running: {sol_file}::{contract}::{test_func}")
    print(f"Desc: {desc}")
    print(f"{'='*70}")

    start = time.time()
    try:
        result = subprocess.run(
            cmd, cwd=FOUNDRY_DIR, capture_output=True, text=True,
            timeout=600, env=env
        )
        elapsed = time.time() - start
        output = result.stdout + "\n" + result.stderr
        passed = result.returncode == 0
    except subprocess.TimeoutExpired:
        elapsed = time.time() - start
        output = "TIMEOUT after 600s"
        passed = False
    except Exception as e:
        elapsed = time.time() - start
        output = f"ERROR: {e}"
        passed = False

    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {test_func} ({elapsed:.1f}s)")

    # 保存完整输出
    safe_name = f"{contract}__{test_func}"
    with open(os.path.join(RESULTS_DIR, f"{safe_name}.log"), "w", encoding="utf-8") as f:
        f.write(f"# {sol_file}::{contract}::{test_func}\n")
        f.write(f"# {desc}\n")
        f.write(f"# Status: {status}, Time: {elapsed:.1f}s\n")
        f.write(f"# {'='*60}\n\n")
        f.write(output)

    return {
        "file": sol_file,
        "contract": contract,
        "test": test_func,
        "desc": desc,
        "status": status,
        "time_s": round(elapsed, 1),
        "output_preview": output[-2000:] if len(output) > 2000 else output,
    }


def extract_key_findings(output):
    """从测试输出中提取关键发现"""
    findings = []
    patterns = [
        (r"\[CRITICAL\].*", "CRITICAL"),
        (r"\[PASS\].*", "PASS"),
        (r"\[FINDING.*?\].*", "FINDING"),
        (r"\[CONCLUSION\].*", "CONCLUSION"),
        (r"\[INFO\].*", "INFO"),
        (r"Net Profit:\s*([\d.]+)", "PROFIT"),
        (r"Victim loss:\s*([\d.]+)", "VICTIM_LOSS"),
        (r"BALANCE-BASED", "BALANCE_BASED"),
        (r"INTERNAL ACCOUNTING", "INTERNAL_ACCOUNTING"),
        (r"Attacker redeemed:\s*([\d.]+)", "REDEEMED"),
    ]
    for pat, label in patterns:
        matches = re.findall(pat, output, re.IGNORECASE)
        if matches:
            findings.append((label, matches))
    return findings


def main():
    print(f"ERC4626 Donation Attack Flash Loan PoC - Batch Experiment")
    print(f"Timestamp: {datetime.now().isoformat()}")
    print(f"Forge: {FORGE}")
    print(f"Foundry dir: {FOUNDRY_DIR}")
    print(f"Results dir: {RESULTS_DIR}")
    print(f"Total tests: {sum(len(t[2]) for t in TESTS)}")

    all_results = []
    total_pass = 0
    total_fail = 0

    for sol_file, contract, test_funcs, desc in TESTS:
        for tf in test_funcs:
            r = run_test(sol_file, contract, tf, desc)
            all_results.append(r)
            if r["status"] == "PASS":
                total_pass += 1
            else:
                total_fail += 1

    # 保存汇总 JSON
    summary = {
        "timestamp": datetime.now().isoformat(),
        "total": len(all_results),
        "passed": total_pass,
        "failed": total_fail,
        "results": all_results,
    }
    with open(os.path.join(RESULTS_DIR, "summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)

    # 打印汇总表
    print(f"\n{'='*70}")
    print(f"EXPERIMENT SUMMARY")
    print(f"{'='*70}")
    print(f"Total: {len(all_results)} | Pass: {total_pass} | Fail: {total_fail}")
    print(f"{'='*70}")
    print(f"{'File':<40} {'Test':<30} {'Status':<8} {'Time':<8}")
    print(f"{'-'*40} {'-'*30} {'-'*8} {'-'*8}")
    for r in all_results:
        fname = r['file'][:38]
        tname = r['test'][:28]
        print(f"{fname:<40} {tname:<30} {r['status']:<8} {r['time_s']:<8.1f}")
    print(f"{'='*70}")

    # 保存文本汇总
    with open(os.path.join(RESULTS_DIR, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(f"ERC4626 Donation Attack Experiment Summary\n")
        f.write(f"Timestamp: {datetime.now().isoformat()}\n")
        f.write(f"Total: {len(all_results)} | Pass: {total_pass} | Fail: {total_fail}\n\n")
        for r in all_results:
            f.write(f"[{r['status']}] {r['file']}::{r['test']} ({r['time_s']}s)\n")
            findings = extract_key_findings(r.get('output_preview', ''))
            for label, matches in findings:
                f.write(f"  {label}: {matches[:3]}\n")
            f.write("\n")

    return total_fail == 0


if __name__ == "__main__":
    ok = main()
    sys.exit(0 if ok else 1)

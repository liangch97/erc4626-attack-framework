#!/usr/bin/env python3
"""批量运行7个新合约的forge测试，收集结果"""
import subprocess, os, re, time

FORGE = r"C:\Users\Administrator\.foundry\bin\forge.exe"
DEFI_DIR = r"d:\区块链\DeFiHackLabs"

cases = [
    "Case_08064a8e_22035016",
    "Case_4a7c6493_22035030",
    "Case_27ab448a_22284975",
    "Case_d42535cd_23265624",
    "Case_22b12110_22035027",
    "Case_39ea8e7f_22035022",
    "Case_2fdd3c0a_24131008",
]

env = os.environ.copy()
env["NO_PROXY"] = "127.0.0.1,localhost"
env["no_proxy"] = "127.0.0.1,localhost"

results = []

for i, case in enumerate(cases):
    path = f"src/test/2026-erc4626/generated/{case}.sol"
    print(f"\n{'='*60}")
    print(f"[{i+1}/7] {case}")
    print(f"{'='*60}")
    
    cmd = [FORGE, "test", "--match-path", path, "-vvv", "--no-match-test", "IGNORE"]
    
    try:
        r = subprocess.run(cmd, cwd=DEFI_DIR, capture_output=True, text=True, timeout=600, env=env)
        output = r.stdout + r.stderr
        
        passed = "[PASS]" in output
        
        # Extract key info
        debt_avail = None
        error_sel = None
        
        # Find totalDebtAvailable return value
        m = re.search(r'totalDebtAvailable\(\).*?\n.*?← \[Return\] (\d+)', output)
        if m:
            debt_avail = int(m.group(1))
        
        # Find error selector
        m = re.search(r'custom error (0x[0-9a-f]+)', output)
        if m:
            error_sel = m.group(1)
        
        # Find borrow amount in call
        m = re.search(r'::borrow\((\d+)', output)
        if m:
            borrow_amt = int(m.group(1))
        else:
            borrow_amt = None
        
        status = "PASS" if passed else f"FAIL ({error_sel or 'unknown'})"
        
        print(f"  Status: {status}")
        print(f"  totalDebtAvailable: {debt_avail}")
        print(f"  borrow amount: {borrow_amt}")
        
        results.append({
            "case": case,
            "status": status,
            "debt_avail": debt_avail,
            "borrow_amt": borrow_amt,
            "error": error_sel,
        })
        
    except subprocess.TimeoutExpired:
        print(f"  TIMEOUT!")
        results.append({"case": case, "status": "TIMEOUT", "debt_avail": None, "borrow_amt": None, "error": None})
    except Exception as e:
        print(f"  Error: {e}")
        results.append({"case": case, "status": f"ERROR: {e}", "debt_avail": None, "borrow_amt": None, "error": None})

print(f"\n\n{'='*70}")
print("SUMMARY")
print(f"{'='*70}")
print(f"{'Case':<35} {'Status':<25} {'DebtAvail':<15}")
print("-" * 70)
for r in results:
    da = str(r['debt_avail']) if r['debt_avail'] is not None else 'N/A'
    print(f"{r['case']:<35} {r['status']:<25} {da:<15}")

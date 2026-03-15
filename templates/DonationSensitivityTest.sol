// SPDX-License-Identifier: UNLICENSED
pragma solidity ^0.8.15;

// ===========================================================================
// ERC-4626 Vault Donation Sensitivity Test (通用模板)
//
// 测试逻辑：
//   1. Fork 到指定区块
//   2. 读取 vault 的 totalAssets() 和 asset.balanceOf(vault)
//   3. 如果 totalAssets == balanceOf → donation 敏感（balance-based）
//   4. 模拟 donation：deal asset 并 transfer 到 vault
//   5. 验证 totalAssets 是否跟随增长
//   6. 检查 vault 在该区块是否为空（totalSupply == 0）
//
// 占位符：
//   {{CONTRACT_NAME}}     - 合约名
//   {{VAULT_ADDRESS}}     - ERC4626 vault 地址
//   {{FORK_BLOCK_NUMBER}} - Fork 区块号
// ===========================================================================

import "../../basetest.sol";

interface IERC20Min {
    function approve(address, uint256) external returns (bool);
    function balanceOf(address) external view returns (uint256);
    function transfer(address, uint256) external returns (bool);
    function decimals() external view returns (uint8);
    function totalSupply() external view returns (uint256);
}

interface IERC4626Min is IERC20Min {
    function convertToAssets(uint256 shares) external view returns (uint256);
    function convertToShares(uint256 assets) external view returns (uint256);
    function totalAssets() external view returns (uint256);
    function asset() external view returns (address);
    function deposit(uint256 assets, address receiver) external returns (uint256);
}

contract {{CONTRACT_NAME}} is BaseTestWithBalanceLog {
    IERC4626Min constant vault = IERC4626Min({{VAULT_ADDRESS}});
    uint256 constant FORK_BLOCK = {{FORK_BLOCK_NUMBER}};

    function setUp() public {
        uint256 blk = vm.envOr("FORK_BLOCK", FORK_BLOCK);
        vm.createSelectFork("mainnet", blk);
    }

    function testDonationSensitivity() public {
        // === Step 1: 基础状态 ===
        address assetAddr = vault.asset();
        IERC20Min asset = IERC20Min(assetAddr);

        uint256 totalAssetsBefore = vault.totalAssets();
        uint256 balanceBefore = asset.balanceOf(address(vault));
        uint256 totalSupply = vault.totalSupply();
        uint8 assetDecimals = asset.decimals();

        emit log_named_address("Vault", address(vault));
        emit log_named_address("Asset", assetAddr);
        emit log_named_uint("Block", FORK_BLOCK);
        emit log_named_uint("Asset decimals", uint256(assetDecimals));
        emit log_named_uint("totalAssets BEFORE", totalAssetsBefore);
        emit log_named_uint("balanceOf(vault) BEFORE", balanceBefore);
        emit log_named_uint("totalSupply (shares)", totalSupply);

        // === Step 2: 判断 balance-based vs internal accounting ===
        bool isBalanceBased;
        if (totalAssetsBefore == 0 && balanceBefore == 0) {
            // vault 完全为空，需要通过 donation 测试
            isBalanceBased = true; // 假设，后续验证
            emit log_string("Vault is EMPTY - will test via donation");
        } else if (totalAssetsBefore == balanceBefore) {
            isBalanceBased = true;
            emit log_string("totalAssets == balanceOf => BALANCE-BASED (donation sensitive)");
        } else {
            // 差异可能来自利息/rebasing，检查差异比例
            uint256 diff;
            if (totalAssetsBefore > balanceBefore) {
                diff = totalAssetsBefore - balanceBefore;
            } else {
                diff = balanceBefore - totalAssetsBefore;
            }
            // 如果差异 < 1%，仍认为是 balance-based（可能有少量利息差异）
            if (totalAssetsBefore > 0 && diff * 100 / totalAssetsBefore < 1) {
                isBalanceBased = true;
                emit log_string("totalAssets ~= balanceOf (< 1% diff) => likely BALANCE-BASED");
            } else {
                isBalanceBased = false;
                emit log_string("totalAssets != balanceOf => INTERNAL ACCOUNTING (donation immune)");
            }
        }

        // === Step 3: 模拟 donation 并验证 ===
        // 确定 donation 金额：vault totalAssets 的 10%，最少 1000 个 token
        uint256 donationAmount;
        if (totalAssetsBefore > 0) {
            donationAmount = totalAssetsBefore / 10;
        } else {
            donationAmount = 1000 * (10 ** uint256(assetDecimals));
        }
        if (donationAmount == 0) {
            donationAmount = 10 ** uint256(assetDecimals);
        }

        // 策略：直接给 vault 地址 deal 资产（增加 vault 的 asset balance）
        uint256 vaultBalanceBefore = asset.balanceOf(address(vault));
        uint256 targetBalance = vaultBalanceBefore + donationAmount;

        // 尝试 deal() — 对标准 ERC20 有效，但 proxy/rebasing token 可能 revert
        bool dealSuccess = _tryDeal(assetAddr, address(vault), targetBalance);

        if (dealSuccess) {
            uint256 vaultBalanceAfterDeal = asset.balanceOf(address(vault));
            emit log_named_uint("Donated via deal()", vaultBalanceAfterDeal - vaultBalanceBefore);
        } else {
            emit log_string("deal() failed, trying vm.store fallback for proxy/rebasing token...");

            // Fallback: 计算 vault 在 asset 合约中的 balanceOf mapping slot
            // 标准 ERC20 mapping: balances[address] 在 slot = keccak256(abi.encode(address, uint256(slot_index)))
            // 常见 slot index: 0, 1, 2, 3, 51 (OpenZeppelin ERC20Upgradeable)
            uint256[] memory commonSlots = new uint256[](6);
            commonSlots[0] = 0;
            commonSlots[1] = 1;
            commonSlots[2] = 2;
            commonSlots[3] = 3;
            commonSlots[4] = 51; // OpenZeppelin ERC20Upgradeable
            commonSlots[5] = 101;

            bool found = false;
            bytes32 balanceSlot;

            for (uint i = 0; i < commonSlots.length; i++) {
                bytes32 candidateSlot = keccak256(abi.encode(address(vault), commonSlots[i]));
                uint256 storedVal = uint256(vm.load(assetAddr, candidateSlot));
                // 验证：存储的值应该等于当前 balanceOf
                if (storedVal == vaultBalanceBefore && vaultBalanceBefore > 0) {
                    balanceSlot = candidateSlot;
                    found = true;
                    emit log_named_uint("Found balance at mapping slot index", commonSlots[i]);
                    break;
                }
            }

            if (!found) {
                // 第二种策略：用 vm.record + vm.accesses 查找，但过滤掉已知的 proxy/admin slots
                vm.record();
                asset.balanceOf(address(vault));
                (bytes32[] memory reads,) = vm.accesses(assetAddr);

                // EIP-1967 已知 slots —— 必须跳过
                bytes32 IMPL_SLOT = 0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc;
                bytes32 ADMIN_SLOT = 0xb53127684a568b3173ae13b9f8a6016e243e63b6e8ee1178d6a717850b5d6103;
                bytes32 BEACON_SLOT = 0xa3f0ad74e5423aebfd80d3ef4346578335a9a72aeaee59ff6cb3582b35133d50;

                for (uint i = 0; i < reads.length; i++) {
                    if (reads[i] == IMPL_SLOT || reads[i] == ADMIN_SLOT || reads[i] == BEACON_SLOT) continue;
                    uint256 slotVal = uint256(reads[i]);
                    if (slotVal < 10) continue; // 跳过低位 slots

                    // 验证候选 slot 的值是否等于当前 balance
                    uint256 storedVal = uint256(vm.load(assetAddr, reads[i]));
                    if (storedVal == vaultBalanceBefore && vaultBalanceBefore > 0) {
                        balanceSlot = reads[i];
                        found = true;
                        break;
                    }
                }

                // 第三种策略：rebasing token — shares != balance
                // balanceOf 读取的 slot 存的是 shares（不等于 balance），直接增加该 shares 值
                if (!found && vaultBalanceBefore > 0) {
                    for (uint i = 0; i < reads.length; i++) {
                        if (reads[i] == IMPL_SLOT || reads[i] == ADMIN_SLOT || reads[i] == BEACON_SLOT) continue;
                        uint256 slotVal = uint256(reads[i]);
                        if (slotVal < 10) continue;

                        uint256 storedVal = uint256(vm.load(assetAddr, reads[i]));
                        // shares 通常与 balance 同数量级，且 > 0
                        if (storedVal > 0 && storedVal < vaultBalanceBefore * 10) {
                            balanceSlot = reads[i];
                            found = true;
                            emit log_string("Using rebasing token shares slot");
                            break;
                        }
                    }
                }
            }

            if (found) {
                uint256 currentVal = uint256(vm.load(assetAddr, balanceSlot));
                uint256 increase = currentVal > 0 ? currentVal / 10 : donationAmount;
                vm.store(assetAddr, balanceSlot, bytes32(currentVal + increase));
                emit log_named_uint("Donated via vm.store", increase);
            } else {
                emit log_string("SKIP: cannot locate balance storage slot for proxy token");
                // 标记为无法测试，但不 revert — assertTrue 会处理
            }
        }

        // === Step 4: 检查 donation 效果 ===
        uint256 totalAssetsAfter = vault.totalAssets();
        uint256 balanceAfter = asset.balanceOf(address(vault));

        emit log_named_uint("totalAssets AFTER", totalAssetsAfter);
        emit log_named_uint("balanceOf(vault) AFTER", balanceAfter);

        bool donationEffective = totalAssetsAfter > totalAssetsBefore;
        emit log_named_uint("totalAssets increased", donationEffective ? 1 : 0);

        if (donationEffective) {
            uint256 inflation;
            if (totalAssetsBefore > 0) {
                inflation = (totalAssetsAfter * 10000) / totalAssetsBefore;
            } else {
                inflation = 99999; // vault was empty, infinite inflation
            }
            emit log_named_uint("Inflation (bps, 10000=1x)", inflation);
        }

        // === Step 5: 综合结论 ===
        if (donationEffective && totalSupply == 0) {
            emit log_string("RESULT: CRITICAL - Vault is EMPTY and DONATION-SENSITIVE");
            emit log_string("STATUS: ATTACKABLE at this block");
        } else if (donationEffective && totalSupply > 0) {
            emit log_string("RESULT: DONATION-SENSITIVE but vault has shares");
            emit log_string("STATUS: Need empty vault window for full attack");
        } else {
            emit log_string("RESULT: DONATION-IMMUNE (internal accounting)");
            emit log_string("STATUS: NOT_VULNERABLE");
        }

        // 断言：donation 有效则 PASS，无效则 FAIL
        // 对于寻找最小区块：我们关注 donation 是否有效
        assertTrue(donationEffective, "Donation did not affect totalAssets");
    }

    /// @dev 用 try/catch 包装 deal()，对 proxy/rebasing token 返回 false 而非 revert
    function _tryDeal(address token, address to, uint256 amount) internal returns (bool) {
        try this._doDeal(token, to, amount) {
            return true;
        } catch {
            return false;
        }
    }

    /// @dev 外部函数以便 try/catch 能捕获 deal() 的 revert
    function _doDeal(address token, address to, uint256 amount) external {
        require(msg.sender == address(this), "only self");
        deal(token, to, amount);
    }
}

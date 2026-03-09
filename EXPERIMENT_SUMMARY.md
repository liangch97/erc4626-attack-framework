# ERC4626 Vault Donation/Inflation Attack 实验总结

> 最后更新：2026-03-09

## 1. 项目概述

本项目对 **11 个可疑 ERC4626 Vault 合约**进行了 Donation/Inflation Attack（捐赠/通胀攻击）的可行性验证。攻击原理：在 Vault 的 `totalSupply = 0` 时，通过闪电贷获取大量资产 → 捐赠（donation）到 Vault → 操纵 `convertToAssets()` 汇率 → 从借贷协议中超额借款获利。

## 2. 最终结果

| 分类 | 数量 | 占比 | 说明 |
|------|------|------|------|
| ✅ **PASS**（漏洞验证成功） | 3 | 27% | 合计损失 ~32,386,024 USDC |
| 🛡️ **NOT VULNERABLE**（不可攻击） | 8 | 73% | 攻击前提条件不满足 |
| ❌ **FAIL**（待修复） | 0 | 0% | — |

## 3. 成功验证案例（3 个）

| Pair 地址 | 区块 | 损失 (USDC) |
|-----------|------|-------------|
| `0x57e69699` | 22,497,642 | 9,792,705 |
| `0xf4a6113f` | 22,497,642 | 9,792,705 |
| `0x3a7d9430` | 22,784,988 | 12,800,614 |

攻击均发生于 **2024 年 4 月**，使用 MorphoBlue 闪电贷 + Curve USDC→crvUSD 交换 + Resupply Protocol 借贷。

## 4. 不可攻击案例分析（8 个）

### 4.1 Fraxlend Vault（5 个）

底层资产为 **frxUSD (CACD token)**，Vault 使用利率模型（`getNewRate()`）计算 `totalAssets()`，不读取 Vault 余额。向 Vault 转入 frxUSD 不会改变 `totalAssets()`，**donation 攻击在架构层面完全无效**。

### 4.2 crvUSD Vault — 深度链上分析（3 个）

原为 FAIL 案例，经 `forge test -vvvv` 追踪 + 链上 RPC 查询 + **storage slot 二分搜索**三重验证，确认不可攻击：

#### Case 9（`0xD210Bc75`，优先级最高）

- **错误**：`0x1abfe8a7`（`!regPair`），`totalDebtAvailable()` 返回 0
- **根因**：pair 的 **storage slot 10**（`borrowLimit`）在所有历史区块均为 **0**
- **结论**：pair 从未被配置借贷上限，`borrow()` 必然 revert

#### Case 8（`0xC5184ccc`）

- **错误**：`0xe99b9f61`（`setUp` 阶段 `addPair` 失败）
- **根因**：双重阻断
  1. `addPair` 在 block 22,034,916 revert
  2. `borrowLimit` 在 block 22,087,804 才被设置，但此时 vault 已有 **1,029,190 crvUSD** 存款
- **结论**：借贷上限设置时 vault 已非空，inflation 攻击不可行

#### Case 3（`0x5254d4f5`）

- **错误**：`0xed27783c`（borrow 限额检查失败）
- **根因**：pair 部署区块 = vault 首笔存款区块（block 23,336,113）
  - vault 空窗期（~block 23,200,000 起）→ pair 尚未部署
  - pair 部署时 → vault 已有 `totalAssets=1e18, totalSupply=1000e18`
- **结论**：不存在「pair 存在 AND vault 为空」的区块，无攻击窗口

## 5. 关键技术发现

1. **Storage slot 10 = `borrowLimit`**：Resupply Protocol pair 合约的核心配置参数，决定了可借出的最大 reUSD 数量。通过与成功案例（pair5, borrowLimit=25M）对比，发现失败案例的 slot 10 值不满足攻击条件。

2. **攻击三要素缺一不可**：
   - Vault `totalSupply = 0`（空 vault）
   - Pair 已部署且 `addPair` 注册成功
   - `borrowLimit > 0`（借贷上限已配置）

3. **Fraxlend 架构天然免疫**：利率模型驱动的 `totalAssets()` 不受 donation 影响，是**架构层面的防御**。

## 6. 文件清单

| 文件 | 说明 |
|------|------|
| `calldata_bridge/WORK_REPORT.md` | 详细工作报告（含完整分析过程） |
| `calldata_bridge/final_report.csv` | 11 个案例的结构化数据 |
| `calldata_bridge/FAIL_CASES_DEBUG_PROMPT.md` | 调试任务描述文档 |
| `DeFiHackLabs/src/test/2026-erc4626/` | Solidity 测试文件 |
| `deep_fix_analysis.py` | Storage slot 对比分析脚本 |
| `find_deploy_blocks.py` | 部署区块二分搜索脚本 |
| `case8_window.py` | Case 8 borrowLimit 窗口分析脚本 |
| `final_check.py` | Case 9 borrowLimit 历史验证脚本 |

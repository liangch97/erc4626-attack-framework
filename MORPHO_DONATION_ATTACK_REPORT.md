# Morpho Blue wUSDM Donation Attack 分析报告

> 日期：2026-03-14
> PoC 文件：`DeFiHackLabs/src/test/2025-03/MorphoDonationAttack.sol`

## 1. 攻击概述

与 Venus zkSync wUSDM 攻击（2025-02）相同的攻击向量，应用于 Ethereum Mainnet 的 Morpho Blue 借贷协议。

**攻击原理**：通过向 ERC-4626 vault（wUSDM）直接转入底层资产（USDM），膨胀 `convertToAssets()` 汇率，从而操纵 Morpho Blue 预言机价格，实现超额借贷。

## 2. 预言机逆向分析

### 2.1 Oracle 0x38Ed（wUSDM/USDC 市场）— 可操纵

| 字段 | 值 | 说明 |
|------|-----|------|
| `BASE_VAULT` | `0x57F5...` (wUSDM) | 直接读取 vault 汇率 |
| `BASE_FEED_1` | `0x0000...0000` | **无 Chainlink 喂价** |
| `BASE_FEED_2` | `0x0000...0000` | **无 Chainlink 喂价** |
| `QUOTE_FEED_1` | `0x0000...0000` | **无 Chainlink 喂价** |
| `QUOTE_FEED_2` | `0x0000...0000` | **无 Chainlink 喂价** |
| `SCALE_FACTOR` | `1e6` | USDC 精度 |

**价格公式**：`price() = wUSDM.convertToAssets(1e18) * 1e6`

无任何 Chainlink / TWAP / CAPO 上限保护，donation 直接等比例膨胀 `price()`。

### 2.2 Oracle 0x72Ee（wUSDL/cbBTC 市场）— 不可利用

- wUSDL 位于 `QUOTE_VAULT`（分母），donation 会**降低**价格而非升高
- `price()` 调用 `convertToAssets()` 时遇到 division-by-zero revert
- **结论：不可攻击**

## 3. PoC 测试结果（PASS）

### 3.1 Donation 敏感性验证

wUSDM vault 的 `totalAssets()` 通过 delegatecall 链路最终读取 `USDM.balanceOf(wUSDM)`：

```
wUSDM.totalAssets()
  → delegatecall 0x616B7...convertToAssets()
    → USDM.balanceOf(wUSDM)
      → delegatecall 0x7f2f9...balanceOf()
        → return shares[wUSDM] * rewardMultiplier / 1e18
```

**Vault 是 balance-based → donation 敏感**

### 3.2 Oracle 操纵数据

| 指标 | 攻击前 | 攻击后 | 变化 |
|------|--------|--------|------|
| `ORACLE.price()` | 1.0817e24 | 2.1633e24 | **2x** |
| `convertToAssets(1e18)` | 1.0817e18 | 2.1633e18 | **2x** |
| `wUSDM totalAssets` | 1,173,203 USDM | 2,346,406 USDM | **2x** |
| `wUSDM totalSupply` | 1,084,619 shares | 不变 | — |
| **价格膨胀** | — | — | **20,000 bps (2.0x)** |

### 3.3 市场流动性

| 指标 | 值 |
|------|-----|
| Market ID | `0x6cd5fd13...` |
| Loan Token | USDC |
| Collateral Token | wUSDM |
| LLTV | 96.5% |
| Total Supply | 104,869 USDC (~$0.10) |
| Total Borrow | 92,819 USDC (~$0.09) |
| 可用流动性 | **12,050 USDC (~$0.01)** |

## 4. 攻击可行性评估

### 4.1 技术可行性：✅ 完全可行

攻击链路每一环均已验证：

1. ✅ wUSDM 是 balance-based vault → donation 直接膨胀 totalAssets
2. ✅ Oracle 0x38Ed 无任何价格保护 → price() 等比例跟随 vault 汇率
3. ✅ Morpho Blue 支持 flashLoan → 攻击者可在单笔交易内完成
4. ✅ 96.5% LLTV → 极高杠杆倍率放大攻击效果

### 4.2 经济可行性：❌ 当前不可行（LATENT）

- 市场流动性仅 $0.01 USDC，即使价格膨胀 100x 也无法获利
- **风险等级：LATENT（潜伏）**
- 一旦市场 TVL 增长至有意义水平（> $10,000），攻击立即变为可行

### 4.3 完整攻击路径（假设市场有流动性）

```
1. Morpho flashLoan(USDC, large_amount)
2. Swap USDC → USDM（通过 Curve/DEX）
3. USDM.transfer(wUSDM_vault, donation_amount)  ← 膨胀汇率
4. Swap 少量 USDM → wUSDM（deposit）
5. Morpho.supplyCollateral(wUSDM, tiny_amount)
6. Morpho.borrow(USDC, inflated_amount)  ← 超额借贷
7. Repay flashLoan, keep profit
```

## 5. 与 Venus zkSync 攻击对比

| 维度 | Venus zkSync | Morpho Blue (本案) |
|------|-------------|-------------------|
| 链 | zkSync Era | Ethereum Mainnet |
| Vault | wUSDM | wUSDM (同) |
| Oracle 保护 | 无 | 无 |
| 攻击方式 | 自清算 35 次 | 单次超额借贷 |
| 利润 | ~86.72 WETH | 潜伏（流动性不足） |
| 状态 | 已发生 | LATENT |

## 6. 技术细节

### 6.1 USDM Rebasing 机制

USDM 是 rebasing 代理合约：
- `balanceOf(addr) = shares[addr] * rewardMultiplier / 1e18`
- Storage layout:
  - Slot 0x164 (356): `rewardMultiplier`
  - Slot 0x5a01...967: 地址 shares 映射

Foundry 的 `deal()` 无法正确操作 rebasing token，PoC 使用 `vm.record()` + `vm.accesses()` + `vm.store()` 直接操纵存储槽。

### 6.2 Morpho Blue 市场参数

```solidity
MarketParams({
    loanToken: 0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48,  // USDC
    collateralToken: 0x57F5E098CaD7A3D1Eed53991D4d66C45C9AF7812,  // wUSDM
    oracle: 0x38Ed40ab78D2D00467CedD5B1631C040768cdfCa,  // 无保护 oracle
    irm: 0x870aC11D48B15DB9a138Cf899d20F13F79Ba00BC,
    lltv: 965000000000000000  // 96.5%
})
```

## 7. 建议

1. **对 Oracle 0x38Ed 添加 TWAP/CAPO 保护**：在 `price()` 中添加价格上限或时间加权平均，防止单笔交易操纵
2. **监控市场 TVL 变化**：一旦 wUSDM/USDC 市场流动性增长，攻击风险立即升级
3. **考虑降低 LLTV**：96.5% 的 LLTV 给攻击者极高的杠杆倍率

## 8. 文件清单

| 文件 | 说明 |
|------|------|
| `DeFiHackLabs/src/test/2025-03/MorphoDonationAttack.sol` | Morpho donation PoC（已通过） |
| `calldata_bridge/templates/MorphoDonationAttack.sol` | 模板备份 |
| `suspicious contracts.csv` | 24 行可疑合约清单 |
| `DeFiHackLabs/src/test/2025-02/Venus_ZKSync_exp.sol` | Venus zkSync 攻击 PoC（参考） |

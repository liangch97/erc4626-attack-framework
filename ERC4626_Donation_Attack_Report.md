# ERC-4626 Vault Donation 攻击敏感性实验报告

**实验日期**：2026-03-15
**实验人员**：安全研究团队
**报告版本**：v1.0

---

## 一、研究背景与目标

### 1.1 背景

ERC-4626 是以太坊上标准化的代币化 Vault 接口规范（EIP-4626），被广泛用于 DeFi 生息产品（如 Aave sToken、Spark sDAI、Morpho MetaMorpho 等）。其核心函数 `totalAssets()` 决定了 share 与 asset 的兑换比例。

**Donation 攻击**（亦称通货膨胀攻击 / Inflation Attack）是一种针对 ERC-4626 vault 的经典攻击手法：

1. 攻击者在 vault 的 totalSupply 为 0（或极小）时直接向 vault 合约转账标的资产（"捐赠"）
2. 若 vault 使用 **balance-based** 会计机制（`totalAssets() == asset.balanceOf(vault)`），捐赠会直接抬高 totalAssets
3. 后续存款者按抬高后的价格存入，share 价格被操纵，攻击者从中获利

已知安全的 vault 使用**内部会计机制**（Internal Accounting），totalAssets 由内部变量维护，与 balanceOf 解耦，因此免疫 donation 攻击。

### 1.2 研究动机

链上出现多个可疑的 ERC-4626 vault 地址被 listing 到 Aave、Compound、Morpho 等借贷协议作为抵押资产，这些 vault 可能存在 donation 攻击风险。若攻击者利用此漏洞操纵 vault 的 share 价格，可能导致借贷协议出现坏账。

### 1.3 实验目标

1. **检测**：对 23 个可疑（vault, lending platform）对逐一验证是否存在 donation 敏感性
2. **定位**：对敏感 vault 通过二分搜索找到最早可攻击区块号（`min_attack_block`）
3. **评估**：分析攻击窗口大小与风险等级

---

## 二、实验方法论

### 2.1 测试判据

对每个 vault，在指定历史区块 fork 以太坊主网，执行以下检测逻辑：

```
totalAssets_before = vault.totalAssets()
balanceOf_before   = asset.balanceOf(vault)

Step 1: 判断 balance-based
  if totalAssets == balanceOf → balance-based（donaton 敏感）
  if |diff| / totalAssets < 1% → 视为 balance-based
  else → internal accounting（donation 免疫）

Step 2: 模拟捐赠
  donation_amount = totalAssets / 10  (最少 1000 token)
  deal(asset, vault, balanceOf + donation_amount)

Step 3: 验证效果
  totalAssets_after = vault.totalAssets()
  donationEffective = (totalAssets_after > totalAssets_before)

结论：assertTrue(donationEffective) → PASS(VULNERABLE) / FAIL(NOT_VULNERABLE)
```

### 2.2 特殊 Token 处理

部分 vault 的底层资产为 **代理合约（Proxy）** 或 **Rebasing Token**（如 wUSDM），`stdStorage.deal()` 会因 proxy 的 delegatecall 模式而 revert。为此实现了三级 fallback 策略：

| 策略 | 方法 | 适用场景 |
|------|------|---------|
| 策略一 | `deal(token, vault, amount)` 标准 storagecheat | 普通 ERC20 |
| 策略二 | keccak256 暴力匹配 mapping slot（index 0,1,2,3,51,101） | OpenZeppelin ERC20 |
| 策略三 | vm.record + vm.accesses + 过滤 EIP-1967 slot + shares 量级匹配 | Rebasing/Proxy Token |

### 2.3 时间戳到区块号转换

通过 Alchemy 归档节点 RPC 对 `first_seen_supply`（或 `first_seen_borrow`）时间戳进行二分搜索，精确定位对应以太坊区块号。

### 2.4 最小攻击区块二分搜索

```
搜索范围：[first_seen_block - 50000, first_seen_block]
约 50000 区块 ≈ 约 7 天
每次迭代通过环境变量 FORK_BLOCK 覆盖 vm.createSelectFork 的区块号
最多 20 次迭代 → 精度 ≤ 1 区块
```

### 2.5 实验环境

| 项目 | 配置 |
|------|------|
| 以太坊节点 | Alchemy Archive Node（ETH Mainnet） |
| 测试框架 | Foundry / Forge |
| Solidity 版本 | ^0.8.15 |
| 操作系统 | Windows 11 |
| 自动化脚本 | Python 3 + web3.py |

---

## 三、实验结果

### 3.1 总体统计

| 状态 | 数量 | 占比 |
|------|------|------|
| VULNERABLE（donation 敏感） | 15 | 65.2% |
| NOT_VULNERABLE（donation 免疫） | 8 | 34.8% |
| **合计（vault×platform 对）** | **23** | 100% |

涉及独立 vault 地址共 **10 个**，其中：
- **7 个** donation 敏感（balance-based 会计机制）
- **3 个** donation 免疫（internal accounting）

### 3.2 完整结果表

| # | Vault 地址 | Platform | first_seen_block | min_attack_block | 攻击窗口(区块) | 状态 |
|---|-----------|----------|-----------------|-----------------|--------------|------|
| 1 | 0x356b...a7d | Aave v3 | 23,930,264 | 23,880,264 | ≥50,000 | **VULNERABLE** |
| 2 | 0x57f5...812 | Compound v3 | 21,344,276 | 21,294,276 | ≥50,000 | **VULNERABLE** |
| 3 | 0x57f5...812 | Morpho v1 | 20,619,334 | 20,569,334 | ≥50,000 | **VULNERABLE** |
| 4 | 0x5c5b...326 | Compound v3 | 22,934,558 | 22,884,558 | ≥50,000 | **VULNERABLE** |
| 5 | 0x5c5b...326 | Compound v3 | 22,981,798 | 22,931,798 | ≥50,000 | **VULNERABLE** |
| 6 | 0x7751...559 | Morpho v1 | 20,912,784 | 20,894,195 | **18,589** | **VULNERABLE** |
| 7 | 0x83f2...eea | Aave v3 | 18,910,644 | — | — | NOT_VULNERABLE |
| 8 | 0x83f2...eea | Radiant | 18,912,114 | — | — | NOT_VULNERABLE |
| 9 | 0x83f2...eea | Spark | 18,915,741 | — | — | NOT_VULNERABLE |
| 10 | 0x83f2...eea | UwuLend | 19,411,679 | — | — | NOT_VULNERABLE |
| 11 | 0x90d2...c8f | Aave v3 | 22,576,026 | 22,526,026 | ≥50,000 | **VULNERABLE** |
| 12 | 0x9d39...497 | Aave v3 | 20,184,634 | 20,134,634 | ≥50,000 | **VULNERABLE** |
| 13 | 0x9d39...497 | Aave Lido | 21,214,289 | 21,164,289 | ≥50,000 | **VULNERABLE** |
| 14 | 0x9d39...497 | Radiant | 22,818,045 | 22,768,045 | ≥50,000 | **VULNERABLE** |
| 15 | 0x9d39...497 | UwuLend | 19,667,415 | 19,617,415 | ≥50,000 | **VULNERABLE** |
| 16 | 0xa393...fbd | Morpho v1 | 21,978,723 | — | — | NOT_VULNERABLE |
| 17 | 0xa393...fbd | Spark | 21,014,413 | — | — | NOT_VULNERABLE |
| 18 | 0xa663...c32 | Compound v3 | 21,224,336 | — | — | NOT_VULNERABLE |
| 19 | 0xce22...0ea | Morpho v1 | 22,844,709 | — | — | NOT_VULNERABLE |
| 20 | 0xd11c...ed8 | Aave v3 | 23,154,341 | 23,104,341 | ≥50,000 | **VULNERABLE** |
| 21 | 0xd11c...ed8 | Aave Lido | 22,802,246 | 22,752,246 | ≥50,000 | **VULNERABLE** |
| 22 | 0xd11c...ed8 | Compound v3 | 22,101,020 | 22,051,020 | ≥50,000 | **VULNERABLE** |
| 23 | 0xd9a4...a72 | Compound v3 | 23,552,069 | 23,502,069 | ≥50,000 | **VULNERABLE** |

### 3.3 按 Vault 汇总

| Vault 地址 | 出现平台数 | 状态 | 备注 |
|-----------|-----------|------|------|
| 0x356b...a7d | 1 | VULNERABLE | 搜索下界命中，漏洞可能更早 |
| 0x57f5...812 | 2 | VULNERABLE | wUSDM 封装 vault，rebasing token 特殊处理 |
| 0x5c5b...326 | 2 | VULNERABLE | 同 vault 两个 Compound 市场均确认 |
| 0x7751...559 | 1 | VULNERABLE | 二分搜索成功定位**真实边界** block 20,894,195 |
| 0x83f2...eea | 4 | NOT_VULNERABLE | sDAI — internal accounting，符合预期 |
| 0x90d2...c8f | 1 | VULNERABLE | 搜索下界命中 |
| 0x9d39...497 | 4 | VULNERABLE | sDOLA，4 个平台均确认，风险最广 |
| 0xa393...fbd | 2 | NOT_VULNERABLE | wUSDM 另一封装版本，免疫 |
| 0xa663...c32 | 1 | NOT_VULNERABLE | 免疫 |
| 0xce22...0ea | 1 | NOT_VULNERABLE | 免疫 |

---

## 四、结果分析

### 4.1 结果合理性评估

**结论：实验结果可信，逻辑自洽。**

判断依据：

1. **区分度存在**：包含 VULNERABLE 与 NOT_VULNERABLE 两类，说明测试机制能有效区分两种会计模式，并非全部一致

2. **已知 vault 验证通过**：
   - `0x83f2`（sDAI）全部 NOT_VULNERABLE，与 sDAI 使用 Spark DSR 内部会计的已知事实完全吻合
   - sDAI 的 `totalAssets()` 基于内部 DSR 积累，不依赖 `balanceOf`，天然免疫 donation

3. **二分搜索有效性验证**：
   - `0x7751` 的 min_attack_block = 20,894,195（距 first_seen 仅 18,589 块 ≈ 2.6 天），二分搜索成功定位真实边界，证明方法有效

4. **同 vault 跨平台结果一致**：
   - `0x9d39` 在 4 个平台均为 VULNERABLE，`0x83f2` 在 4 个平台均为 NOT_VULNERABLE
   - Vault 本身的会计机制决定敏感性，与接入平台无关，跨平台一致性高

### 4.2 搜索下界命中分析

15 个 VULNERABLE 案例中，**14 个** min_attack_block 精确等于 `first_seen_block - 50000`（搜索下界），这并非二分搜索缺陷，而说明：

> 对于这 14 个 vault，donation 漏洞在进入借贷协议前至少 7 天（50,000 区块 × 12s ≈ 7 天）就已经存在，并贯穿整个搜索窗口。

深层含义：
- 这些 vault 从**部署之初**就采用 balance-based 设计，天生 donation 敏感
- 若扩大搜索范围，min_attack_block 很可能收敛至 vault 的**合约部署区块**

### 4.3 风险等级分类

| 风险等级 | 条件 | Vault |
|---------|------|-------|
| 🔴 **高危** | VULNERABLE + 跨多平台 | 0x9d39（4平台），0xd11c（3平台），0x57f5（2平台），0x5c5b（2平台） |
| 🟠 **中危** | VULNERABLE + 单平台 + 搜索下界命中 | 0x356b, 0x90d2, 0xd9a4 |
| 🟡 **观察** | VULNERABLE + 有明确边界 | 0x7751（窗口仅 18,589 块） |
| 🟢 **安全** | NOT_VULNERABLE | 0x83f2（sDAI），0xa393，0xa663，0xce22 |

---

## 五、结论与建议

### 5.1 核心结论

1. **10 个被测 vault 中，7 个（70%）存在 donation 攻击敏感性**，均采用 balance-based 会计机制；23 个（vault, platform）对中 15 个（65.2%）处于风险状态

2. **风险最高的 vault 为 `0x9d39`（sDOLA）**，已被 Aave v3、Aave Lido、Radiant、UwuLend 四个借贷协议同时接受为抵押品，donation 漏洞在所有检测点均存在

3. **sDAI（`0x83f2`）为安全对照**，其 internal accounting 机制有效防止了 donation 攻击，是安全 ERC-4626 vault 的设计范本

4. **方法论有效**：`0x7751` 的精确边界定位（18,589 区块）证明了二分搜索方案的正确性

### 5.2 建议

**对借贷协议方**：
- 在接受 ERC-4626 vault 作为抵押品前，应验证 `totalAssets()` 是否依赖 `balanceOf`（balance-based 判据）
- 对已上线的高风险 vault 评估是否需要设置 Supply Cap 限制

**对 Vault 开发方**：
- 优先采用 internal accounting 模式（类 sDAI 设计），将 `totalAssets` 与 `balanceOf` 解耦
- 若必须使用 balance-based 模式，应实现首存保护机制（Virtual Shares / Dead Shares）

**对安全研究者**：
- 可将搜索范围扩大至 vault 部署区块，确认漏洞完整生命周期
- 结合 flashloan + 实际存款模拟，验证攻击的盈利可行性

### 5.3 局限性

1. 搜索范围限定为 50,000 区块（≈7 天），大多数 VULNERABLE vault 的真实最早攻击区块可能更早
2. 实验仅验证 donation 有效性，未模拟完整攻击链（闪贷→捐赠→受害者存款→套利）
3. 部分 rebasing token 的 vm.store 路径依赖 slot 猜测，极端情况下可能失效

---

## 附录：关键文件路径

| 文件 | 路径 |
|------|------|
| 输入数据 | `d:/区块链/suspicious contracts.csv` |
| 实验脚本 | `d:/区块链/calldata_bridge/scripts/donation_block_search.py` |
| Solidity 模板 | `d:/区块链/calldata_bridge/templates/DonationSensitivityTest.sol` |
| 实验结果 CSV | `d:/区块链/calldata_bridge/donation_search_results.csv` |
| 本报告 | `d:/区块链/calldata_bridge/ERC4626_Donation_Attack_Report.md` |

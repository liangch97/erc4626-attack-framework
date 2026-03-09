# ERC4626 攻击验证 — 3个失败案例调试任务

## 任务概述

在 ERC4626 Vault Donation/Inflation Attack 的批量验证项目中，11 个案例里有 3 个 **crvUSD Vault 案例测试失败（FAIL）**。这 3 个案例的 Vault 底层资产都是 crvUSD（与 3 个已成功验证的案例相同类型），理论上攻击应该可行，但因为不同的 revert 错误导致 Forge 测试未通过。

**你的任务**：调查这 3 个失败案例的 revert 原因，修复测试使其通过（或给出无法攻击的技术论证）。

---

## 环境信息

### 基础设施
- **操作系统**：Windows 11，默认 Shell 为 PowerShell 7
- **工作目录**：`d:\区块链`
- **Forge 路径**：`C:\Users\Administrator\.foundry\bin\forge.exe`
- **以太坊 RPC**：`http://127.0.0.1:18545`（SSH 隧道转发到远程 Geth 归档节点）
- **Foundry 配置**：`DeFiHackLabs/foundry.toml` 中 `mainnet = "http://127.0.0.1:18545"`
- **FlashSwap 服务**：正在运行，监听 `127.0.0.1:3001`（inputdata 端口）和 `127.0.0.1:3002`（主服务端口）

### 运行测试命令
```powershell
cd d:\区块链\DeFiHackLabs
C:\Users\Administrator\.foundry\bin\forge.exe test --match-path "src/test/2026-erc4626/generated/Case_XXXX.sol" -vvvv
```

### 关键合约地址（以太坊主网）
| 名称 | 地址 |
|------|------|
| USDC | `0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48` |
| crvUSD | `0xf939E0A03FB07F59A73314E73794Be0E57ac1b4E` |
| sCrvUSD Vault | `0x0655977FEb2f289A4aB78af67BAB0d17aAb84367` |
| reUSD | `0x57aB1E0003F623289CD798B1824Be09a793e4Bec` |
| MorphoBlue | `0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb` |
| Curve USDC/crvUSD Pool | `0x4DEcE678ceceb27446b35C672dC7d61F30bAD69E` |
| Curve reUSD Pool | `0xc522A6606BBA746d7960404F22a3DB936B6F4F50` |
| Minter | `0x10101010E0C3171D894B71B3400668aF311e7D94` |
| Minter Owner | `0xc07e000044F95655c11fda4cD37F70A94d7e0a7d` |

---

## 成功案例参考（理解攻击流程）

### 攻击流程（5步）
1. **setUp**：`vm.createSelectFork("mainnet", blockNumber)` + 动态查询 vault/asset 地址 + `addPair`
2. **MorphoBlue 闪电贷**：借 4,000 USDC
3. **Curve swap**：USDC → crvUSD（通过 `0x4DEcE678...` 的 `exchange(0, 1, 4000e6, 0)`）
4. **Donation + Mint**：将 2,000 crvUSD 转给 `controller()`（= sCrvUSD vault），然后 `vault.mint(1)` 获得 1 share（价值被膨胀）
5. **借贷获利**：用膨胀的 share 作为抵押品，通过 `suspiciousContract.addCollateralVault()` + `borrow()` 借出 reUSD，再 swap 回 USDC

### 成功案例列表
| 案例 | 可疑合约 | Vault | Fork 区块 | 利润 |
|------|---------|-------|----------|------|
| 5 | `0x57e69699...` | `0xc33aa628...` | 22497642 | 12,786,922 USDC |
| 6 | `0x6e90c85a...` | `0x01144442...` | 22784988 | 9,806,396 USDC |
| 10 | `0xf4a6113f...` | `0xdfA525BD...` | 22497642 | 9,792,705 USDC |

### 成功案例代码参考
成功案例 5 的测试文件：`DeFiHackLabs/src/test/2026-erc4626/Case_57e69699_22497642.sol`

关键代码片段：
```solidity
function setUp() public {
    vm.createSelectFork("mainnet", forkBlockNumber);
    fundingToken = address(usdc);
    erc4626vault = IERC4626Case1(suspiciousVulnerableContract.collateral());
    assetController = erc4626vault.controller();  // 返回 sCrvUSD vault 地址
    vaultAsset = IERC20Case1(erc4626vault.asset()); // 返回 crvUSD
    
    // Minter addPair 注入
    address minterOwner = 0xc07e000044F95655c11fda4cD37F70A94d7e0a7d;
    IMinter1 minter = IMinter1(0x10101010E0C3171D894B71B3400668aF311e7D94);
    vm.prank(minterOwner);
    minter.addPair(address(suspiciousVulnerableContract));
}

function _manipulateOracle() internal {
    vaultAsset.transfer(assetController, attackerTransferAmount); // donation
    vaultAsset.approve(address(erc4626vault), type(uint256).max);
    erc4626vault.mint(attackerMintAmount); // mint 1 share
}
```

---

## ❌ 失败案例 3：0x5254d4f5（自定义错误 0xed27783c）

### 基本信息
| 字段 | 值 |
|------|---|
| **可疑合约** | `0x5254d4f55559f9ca38caf40a508a5b60e9af3202` |
| **Vault 地址** | `0xb89aAF59FfD0c2Bf653F45B60441B875027696733` |
| **底层资产** | crvUSD (`0xf939E0A03FB07F59A73314E73794Be0E57ac1b4E`) |
| **原始 Fork 区块** | 23336113 |
| **错误信号** | `0xed27783c`（自定义错误，可能是余额/金额限制） |
| **错误位置** | `testExploit()` 执行中（setUp 通过了） |
| **addPair** | ✅ 已注入 |

### 测试文件
`DeFiHackLabs/src/test/2026-erc4626/generated/Case_5254d4f5_23336113.sol`

### 分析线索
- setUp() 成功 → `collateral()`、`controller()`、`asset()` 调用正常
- addPair 已注入
- 但攻击步骤中某处 revert（错误 `0xed27783c`）
- **可能原因**：
  1. 区块 23336113 较晚（比成功案例晚约 100 万区块），Vault 状态可能已变化
  2. 攻击参数（4000 USDC / 2000 crvUSD donation）可能不满足该合约的最小/最大限制
  3. `borrow()` 可能有金额限制（borrowLimit）
  4. Vault 可能有存款上限（maxDeposit/maxMint 限制）

### CSV 中的其他可用区块
该案例在 CSV 中只有一个区块号：`23336113`

### 调试建议
1. 先用 `-vvvv` 运行看完整的 revert 调用栈，确定 `0xed27783c` 发生在哪个具体函数
2. 用 Python RPC 查询该合约在区块 23336113 的状态（borrowLimit、totalDebtAvailable 等）
3. 尝试调整攻击参数（增大/减小闪电贷金额、donation 金额）
4. 查看 `0xed27783c` 的 4byte selector，搜索已知错误签名

---

## ❌ 失败案例 8：0xc5184ccc（setUp revert 0xe99b9f61）

### 基本信息
| 字段 | 值 |
|------|---|
| **可疑合约** | `0xc5184cccf85b81eddc661330acb3e41bd89f34a1` |
| **Vault 地址** | `0x8E3009b59200668e1efda0a2F2Ac42b24baa2982` |
| **底层资产** | crvUSD (`0xf939E0A03FB07F59A73314E73794Be0E57ac1b4E`) |
| **原始 Fork 区块** | 22034916 |
| **错误信号** | `0xe99b9f61` |
| **错误位置** | `setUp()` 阶段（还没进入攻击流程就失败了） |
| **addPair** | ✅ 已在模板中注入 |

### 测试文件
`DeFiHackLabs/src/test/2026-erc4626/generated/Case_c5184ccc_22034916.sol`

### 分析线索
- **setUp() 就失败了**：说明 `collateral()` 或 `controller()` 或 `asset()` 调用 revert
- 区块 22034916 比较早，**可疑合约可能尚未部署**
- `0xe99b9f61` 是自定义错误签名
- **关键观察**：案例 8 和案例 9 **共用同一个 Vault 地址** `0x8E3009b59200668e1efda0a2F2Ac42b24baa2982`
- **可能原因**：
  1. 区块 22034916 时可疑合约 `0xc5184ccc...` 尚未部署
  2. `controller()` 在该区块返回了无效地址
  3. 需要尝试 CSV 中的后续区块

### CSV 中的其他可用区块（部分）
```
22034916, 22088143, 22088167, 22088172, 22088204, 22088224,
22088438, 22088521, 22088637, 22088682, 22088717, 22088762,
22088888, 22088915, ...
```
**注意**：22088xxx 系列区块可能是合约实际部署的区块范围。

### 调试建议
1. 先用 Python RPC 查询 `0xc5184ccc...` 在区块 22034916 和 22088143 是否有 bytecode
2. 如果 22034916 无 bytecode，尝试用 22088143 或更晚的区块运行
3. 修改 `.sol` 文件中的 `forkBlockNumber` 为有效区块重新测试
4. 查询 `0xe99b9f61` 的错误签名含义

---

## ❌ 失败案例 9：0xd210bc75（testExploit revert 0x1abfe8a7）

### 基本信息
| 字段 | 值 |
|------|---|
| **可疑合约** | `0xd210bc75b822795a80672413e189312598e1e42b` |
| **Vault 地址** | `0x8E3009b59200668e1efda0a2F2Ac42b24baa2982`（⚠️ 与案例 8 相同） |
| **底层资产** | crvUSD (`0xf939E0A03FB07F59A73314E73794Be0E57ac1b4E`) |
| **原始 Fork 区块** | 22035955 |
| **错误信号** | `0x1abfe8a7`（已知为 `!regPair` — 未注册配对） |
| **错误位置** | `testExploit()` 执行中 |
| **addPair** | ✅ 已在模板中注入 |

### 测试文件
`DeFiHackLabs/src/test/2026-erc4626/generated/Case_d210bc75_22035955.sol`

### 分析线索
- setUp() 通过了 → `collateral()`、`controller()`、`asset()` 正常
- addPair 已注入（`vm.prank(minterOwner); minter.addPair(address(suspiciousVulnerableContract))`）
- 但仍然收到 `!regPair` 错误 `0x1abfe8a7`
- **可能原因**：
  1. **Minter 合约地址在该区块不同**：区块 22035955 时 Minter 可能不是 `0x1010...`
  2. **addPair 注入的地址不对**：可能需要注册的不是 `suspiciousVulnerableContract` 本身
  3. **Minter owner 在该区块不同**：`0xc07e...` 可能还不是 owner
  4. **区块太早**：和案例 8 一样，22035955 可能太早
  5. **需要多个 addPair**：可能除了当前合约外还需要注册其他 pair

### CSV 中的其他可用区块
案例 9 在 CSV 中没有其他区块号（`[]`）

### 调试建议
1. 用 `-vvvv` 看完整 revert 调用栈，确认 `0x1abfe8a7` 发生在哪个合约的哪个函数
2. 检查 addPair 是否真的执行成功了（可能 setUp 中 addPair 也 revert 了但被吞掉）
3. 查询 Minter (`0x1010...`) 在区块 22035955 的 owner 是否为 `0xc07e...`
4. 查询 Minter 是否有其他注册函数或条件
5. 由于案例 8 和 9 共用 Vault `0x8E3009b5...`，可以对比两者的差异

---

## 攻击模板说明

所有失败案例使用的模板代码结构相同（来自 `calldata_bridge/templates/ERC4626AttackTemplate.sol`）：

```solidity
// setUp: fork + 查询 + addPair
function setUp() public {
    vm.createSelectFork("mainnet", forkBlockNumber);
    fundingToken = address(usdc);
    erc4626vault = IERC4626Gen(suspiciousVulnerableContract.collateral());
    assetController = erc4626vault.controller();
    vaultAsset = IERC20Gen(erc4626vault.asset());
    // addPair 注入（如果合约在 CONTRACTS_NEEDING_ADDPAIR 中）
    vm.prank(0xc07e000044F95655c11fda4cD37F70A94d7e0a7d);
    IMinterForSetup(0x10101010E0C3171D894B71B3400668aF311e7D94).addPair(address(suspiciousVulnerableContract));
}

// 闪电贷入口
function testExploit() public balanceLog {
    usdc.approve(address(morphoBlue), type(uint256).max);
    morphoBlue.flashLoan(address(usdc), flashLoanAmount, hex"");
}

// 闪电贷回调 — 核心攻击逻辑
function onMorphoFlashLoan(uint256, bytes calldata) external {
    _swapUsdcForAsset();      // Curve: USDC → crvUSD
    _manipulateOracle();      // donation to controller + mint 1 share
    _borrowAndSwapReUSD();    // addCollateralVault + borrow + reUSD → crvUSD
    _redeemAndFinalSwap();    // redeem sCrvUSD + crvUSD → USDC
}
```

### 攻击参数（当前所有案例统一）
- `flashLoanAmount` = 4,000 USDC (`4_000 * 1e6`)
- `attackerTransferAmount` = 2,000 crvUSD (`2_000 * 1e18`)
- `attackerMintAmount` = 1

---

## 关键文件路径

| 文件 | 说明 |
|------|------|
| `calldata_bridge/WORK_REPORT.md` | 完整工作报告（含成功/失败案例分析） |
| `calldata_bridge/final_report.csv` | 最终结果 CSV（含所有 11 个案例） |
| `calldata_bridge/batch_test.py` | 批量测试主脚本（生成 .sol 文件 + 运行 forge） |
| `calldata_bridge/templates/ERC4626AttackTemplate.sol` | Solidity 攻击模板 |
| `DeFiHackLabs/src/test/2026-erc4626/Case_57e69699_22497642.sol` | ✅ 成功案例 5（参考） |
| `DeFiHackLabs/src/test/2026-erc4626/Case_6e90c85a_22784988.sol` | ✅ 成功案例 6（参考） |
| `DeFiHackLabs/src/test/2026-erc4626/generated/Case_5254d4f5_23336113.sol` | ❌ 失败案例 3 |
| `DeFiHackLabs/src/test/2026-erc4626/generated/Case_c5184ccc_22034916.sol` | ❌ 失败案例 8 |
| `DeFiHackLabs/src/test/2026-erc4626/generated/Case_d210bc75_22035955.sol` | ❌ 失败案例 9 |
| `DeFiHackLabs/foundry.toml` | Foundry 配置（RPC endpoint 等） |

---

## 交付要求

1. **对每个失败案例**：
   - 确定 revert 的根本原因（通过 `forge test -vvvv` 调用栈分析 + Python RPC 链上查询）
   - 如果可修复：创建修复后的 `.sol` 文件，运行 forge test 验证通过，记录利润
   - 如果不可修复：给出技术论证说明为什么该攻击不可行

2. **更新文档**：
   - 更新 `calldata_bridge/WORK_REPORT.md` 中案例 3、8、9 的分析结论
   - 更新 `calldata_bridge/final_report.csv` 中对应行的状态

3. **优先级**：案例 9 > 案例 8 > 案例 3
   - 案例 9 的 `!regPair` 错误最可能通过修复 addPair 逻辑解决
   - 案例 8 可能需要换区块
   - 案例 3 需要更深入的合约分析

---

## 4byte 错误签名查询提示

可用 https://www.4byte.directory/api/v1/signatures/?hex_signature=0xXXXXXXXX 查询错误签名含义。

已知：
- `0x1abfe8a7` → `!regPair`（Minter 未注册配对）
- `0xed27783c` → 待查（疑似余额/金额限制）
- `0xe99b9f61` → 待查（setUp 阶段错误）

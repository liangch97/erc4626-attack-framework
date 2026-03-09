# Calldata 桥接工具 - DeFi 攻击脚本动态化

## 项目概述

本工具将 DeFiHackLabs ResupplyFi 攻击脚本从硬编码参数升级为参数化测试框架。通过 FlashSwap InputData 服务动态生成 Curve `exchange()` 的 calldata，自动注入到 Solidity PoC 文件并运行 Foundry 测试。

**核心功能**：
- 动态生成 Curve 交易 calldata
- 与 FlashSwap 服务集成（支持降级到独立模式）
- 自动更新 Solidity 测试文件
- 一键运行 Foundry 测试

## 目录结构

```
d:/区块链/
├── calldata_bridge/         ← 本工具
│   ├── dynamic_exploit.py   ← 主脚本
│   ├── exploit_config.json  ← 配置文件
│   └── README.md            ← 本文档
├── FlashSwap/               ← Calldata 生成服务（Rust）
│   └── docs/INPUTDATA_SERVICE.md
└── DeFiHackLabs/            ← 攻击 PoC 项目（Foundry）
    └── ResupplyFi_other(1).sol
```

## 环境依赖

- **Python 3.8+** - 运行桥接脚本
- **Foundry (forge)** - 运行 Solidity 测试
- **FlashSwap 服务** (Rust + Cargo) - 动态 calldata 生成
- **可选**: `requests` 库 - 调用 FlashSwap API
  ```bash
  pip install requests
  ```

## 快速开始

### 1. 启动 FlashSwap 服务

```bash
cd d:/区块链/FlashSwap
cargo run --release -- server --addr 127.0.0.1:3000 --input-addr 127.0.0.1:3001
```

### 2. 运行桥接工具

```bash
cd d:/区块链/calldata_bridge
python dynamic_exploit.py 4000000000
```

## 命令行参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `amount` | int | 必填 | USDC 金额。>1000000 视为原始精度，否则自动×10^6 |
| `--dry-run` | flag | false | 仅生成并显示 calldata，不修改文件 |
| `--no-api` | flag | false | 不使用 FlashSwap API，使用独立生成器 |
| `--skip-test` | flag | false | 跳过 forge test |
| `--no-backup` | flag | false | 不创建 .bak 备份 |
| `--api-url` | str | http://127.0.0.1:3001 | FlashSwap InputData 地址 |
| `--config` | str | exploit_config.json | 配置文件路径 |
| `--sol-file` | str | 自动检测 | 目标 Solidity 文件路径 |
| `--log-level` | str | INFO | 日志级别 (DEBUG/INFO/WARNING/ERROR) |

## 使用示例

### 示例 1：标准攻击复现（4000 USDC）

```bash
python dynamic_exploit.py 4000000000
```

### 示例 2：USDC 单位自动转换

```bash
python dynamic_exploit.py 4000   # 自动转换为 4000000000
```

### 示例 3：Dry-run 模式（不修改文件）

```bash
python dynamic_exploit.py 4000000000 --dry-run
```

### 示例 4：离线模式（不使用 FlashSwap）

```bash
python dynamic_exploit.py 4000000000 --no-api
```

### 示例 5：自定义 API 地址

```bash
python dynamic_exploit.py 4000000000 --api-url http://192.168.1.100:3001
```

### 示例 6：调试日志

```bash
python dynamic_exploit.py 4000000000 --log-level DEBUG
```

### 示例 7：较大金额测试

```bash
python dynamic_exploit.py 5000000000 --skip-test
```

## FlashSwap 服务集成

### InputData 端点 (`/inputdata`)

**请求格式**：
```json
POST http://127.0.0.1:3001/inputdata
{
  "token_in": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
  "token_out": "0xf939E0A03FB07F59A73314E73794Be0E57ac1b4E",
  "amount": "4000000000",
  "max_hops": 1,
  "enable_verification": false,
  "allowed_protocols": ["curve_v1"]
}
```

**响应格式**：
```json
{
  "multicall_to": "0xcA11bde05977b3631167028862bE2a173976CA11",
  "multicall_data": "0x82ad56cb...",
  "steps": [
    {
      "step_number": 1,
      "protocol": "curve_v1",
      "pool_address": "0x4DEcE678ceceb27446b35C672dC7d61F30bAD69E",
      "encoded_data": "0x3df02124..."
    }
  ]
}
```

### 降级逻辑

当 FlashSwap 服务不可用时，脚本自动降级到独立 calldata 生成器：

1. 尝试调用 `/inputdata` → 成功则使用 `steps[0].encoded_data`
2. API 超时/不可用 → 使用纯 Python ABI 编码生成相同的 calldata
3. 独立模式使用硬编码函数选择器 `0x3df02124`（避免 SHA3 vs Keccak256 问题）

### 健康检查

```bash
curl http://127.0.0.1:3000/health
```

## Calldata 格式说明

### Curve `exchange()` 函数

```
选择器: 0x3df02124 = keccak256("exchange(int128,int128,uint256,uint256)")[:4]

参数编码（ABI，每个 32 字节大端序）：
┌────────────┬──────────┬───────────────┬──────────────────┐
│ 参数       │ 类型     │ 值            │ 说明             │
├────────────┼──────────┼───────────────┼──────────────────┤
│ i          │ int128   │ 0             │ USDC 索引        │
│ j          │ int128   │ 1             │ crvUSD 索引      │
│ dx         │ uint256  │ 4000000000    │ 输入金额(6精度)  │
│ min_dy     │ uint256  │ 0             │ 最小输出量       │
└────────────┴──────────┴───────────────┴──────────────────┘

完整 calldata（4000 USDC 示例）：
0x3df02124
  0000000000000000000000000000000000000000000000000000000000000000  # i=0
  0000000000000000000000000000000000000000000000000000000000000001  # j=1
  00000000000000000000000000000000000000000000000000000000ee6b2800  # dx=4000000000
  0000000000000000000000000000000000000000000000000000000000000000  # min_dy=0
```

## 故障排查

### FlashSwap 服务未启动

```
WARNING: FlashSwap InputData API 调用失败: ConnectionError
INFO: 降级到独立 calldata 生成器...
```

**解决**：启动 FlashSwap 服务，或使用 `--no-api` 参数

### API 响应超时

**解决**：在配置文件中增大 `timeout_seconds`，或检查 FlashSwap 日志

### Forge 测试失败

**解决**：
1. 检查 RPC 节点是否可用
2. 确认区块高度设置正确
3. 脚本会自动从 .bak 备份恢复原文件

### 文件未找到

```
ERROR: 目标文件不存在: .../ResupplyFi_other(1).sol
```

**解决**：使用 `--sol-file` 指定正确的文件路径

### Calldata 选择器不匹配

```
ERROR: 函数选择器不匹配! 期望: 0x3df02124, 实际: 0x...
```

**解决**：检查 FlashSwap API 返回的协议类型是否正确

## 安全注意事项

⚠️ **本工具仅用于安全研究和教育目的**

⚠️ **不要将生成的 calldata 用于真实主网攻击**

⚠️ **FlashSwap 服务仅在本地运行**

⚠️ **敏感信息（RPC URL）使用环境变量或配置文件管理**

## 与旧版对比

| 改动项 | 旧版 (DeFiHackLabs/) | 新版 (calldata_bridge/) |
|--------|----------------------|------------------------|
| 位置 | DeFiHackLabs/ 内部 | 独立目录 calldata_bridge/ |
| API 端点 | `/api/generate-calldata`（不存在） | `/inputdata`（FlashSwap 真实端点） |
| API 端口 | 8080 | 3001 |
| 请求格式 | `{pool_address, function, params}` | `{token_in, token_out, amount, ...}` |
| 响应解析 | `response["calldata"]` | `response["steps"][0]["encoded_data"]` |
| API 默认 | 禁用 | 启用 |
| 路径引用 | 同目录 | `../DeFiHackLabs/` |

## 配置文件说明

[`exploit_config.json`](exploit_config.json:1) 包含以下配置项：

- **flashswap_api**: FlashSwap 服务配置
  - `inputdata_url`: InputData 服务地址
  - `inputdata_endpoint`: 端点路径
  - `enabled`: 是否启用 API
  - `timeout_seconds`: 请求超时时间

- **target_contract**: 目标合约信息
  - `pool_address`: Curve 池地址
  - `token_in/token_out`: 代币地址
  - `exchange_function_selector`: 函数选择器

- **solidity_file**: Solidity 文件配置
  - `defihacklabs_dir`: DeFiHackLabs 目录路径
  - `path`: 目标文件相对路径
  - `backup_enabled`: 是否启用备份

- **foundry**: Foundry 测试配置
  - `test_command`: forge test 命令
  - `working_directory`: 工作目录
  - `timeout_seconds`: 测试超时时间

## 工作流程

1. **解析参数** - 读取命令行参数和配置文件
2. **生成 Calldata** - 调用 FlashSwap API 或使用独立生成器
3. **验证选择器** - 确保函数选择器为 `0x3df02124`
4. **备份文件** - 创建 `.bak` 备份（可选）
5. **更新 Solidity** - 替换 `inputData` 变量
6. **运行测试** - 执行 `forge test`
7. **处理结果** - 测试失败时自动回滚

## 技术细节

### 函数选择器计算

脚本使用硬编码选择器 `0x3df02124` 而非动态计算，原因：

- Python 的 `hashlib.sha3_256` 使用 SHA-3 标准
- Solidity 的 `keccak256` 使用 Keccak-256（SHA-3 前身）
- 两者结果不同，导致选择器不匹配

### ABI 编码规则

- 函数选择器：4 字节
- 每个参数：32 字节（大端序）
- `int128` 参数：填充到 32 字节
- `uint256` 参数：原生 32 字节

### 文件更新策略

使用正则表达式匹配并替换：
```python
pattern = r'string memory inputData = "0x[0-9a-fA-F]+";'
replacement = f'string memory inputData = "{calldata}";'
```

## 许可证

本工具遵循 DeFiHackLabs 项目的许可证。仅用于安全研究和教育目的。

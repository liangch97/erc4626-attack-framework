#!/usr/bin/env python3
"""
sDOLA Vault 链上数据收集脚本
用于收集 PoC 所需的链上数据
"""

from web3 import Web3
import json

# 配置
RPC_URL = "https://eth-mainnet.g.alchemy.com/v2/wXOiJF5OutGtBTTDgBM65"
VAULT_ADDRESS = Web3.to_checksum_address("0x9d39a5DE30e57443BfF2A8307A4256c8797a3497")
AAVE_POOL_ADDRESS = Web3.to_checksum_address("0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2")
FORK_BLOCK = 20134634

# 初始化 Web3
w3 = Web3(Web3.HTTPProvider(RPC_URL))

# ERC20 ABI 片段
ERC20_ABI = [
    {"name": "name", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
    {"name": "symbol", "outputs": [{"type": "string"}], "stateMutability": "view", "type": "function"},
    {"name": "decimals", "outputs": [{"type": "uint8"}], "stateMutability": "view", "type": "function"},
    {"name": "totalSupply", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"name": "balanceOf", "inputs": [{"name": "account", "type": "address"}], "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
]

# ERC4626 ABI 片段
ERC4626_ABI = ERC20_ABI + [
    {"name": "asset", "outputs": [{"type": "address"}], "stateMutability": "view", "type": "function"},
    {"name": "totalAssets", "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"name": "convertToShares", "inputs": [{"name": "assets", "type": "uint256"}], "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
    {"name": "convertToAssets", "inputs": [{"name": "shares", "type": "uint256"}], "outputs": [{"type": "uint256"}], "stateMutability": "view", "type": "function"},
]

# Aave Pool ABI 片段
AAVE_POOL_ABI = [
    {
        "name": "getReserveData",
        "inputs": [{"name": "asset", "type": "address"}],
        "outputs": [
            {
                "components": [
                    {"name": "unbacked", "type": "uint256"},
                    {"name": "accruedToTreasuryScaled", "type": "uint256"},
                    {"name": "totalAToken", "type": "uint256"},
                    {"name": "totalStableDebt", "type": "uint256"},
                    {"name": "totalVariableDebt", "type": "uint256"},
                    {"name": "liquidityRate", "type": "uint256"},
                    {"name": "variableBorrowRate", "type": "uint256"},
                    {"name": "stableBorrowRate", "type": "uint256"},
                    {"name": "averageStableBorrowRate", "type": "uint256"},
                    {"name": "liquidityIndex", "type": "uint256"},
                    {"name": "variableBorrowIndex", "type": "uint256"},
                    {"name": "lastUpdateTimestamp", "type": "uint40"},
                ],
                "name": "configuration",
                "type": "tuple",
            },
            {"name": "id", "type": "uint8"},
            {"name": "aTokenAddress", "type": "address"},
            {"name": "stableDebtTokenAddress", "type": "address"},
            {"name": "variableDebtTokenAddress", "type": "address"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "getUserAccountData",
        "inputs": [{"name": "user", "type": "address"}],
        "outputs": [
            {"name": "totalCollateralBase", "type": "uint256"},
            {"name": "totalDebtBase", "type": "uint256"},
            {"name": "availableBorrowsBase", "type": "uint256"},
            {"name": "currentLiquidationThreshold", "type": "uint256"},
            {"name": "ltv", "type": "uint256"},
            {"name": "healthFactor", "type": "uint256"},
        ],
        "stateMutability": "view",
        "type": "function",
    },
    {
        "name": "getConfiguration",
        "inputs": [{"name": "asset", "type": "address"}],
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function",
    },
]


def query_vault_data():
    """查询 Vault 数据"""
    print(f"\n{'='*60}")
    print(f"Querying Vault: {VAULT_ADDRESS}")
    print(f"At Block: {FORK_BLOCK}")
    print(f"{'='*60}")
    
    vault = w3.eth.contract(address=VAULT_ADDRESS, abi=ERC4626_ABI)
    
    # 基本信息
    name = vault.functions.name().call(block_identifier=FORK_BLOCK)
    symbol = vault.functions.symbol().call(block_identifier=FORK_BLOCK)
    decimals = vault.functions.decimals().call(block_identifier=FORK_BLOCK)
    
    print(f"\nVault Name: {name}")
    print(f"Vault Symbol: {symbol}")
    print(f"Vault Decimals: {decimals}")
    
    # ERC4626 特有信息
    try:
        asset_address = vault.functions.asset().call(block_identifier=FORK_BLOCK)
        total_assets = vault.functions.totalAssets().call(block_identifier=FORK_BLOCK)
        total_supply = vault.functions.totalSupply().call(block_identifier=FORK_BLOCK)
        
        print(f"\nAsset Address: {asset_address}")
        print(f"Total Assets: {total_assets} ({total_assets / 10**decimals:.6f})")
        print(f"Total Supply (Shares): {total_supply} ({total_supply / 10**decimals:.6f})")
        
        if total_supply > 0:
            price_per_share = total_assets / total_supply
            print(f"Price Per Share: {price_per_share:.10f}")
        
        return {
            "name": name,
            "symbol": symbol,
            "decimals": decimals,
            "asset_address": asset_address,
            "total_assets": total_assets,
            "total_supply": total_supply,
        }
    except Exception as e:
        print(f"Error querying ERC4626 functions: {e}")
        return None


def query_asset_data(asset_address):
    """查询底层资产数据"""
    print(f"\n{'='*60}")
    print(f"Querying Asset: {asset_address}")
    print(f"{'='*60}")
    
    asset = w3.eth.contract(address=asset_address, abi=ERC20_ABI)
    
    try:
        name = asset.functions.name().call(block_identifier=FORK_BLOCK)
        symbol = asset.functions.symbol().call(block_identifier=FORK_BLOCK)
        decimals = asset.functions.decimals().call(block_identifier=FORK_BLOCK)
        total_supply = asset.functions.totalSupply().call(block_identifier=FORK_BLOCK)
        
        # Vault 持有的资产
        vault_balance = asset.functions.balanceOf(VAULT_ADDRESS).call(block_identifier=FORK_BLOCK)
        
        print(f"\nAsset Name: {name}")
        print(f"Asset Symbol: {symbol}")
        print(f"Asset Decimals: {decimals}")
        print(f"Asset Total Supply: {total_supply} ({total_supply / 10**decimals:.2f})")
        print(f"Vault Balance: {vault_balance} ({vault_balance / 10**decimals:.6f})")
        
        return {
            "name": name,
            "symbol": symbol,
            "decimals": decimals,
            "total_supply": total_supply,
            "vault_balance": vault_balance,
        }
    except Exception as e:
        print(f"Error querying asset: {e}")
        return None


def query_aave_reserve(asset_address):
    """查询 Aave 储备数据"""
    print(f"\n{'='*60}")
    print(f"Querying Aave Reserve for: {asset_address}")
    print(f"{'='*60}")
    
    pool = w3.eth.contract(address=AAVE_POOL_ADDRESS, abi=AAVE_POOL_ABI)
    
    try:
        reserve_data = pool.functions.getReserveData(asset_address).call(block_identifier=FORK_BLOCK)
        
        print(f"\naToken Address: {reserve_data[2]}")
        print(f"Stable Debt Token: {reserve_data[3]}")
        print(f"Variable Debt Token: {reserve_data[4]}")
        
        return {
            "a_token": reserve_data[2],
            "stable_debt_token": reserve_data[3],
            "variable_debt_token": reserve_data[4],
        }
    except Exception as e:
        print(f"Error querying Aave reserve: {e}")
        return None


def check_balance_based(vault_data, asset_data):
    """检查是否为 balance-based 会计"""
    print(f"\n{'='*60}")
    print("Checking Balance-Based Accounting")
    print(f"{'='*60}")
    
    if vault_data is None or asset_data is None:
        print("Cannot check - missing data")
        return None
    
    total_assets = vault_data["total_assets"]
    vault_balance = asset_data["vault_balance"]
    
    print(f"\ntotalAssets(): {total_assets}")
    print(f"asset.balanceOf(vault): {vault_balance}")
    
    if total_assets == vault_balance:
        print("\n✅ BALANCE-BASED (Donation Sensitive)")
        print("   totalAssets == balanceOf(vault)")
        return True
    elif total_assets == 0 and vault_balance == 0:
        print("\n⚠️  VAULT EMPTY - Need donation test")
        return "EMPTY"
    else:
        diff = abs(total_assets - vault_balance)
        if total_assets > 0 and diff * 100 / total_assets < 1:
            print(f"\n✅ LIKELY BALANCE-BASED (diff < 1%)")
            return True
        else:
            print(f"\n❌ INTERNAL ACCOUNTING (Donation Immune)")
            print(f"   Difference: {diff}")
            return False


def main():
    print("sDOLA Vault Data Collection Script")
    print("="*60)
    
    # 检查连接
    if not w3.is_connected():
        print("Error: Cannot connect to RPC")
        return
    
    print(f"Connected to: {w3.client_version}")
    current_block = w3.eth.block_number
    print(f"Current Block: {current_block}")
    
    # 查询 Vault 数据
    vault_data = query_vault_data()
    
    # 查询 Asset 数据
    if vault_data and vault_data.get("asset_address"):
        asset_data = query_asset_data(vault_data["asset_address"])
    else:
        asset_data = None
    
    # 查询 Aave 储备
    if vault_data and vault_data.get("asset_address"):
        aave_data = query_aave_reserve(vault_data["asset_address"])
    else:
        aave_data = None
    
    # 检查 balance-based
    is_balance_based = check_balance_based(vault_data, asset_data)
    
    # 汇总结果
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    
    result = {
        "vault_address": VAULT_ADDRESS,
        "fork_block": FORK_BLOCK,
        "vault": vault_data,
        "asset": asset_data,
        "aave": aave_data,
        "is_balance_based": is_balance_based,
    }
    
    print(json.dumps(result, indent=2, default=str))
    
    # 保存结果
    output_file = "calldata_bridge/data/sdola_query_result.json"
    with open(output_file, "w") as f:
        json.dump(result, f, indent=2, default=str)
    print(f"\nResults saved to: {output_file}")


if __name__ == "__main__":
    main()

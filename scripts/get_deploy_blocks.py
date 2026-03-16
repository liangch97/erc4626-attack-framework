#!/usr/bin/env python3
"""
查询 VULNERABLE vault 的合约部署区块号
使用 Alchemy RPC 通过 eth_getTransactionReceipt 获取合约创建交易
"""

from web3 import Web3, HTTPProvider
import json

# Alchemy RPC 端点
RPC_URL = "https://eth-mainnet.g.alchemy.com/v2/wXOiJF5OutGtBTTDgBM65"

# 7个去重后的 VULNERABLE vault 地址
VULNERABLE_VAULTS = [
    "0x356b8d89c1e1239cbbb9de4815c39a1474d5ba7d",
    "0x57f5e098cad7a3d1eed53991d4d66c45c9af7812",
    "0x5c5b196abe0d54485975d1ec29617d42d9198326",
    "0x7751e2f4b8ae93ef6b79d86419d42fe3295a4559",
    "0x90d2af7d622ca3141efa4d8f1f24d86e5974cc8f",
    "0x9d39a5de30e57443bff2a8307a4256c8797a3497",
    "0xd11c452fc99cf405034ee446803b6f6c1f6d5ed8",
]

# 初始化 Web3
w3 = Web3(HTTPProvider(RPC_URL))


def get_contract_deploy_block_via_alchemy(address: str) -> int:
    """
    使用 Alchemy 的 eth_getTransactionReceipt 通过合约创建交易的哈希获取部署区块
    首先通过 eth_getCode 确认合约存在，然后通过二分搜索找到部署区块
    """
    address = w3.to_checksum_address(address)
    
    # 确认合约存在
    code = w3.eth.get_code(address)
    if len(code) == 0:
        print(f"  WARNING: No code at {address}")
        return None
    
    # 使用二分搜索找到合约部署的最早区块
    # 从区块 0 开始搜索到当前区块
    current_block = w3.eth.block_number
    
    # 首先检查合约在当前区块是否存在
    low = 0
    high = current_block
    
    # 找到合约代码首次出现的区块
    deploy_block = None
    
    # 先找到一个合约存在的区块点
    # 从创世区块开始，逐步检查
    print(f"  Binary searching deploy block in [0, {current_block}]...")
    
    for iteration in range(30):  # 最多30次迭代，覆盖约10亿区块
        if low > high:
            break
        
        mid = (low + high) // 2
        
        try:
            code_at_mid = w3.eth.get_code(address, block_identifier=mid)
            has_code = len(code_at_mid) > 0
            
            if has_code:
                # 合约已存在，搜索更早的区块
                deploy_block = mid
                high = mid - 1
            else:
                # 合约不存在，搜索更晚的区块
                low = mid + 1
            
            if iteration % 5 == 0:
                print(f"    iter {iteration}: mid={mid} has_code={has_code} window=[{low}, {high}]")
                
        except Exception as e:
            print(f"    Error at block {mid}: {e}")
            # 如果出错，尝试搜索更晚的区块
            low = mid + 1
    
    return deploy_block


def get_contract_deploy_block_via_etherscan(address: str) -> int:
    """
    备用方案：通过 Etherscan API 获取合约部署信息
    注意：需要 Etherscan API key
    """
    import requests
    
    # Etherscan API (免费版有速率限制)
    ETHERSCAN_API_KEY = "YourApiKeyToken"  # 需要替换为实际的 API key
    url = f"https://api.etherscan.io/api?module=account&action=txlist&address={address}&startblock=0&endblock=99999999&page=1&offset=1&sort=asc&apikey={ETHERSCAN_API_KEY}"
    
    try:
        resp = requests.get(url, timeout=30)
        data = resp.json()
        if data.get("status") == "1" and data.get("message") == "OK":
            txs = data.get("result", [])
            if txs:
                # 第一个交易通常是合约创建
                first_tx = txs[0]
                if first_tx.get("to") == "":  # 合约创建交易的 to 为空
                    return int(first_tx.get("blockNumber", 0))
    except Exception as e:
        print(f"  Etherscan API error: {e}")
    
    return None


def main():
    print("=" * 70)
    print("Querying deploy blocks for VULNERABLE vaults")
    print("=" * 70)
    
    results = {}
    
    for vault in VULNERABLE_VAULTS:
        print(f"\nVault: {vault}")
        
        # 方法1: 通过 RPC 二分搜索
        deploy_block = get_contract_deploy_block_via_alchemy(vault)
        
        if deploy_block:
            print(f"  => Deploy block: {deploy_block}")
            results[vault] = deploy_block
        else:
            print(f"  => FAILED to find deploy block")
            results[vault] = None
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    
    print("\nDeploy blocks (Python dict format):")
    print("DEPLOY_BLOCKS = {")
    for vault, block in results.items():
        print(f'    "{vault}": {block},')
    print("}")
    
    print("\nDeploy blocks (JSON format):")
    print(json.dumps(results, indent=2))
    
    # 保存到文件
    output_path = "d:/区块链/calldata_bridge/data/vault_deploy_blocks.json"
    import os
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to {output_path}")


if __name__ == "__main__":
    main()

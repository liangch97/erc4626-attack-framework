#!/usr/bin/env python3
"""
查找7个vault的部署区块，判断是否存在空vault窗口期。
二分搜索 eth_getCode 确定部署区块，然后检查部署后的 totalSupply。
"""
import json, urllib.request, time

RPC = 'http://127.0.0.1:18545'

def rpc_call(method, params):
    payload = json.dumps({'jsonrpc':'2.0','method':method,'params':params,'id':1}).encode()
    req = urllib.request.Request(RPC, data=payload, headers={'Content-Type':'application/json'})
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if 'error' in resp:
        return None
    return resp.get('result')

def has_code(addr, block):
    code = rpc_call('eth_getCode', [addr, hex(block)])
    return code is not None and len(code) > 2

def eth_call(to, data, block):
    result = rpc_call('eth_call', [{'to': to, 'data': data}, hex(block)])
    return result

def find_deploy_block(addr, lo, hi):
    """二分查找合约部署区块（第一个有code的区块）"""
    if not has_code(addr, hi):
        return None  # 在hi也没有
    if has_code(addr, lo):
        return lo  # 在lo就已经存在
    
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if has_code(addr, mid):
            hi = mid
        else:
            lo = mid
    return hi

def get_total_supply(vault, block):
    raw = eth_call(vault, '0x18160ddd', block)
    if raw and raw != '0x':
        return int(raw, 16)
    return None

def get_total_assets(vault, block):
    raw = eth_call(vault, '0x01e1d114', block)
    if raw and raw != '0x':
        return int(raw, 16)
    return None

# 7 new vaults + suspicious contract info
cases = [
    {'name': '08064a8e', 'suspicious': '0x08064A8eEecf71203449228f3eaC65E462009fdF', 'vault': '0x14361c243174794e2207296a6ad59bb0dec1d388', 'block': 22035016},
    {'name': '4a7c6493', 'suspicious': '0x4A7c64932d1ef0b4a2d430ea10184e3B87095E33', 'vault': '0x21cf1c5dc48c603b89907fe6a7ae83ea5e3709af', 'block': 22035030},
    {'name': '27ab448a', 'suspicious': '0x27AB448a75d548ECfF73f8b4F36fCc9496768797', 'vault': '0x992b77179a5cf876bcd566ff4b3eae6482012b90', 'block': 22284975},
    {'name': 'd42535cd', 'suspicious': '0xD42535Cda82a4569BA7209857446222ABd14A82c', 'vault': '0x7430f11eeb64a4ce50c8f92177485d34c48da72c', 'block': 23265624},
    {'name': '22b12110', 'suspicious': '0x22B12110f1479d5D6Fd53D0dA35482371fEB3c7e', 'vault': '0xb2b23c87a4b6d1b03ba603f7c3eb9a81fdc0aac9', 'block': 22035027},
    {'name': '39ea8e7f', 'suspicious': '0x39Ea8e7f44E9303A7441b1E1a4F5731F1028505C', 'vault': '0x4a7999c55d3a93daf72ea112985e57c2e3b9e95d', 'block': 22035022},
    {'name': '2fdd3c0a', 'suspicious': '0x2fdD3c0a682e5774205F0F6D3eD3c9D1b9Cb9413', 'vault': '0xc32b0cf36e06c790a568667a17de80cba95a5aad', 'block': 24131008},
]

# Also find deploy block for suspicious contract itself
print("=" * 80)
print("Phase 1: 二分查找 vault 和 suspicious contract 的部署区块")
print("=" * 80)

# Get current block
latest = rpc_call('eth_blockNumber', [])
latest_block = int(latest, 16)
print(f"当前最新区块: {latest_block}")
print()

for c in cases:
    # Search range: vault likely deployed within 500k blocks of suspicious block
    lo = max(c['block'] - 2_000_000, 19_000_000)  # Mainnet ~2024 start
    hi = c['block']
    
    print(f"--- {c['name']} (suspicious block: {c['block']}) ---")
    
    # Find vault deploy block
    vault_deploy = find_deploy_block(c['vault'], lo, hi)
    c['vault_deploy'] = vault_deploy
    
    # Find suspicious contract deploy block  
    susp_deploy = find_deploy_block(c['suspicious'], lo, hi)
    c['susp_deploy'] = susp_deploy
    
    print(f"  Vault {c['vault'][:10]}... 部署区块: {vault_deploy}")
    print(f"  Suspicious {c['suspicious'][:10]}... 部署区块: {susp_deploy}")
    
    if vault_deploy:
        gap = c['block'] - vault_deploy
        print(f"  Vault部署 → 可疑区块 间隔: {gap} 区块")
    print()

print()
print("=" * 80)
print("Phase 2: 检查 vault 部署后的 totalSupply 变化（找空窗口）")
print("=" * 80)
print()

for c in cases:
    vd = c.get('vault_deploy')
    if not vd:
        print(f"--- {c['name']}: vault部署区块未找到，跳过 ---")
        continue
    
    print(f"--- {c['name']} (vault deployed at {vd}) ---")
    
    # Check totalSupply at deploy block and a few blocks after
    blocks_to_check = [vd, vd + 1, vd + 5, vd + 10, vd + 50, vd + 100, vd + 500, vd + 1000]
    # Also check susp_deploy if available
    sd = c.get('susp_deploy')
    if sd and sd > vd:
        blocks_to_check.extend([sd - 1, sd, sd + 1])
    blocks_to_check = sorted(set(b for b in blocks_to_check if b <= c['block']))
    
    empty_blocks = []
    first_nonempty = None
    
    for blk in blocks_to_check:
        ts = get_total_supply(c['vault'], blk)
        if ts is not None:
            is_empty = (ts == 0)
            if is_empty:
                empty_blocks.append(blk)
            elif first_nonempty is None:
                first_nonempty = blk
            print(f"  Block {blk}: totalSupply = {ts:,} {'[EMPTY!]' if is_empty else ''}")
        else:
            print(f"  Block {blk}: totalSupply query failed (contract not deployed yet?)")
    
    if empty_blocks:
        print(f"  >>> 发现空窗口! 区块 {empty_blocks[0]} - {empty_blocks[-1]} vault 为空")
        c['empty_window'] = (empty_blocks[0], empty_blocks[-1])
        
        # Now find the exact block where totalSupply transitions from 0 to >0
        # Binary search between last empty and first nonempty
        if first_nonempty:
            lo_e, hi_e = empty_blocks[-1], first_nonempty
            while lo_e < hi_e - 1:
                mid_e = (lo_e + hi_e) // 2
                ts_mid = get_total_supply(c['vault'], mid_e)
                if ts_mid == 0:
                    lo_e = mid_e
                else:
                    hi_e = mid_e
            print(f"  >>> 精确搜索: 最后空区块 = {lo_e}, 首个有存款区块 = {hi_e}")
            c['last_empty'] = lo_e
            c['first_deposit'] = hi_e
    else:
        print(f"  Vault 在部署时就已有存款（部署交易包含初始deposit）")
        c['empty_window'] = None
    
    # Check if suspicious contract exists at the empty window
    if c.get('last_empty') and c.get('susp_deploy'):
        if c['susp_deploy'] <= c['last_empty']:
            print(f"  >>> Suspicious contract 在空窗口期已部署! 可能可以攻击!")
            c['attackable'] = True
        else:
            print(f"  >>> Suspicious contract 在空窗口之后才部署 (susp: {c['susp_deploy']}, last_empty: {c['last_empty']})")
            c['attackable'] = False
    print()

print()
print("=" * 80)
print("SUMMARY")
print("=" * 80)
print(f"{'Case':<12} {'VaultDeploy':<13} {'SuspDeploy':<13} {'LastEmpty':<13} {'FirstDeposit':<13} {'Window':<8} {'Attackable'}")
print("-" * 90)
for c in cases:
    vd = c.get('vault_deploy', 'N/A')
    sd = c.get('susp_deploy', 'N/A')
    le = c.get('last_empty', 'N/A')
    fd = c.get('first_deposit', 'N/A')
    ew = c.get('empty_window')
    window = f"{ew[1] - ew[0]} blks" if ew else "None"
    att = c.get('attackable', False)
    print(f"{c['name']:<12} {str(vd):<13} {str(sd):<13} {str(le):<13} {str(fd):<13} {window:<8} {'YES!' if att else 'No'}")

#!/usr/bin/env python3
"""精确查找 22b12110/39ea8e7f 的空窗口：真实部署区块 → 首次存款区块"""
import json, urllib.request

RPC = 'http://127.0.0.1:18545'

def rpc_call(method, params):
    payload = json.dumps({'jsonrpc':'2.0','method':method,'params':params,'id':1}).encode()
    req = urllib.request.Request(RPC, data=payload, headers={'Content-Type':'application/json'})
    resp = json.loads(urllib.request.urlopen(req, timeout=30).read())
    return resp.get('result')

def get_total_supply(vault, block):
    result = rpc_call('eth_call', [{'to': vault, 'data': '0x18160ddd'}, hex(block)])
    if result and result != '0x':
        return int(result, 16)
    return None

targets = [
    {'name':'22b12110', 'vault':'0xb2b23c87a4b6d1b03ba603f7c3eb9a81fdc0aac9',
     'real_deploy': 19422684, 'susp_deploy': 22034927,
     'known_nonempty': 20035027},  # At this block totalSupply was already large
    {'name':'39ea8e7f', 'vault':'0x4a7999c55d3a93daf72ea112985e57c2e3b9e95d',
     'real_deploy': 19999153, 'susp_deploy': 22034922,
     'known_nonempty': 20035022},
]

for t in targets:
    print(f"=== {t['name']} ===")
    print(f"  真实部署区块: {t['real_deploy']}")
    print(f"  已知有存款区块: {t['known_nonempty']}")
    print(f"  可疑合约部署区块: {t['susp_deploy']}")
    
    # 先确认部署区块确实为空
    ts_deploy = get_total_supply(t['vault'], t['real_deploy'])
    print(f"  部署区块 totalSupply: {ts_deploy}")
    
    # 确认 known_nonempty 确实有存款
    ts_known = get_total_supply(t['vault'], t['known_nonempty'])
    print(f"  已知非空区块 totalSupply: {ts_known:,}")
    
    # 二分查找: 找到 totalSupply 从 0 变为 >0 的精确区块
    lo = t['real_deploy']
    hi = t['known_nonempty']
    
    while lo < hi - 1:
        mid = (lo + hi) // 2
        ts = get_total_supply(t['vault'], mid)
        if ts == 0:
            lo = mid
        else:
            hi = mid
    
    print(f"  --- 二分搜索结果 ---")
    print(f"  最后空区块 (last_empty): {lo}")
    print(f"  首次有存款区块 (first_deposit): {hi}")
    ts_le = get_total_supply(t['vault'], lo)
    ts_fd = get_total_supply(t['vault'], hi)
    print(f"  验证: block {lo} totalSupply = {ts_le}")
    print(f"  验证: block {hi} totalSupply = {ts_fd:,}")
    
    gap_deploy_to_deposit = hi - t['real_deploy']
    gap_deposit_to_susp = t['susp_deploy'] - hi
    print(f"  空窗口持续: {gap_deploy_to_deposit} 区块 (部署 → 首次存款)")
    print(f"  存款后到可疑合约部署: {gap_deposit_to_susp} 区块")
    
    if t['susp_deploy'] > lo:
        print(f"  结论: 可疑合约在空窗口关闭后 {gap_deposit_to_susp} 区块才部署 → 不可攻击 [NO CHANGE]")
    else:
        print(f"  !! 可疑合约在空窗口期内! → 可能可攻击! [NEEDS REVIEW]")
    print()

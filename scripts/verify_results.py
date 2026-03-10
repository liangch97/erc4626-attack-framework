#!/usr/bin/env python3
"""
独立验证脚本：
1. 在每个 suspicious contract 部署区块确认 vault totalSupply ≠ 0
2. 在 vault 空窗口边界 (last_empty, first_deposit) 独立确认 totalSupply 精确转变
3. 对 22b12110/39ea8e7f 扩大搜索范围找到真实部署区块
4. 检查 vault 在中间是否曾被提空
"""
import json, urllib.request

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

def get_total_supply(vault, block):
    result = rpc_call('eth_call', [{'to': vault, 'data': '0x18160ddd'}, hex(block)])
    if result and result != '0x':
        return int(result, 16)
    return None

def find_deploy_block(addr, lo, hi):
    if not has_code(addr, hi):
        return None
    if has_code(addr, lo):
        return lo
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if has_code(addr, mid):
            hi = mid
        else:
            lo = mid
    return hi

# Cases data matching find_empty_window.py
cases = [
    {'name': '08064a8e', 'suspicious': '0x08064A8eEecf71203449228f3eaC65E462009fdF',
     'vault': '0x14361c243174794e2207296a6ad59bb0dec1d388', 'block': 22035016,
     'susp_deploy': 22034916, 'vault_deploy': 20325474, 'last_empty': 20327284, 'first_deposit': 20327285},
    {'name': '4a7c6493', 'suspicious': '0x4A7c64932d1ef0b4a2d430ea10184e3B87095E33',
     'vault': '0x21cf1c5dc48c603b89907fe6a7ae83ea5e3709af', 'block': 22035030,
     'susp_deploy': 22034930, 'vault_deploy': 20035301, 'last_empty': 20035478, 'first_deposit': 20035479},
    {'name': '27ab448a', 'suspicious': '0x27AB448a75d548ECfF73f8b4F36fCc9496768797',
     'vault': '0x992b77179a5cf876bcd566ff4b3eae6482012b90', 'block': 22284975,
     'susp_deploy': 22284875, 'vault_deploy': 22083746, 'last_empty': 22083849, 'first_deposit': 22083850},
    {'name': 'd42535cd', 'suspicious': '0xD42535Cda82a4569BA7209857446222ABd14A82c',
     'vault': '0x7430f11eeb64a4ce50c8f92177485d34c48da72c', 'block': 23265624,
     'susp_deploy': 23265524, 'vault_deploy': 22719937, 'last_empty': 22946241, 'first_deposit': 22946242},
    {'name': '22b12110', 'suspicious': '0x22B12110f1479d5D6Fd53D0dA35482371fEB3c7e',
     'vault': '0xb2b23c87a4b6d1b03ba603f7c3eb9a81fdc0aac9', 'block': 22035027,
     'susp_deploy': 22034927, 'vault_deploy': 20035027, 'last_empty': None, 'first_deposit': None},
    {'name': '39ea8e7f', 'suspicious': '0x39Ea8e7f44E9303A7441b1E1a4F5731F1028505C',
     'vault': '0x4a7999c55d3a93daf72ea112985e57c2e3b9e95d', 'block': 22035022,
     'susp_deploy': 22034922, 'vault_deploy': 20035022, 'last_empty': None, 'first_deposit': None},
    {'name': '2fdd3c0a', 'suspicious': '0x2fdD3c0a682e5774205F0F6D3eD3c9D1b9Cb9413',
     'vault': '0xc32b0cf36e06c790a568667a17de80cba95a5aad', 'block': 24131008,
     'susp_deploy': 24130908, 'vault_deploy': 23249368, 'last_empty': 23286133, 'first_deposit': 23286134},
]

errors = []

print("=" * 80)
print("验证 1: 在可疑合约部署区块确认 vault totalSupply ≠ 0")
print("=" * 80)
for c in cases:
    ts = get_total_supply(c['vault'], c['susp_deploy'])
    status = "OK" if ts and ts > 0 else "FAIL!"
    if not ts or ts == 0:
        errors.append(f"{c['name']}: vault 在 susp_deploy={c['susp_deploy']} 时 totalSupply=0!")
    print(f"  {c['name']}: vault totalSupply @ block {c['susp_deploy']} = {ts:,} [{status}]")

print()
print("=" * 80)
print("验证 2: 空窗口边界精确性 (last_empty 应为 0, first_deposit 应 > 0)")
print("=" * 80)
for c in cases:
    le = c['last_empty']
    fd = c['first_deposit']
    if le is None:
        print(f"  {c['name']}: 无空窗口（跳过边界检查）")
        continue
    ts_le = get_total_supply(c['vault'], le)
    ts_fd = get_total_supply(c['vault'], fd)
    ok_le = ts_le == 0
    ok_fd = ts_fd is not None and ts_fd > 0
    status = "OK" if (ok_le and ok_fd) else "FAIL!"
    if not (ok_le and ok_fd):
        errors.append(f"{c['name']}: 边界不精确! last_empty({le})={ts_le}, first_deposit({fd})={ts_fd}")
    print(f"  {c['name']}: block {le} totalSupply={ts_le} (should=0), block {fd} totalSupply={ts_fd:,} (should>0) [{status}]")

print()
print("=" * 80)
print("验证 3: 22b12110/39ea8e7f 扩大搜索范围找真实部署区块")
print("=" * 80)
for c in [cases[4], cases[5]]:
    # Search from block 15000000 (well before any Fraxlend deployment)
    real_deploy = find_deploy_block(c['vault'], 15_000_000, c['susp_deploy'])
    print(f"  {c['name']}: vault {c['vault']}")
    print(f"    原始报告部署区块: {c['vault_deploy']}")
    print(f"    扩大搜索后部署区块: {real_deploy}")
    if real_deploy and real_deploy < c['vault_deploy']:
        # Check if vault was empty at the real deploy block
        ts_at_deploy = get_total_supply(c['vault'], real_deploy)
        print(f"    真实部署区块 totalSupply: {ts_at_deploy}")
        if ts_at_deploy == 0:
            errors.append(f"{c['name']}: vault 在真实部署区块 {real_deploy} 时 totalSupply=0! 可能存在更早的空窗口!")
            # Find first deposit after real deploy
            ts_check = get_total_supply(c['vault'], real_deploy + 1)
            print(f"    block {real_deploy+1} totalSupply: {ts_check}")
            ts_check2 = get_total_supply(c['vault'], real_deploy + 10)
            print(f"    block {real_deploy+10} totalSupply: {ts_check2}")
    elif real_deploy == c['vault_deploy']:
        print(f"    部署区块一致 [OK]")
    c['real_deploy'] = real_deploy

print()
print("=" * 80)
print("验证 4: 检查 vault 中途是否曾被提空 (抽样 10 个中间区块)")
print("=" * 80)
for c in cases:
    fd = c['first_deposit'] or c.get('real_deploy', c['vault_deploy'])
    sd = c['susp_deploy']
    if sd <= fd:
        print(f"  {c['name']}: susp_deploy <= first_deposit, 跳过")
        continue
    # Sample 10 blocks uniformly between first_deposit and susp_deploy
    gap = sd - fd
    step = max(gap // 11, 1)
    sample_blocks = [fd + step * i for i in range(1, 11) if fd + step * i < sd]
    found_empty = False
    for blk in sample_blocks:
        ts = get_total_supply(c['vault'], blk)
        if ts is not None and ts == 0:
            print(f"  {c['name']}: !! 在 block {blk} 发现 totalSupply=0 [EMPTY]")
            found_empty = True
            errors.append(f"{c['name']}: vault 在 block {blk} 被提空过!")
            break
    if not found_empty:
        min_ts = None
        for blk in sample_blocks:
            ts = get_total_supply(c['vault'], blk)
            if ts is not None and (min_ts is None or ts < min_ts):
                min_ts = ts
        print(f"  {c['name']}: {len(sample_blocks)} 个抽样点均有存款, 最小 totalSupply={min_ts:,} [OK]")

print()
print("=" * 80)
print("验证 5: 确认 vault 地址的 controller() 在可疑区块可调用")
print("=" * 80)
for c in cases:
    # controller() selector = 0xf77c4791
    result = rpc_call('eth_call', [{'to': c['vault'], 'data': '0xf77c4791'}, hex(c['block'])])
    if result and len(result) >= 66:
        ctrl = '0x' + result[26:66]
        print(f"  {c['name']}: controller = {ctrl} [OK]")
    else:
        print(f"  {c['name']}: controller() 调用失败! result={result}")
        errors.append(f"{c['name']}: controller() 调用失败")

print()
print("=" * 80)
print(f"最终验证结果: {'ALL PASSED' if not errors else f'{len(errors)} ISSUES FOUND'}")
print("=" * 80)
if errors:
    for e in errors:
        print(f"  !! {e}")
else:
    print("  所有验证通过，结论可靠：7 个 vault 在可疑合约部署时均非空，不可攻击。")

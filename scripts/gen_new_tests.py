#!/usr/bin/env python3
"""为7个新合约生成crvUSD模板测试文件"""
import os

def to_checksum(addr):
    addr_lower = addr.lower().replace('0x', '')
    try:
        import sha3
        h = sha3.keccak_256(addr_lower.encode()).hexdigest()
    except ImportError:
        from Crypto.Hash import keccak
        k = keccak.new(digest_bits=256)
        k.update(addr_lower.encode())
        h = k.hexdigest()
    result = '0x'
    for i, c in enumerate(addr_lower):
        if c.isdigit():
            result += c
        elif int(h[i], 16) >= 8:
            result += c.upper()
        else:
            result += c.lower()
    return result

TEMPLATE_PATH = r'd:\区块链\calldata_bridge\templates\ERC4626AttackTemplate.sol'
OUTPUT_DIR = r'd:\区块链\DeFiHackLabs\src\test\2026-erc4626\generated'

with open(TEMPLATE_PATH, 'r', encoding='utf-8') as f:
    template_base = f.read()

# Fix import path for generated/ subdir
template_base = template_base.replace(
    'import "../basetest.sol";',
    'import "../../basetest.sol";'
)

CURVE_CALLDATA = (
    "0x3df02124"
    "0000000000000000000000000000000000000000000000000000000000000000"
    "0000000000000000000000000000000000000000000000000000000000000001"
    "00000000000000000000000000000000000000000000000000000000ee6b2800"
    "0000000000000000000000000000000000000000000000000000000000000000"
)

contracts = [
    ('0x08064A8eEecf71203449228f3eaC65E462009fdF', 22035016),
    ('0x4A7c64932d1ef0b4a2d430ea10184e3B87095E33', 22035030),
    ('0x27AB448a75d548ECfF73f8b4F36fCc9496768797', 22284975),
    ('0xD42535Cda82a4569BA7209857446222ABd14A82c', 23265624),
    ('0x22B12110f1479d5D6Fd53D0dA35482371fEB3c7e', 22035027),
    ('0x39Ea8e7f44E9303A7441b1E1a4F5731F1028505C', 22035022),
    ('0x2fdD3c0a682e5774205F0F6D3eD3c9D1b9Cb9413', 24131008),
]

os.makedirs(OUTPUT_DIR, exist_ok=True)

for addr, block in contracts:
    short = addr.replace('0x', '')[:8].lower()
    contract_name = f'Case_{short}_{block}'
    checksummed = to_checksum(addr)

    sol = template_base
    sol = sol.replace('{{CONTRACT_NAME}}', contract_name)
    sol = sol.replace('{{SUSPICIOUS_CONTRACT}}', checksummed)
    sol = sol.replace('{{FORK_BLOCK_NUMBER}}', str(block))
    sol = sol.replace('{{FLASH_LOAN_AMOUNT}}', '4_000 * 1e6')
    sol = sol.replace('{{ATTACKER_TRANSFER_AMOUNT}}', '2_000 * 1e18')
    sol = sol.replace('{{ATTACKER_MINT_AMOUNT}}', '1')
    sol = sol.replace('{{CURVE_INPUTDATA}}', CURVE_CALLDATA)

    out_path = os.path.join(OUTPUT_DIR, f'{contract_name}.sol')
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(sol)
    print(f'Generated: {contract_name}.sol  ({checksummed})')

print(f'\nDone! Generated {len(contracts)} files in {OUTPUT_DIR}')

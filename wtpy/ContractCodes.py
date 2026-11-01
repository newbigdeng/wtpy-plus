"""Futures contract identities; CZCE decades come from frozen metadata."""
from datetime import date
import re


def raw_code(standard):
    match = re.fullmatch(r'([A-Za-z0-9_]+)\.([A-Za-z]+)\.(\d{4})', standard)
    if not match:
        raise ValueError('Expected a four-digit standard futures month: '+str(standard))
    exchange, product, month = match.groups()
    return product+(month[1:] if exchange == 'CZCE' else month)


def contract_standard_code(exchange, raw, metadata):
    product = metadata['product']
    if not raw.startswith(product):
        raise ValueError('Contract product does not match its raw code')
    month = raw[len(product):]
    if not month.isdecimal() or len(month) not in (3,4):
        raise ValueError('Expected a numeric futures delivery month')
    if len(month) == 3:
        if exchange != 'CZCE':
            raise ValueError('Only CZCE uses a three-digit raw delivery month')
        expiry_text = str(metadata.get('expiredate', ''))
        if not re.fullmatch(r'\d{8}', expiry_text):
            raise ValueError('CZCE raw code needs a frozen contract expiry date')
        expiry = int(expiry_text)
        delivery = date(expiry//10000, expiry//100%100, expiry%100)
        if delivery.year%10 != int(month[0]) or delivery.month != int(month[1:]):
            raise ValueError('CZCE raw delivery month conflicts with its frozen expiry')
        month = f'{delivery.year%100:02d}{delivery.month:02d}'
    return f'{exchange}.{product}.{month}'


def contract_info(contracts, standard):
    exchange = standard.split('.')[0]
    raw = raw_code(standard)
    metadata = contracts[exchange][raw]
    if contract_standard_code(exchange, raw, metadata) != standard:
        raise ValueError('Standard contract conflicts with its frozen expiry: '+standard)
    return metadata

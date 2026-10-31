"""Explicit futures input contracts and conservative per-label cost diagnostics."""
import math


def check_requirements(spec, snapshot):
    requirements = spec.get('data_requirements', {})
    series = requirements.get('contract_series', 'single_contract')
    if series != snapshot.get('contract_series', 'single_contract'):
        raise ValueError('Required contract series is unavailable: ' + series)
    if requirements.get('code', snapshot['code']) != snapshot['code']:
        raise ValueError('Requested contract differs from the frozen data')
    fields = {item['field'] for item in spec['inputs']}
    contracts = snapshot.get('field_contracts', {})
    if snapshot.get('schema', 1) >= 2:
        for field in fields:
            item = contracts.get(field, {})
            if item.get('availability') not in ('bar_close', 'next_trading_session') or not item.get('source'):
                raise ValueError('Field source and availability are required: ' + field)
        if 'settlement' in fields and spec['frequency'] != 'd':
            raise ValueError('Settlement is a daily publication, not an intraday close')
        if not snapshot.get('instrument', {}).get('multiplier'):
            raise ValueError('Futures contract metadata is unavailable')


def roundtrip_cost(entry, exit_price, snapshot):
    """One-lot diagnostic, not a fill or a portfolio accounting model."""
    binding = snapshot.get('cost_model')
    if not binding:
        return None
    finance = binding['finance']; fees = finance['fees']
    instrument = snapshot['instrument']; multiplier = instrument['multiplier']
    if min(entry, exit_price, multiplier) <= 0 or not all(map(math.isfinite, (entry, exit_price, multiplier))):
        raise ValueError('Positive finite prices and contract multiplier are required')
    close_rate = max(fees['close'], fees['closetoday'])
    def charge(price, rate):
        return max(rate if fees['byvolume'] else price * multiplier * rate, fees['minfee'])
    fee = charge(entry, fees['open']) + charge(exit_price, close_rate)
    slip = 2 * finance['slippage_ticks'] * instrument['price_tick'] * multiplier
    return (fee + slip) / (entry * multiplier)

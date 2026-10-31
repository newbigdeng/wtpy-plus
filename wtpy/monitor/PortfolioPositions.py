"""Actual native positions only; missing receipts never mean an empty account."""
from __future__ import annotations
import json
import ctypes
import math
import os
from pathlib import Path
import re
import time


def _read(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    if not isinstance(value, dict): raise ValueError('Native receipt must be an object')
    return value


def _lots(value):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or int(value) != value:
        raise ValueError('Native futures volume must be nonnegative integer lots')
    return int(value)


def standard_code(value, codes=None):
    if not isinstance(value, str): raise ValueError('Native position code is missing')
    if codes is not None:
        aliases = {}
        for code in codes:
            exchange, product, month = code.split('.')
            for alias in (code, exchange + '.' + product + month):
                if alias in aliases and aliases[alias] != code: raise ValueError('Ambiguous contract identity')
                aliases[alias] = code
        if value not in aliases: raise ValueError('Native receipt contains an unknown contract: ' + value)
        return aliases[value]
    if re.fullmatch(r'[A-Za-z0-9_]+\.[A-Za-z]+\.\d{3,4}', value): return value
    match = re.fullmatch(r'([A-Za-z0-9_]+)\.([A-Za-z]+)(\d{3,4})', value)
    if not match: raise ValueError('Native position code is not a standard futures contract')
    return '.'.join(match.groups())


def signed_positions(rows, codes=None, *, replay=False):
    if not isinstance(rows, list): raise ValueError('Native positions list is missing')
    sides = {}
    for row in rows:
        if not isinstance(row, dict): raise ValueError('Malformed native position row')
        code = standard_code(row.get('code'), codes)
        current = sides.setdefault(code, [0, 0])
        if replay:
            side = row.get('direction')
            if type(side) is not int or side not in (0, 1, 48, 49): raise ValueError('Unknown native position direction')
            current[0 if side in (0, 48) else 1] += _lots(row.get('volume'))
        else:
            for index, name in enumerate(('long', 'short')):
                leg = row.get(name)
                if not isinstance(leg, dict): raise ValueError('Native long/short volumes are missing')
                current[index] += _lots(leg.get('newvol')) + _lots(leg.get('prevol'))
    if any(long and short for long, short in sides.values()):
        raise ValueError('Hedged native positions cannot be treated as a flat net position')
    result = {code: 0 for code in codes or ()}
    result.update({code: long - short for code, (long, short) in sides.items()})
    return result


def _trading_day(context, receipt):
    day = receipt.get('trading_date')
    if type(day) is not int or day <= 0 or day != int(context.stra_get_tdate()):
        raise ValueError('Native positions do not belong to the current trading day')


def replay_positions_provider(path, codes, *, clock=None, started_at=0):
    path = Path(path)
    def provider(context):
        if path.stat().st_mtime < started_at: raise ValueError('Replay position receipt predates this run')
        receipt = _read(path); _trading_day(context, receipt)
        stamp = int(receipt['date']) * 1000000000 + int(receipt['time'])
        limit = clock() if clock is not None else int(context.stra_get_date()) * 1000000000 + int(context.stra_get_time()) * 100000
        if stamp > limit: raise ValueError('Replay position receipt is ahead of the decision clock')
        return signed_positions(receipt.get('positions'), codes, replay=True)
    return provider


def _live_receipt(root, context, *, now=None, max_age=300):
    root = Path(root); engine = context.__engine__
    strategy = context.__stra_info__
    channel = getattr(engine, '_risk_channels', {}).get(strategy.name())
    if not isinstance(channel, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', channel):
        raise ValueError('The strategy has no verified native trader channel')
    path = root / 'generated' / 'traders' / channel / 'rtdata.json'
    status = _read(path.with_name(path.name + '.status.json'))
    control = _read(root / 'generated' / 'strategy-control' / 'status.json')
    if (type(status.get('pid')) is not int or type(control.get('pid')) is not int
            or status.get('pid') != os.getpid() or control.get('pid') != os.getpid()
            or status.get('ready') is not True or status.get('connected') is not True
            or control.get('alive') is False):
        raise ValueError('Native trader/control receipts are not ready in this process')
    updated = status.get('updated_at')
    if type(updated) not in (int, float) or not math.isfinite(updated): raise ValueError('Native status timestamp is missing')
    updated = updated / 1000 if updated > 100000000000 else updated
    if not -1 <= (time.time() if now is None else now) - updated <= max_age:
        raise ValueError('Native trader status is stale or in the future')
    _trading_day(context, status)
    receipt = _read(path)
    if status != _read(path.with_name(path.name + '.status.json')):
        raise ValueError('Native trader identity changed while reading positions')
    return receipt


def read_live_positions(context, root='.', codes=None, *, now=None, max_age=300):
    return signed_positions(_live_receipt(root, context, now=now, max_age=max_age).get('positions'), codes)


def query_portfolio_state(engine, traderid, strategyid):
    """Return the native read-only DTO; never manufacture an empty exposure."""
    getter = getattr(getattr(getattr(engine, '__wrapper__', None), 'api', None), 'query_portfolio_state', None)
    if getter is None: return dict(schema=1, producer='wt-native-portfolio/v1', valid=False, reason='Native portfolio API unavailable')
    try:
        getter.argtypes = [ctypes.c_char_p, ctypes.c_char_p]; getter.restype = ctypes.c_char_p
        raw = getter(traderid.encode(), strategyid.encode())
        value = json.loads(raw) if raw else None
        if not isinstance(value, dict): raise ValueError('Native exposure must be an object')
        return value
    except (OSError, ValueError, TypeError, AttributeError) as error:
        return dict(schema=1, producer='wt-native-portfolio/v1', valid=False, reason=str(error))


def _native_state(engine, context, root, replay):
    getter = getattr(getattr(getattr(engine, '__wrapper__', None), 'api', None), 'query_portfolio_state', None)
    if getter is None: return None
    strategy = context.__stra_info__.name()
    channel = getattr(engine, '_risk_channels', {}).get(strategy)
    if not isinstance(channel, str) or not channel: raise ValueError('Portfolio strategy has no native account route')
    value = query_portfolio_state(engine, channel, strategy)
    if (value.get('schema') != 1 or value.get('producer') != 'wt-native-portfolio/v1'
            or value.get('scope') != 'trader_account' or type(value.get('pid')) is not int
            or value['pid'] != os.getpid() or value.get('traderid') != channel
            or value.get('strategyid') != strategy or value.get('ready') is not True
            or value.get('connected') is not True or value.get('valid') is not True
            or value.get('positions_complete') is not True or value.get('orders_complete') is not True):
        raise ValueError('Native portfolio exposure is busy, unready or from another process/account')
    _trading_day(context, value)
    if type(value.get('event_seq')) is not int or value['event_seq'] < 0:
        raise ValueError('Native exposure event sequence is missing')
    if not replay:
        control = _read(Path(root) / 'generated' / 'strategy-control' / 'status.json')
        if type(control.get('pid')) is not int or control['pid'] != os.getpid() or control.get('alive') is False:
            raise ValueError('Native exposure does not match the managed process')
    return value


def _native_positions(value, codes):
    if not isinstance(value.get('actual_positions'), list): raise ValueError('Native actual positions are missing')
    rows = []
    for row in value['actual_positions']:
        if not isinstance(row, dict) or 'code' not in row: raise ValueError('Malformed native actual position')
        for side, field in enumerate(('long', 'short')):
            rows.append(dict(code=row['code'], direction=side, volume=row.get(field)))
    return signed_positions(rows, codes, replay=True)


def _native_orders(value, codes):
    pending = value.get('pending_submissions'); targets = value.get('target_pending'); tasks = value.get('execution_tasks')
    if (type(pending) is not int or pending < 0 or type(targets) not in (bool, int)
            or type(tasks) is not int or tasks < 0 or targets < 0
            or value.get('execution_idle') is not True or pending or targets or tasks):
        raise ValueError('Native submissions or execution targets have not settled')
    orders = value.get('working_orders')
    if not isinstance(orders, dict): raise ValueError('Native working orders are missing')
    result = {code: 0 for code in codes or ()}
    for code, count in orders.items(): result[standard_code(code, codes)] = _lots(count)
    return result


def attach_portfolio_providers(engine, root, *, codes=None, replay=False, clock=None):
    root = Path(root)
    snapshots = {}
    if replay:
        path = root / 'replay_account.json'; started_at = time.time()
        legacy = replay_positions_provider(path, codes, clock=clock, started_at=started_at)
    else:
        legacy = lambda context: read_live_positions(context, root, codes)
    def positions(context):
        snapshots.pop(id(context), None)
        value = _native_state(engine, context, root, replay)
        if value is None: return legacy(context)
        result = _native_positions(value, codes)
        snapshots[id(context)] = value
        return result
    def orders(context):
        value = snapshots.pop(id(context), None)
        if value is None: value = _native_state(engine, context, root, replay)
        if value is None: raise ValueError('No complete native working-order snapshot; opening risk remains reserved')
        return _native_orders(value, codes)
    engine._portfolio_position_provider = positions
    engine._portfolio_orders_provider = orders

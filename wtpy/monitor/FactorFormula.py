"""Small closed-bar expression interpreter; no Python evaluation or imports from input."""
from __future__ import annotations

import ast
from collections import deque
from datetime import datetime, timedelta, timezone
import math

FIELDS = frozenset(('open', 'high', 'low', 'close', 'volume', 'open_interest'))
FUNCTIONS = frozenset(('lag', 'delta', 'mean', 'std', 'abs'))


def current_stamp():
    return int(datetime.now(timezone(timedelta(hours=8))).strftime('%Y%m%d%H%M'))


def _datetime(stamp):
    if type(stamp) is not int or len(str(stamp)) != 12:
        raise ValueError('availability must be a YYYYMMDDHHMM Shanghai timestamp')
    return datetime.strptime(str(stamp), '%Y%m%d%H%M')


def bar_available_at(row, period, snapshot=None):
    """Daily dates are trading-bar identities, never midnight availability."""
    stamp = row['time']; snapshot = snapshot or {}
    if period == 'd':
        day = str(stamp)[:8]
        datetime.strptime(day, '%Y%m%d')
        close_time = snapshot.get('daily_close_time', 1600)
        if type(close_time) is not int:
            raise ValueError('daily_close_time must be HHMM')
        close = int(day) * 10000 + close_time
        if len(str(stamp)) == 12:
            close = max(close, stamp)
        elif type(stamp) is not int or len(str(stamp)) != 8:
            raise ValueError('daily time must identify a real trading date')
    elif period in ('m1', 'm5'):
        close = stamp
    else:
        raise ValueError('period must be d, m1 or m5')
    _datetime(close)
    available = row.get('available_at', close)
    _datetime(available)
    if available < close:
        raise ValueError('available_at precedes bar close')
    return available


def normalize_asof(value, period, snapshot=None):
    """Read old daily snapshots without rewriting their frozen eight-digit date."""
    if period == 'd' and type(value) is int and len(str(value)) == 8:
        return bar_available_at({'time': value}, period, snapshot)
    _datetime(value)
    return value


def check_closed_rows(rows, period, snapshot=None, as_of=None):
    """Reject future snapshots and unavailable bars, including today's open daily bar."""
    snapshot = snapshot or {}
    if snapshot.get('period', period) != period or snapshot.get('bar_time_semantics', 'close') != 'close':
        raise ValueError('snapshot frequency and close-time semantics must match the formula')
    now = current_stamp()
    cutoff = normalize_asof(as_of if as_of is not None else snapshot.get('as_of', now), period, snapshot)
    snapshot_cutoff = normalize_asof(snapshot.get('as_of', cutoff), period, snapshot)
    if cutoff > now or snapshot_cutoff > now:
        raise ValueError('future_asof is not a closed-bar snapshot')
    if cutoff > snapshot_cutoff or snapshot.get('closed_bars_only', True) is not True:
        raise ValueError('requested cutoff must stay within a closed-bar snapshot')
    stamps = [row['time'] for row in rows]
    if any(type(t) is not int for t in stamps) or stamps != sorted(set(stamps)):
        raise ValueError('bar times must be unique and strictly increasing')
    if period == 'd' and len({str(t)[:8] for t in stamps}) != len(stamps):
        raise ValueError('daily frequency permits one actual bar per trading date')
    if any(bar_available_at(row, period, snapshot) > cutoff for row in rows):
        raise ValueError('input contains a bar not yet available at as_of')
    if any('available_at' in row and row['available_at'] != bar_available_at(
            {'time': row['time']}, period, snapshot) for row in rows):
        raise ValueError('delayed inputs require an asof_join at the decision time')
    return cutoff


def bar_segments(rows, period, window_semantics=None, data_snapshot=None):
    """Return segment IDs; a missing minute restarts both lookbacks and labels."""
    semantics = window_semantics or ('trading_bars' if period == 'd' else 'session_contiguous')
    if semantics not in ('trading_bars', 'session_contiguous'):
        raise ValueError('unsupported window_semantics')
    if period == 'd':
        if semantics != 'trading_bars':
            raise ValueError('daily windows count actual trading bars')
        if not (data_snapshot or {}).get('expected_bar_closes'):
            return [0] * len(rows)
    elif period not in ('m1', 'm5'):
        raise ValueError('period must be d, m1 or m5')
    expected = None
    if semantics == 'trading_bars':
        snapshot = data_snapshot or {}
        calendar = snapshot.get('expected_bar_closes')
        if (not snapshot.get('calendar_id') or not isinstance(calendar, list)
                or any(type(t) is not int for t in calendar) or calendar != sorted(set(calendar))):
            raise ValueError('trading_bars requires a frozen exchange calendar of expected_bar_closes')
        def identity(stamp):
            return int(str(stamp)[:8]) if period == 'd' else stamp
        identities = [identity(stamp) for stamp in calendar]
        if len(set(identities)) != len(identities):
            raise ValueError('calendar must contain one close per source bar')
        expected = {stamp: index for index, stamp in enumerate(identities)}
        if any(identity(row['time']) not in expected for row in rows):
            raise ValueError('bar time is outside the frozen exchange calendar')
    result = []; segment = 0; interval = timedelta(minutes=1 if period == 'm1' else 5)
    for index, row in enumerate(rows):
        stamp = row['time']
        if period != 'd':
            _datetime(stamp)
        if index:
            previous = rows[index - 1]['time']
            continuous = (expected[identity(stamp)] == expected[identity(previous)] + 1) if expected is not None else (
                _datetime(stamp) - _datetime(previous) == interval)
            if not continuous:
                segment += 1
        result.append(segment)
    return result


def asof_join(decisions, higher_rows):
    """Carry only higher-period values already available at each decision close."""
    if decisions != sorted(set(decisions)):
        raise ValueError('decision times must be strictly increasing')
    available = [row['available_at'] for row in higher_rows]
    if available != sorted(available) or any(row['time'] > row['available_at'] for row in higher_rows):
        raise ValueError('higher-period availability must follow its close, in order')
    result = []; index = 0; latest = None
    for decision in decisions:
        while index < len(higher_rows) and available[index] <= decision:
            latest = higher_rows[index]; index += 1
        result.append(latest)
    return result


def _tree(expression):
    if not isinstance(expression, str) or not expression.strip() or len(expression) > 1000:
        raise ValueError('expression must contain 1 to 1000 characters')
    tree = ast.parse(expression.strip(), mode='eval')
    if len(list(ast.walk(tree))) > 100:
        raise ValueError('expression is too complex')
    return tree.body


def _window(node, lookback):
    value = lookback if isinstance(node, ast.Name) and node.id == 'lookback' else (
        node.value if isinstance(node, ast.Constant) else None)
    if type(value) is not int or not 1 <= value <= 500:
        raise ValueError('windows must be integer 1..500 or lookback')
    return value


def validate(expression, lookback):
    """Return the exact fields and number of preceding bars required by this AST."""
    if type(lookback) is not int or not 1 <= lookback <= 500:
        raise ValueError('lookback must be integer 1..500')
    fields = set()

    def visit(node, depth=0):
        if depth > 15:
            raise ValueError('expression nesting exceeds 15')
        if isinstance(node, ast.Name) and node.id in FIELDS:
            fields.add(node.id)
            return 0
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if math.isfinite(node.value) and abs(node.value) <= 1000000:
                return 0
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            return visit(node.operand, depth + 1)
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            return max(visit(node.left, depth + 1), visit(node.right, depth + 1))
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
            name = node.func.id
            if name == 'abs' and len(node.args) == 1:
                return visit(node.args[0], depth + 1)
            if name in FUNCTIONS - {'abs'} and len(node.args) == 2:
                history = visit(node.args[0], depth + 1)
                window = _window(node.args[1], lookback)
                if name == 'std' and window < 2:
                    raise ValueError('sample standard deviation needs at least two bars')
                return history + (window if name in ('lag', 'delta') else window - 1)
        raise ValueError('unsupported expression node: ' + type(node).__name__)

    history = visit(_tree(expression))
    if not fields or history > 2000:
        raise ValueError('expression needs market inputs and at most 2000 warmup bars')
    return dict(expression=expression, lookback=lookback, fields=sorted(fields), required_history=history,
                availability='bar_close', dsl_version=1)


def canonical(expression):
    """Structural identity for already allowed formulas, ignoring whitespace/math glyphs."""
    normalized = expression.translate(str.maketrans({'×': '*', '÷': '/', '−': '-'}))
    return ast.dump(_tree(normalized), annotate_fields=False)


def evaluate(rows, expression, lookback, *, period=None, window_semantics=None, data_snapshot=None, as_of=None):
    """Return one finite value or None per input row; all accesses are backward only."""
    definition = validate(expression, lookback)
    if len(rows) > 50000:
        raise ValueError('at most 50000 frozen bars')
    stamps = [row['time'] for row in rows]
    if any(type(t) is not int for t in stamps) or stamps != sorted(set(stamps)):
        raise ValueError('time must be unique, increasing integer bar-close stamps')
    if period is None and rows and len(str(stamps[0])) in (8, 12):
        period = 'd' if len(str(stamps[0])) == 8 else 'm1'
    if period is not None:
        check_closed_rows(rows, period, data_snapshot, as_of)
        segments = bar_segments(rows, period, window_semantics, data_snapshot)
    else:
        if window_semantics is not None or data_snapshot is not None or as_of is not None:
            raise ValueError('explicit temporal context requires a period')
        segments = [0] * len(rows)  # Legacy ordinal fixtures, not calendar timestamps.
    columns = {}
    for field in definition['fields']:
        values = []
        for row in rows:
            value = row.get(field)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError('missing or nonfinite input: ' + field)
            if field in ('open', 'high', 'low', 'close') and value <= 0:
                raise ValueError('prices must be positive')
            if field in ('volume', 'open_interest') and value < 0:
                raise ValueError('volume/open_interest must be nonnegative')
            values.append(float(value))
        columns[field] = values

    def safe(op, left, right):
        if left is None or right is None:
            return None
        if op == '/' and right == 0:
            return None
        value = left + right if op == '+' else left - right if op == '-' else left * right if op == '*' else left / right
        return value if math.isfinite(value) else None

    def rolling(values, window, std=False):
        queue = deque(); total = squared = 0.0; missing = 0; result = []
        anchor = None
        for index, value in enumerate(values):
            if index and segments[index] != segments[index - 1]:
                queue.clear(); total = squared = 0.0; missing = 0; anchor = None
            queue.append(value)
            if value is None:
                missing += 1
            else:
                if anchor is None:
                    anchor = value
                centered = value - anchor
                total += centered; squared += centered * centered
            if len(queue) > window:
                old = queue.popleft()
                if old is None:
                    missing -= 1
                else:
                    centered = old - anchor
                    total -= centered; squared -= centered * centered
            if len(queue) != window or missing:
                result.append(None)
            elif std:
                result.append(math.sqrt(max(0.0, (squared - total * total / window) / (window - 1))))
            else:
                result.append(anchor + total / window)
        return result

    def compute(node):
        if isinstance(node, ast.Name):
            return columns[node.id]
        if isinstance(node, ast.Constant):
            return [float(node.value)] * len(rows)
        if isinstance(node, ast.UnaryOp):
            sign = -1 if isinstance(node.op, ast.USub) else 1
            return [None if value is None else sign * value for value in compute(node.operand)]
        if isinstance(node, ast.BinOp):
            op = {ast.Add: '+', ast.Sub: '-', ast.Mult: '*', ast.Div: '/'}[type(node.op)]
            return [safe(op, left, right) for left, right in zip(compute(node.left), compute(node.right))]
        values = compute(node.args[0]); name = node.func.id
        if name == 'abs':
            return [None if value is None else abs(value) for value in values]
        window = _window(node.args[1], lookback)
        if name in ('mean', 'std'):
            return rolling(values, window, name == 'std')
        shifted = [values[index - window] if index >= window and segments[index] == segments[index - window]
                   else None for index in range(len(rows))]
        return shifted if name == 'lag' else [safe('-', a, b) for a, b in zip(values, shifted)]

    return [value if value is None or math.isfinite(value) else None for value in compute(_tree(expression))]

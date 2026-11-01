"""Daily physical delivery ledger for DCE soybean meal options. No broker connections."""

from collections import defaultdict
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import re


def number(x):
    try:
        v = Decimal(str(x))
    except InvalidOperation:
        raise ValueError("数值字段无效") from None
    if not v.is_finite():
        raise ValueError("价格、资金和费率须为有限数")
    return v


def money(x):
    return float(x.quantize(Decimal(".01"), rounding=ROUND_HALF_UP))


def replay(
    data,
    *,
    old,
    new,
    lots=2,
    roll=1,
    before=1,
    capital=100000,
    fee=1,
    exercise_fee=1,
    margin_rate=0.10,
    declarations=(),
    native_fills=None,
):
    contracts = data["contracts"]
    days = data["days"]
    if days != sorted(set(days)) or old not in contracts or new not in contracts:
        raise ValueError("请检查交易日历和合约")
    a, b = contracts[old], contracts[new]
    for code, c in ((old, a), (new, b)):
        m = re.fullmatch(r"(M\d{4})-([CP])-(\d+)\.DCE", code)
        if not m or (m[1] + ".DCE", m[2], int(m[3]), 10) != (c["future"], c["kind"], c["strike"], c["mult"]):
            raise ValueError("合约资料必须对应一手十吨的大商所豆粕期权")
    if (
        not days
        or a["expiry"] not in days
        or not days[0] < a["expiry"] < days[-1]
        or b["expiry"] <= a["expiry"]
        or a["kind"] != b["kind"]
    ):
        raise ValueError("请选择方向相同的新旧合约，行情须跨旧合约到期日")
    if b["expiry"] <= days[-1] and b["expiry"] not in days:
        raise ValueError("日历缺少新合约到期日")
    max_roll = 10000 if native_fills is not None else abs(lots)
    if not (type(lots) is int and 0 < abs(lots) <= 10000 and type(roll) is int and 0 <= roll <= max_roll):
        raise ValueError("移仓手数不得超过初始手数")
    if type(before) is not int or not 0 <= before < days.index(a["expiry"]):
        raise ValueError("移仓日必须在开仓日之后")
    roll_day = days[days.index(a["expiry"]) - before]
    cash = initial = number(capital)
    fee, exercise_fee, rate = map(number, (fee, exercise_fee, margin_rate))
    if initial <= 0 or min(fee, exercise_fee) < 0 or not 0 < rate <= 1:
        raise ValueError("请检查初始资金、手续费和保证金比例")
    actions = {}
    for row in declarations:
        key = (row["day"], row["code"])
        if (
            key in actions
            or key[0] not in days
            or key[1] not in (old, new)
            or key[0] > contracts[key[1]]["expiry"]
            or row["action"] not in ("exercise", "assign", "abandon", "unassigned")
            or type(row["qty"]) is not int
            or row["qty"] <= 0
        ):
            raise ValueError("历史指令的日期、合约、动作或手数无效")
        actions[key] = row
    op = {old: 0, new: 0}
    fut = defaultdict(int)
    prev = {}
    journal, daily = [], []

    def post(action, code, qty, price, premium=0, variation=0, fees=0):
        nonlocal cash
        premium, variation, fees = (
            number(v).quantize(Decimal(".01"), rounding=ROUND_HALF_UP) for v in (premium, variation, fees)
        )
        cash += premium + variation - fees
        journal.append(
            dict(
                day=day,
                action=action,
                code=code,
                qty=qty,
                price=float(price) if price is not None else None,
                premium=money(number(premium)),
                variation=money(number(variation)),
                fee=money(number(fees)),
                cash=money(cash),
            )
        )

    def settle(f):
        v = data["futures"].get(day, {}).get(f)
        if v is None or number(v) <= 0:
            raise ValueError(f"{day} 缺少 {f} 当日结算价")
        return number(v)

    def opening(code):
        bar = contracts[code]["bars"].get(day) or {}
        if number(bar.get("volume", 0)) <= 0 or number(bar.get("open") or 0) <= 0:
            return None
        return number(bar["open"])

    def trade(code, qty, price, action, charge=None):
        op[code] += qty
        post(
            action,
            code,
            qty,
            price,
            premium=-qty * price * 10,
            fees=abs(qty) * fee if charge is None else charge,
        )
        if cash < 0:
            raise ValueError(f"{day} 权利金和手续费超过现金余额")

    def deliver(code, qty, action):
        c = contracts[code]
        signed = qty * (1 if c["kind"] == "C" else -1)
        f = c["future"]
        op[code] -= qty
        fut[f] += signed
        post(action, code, -qty, c["strike"])
        post(
            "期货承接",
            f,
            signed,
            c["strike"],
            variation=signed * (settle(f) - number(c["strike"])) * 10,
            fees=abs(qty) * exercise_fee,
        )

    native = defaultdict(list)
    for r in native_fills or ():
        if r["day"] not in days or r["day"] >= roll_day or r["code"] != old:
            raise ValueError("原生成交须全部属于旧合约且早于移仓日")
        native[r["day"]].append(r)
    for day in days:
        if native_fills is None and day == days[0]:
            px = opening(old)
            if px is None:
                raise ValueError(f"{day} 旧合约没有可用开盘成交")
            trade(old, lots, px, "开仓")
        for r in native[day]:
            q = r["qty"]
            if (r["close"] and (q * op[old] >= 0 or abs(q) > abs(op[old]))) or (
                not r["close"] and q * op[old] < 0
            ):
                raise ValueError("原生开平和已有仓位不符")
            trade(old, q, number(r["price"]), "原生成交", number(r["fee"]))
        if day == roll_day and roll:
            if abs(op[old]) < roll:
                raise ValueError("移仓手数超过旧合约当日持仓")
            q = roll if op[old] > 0 else -roll
            p1 = opening(old)
            if p1 is None:
                post("移仓未执行：旧合约无开盘成交", old, 0, None)
            else:
                trade(old, -q, p1, "移仓平旧")
                p2 = opening(new)
                if p2 is None:
                    post("移仓未开新：新合约无开盘成交", new, 0, None)
                else:
                    trade(new, q, p2, "移仓开新")
        for f, qty in list(fut.items()):
            if qty:
                px = settle(f)
                post("期货逐日盯市", f, qty, px, variation=qty * (px - prev[f]) * 10)
        for code in (old, new):
            c = contracts[code]
            d = actions.get((day, code))
            if d:
                q = d["qty"] * (-1 if d["action"] in ("assign", "unassigned") else 1)
                if q * op[code] <= 0 or abs(q) > abs(op[code]):
                    raise ValueError("历史行权、指派或放弃超出对应方向持仓")
                if d["action"] in ("abandon", "unassigned"):
                    if day != c["expiry"]:
                        raise ValueError("放弃自动行权仅适用于到期日")
                    op[code] -= q
                    post("主动放弃" if q > 0 else "到期未指派", code, -q, None)
                else:
                    deliver(code, q, "指定行权" if q > 0 else "指定指派")
                    if q < 0 and day == c["expiry"] and op[code]:
                        post("到期未指派", code, -op[code], None)
                        op[code] = 0
            if day == c["expiry"] and op[code]:
                q = op[code]
                itm = (settle(c["future"]) - number(c["strike"])) * (1 if c["kind"] == "C" else -1) > 0
                if itm:
                    deliver(code, q, "到期行权" if q > 0 else "到期指派（全配情景）")
                else:
                    op[code] = 0
                    post("到期失效", code, -q, None)
        margin = value = number(0)
        for f, q in fut.items():
            if q:
                prev[f] = settle(f)
                margin += abs(q) * prev[f] * 10 * rate
        for code, q in op.items():
            if not q:
                continue
            c = contracts[code]
            bar = c["bars"].get(day) or {}
            mark = bar.get("close")
            if mark is None or number(mark) <= 0:
                raise ValueError(f"{day} 缺少 {code} 收盘估值")
            mark = number(mark)
            value += q * mark * 10
            if q < 0:
                forward = settle(c["future"])
                fm = forward * rate
                otm = max(Decimal(0), (number(c["strike"]) - forward) * (1 if c["kind"] == "C" else -1))
                margin += abs(q) * 10 * (mark + max(fm - otm / 2, fm / 2))
        if cash < margin:
            raise ValueError(f"{day} 资金不足以覆盖回放保证金；不生成假定清算结果")
        daily.append(
            dict(
                day=day,
                options=dict(op),
                futures=dict(fut),
                cash=money(cash),
                option_value=money(value),
                equity=money(cash + value),
                margin=money(margin),
                available=money(cash - margin),
            )
        )
    return dict(
        source=data["source"],
        old=old,
        new=new,
        initial=money(initial),
        roll_day=roll_day,
        events=journal,
        daily=daily,
        final=daily[-1],
    )

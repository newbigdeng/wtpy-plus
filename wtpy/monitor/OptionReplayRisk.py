"""Minute-end exposures from the replay's own frozen market files and native fills."""

from collections import defaultdict
import csv
from pathlib import Path

from .HistoryDataWorker import dataset_records
from .OptionModels import contract_metrics, qdate
from wtpy.wrapper.WtDtHelper import WtDataHelper

FIELDS = ("delta", "gamma", "theta", "vega", "rho")


def marks(leg, folder, day, helper):
    period, ticker, exchange = leg["period"], leg["ticker"], leg["exchange"]
    root = folder / "market-data/his"
    if period == "tick":
        file = root / "ticks" / exchange / str(day) / (ticker + ".dsb")
    else:
        file = root / {"d": "day", "m1": "min1", "m5": "min5"}[period] / exchange / (ticker + ".dsb")
    if not file.is_file():
        return {}
    rows, stamp = dataset_records(dict(period=period, files=[str(file)]), helper)
    values = {}
    for row in rows:
        if period == "tick":
            if row.trading_date != day:
                continue
            good = 0 < row.bid_price_0 <= row.ask_price_0 < 1e12 and min(row.bid_qty_0, row.ask_qty_0) > 0
            price = (row.bid_price_0 + row.ask_price_0) / 2 if good else None
        else:
            if row.date != day:
                continue
            price = row.close if 0 < row.close < 1e12 else None
        values[stamp(row)] = price
    return values


def report(data):
    legs, day = data["legs"], int(data["day"])
    folder = Path(data["folder"])
    file = folder / "outputs_bt" / data["task"] / "trades.csv"
    if not file.is_file():
        raise ValueError("未找到原生回测成交流水")
    with file.open(encoding="utf-8-sig") as stream:
        trades = sorted(
            (
                (
                    int(t["time"][:12]),
                    t["code"],
                    float(t["qty"])
                    * (1 if t["direct"] == "LONG" else -1)
                    * (1 if t["action"] == "OPEN" else -1),
                )
                for t in csv.DictReader(stream)
            )
        )
    registered = {leg["code"] for leg in legs}
    if any(code not in registered for _, code, _ in trades):
        raise ValueError("成交含未登记的腿，无法完整汇总风险")
    helper = WtDataHelper()
    prices = {leg["code"]: marks(leg, folder, day, helper) for leg in legs}
    clocks = sorted({t for values in prices.values() for t in values})
    if not clocks:
        raise ValueError("该交易日没有任务行情快照")
    futures = {leg["underlying"]: leg["code"] for leg in legs if leg["leg_type"] == "future"}
    totals = []
    pos = defaultdict(float)
    idx = 0
    today = qdate(day)
    for stamp in clocks:
        while idx < len(trades) and trades[idx][0] <= stamp:
            _, code, n = trades[idx]
            pos[code] += n
            idx += 1
        grouped = {}
        for leg in legs:
            code = leg["code"]
            qty = pos[code]
            if not qty:
                continue
            group = grouped.setdefault(leg["underlying"], dict.fromkeys(FIELDS, 0.0))
            try:
                scale = leg["multiplier"]
                if not scale or scale <= 0:
                    raise ValueError("缺合约乘数")
                if leg["leg_type"] == "future":
                    group["delta"] += qty * scale
                    continue
                premium = prices[code].get(stamp)
                forward = prices.get(futures.get(leg["underlying"]), {}).get(stamp)
                if not premium or not forward:
                    raise ValueError("缺同期权或标的价格")
                metrics = contract_metrics(
                    forward,
                    leg["strike"],
                    premium,
                    qdate(leg["expiry"]) - today,
                    data["rate"],
                    leg["side"] == "C",
                    "american",
                    today,
                )
                for k in FIELDS:
                    group[k] += qty * scale * metrics[k]
            except (ValueError, RuntimeError, OverflowError) as err:
                group.setdefault("missing", []).append(code + " · " + str(err)[:96])
        for underlying, g in grouped.items():
            if g.get("missing"):
                g.update(dict.fromkeys(FIELDS, None))
            totals.append(dict(time=stamp, underlying=underlying, **g))
    return dict(
        day=day,
        rows=totals,
        sampled_minutes=len(clocks),
        missing_rows=sum(bool(g.get("missing")) for g in totals),
    )

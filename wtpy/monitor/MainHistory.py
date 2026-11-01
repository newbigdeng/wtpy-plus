"""Point-in-time futures leadership and contract-specific option selection."""

import csv
from datetime import date, datetime, timedelta
import json
import re
import sqlite3
from urllib.parse import urlencode
from urllib.request import urlopen
from zoneinfo import ZoneInfo

from .AssetMarkets import COMMODITY_EXCHANGES, inside
from .OptionBars import valid_bar

CN = ZoneInfo("Asia/Shanghai")


def product_key(code):
    match = re.fullmatch(r"([A-Z]+)\.([A-Za-z]{1,3})(?:\.?\d{3,4})?", code)
    if not match or match[1] not in (*COMMODITY_EXCHANGES, "CFFEX"):
        raise ValueError("请选择交易所和期货品种")
    return match[1], match[2]


def deliveries(start, end):
    # Domestic monthly contracts may list more than one year ahead.
    cursor = start.replace(day=1)
    while cursor <= end + timedelta(days=548):
        yield cursor.strftime("%y%m")
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)


def edb(code, begin, end, minutes=False):
    params = dict(
        symbol=code,
        period=60 if minutes else 86400,
        start_time=str(begin) + " 00:00:00",
        end_time=str(end) + " 23:59:59",
    )
    with urlopen(
        "https://edb.shinnytech.com/md/kline?" + urlencode(params), timeout=10
    ) as response:
        lines = response.read().decode("utf8").splitlines()
    if not lines or not lines[0].startswith("datetime_nano,"):
        raise ValueError("行情源未返回可信的合约 K 线")
    result = {}
    for r in csv.DictReader(lines):
        dt = datetime.fromtimestamp(int(r["datetime_nano"]) // 10**9, CN)
        if minutes:
            dt += timedelta(minutes=1)
        key = dt.strftime("%Y%m%d%H%M" if minutes else "%Y%m%d")
        values = [float(r[k]) for k in ("open", "high", "low", "close", "volume")]
        if valid_bar(*values):
            result[key] = dict(
                zip(("open", "high", "low", "close", "volume"), values),
                oi=float(r["close_oi"]),
                source="信易 EDB",
            )
    return result


def fetch(root, code, begin, end, minutes, local):
    """Cache exact closed windows; an unavailable provider never poisons a later retry."""
    folder = inside(root, "history", "main-cache")
    folder.mkdir(parents=True, exist_ok=True)
    file = inside(folder, code + ("-m1" if minutes else "-d") + ".sqlite3")
    a, b = str(begin), str(end)
    with sqlite3.connect(file, timeout=15) as db:
        db.execute(
            "create table if not exists bars (stamp text primary key, payload text)"
        )
        db.execute(
            "create table if not exists windows (a text,b text,primary key(a,b))"
        )
        ranges = db.execute(
            "select a,b from windows where b>=? and a<=? order by a", (a, b)
        ).fetchall()
        if not minutes:
            ranges = sorted(
                ranges
                + [
                    (str(datetime.strptime(k, "%Y%m%d").date()),) * 2
                    for k, bar in local.items()
                    if len(k) == 8 and bar.get("oi", 0) > 0
                ]
            )
        cursor = begin

        def request(first, last):
            data = edb(code, first, last, minutes)
            db.executemany(
                "insert or replace into bars values(?,?)",
                ((k, json.dumps(v)) for k, v in data.items()),
            )
            if last < datetime.now(CN).date():
                db.execute(
                    "insert or replace into windows values(?,?)",
                    (str(first), str(last)),
                )

        for lo, hi in ranges:
            if cursor < date.fromisoformat(lo):
                request(cursor, date.fromisoformat(lo) - timedelta(days=1))
            cursor = max(cursor, date.fromisoformat(hi) + timedelta(days=1))
        if cursor <= end:
            request(cursor, end)
        result = {
            t: json.loads(p)
            for t, p in db.execute(
                "select stamp,payload from bars where stamp between ? and ?",
                (a.replace("-", ""), b.replace("-", "") + "~"),
            )
        }
    for k, bar in local.items():
        upstream = result.get(k, {})
        result[k] = dict(bar, oi=bar.get("oi") or upstream.get("oi") or 0)
    return result


def main_plan(daily, begin, end):
    """Forward-only roll. A decision for D uses the preceding session's closing OI."""
    from .MarketHistory import open_day

    dates = sorted({d for data in daily.values() for d in data})
    result = []
    active = None
    factor = 1.0
    for prior, day in zip(dates, dates[1:]):
        if day < begin or day > end:
            continue
        prior_date = datetime.strptime(prior, "%Y%m%d").date()
        next_date = prior_date + timedelta(days=1)
        while next_date.strftime("%Y%m%d") < day:
            if open_day(next_date):
                raise ValueError(f"{next_date} 缺少决定主力的持仓量")
            next_date += timedelta(days=1)
        last_delivery = (prior_date + timedelta(days=548)).strftime("%Y%m")
        eligible = [
            (v[prior]["oi"], v[prior]["volume"], month)
            for month, v in daily.items()
            if prior in v
            and v[prior].get("oi", 0) > 0
            and (active is None or month >= active)
            and day[:6] <= "20" + month <= last_delivery
        ]
        if not eligible:
            raise ValueError(f"{prior} 缺少可决定 {day} 主力的有效持仓量")
        month = max(eligible)[2]
        if active and active != month:
            old = daily[active].get(prior)
            if not old:
                raise ValueError(f"{prior} 缺少旧约同步收盘价")
            factor *= old["close"] / daily[month][prior]["close"]
        if day not in daily[month]:
            raise ValueError(f"{day} 主力 {month} 缺少行情")
        result.append(dict(day=day, prior=prior, month=month, factor=factor))
        active = month
    if not result:
        raise ValueError("区间内没有足够的主力历史")
    observed = {row["day"] for row in result}
    cursor = datetime.strptime(begin, "%Y%m%d").date()
    while cursor.strftime("%Y%m%d") <= end:
        if open_day(cursor) and cursor.strftime("%Y%m%d") not in observed:
            raise ValueError(f"{cursor} 是交易日，历史目录缺少可用行情")
        cursor += timedelta(days=1)
    return result


def segments(plan):
    groups = []
    for r in plan:
        if not groups or (groups[-1]["month"], groups[-1].get("code")) != (
            r["month"],
            r.get("code"),
        ):
            groups.append(
                dict(
                    month=r["month"],
                    code=r.get("code"),
                    start=r["day"],
                    prior=r["prior"],
                    factor=r["factor"],
                )
            )
        groups[-1]["end"] = r["day"]
    return groups


def ranked_successors(old_strike, old_future, new_future, contracts):
    target = old_strike / old_future * new_future
    if not contracts:
        raise ValueError("新标的缺少同方向、当时已挂牌且有报价的期权")
    return sorted(
        contracts, key=lambda r: (abs(r["strike"] - target), r["strike"], r["raw"])
    )


def range_dates(start, end):
    a, b = date.fromisoformat(start), date.fromisoformat(end)
    if b < a or (b - a).days > 730 or b >= datetime.now(CN).date():
        raise ValueError("请选择截至昨日、两年以内的历史区间")
    return a, b


def local_bars(root, exchange, raw, period, start, end):
    from .HistoryDataWorker import dataset_records, WtDataHelper
    from .OptionWatchArchive import native_paths

    files = native_paths(root, exchange, raw, period)
    if not files:
        return {}
    a = int(start.strftime("%Y%m%d") + "0000")
    b = int(end.strftime("%Y%m%d") + "2359")
    rows, clock = dataset_records(
        dict(period=period, files=files, start=a, end=b), WtDataHelper()
    )
    return {
        str(clock(r))[: 8 if period == "d" else 12]: dict(
            open=r.open,
            high=r.high,
            low=r.low,
            close=r.close,
            volume=r.vol,
            oi=r.hold,
            day=str(r.date),
            source="WT",
        )
        for r in rows
        if valid_bar(r.open, r.high, r.low, r.close, r.vol)
    }


def product_history(root, code, start, end):
    exchange, product = product_key(code)
    a, b = range_dates(start, end)
    warm = a - timedelta(days=15)
    from concurrent.futures import ThreadPoolExecutor

    def load(month):
        raw = product + (month[1:] if exchange == "CZCE" else month)
        local = local_bars(root, exchange, raw, "d", warm, b)
        bars = fetch(root, exchange + "." + raw, warm, b, False, local)
        return month, bars

    with ThreadPoolExecutor(max_workers=3) as pool:
        data = {m: v for m, v in pool.map(load, deliveries(warm, b)) if v}
    plan = main_plan(data, a.strftime("%Y%m%d"), b.strftime("%Y%m%d"))
    return data, plan

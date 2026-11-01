"""Prepare an isolated WT CTA replay with a signal series and tradable real legs."""

from collections import defaultdict
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
from .MainHistory import (
    product_key,
    product_history,
    range_dates,
    fetch,
    local_bars,
    ranked_successors,
)
from .AssetMarkets import OPTION, dataset_code, inside
from .MarketSubscriptions import _load
from .PlusBacktest import write_json


def option_terms(root, exchange):
    terms = {}
    for file in sorted((root / "history/native").glob("*/common/contracts.json")) + [
        root / "common/contracts.json"
    ]:
        if not file.is_file():
            continue
        for raw, item in _load(file).get(exchange, {}).items():
            m = OPTION.fullmatch(raw)
            if not m:
                continue
            terms[raw] = dict(
                terms.get(raw, {}),
                underlying=exchange + "." + m[1] + m[2],
                strike=float(m[5]),
                side="看涨" if m[4] == "C" else "看跌",
                expiry=item.get("expiredate"),
            )
    return terms


def listed_options(root, ex, product, day, exact=None):
    """A dated directory is cached separately from today's executable base files."""
    from urllib.request import urlopen
    from urllib.parse import urlencode

    cache = inside(root, "history", "main-cache")
    cache.mkdir(parents=True, exist_ok=True)
    file = inside(cache, f"{ex}-{exact or product}-{day}.json")
    if file.exists():
        return json.loads(file.read_bytes())
    stamp = datetime.strptime(day, "%Y%m%d").date().isoformat()
    params = dict(
        markets=ex,
        types="option",
        products=product + "_o" if ex == "DCE" else product,
        tradingday=stamp,
    )
    if exact:
        params.pop("products")
        params["instruments"] = exact
    with urlopen(
        "http://dict.openctp.cn/instruments?" + urlencode(params), timeout=8
    ) as r:
        payload = json.load(r)
    if payload.get("rsp_code") != 0:
        raise ValueError("历史期权目录不可用")
    result = {}
    for item in payload.get("data", []):
        raw = item["InstrumentID"]
        m = OPTION.fullmatch(raw)
        if (
            not m
            or m[1] != product
            or item.get("ExchangeID") != ex
            or not item.get("OpenDate")
            or item["OpenDate"] > stamp
        ):
            continue
        result[raw] = dict(
            underlying=ex + "." + m[1] + m[2],
            strike=float(m[5]),
            side="看涨" if m[4] == "C" else "看跌",
            expiry=item["ExpireDate"].replace("-", ""),
            opened=item["OpenDate"].replace("-", ""),
        )
    if result:
        write_json(file, result)
    return result


def option_day(root, ex, raw, start, end, expiry, required):
    from .OptionWatchArchive import database, read, put
    from .OptionWatch import provider_future
    from .OptionExpiryHistory import daily
    from .OptionBars import valid_bar
    from contextlib import closing

    local = local_bars(root, ex, raw, "d", start, end)
    with closing(database(root, ex, raw)) as db:
        for r in read(db, "d", end.isoformat(), 800):
            local.setdefault(r["day"].replace("-", ""), r)
    m = OPTION.fullmatch(raw)
    missing = any(k not in local for k in required if not expiry or k <= str(expiry))
    if missing:
        # Resolve CZCE's decade using the selected historical window.
        month = provider_future(ex, m[1] + m[2], expiry or end.strftime("%Y%m%d"))
        imported = []
        for r in daily(month + m[4] + m[5], True):
            k = r["d"].replace("-", "")
            if not start.strftime("%Y%m%d") <= k <= end.strftime("%Y%m%d"):
                continue
            nums = [float(r[x]) for x in ("o", "h", "l", "c", "v")]
            if valid_bar(*nums) and nums[-1] > 0 and k not in local:
                local[k] = dict(
                    zip(("open", "high", "low", "close", "volume"), nums),
                    day=r["d"],
                    source="新浪期权日线",
                )
                imported.append(local[k])
        if imported:
            with closing(database(root, ex, raw)) as db:
                put(db, "d", imported)
    return local


def trade_day(clock, days):
    day = clock[:8]
    if clock[8:12] >= "1800":
        return next((d for d in days if d > day), None)
    return next((d for d in days if d >= day), None)


def prepare(folder, root, task):
    from wtpy.WtCoreDefs import WTSBarStruct
    from wtpy.wrapper.WtDtHelper import WtDataHelper

    ex, product = product_key(task["product"])
    start, end = range_dates(task["start"], task["end"])
    data, plan = product_history(root, task["product"], task["start"], task["end"])
    terms = option_terms(root, ex) if task["asset"] == "option" else {}
    raw_future = lambda m: product + (m[1:] if ex == "CZCE" else m)
    optional = task["asset"] == "option"
    old_raw = None
    old_month = None
    factor = 1.0
    daily = {}
    days = [r["day"] for r in plan]

    def option_prices(raw):
        if raw not in daily:
            daily[raw] = option_day(
                root,
                ex,
                raw,
                start - timedelta(days=15),
                end,
                terms.get(raw, {}).get("expiry"),
                [days[0], plan[0]["prior"], *days],
            )
        return daily[raw]

    for row in plan:
        month = row["month"]
        prior = row["prior"]
        if optional and (
            old_raw is None
            or month != old_month
            or str(terms[old_raw].get("expiry") or "") <= row["day"]
        ):
            try:
                terms.update(listed_options(root, ex, product, prior))
            except (OSError, ValueError):
                if not terms:
                    raise
        if optional and old_month:
            month = max(old_month, month)
            if (
                month == old_month
                and str(terms[old_raw].get("expiry") or "") <= row["day"]
            ):
                later = [
                    (v[prior].get("oi", 0), m)
                    for m, v in data.items()
                    if m > month
                    and prior in v
                    and any(
                        t.get("underlying") == ex + "." + raw_future(m)
                        and str(t.get("expiry") or "") > row["day"]
                        for t in terms.values()
                    )
                ]
                if not later:
                    raise ValueError("到期前没有可用的新月份")
                month = max(later)[1]
        row["month"] = month
        raw = raw_future(month)
        if optional:
            if old_raw is None:
                raw = task["option"].partition(".")[2]
                if raw not in terms:
                    terms.update(listed_options(root, ex, product, prior, raw))
                chosen = terms.get(raw, {})
                if (
                    chosen.get("opened", prior) > prior
                    or not option_prices(raw).get(prior, {}).get("volume", 0) > 0
                ):
                    raise ValueError("起始期权在决策日缺少成交记录")
                if chosen.get("underlying") != ex + "." + raw_future(month):
                    raise ValueError("起始期权必须属于首个回测交易日的主力月份")
            elif month == old_month:
                raw = old_raw
            else:
                old = terms[old_raw]
                new_f = data[month][prior]["close"]
                matches = ranked_successors(
                    old["strike"],
                    data[old_month][prior]["close"],
                    new_f,
                    [
                        dict(t, raw=k)
                        for k, t in terms.items()
                        if t.get("underlying") == ex + "." + raw
                        and t.get("side") == old["side"]
                        and str(t.get("expiry") or "") > row["day"]
                    ],
                )
                chosen = next(
                    (
                        t
                        for t in matches
                        if t.get("opened", prior) <= prior
                        and option_prices(t["raw"]).get(prior, {}).get("volume", 0) > 0
                    ),
                    None,
                )
                if chosen is None:
                    raise ValueError("缺少切换前已经有真实成交的同方向期权")
                raw = chosen["raw"]
                old_c = option_prices(old_raw).get(prior, {}).get("close")
                if not old_c:
                    raise ValueError("缺少旧期权换月前的收盘价")
                factor *= old_c / option_prices(raw)[prior]["close"]
            if str(terms[raw].get("expiry") or "") <= row["day"]:
                raise ValueError("期权已到期；请使用到期清算或缩短区间")
        prices = option_prices(raw) if optional else data[month]
        if not prices.get(row["day"], {}).get("volume", 0) > 0:
            raise ValueError(f"{row['day']} {raw} 缺少有效成交 K 线")
        daily[raw] = prices
        row.update(raw=raw, factor=factor if optional else row["factor"])
        old_raw = raw
        old_month = month
    common = (
        Path(os.environ.get("WT_MAIN_COMMON", "/quote-reference/common"))
        if not optional
        else root / "common"
    )
    base = folder / "market-base"
    base.mkdir()
    market = folder / "market-data"
    prods = _load(common / "commodities.json")
    products = prods.get(ex, {})
    prod = products.get(product)
    if not prod:
        raise ValueError("缺少品种合约乘数或交易时段配置")
    signal_product = "WQ"
    signal_raw = signal_product + ("912" if ex == "CZCE" else "9912")
    signal = ex + "." + signal_product + ".9912"
    products[signal_product] = dict(prod, name="Continuous signal")
    contracts = {}
    codes = {}
    helper = WtDataHelper()

    def store(raw, bars, period):
        name = {"d": "day", "m1": "min1", "m5": "min5"}[period]
        target = market / "his" / name / ex / (raw + ".dsb")
        target.parent.mkdir(parents=True, exist_ok=True)
        items = []
        for k, r in sorted(bars.items()):
            t = 0 if period == "d" else int(k) - 199000000000
            items.append(
                WTSBarStruct(
                    date=int(r.get("day") or k[:8]),
                    time=t,
                    open=r["open"],
                    high=r["high"],
                    low=r["low"],
                    close=r["close"],
                    vol=r["volume"],
                    hold=r.get("oi") or 0,
                )
            )
        if items and not helper.store_bars(
            str(target), (WTSBarStruct * len(items))(*items), len(items), period
        ):
            raise ValueError("无法冻结回测 K 线")

    raw_bars = {}
    tick_minutes = {}
    tick_start = None
    if task.get("model") == "option_tick":
        from .MainOptionTicks import option_ticks

        tick_minutes, tick_start = option_ticks(root, folder, task, signal, plan)
    period = task["period"]
    minute = period == "m15"

    def five(bars, existing):
        groups = defaultdict(list)
        for t, r in sorted(bars.items()):
            dt = datetime.strptime(t, "%Y%m%d%H%M")
            end5 = dt + timedelta(minutes=(-dt.minute) % 5)
            groups[end5.strftime("%Y%m%d%H%M")].append(r)
        merged = {
            k: dict(
                day=v[-1]["day"],
                open=v[0]["open"],
                high=max(b["high"] for b in v),
                low=min(b["low"] for b in v),
                close=v[-1]["close"],
                volume=sum(b["volume"] for b in v),
            )
            for k, v in groups.items()
            if len(v) == 5
        }
        merged.update(existing)
        blocks = defaultdict(list)
        for stamp in merged:
            dt = datetime.strptime(stamp, "%Y%m%d%H%M")
            blocks[dt + timedelta(minutes=(-dt.minute) % 15)].append(stamp)
        return {
            t: merged[t] for items in blocks.values() if len(items) == 3 for t in items
        }

    for raw in sorted(set(r["raw"] for r in plan)):
        if optional:
            m = OPTION.fullmatch(raw)
            t = terms[raw]
            p = next(
                (
                    key
                    for key in (product + "_o", product + m[4], product)
                    if key in products
                ),
                None,
            )
            contracts[raw] = dict(
                name=raw,
                product=p,
                exchg=ex,
                code=raw,
                expiredate=int(t["expiry"]),
                option=dict(
                    underlying=m[1] + m[2],
                    optiontype=49 if m[4] == "C" else 50,
                    strikeprice=t["strike"],
                    underlyingscale=1,
                ),
            )
            codes[raw] = dataset_code("option", ex, raw, contracts[raw])
        else:
            month = next(r["month"] for r in plan if r["raw"] == raw)
            codes[raw] = ex + "." + product + "." + month
            contracts[raw] = dict(
                name=raw,
                product=product,
                exchg=ex,
                code=raw,
                standard_code=ex + "." + product + "." + month,
            )
        required = {r["day"] for r in plan if r["raw"] == raw}
        required.update(
            row["day"]
            for previous, row in zip(plan, plan[1:])
            if previous["raw"] == raw and row["raw"] != raw
        )
        if task.get("model") == "plus_tick":
            continue
        if minute:
            a = start - timedelta(days=6)
            native = dict(tick_minutes.get(raw, {}))
            native.update(local_bars(root, ex, raw, "m1", a, end))
            if optional:
                from .OptionWatchArchive import database, read
                from contextlib import closing

                with closing(database(root, ex, raw)) as db:
                    for x in read(db, "m1", end.isoformat(), 250000):
                        k = x["day"].replace("-", "").replace(":", "").replace(" ", "")
                        if len(k) == 12 and k[:8] >= a.strftime("%Y%m%d"):
                            native.setdefault(k, dict(x, day=x.get("trading_day")))
            from .MarketHistory import expected_stamps

            calendar = sorted(data[next(s["month"] for s in plan if s["raw"] == raw)])

            def labeled(rows):
                output = {}
                for k, r in rows.items():
                    day = r.get("day")
                    day = str(day) if str(day).isdigit() else trade_day(k, calendar)
                    if day in days:
                        output[k] = dict(r, day=day)
                return output

            native5 = labeled(local_bars(root, ex, raw, "m5", a, end))
            session = _load(common / "sessions.json").get(prod["session"], {})
            expected = set()
            for day in required:
                expected.update(
                    expected_stamps(
                        datetime.strptime(day, "%Y%m%d").date(), session, "m5"
                    )
                    or ()
                )
            from_ticks = task.get("model") == "option_tick"
            if from_ticks:
                native = tick_minutes.get(raw, {})
                native5 = {}
            available = five(labeled(native), native5)
            has_full = (
                from_ticks
                or bool(expected)
                and all(str(k) in available for k in expected)
            )
            bars = (
                native
                if has_full
                else fetch(root, ex + "." + raw, a, end, True, native)
            )
            full = five(labeled(bars), native5)
            missing = required - {r["day"] for r in full.values()}
            if missing and optional and not from_ticks:
                from .OptionWatchArchive import bars_for
                from .OptionWatch import provider_future

                provider = (
                    provider_future(ex, m[1] + m[2], terms[raw]["expiry"]) + m[4] + m[5]
                )
                with closing(database(root, ex, raw)) as db:
                    for day in sorted(missing):
                        rows, _ = bars_for(
                            root,
                            db,
                            ex,
                            raw,
                            provider,
                            "m1",
                            datetime.strptime(day, "%Y%m%d").date().isoformat(),
                        )
                        for x in rows:
                            k = (
                                x["day"]
                                .replace("-", "")
                                .replace(":", "")
                                .replace(" ", "")
                            )
                            if len(k) == 12:
                                bars.setdefault(k, dict(x, day=x.get("trading_day")))
                full = five(labeled(bars), native5)
            if missing := required - {r["day"] for r in full.values()}:
                raise ValueError(
                    f"{raw} 缺少完整的 15 分钟 K 线，首个缺口 {min(missing)}"
                )
            raw_bars[raw] = full
        else:
            raw_bars[raw] = {
                k: dict(r, day=k) for k, r in daily[raw].items() if k in days
            }
            for day in required:
                if raw_bars[raw].get(day, {}).get("volume", 0) <= 0:
                    raise ValueError(f"{day} 移仓腿 {raw} 缺少真实成交")
        store(raw, raw_bars[raw], "m5" if minute else "d")
    selected = {r["day"]: r for r in plan}
    signal_bars = {}
    for raw, bars in raw_bars.items():
        for k, b in bars.items():
            r = selected.get(b["day"])
            if r and r["raw"] == raw:
                signal_bars[k] = dict(
                    b,
                    **{f: b[f] * r["factor"] for f in ("open", "high", "low", "close")},
                )
    if task.get("model") != "plus_tick" and set(days) - {
        x["day"] for x in signal_bars.values()
    }:
        raise ValueError("主力信号线存在整日缺口")
    store(signal_raw, signal_bars, "m5" if minute else "d")
    task["replay_start"] = int(days[0] + "0000")
    # Use the actual first night-session minute and preserve the trading-day label.
    if task.get("model") == "plus_tick":
        task["replay_start"] = int(
            (start - timedelta(days=7)).strftime("%Y%m%d") + "0000"
        )
    elif minute:
        first = datetime.strptime(min(signal_bars), "%Y%m%d%H%M") - timedelta(minutes=5)
        task["replay_start"] = int(first.strftime("%Y%m%d%H%M"))
    if tick_start:
        task["replay_start"] = tick_start
    contracts[signal_raw] = dict(
        name="Continuous signal", product=signal_product, exchg=ex, code=signal_raw
    )
    # Execute only on real contracts. The private signal instrument is never an order target.
    for row in plan:
        row["code"] = codes[row["raw"]]
    legs = [dict(code=v, period="m5" if minute else "d") for v in codes.values()]
    if optional:
        for month in sorted(set(r["month"] for r in plan)):
            raw = raw_future(month)
            contracts[raw] = dict(
                name=raw,
                product=product,
                exchg=ex,
                code=raw,
                standard_code=ex + "." + product + "." + month,
            )
            legs.append(dict(code=ex + "." + product + "." + month, period="d"))
            store(
                raw,
                {k: dict(v, day=k) for k, v in data[month].items() if k in days},
                "d",
            )
    (base / "contracts.json").write_text(json.dumps({ex: contracts}, ensure_ascii=True))
    (base / "commodities.json").write_text(
        json.dumps({ex: products}, ensure_ascii=True)
    )
    for f in ("sessions.json", "holidays.json"):
        (base / f).write_text(json.dumps(_load(common / f), ensure_ascii=True))
    write_json(base / "hots.json", {})
    fees = {
        ex + "." + item["product"]: dict(
            open=task["fee"], close=task["fee"], closetoday=task["fee"], byvolume=True
        )
        for item in contracts.values()
    }
    write_json(base / "fees.json", fees)
    write_json(folder / "plan.json", plan)
    write_json(
        folder / "market-data.json",
        dict(
            code=plan[0]["code"],
            period="m5" if minute else "d",
            start=task["replay_start"],
            end=int(task["end"].replace("-", "") + "2359"),
            legs=legs,
        ),
    )
    return signal, plan

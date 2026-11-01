"""Financial diagnostics derived only from a selected run's frozen inputs and ledgers."""

import csv
from collections import defaultdict

from datetime import datetime
from itertools import combinations
from math import log, sqrt
from statistics import stdev

from .ReportChart import read, ticker
from .AssetMarkets import dataset_code, inside, instrument
from .OptionModels import contract_metrics, qdate


def rows(path):
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def round_stats(closes):
    profits = [float(r["profit"]) for r in closes]
    wins = [v for v in profits if v > 0]
    losses = [-v for v in profits if v < 0]
    bars = [
        int(r["closebarno"]) - int(r["openbarno"])
        for r in closes
        if r.get("closebarno") and r.get("openbarno")
    ]
    return dict(
        count=len(profits),
        win_rate=len(wins) / len(profits) if profits else None,
        profit_factor=sum(wins) / sum(losses) if losses else None,
        payoff=(sum(wins) / len(wins)) / (sum(losses) / len(losses))
        if wins and losses
        else None,
        hold=sum(bars) / len(bars) if bars else None,
    )


def roll_pairs(trades, scales):
    """Only an explicit shared roll tag can pair a close and an open."""
    tagged = defaultdict(list)
    for r in trades:
        tag = r.get("tag") or r.get("usertag") or ""
        if tag.startswith("roll:") and tag[5:]:
            tagged[tag].append(r)
    result = []
    for tag, group in tagged.items():
        orders = {}
        for side in ("OPEN", "CLOSE"):
            selected = [r for r in group if r["action"].upper().startswith(side)]
            if not selected or len({(r["code"], r["direct"]) for r in selected}) != 1:
                break
            qty = sum(float(r["qty"]) for r in selected)
            if qty <= 0:
                break
            orders[side] = (
                selected[0]["code"],
                selected[0]["direct"],
                qty,
                sum(float(r["price"]) * float(r["qty"]) for r in selected) / qty,
            )
        if len(orders) != 2:
            continue
        old, side, qty, old_price = orders["CLOSE"]
        new, new_side, new_qty, new_price = orders["OPEN"]
        if (
            old == new
            or side != new_side
            or qty != new_qty
            or scales.get(old) != scales.get(new)
            or not scales.get(old)
        ):
            continue
        if old.rsplit(".", 1)[0] != new.rsplit(".", 1)[0]:
            continue
        fee = sum(float(r["fee"]) for r in group)
        gap = (
            (new_price - old_price)
            * (1 if side.upper() == "LONG" else -1)
            * qty
            * scales[old]
        )
        result.append(
            dict(
                day=max(str(r["time"])[:8] for r in group),
                old=old,
                new=new,
                qty=qty,
                fee=fee,
                gap=gap,
                cost=gap + fee,
            )
        )
    return sorted(result, key=lambda r: r["day"])


def margin_yield(task, closes):
    # The native fill gives realized PnL. Recover its volume-weighted entry
    # price and apply the run's frozen margin rate to those same closed lots.
    margins = profit = lots = 0.0
    codes = task.get("volume_scale_by_code") or {
        task.get("code"): task.get("volume_scale")
    }
    finance = task.get("finance_by_code") or {
        task.get("code"): task.get("finance") or {}
    }
    for fill in closes:
        code = next(
            (
                k
                for k in codes
                if k and (fill["code"] == k or fill["code"] in ticker(k, "futures")[1])
            ),
            None,
        )
        side = str(fill["direction"]).lower()
        if not code or side not in ("long", "short"):
            return None
        scale = codes[code]
        rate = finance.get(code, {}).get("margin", {}).get(side)
        qty = float(fill["volume"])
        gain = float(fill["realized"])
        if not scale or not rate or qty <= 0:
            return None
        entry = float(fill["price"]) - (1 if side == "long" else -1) * gain / (
            qty * scale
        )
        if entry <= 0:
            return None
        margins += qty * scale * entry * rate
        lots += qty
        profit += gain
    return (
        dict(pnl=profit / lots, margin=margins / lots, ratio=profit / margins)
        if margins > 0
        else None
    )


def holding_minutes(fills):
    positions = defaultdict(float)
    previous = None
    area = 0.0
    closed = 0.0
    for f in fills:
        stamp = datetime.strptime(
            str(f["date"]) + str(f["time"]).zfill(9), "%Y%m%d%H%M%S%f"
        )
        if previous is not None:
            seconds = (stamp - previous).total_seconds()
            if seconds < 0:
                return None
            area += sum(positions.values()) * seconds / 60
        key = (f["code"], f["direction"])
        qty = float(f["volume"])
        if str(f["offset"]).lower() == "open":
            positions[key] += qty
        elif str(f["offset"]).lower().startswith("close"):
            positions[key] -= qty
            closed += qty
        else:
            return None
        if positions[key] < 0:
            return None
        previous = stamp
    return area / closed if closed and not any(positions.values()) else None


def plus_details(folder):
    report = read(folder / "report.json", {})
    daily = {}
    utilization = []
    margin = []
    peak = report.get("summary", {}).get("capital")
    max_dd = None
    for r in report.get("funds", []):
        day = str(r["trading_date"])
        eq = r.get("equity")
        used = r.get("margin")
        if eq is None:
            continue
        peak = max(peak or eq, eq)
        if peak > 0:
            max_dd = max(max_dd or 0, 1 - eq / peak)
        if used is not None:
            margin.append(used)
            if eq > 0:
                utilization.append(used / eq)
        daily[day] = dict(
            day=day,
            equity=eq,
            margin=used,
            use=used / eq if used is not None and eq > 0 else None,
        )
    fills = report.get("trades", [])
    closes = [
        r
        for r in fills
        if str(r.get("offset")).lower().startswith("close")
        and r.get("realized") is not None
    ]
    rounds = (
        round_stats([dict(profit=r["realized"]) for r in closes])
        if "trades" in report
        else None
    )
    return dict(
        daily=list(daily.values()),
        rounds=rounds,
        holding_minutes=holding_minutes(fills),
        per_lot=margin_yield(read(folder / "task.json", {}), closes),
        margin_peak=max(margin, default=None),
        margin_mean=sum(margin) / len(margin) if margin else None,
        utilization=sum(utilization) / len(utilization) if utilization else None,
        full_dd=max_dd,
        fills=len(report["trades"]) if "trades" in report else None,
        roll=rows(folder / "rolls.csv"),
        basis=rows(folder / "basis.csv"),
    )


def frozen_legs(folder, helper):
    from .HistoryDataWorker import dataset_records

    contracts = read(folder / "market-base/contracts.json", {})
    products = read(folder / "market-base/commodities.json", {})
    meta = read(folder / "market-data.json", {})
    legs = {}
    for source in meta.get("option_legs") or meta.get("legs") or []:
        code = source["code"]
        ex = code.split(".")[0]
        for raw, contract in contracts.get(ex, {}).items():
            option = contract.get("option")
            if option:
                if dataset_code("option", ex, raw, contract) != code:
                    continue
            elif raw not in ticker(code, "futures")[1]:
                continue
            instrument("option" if option else "futures", ex + "." + raw)
            period = source["period"]
            market = inside(folder, "market-data", "his")
            files = (
                sorted((market / "ticks" / ex).glob("*/" + raw + ".dsb"))
                if period == "tick"
                else [
                    market
                    / {"m1": "min1", "m5": "min5", "d": "day"}[period]
                    / ex
                    / (raw + ".dsb")
                ]
            )
            marks = {}
            for file in files:
                if not file.is_file() or file.resolve() != file:
                    continue
                data, clock = dataset_records(
                    dict(period=period, files=[str(file)]), helper
                )
                for r in data:
                    tick = period == "tick"
                    day = str(r.trading_date if tick else r.date)
                    bar = marks.setdefault(day, dict(volume=0))
                    if tick:
                        good = (
                            0 < r.bid_price_0 <= r.ask_price_0 < 1e12
                            and min(r.bid_qty_0, r.ask_qty_0) > 0
                        )
                        price = (r.ask_price_0 + r.bid_price_0) / 2 if good else None
                        bar.update(
                            spread=r.ask_price_0 - r.bid_price_0 if good else None,
                            oi=r.open_interest,
                            volume=r.total_volume,
                        )
                    else:
                        price = r.close
                        bar["volume"] += r.vol
                        bar["oi"] = r.hold
                    bar.update(
                        price=price if price and 0 < price < 1e12 else None,
                        clock=clock(r),
                    )
            legs[code] = dict(
                marks=marks,
                under=ex + "." + (option["underlying"] if option else raw),
                mult=products.get(ex, {}).get(contract["product"], {}).get("volscale"),
                expiry=contract.get("expiredate"),
                strike=option.get("strikeprice") if option else None,
                call=option.get("optiontype") in (49, "49", "C") if option else None,
            )
            break
    return legs


def option_details(legs, positions, days, funds):
    futures = {l["under"]: l["marks"] for l in legs.values() if l["strike"] is None}
    observations = []
    exposure = []
    attribution = []
    payoffs = {}
    spreads = []
    previous = {}
    iv_history = defaultdict(list)
    for i, day in enumerate(days):
        pos = positions.get(day, {})
        groups = defaultdict(list)
        current = {}
        parts = dict(delta=0.0, gamma=0.0, theta=0.0, vega=0.0)
        complete = True
        for code, leg in legs.items():
            mark = leg["marks"].get(day, {})
            under = leg["under"]
            ref = futures.get(under, {}).get(day, {})
            f = ref.get("price")
            premium = mark.get("price")
            qty = pos.get(code, 0)
            scale = leg["mult"]
            option = leg["strike"] is not None
            metrics = None
            if (
                option
                and f
                and premium
                and leg.get("expiry")
                and ref.get("clock") == mark.get("clock")
            ):
                try:
                    t = qdate(day)
                    metrics = contract_metrics(
                        f,
                        leg["strike"],
                        premium,
                        qdate(leg["expiry"]) - t,
                        0.02,
                        leg["call"],
                        "american",
                        t,
                    )
                except (RuntimeError, ValueError):
                    pass
            elif not option and f:
                metrics = dict(delta=1, gamma=0, vega=0, theta=0, iv=0)
            current[code] = (qty, metrics, f, premium)
            if option:
                hist = iv_history[code]
                iv = metrics["iv"] if metrics else None
                prices = [
                    futures.get(under, {}).get(d, {}).get("price")
                    for d in days[max(0, i - 20) : i + 1]
                ]
                hv = (
                    stdev(log(b / a) for a, b in zip(prices, prices[1:])) * sqrt(252)
                    if len(prices) == 21 and all(prices)
                    else None
                )
                percentile = (
                    sum(v <= iv for v in hist) / len(hist)
                    if iv is not None and len(hist) >= 20
                    else None
                )
                observations.append(
                    dict(
                        day=day,
                        code=code,
                        iv=iv,
                        hv=hv,
                        rank=percentile,
                        volume=mark.get("volume"),
                        oi=mark.get("oi"),
                        spread=mark.get("spread"),
                    )
                )
                if iv is not None:
                    hist.append(iv)
            if qty:
                groups[under].append((qty, scale, metrics))

            if i and code in previous:
                n, old, old_f, _ = previous[code]
                if n:
                    if not (old and metrics and scale and old_f and f):
                        complete = False
                        continue
                    move = f - old_f
                    dt = (
                        datetime.strptime(day, "%Y%m%d")
                        - datetime.strptime(days[i - 1], "%Y%m%d")
                    ).days
                    parts["delta"] += n * scale * old["delta"] * move
                    parts["gamma"] += n * scale * 0.5 * old["gamma"] * move * move
                    parts["theta"] += n * scale * old["theta"] * dt
                    parts["vega"] += (
                        n * scale * old["vega"] * (metrics["iv"] - old["iv"]) * 100
                    )
        for under in futures.keys() | groups.keys():
            g = groups[under]
            valid = all(scale and m for _, scale, m in g)
            exposure.append(
                dict(
                    day=day,
                    under=under,
                    **{
                        k: sum(n * s * m[k] for n, s, m in g) if valid else None
                        for k in parts
                    },
                )
            )
        if i:
            pnl = (
                funds[day] - funds[days[i - 1]]
                if day in funds and days[i - 1] in funds
                else None
            )
            attribution.append(
                dict(
                    day=day,
                    pnl=pnl,
                    residual=pnl - sum(parts.values())
                    if pnl is not None and complete
                    else None,
                    **(parts if complete else dict.fromkeys(parts)),
                )
            )
        for under, marks in futures.items():
            spot = marks.get(day, {}).get("price")
            if not spot:
                continue
            expiries = {
                l["expiry"]
                for c, l in legs.items()
                if pos.get(c) and l["under"] == under and l["strike"] is not None
            }
            for expiry in expiries:
                basket = [
                    (pos[c], l, l["marks"].get(day, {}).get("price"))
                    for c, l in legs.items()
                    if pos.get(c)
                    and l["under"] == under
                    and (l["strike"] is None or l["expiry"] == expiry)
                ]
                if len(expiries) > 1:
                    basket = [b for b in basket if b[1]["strike"] is not None]
                if not all(p is not None and l["mult"] for _, l, p in basket):
                    continue
                grid = sorted(
                    {round(spot * (0.65 + j * 0.025), 4) for j in range(29)}
                    | {l["strike"] for _, l, _ in basket if l["strike"] is not None}
                )
                values = [
                    sum(
                        n
                        * l["mult"]
                        * (
                            (
                                x
                                if l["strike"] is None
                                else max(
                                    0, (x - l["strike"]) * (1 if l["call"] else -1)
                                )
                            )
                            - p
                        )
                        for n, l, p in basket
                    )
                    for x in grid
                ]
                payoffs[(under, expiry)] = dict(
                    day=day, under=under, expiry=expiry, grid=grid, pnl=values
                )
        for a, b in combinations(sorted(futures), 2):
            if a.split(".")[0] != b.split(".")[0] or a.rstrip("0123456789") != b.rstrip(
                "0123456789"
            ):
                continue
            pa = futures[a].get(day, {}).get("price")
            pb = futures[b].get(day, {}).get("price")
            if pa and pb:
                spreads.append(dict(day=day, pair=a + " − " + b, value=pa - pb))
        previous = current
    return dict(
        observations=observations,
        exposure=exposure,
        attribution=attribution,
        payoffs=list(payoffs.values()),
        spreads=spreads,
    )


def native_details(q, helper):
    from pathlib import Path

    folder = Path(q["folder"])
    out = folder / "outputs_bt" / q["task"]
    funds = {r["date"]: float(r["dynbalance"]) for r in rows(out / "funds.csv")}
    result = dict(
        rounds=round_stats(rows(out / "closes.csv"))
        if (out / "closes.csv").is_file()
        else None
    )
    if q["asset"] == "option":
        legs = frozen_legs(folder, helper)
        if legs and (out / "positions.csv").is_file():
            positions = defaultdict(dict)
            for r in rows(out / "positions.csv"):
                positions[r["date"]][r["code"]] = float(r["volume"])
            if any(set(p) - legs.keys() for p in positions.values()):
                return result
            option = result["option"] = option_details(
                legs, positions, sorted(funds), funds
            )
            history = defaultdict(list)
            for obs in option["observations"]:
                if obs["rank"] is not None:
                    history[obs["code"]].append(obs)
            cohorts = defaultdict(list)
            for trade in rows(out / "closes.csv"):
                prior = [
                    r
                    for r in history[trade["code"]]
                    if r["day"] + "1500" < str(trade["opentime"])[:12]
                ]
                if prior:
                    rank = prior[-1]["rank"]
                    cohorts[
                        "≤30%" if rank <= 0.30 else "≥70%" if rank >= 0.70 else "中间"
                    ].append(float(trade["profit"]))
            option["cohorts"] = [
                dict(
                    label=k,
                    count=len(v),
                    win=sum(x > 0 for x in v) / len(v),
                    mean=sum(v) / len(v),
                )
                for k, v in cohorts.items()
            ]
    elif q["asset"] == "futures":
        contracts = read(folder / "market-base/contracts.json", {})
        products = read(folder / "market-base/commodities.json", {})
        scales = {
            (c.get("standard_code") or dataset_code("futures", ex, raw, c)): products.get(ex, {})
            .get(c.get("product"), {})
            .get("volscale")
            for ex, items in contracts.items()
            for raw, c in items.items()
        }
        result.update(
            roll=rows(out / "rolls.csv")
            or roll_pairs(rows(out / "trades.csv"), scales),
            basis=rows(out / "basis.csv"),
        )
        if not result["basis"]:
            legs = frozen_legs(folder, helper)
            for a, b in combinations(sorted(legs), 2):
                if a.rsplit(".", 1)[0] != b.rsplit(".", 1)[0]:
                    continue
                for day in sorted(legs[a]["marks"].keys() & legs[b]["marks"].keys()):
                    left, right = legs[a]["marks"][day], legs[b]["marks"][day]
                    if (
                        left["clock"] == right["clock"]
                        and left["price"]
                        and right["price"]
                    ):
                        result["basis"].append(
                            dict(
                                day=day,
                                pair=a + " − " + b,
                                value=left["price"] - right["price"],
                            )
                        )
    return result


def expiry_details(rec, sample):
    legs = {}
    for c in (rec["old"], rec["new"]):
        item = sample["contracts"][c]
        under = item["future"]
        legs[c] = dict(
            under=under,
            strike=item["strike"],
            call=item["kind"] == "C",
            expiry=item["expiry"],
            mult=item["mult"],
            marks={
                d: dict(price=b["close"], volume=b.get("volume"), clock=d)
                for d, b in item["bars"].items()
            },
        )
        legs[under] = dict(
            under=under,
            strike=None,
            mult=item["mult"],
            marks={
                d: dict(price=m[under], clock=d)
                for d, m in sample["futures"].items()
                if m.get(under)
            },
        )
    funds = {r["day"]: r["equity"] for r in rec["daily"]}
    positions = {r["day"]: {**r["options"], **r["futures"]} for r in rec["daily"]}
    return dict(
        option=option_details(legs, positions, list(funds), funds),
        daily=[
            dict(
                day=r["day"],
                equity=r["equity"],
                margin=r["margin"],
                use=r["margin"] / r["equity"] if r["equity"] > 0 else None,
            )
            for r in rec["daily"]
        ],
    )

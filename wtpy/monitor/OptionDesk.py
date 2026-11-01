"""Combine only the explicitly requested CTP books into a dated option chain."""
from collections import defaultdict, deque
from datetime import datetime
from statistics import mean, pstdev
import time

from .OptionModels import contract_metrics, kirk_price, qdate, smile_summary, variance_surface

BOOK_AGE = 60


def midpoint(tick):
    bid, ask = tick.get("bid"), tick.get("ask")
    if not bid or not ask or not 0 < bid <= ask or not tick.get("bid_size") or not tick.get("ask_size"):
        raise ValueError("等待有效双边盘口")
    return (bid+ask)/2


def stamp(tick):
    return datetime.strptime(tick["time"], "%Y%m%d %H:%M:%S.%f")


class OptionDesk:
    def __init__(self, plan):
        self.plan = plan
        self.trails = {}
        self.cache = {}

    def history(self, key, minute, value, members):
        trail = self.trails.get(key)
        if not trail or trail["members"] != members or minute < trail["minute"]:
            trail = dict(members=members,minute=minute,value=value,history=deque(maxlen=60))
            self.trails[key] = trail
        if minute > trail["minute"]:
            trail["history"].append((trail["minute"],trail["value"]))
        trail.update(minute=minute,value=value)
        past = [v for _,v in trail["history"]]
        deviation = pstdev(past) if len(past)>=2 else 0
        return dict(history=[*trail["history"],(minute,value)], prior_minutes=len(past),
                    zscore=(value-mean(past))/deviation if deviation else None)

    def snapshot(self, books, session, now=None):
        now = time.time() if now is None else now
        plan = self.plan
        day = max((tick["trading_day"] for tick in books.values() if tick.get("trading_day")),default=None)
        rows, groups = [], defaultdict(list)
        for contract in plan["contracts"]:
            row = dict(contract)
            rows.append(row)
            quote, future = books.get(row["symbol"]), books.get(row["underlying"])
            if not quote or not future:
                row["reason"] = "等待期权和标的报价"
                continue
            try:
                if day != quote["trading_day"] or day != future["trading_day"]:
                    raise ValueError("等待同一交易日的报价")
                if now-min(quote["received"],future["received"])>BOOK_AGE or abs((stamp(quote)-stamp(future)).total_seconds())>BOOK_AGE:
                    raise ValueError("报价超时或期权与期货不同步")
                mid, fwd = midpoint(quote), midpoint(future)
                today = qdate(day)
                tenor = qdate(row["expiry"])-today
                cache_key = (day, mid, fwd)
                cached = self.cache.get(row["symbol"])
                if not cached or cached[0]!=cache_key:
                    metrics = contract_metrics(fwd,row["strike"],mid,tenor,plan["rate"],row["side"]=="看涨",plan["model"],today)
                    self.cache[row["symbol"]] = (cache_key,metrics)
                row.update(self.cache[row["symbol"]][1], forward=fwd, premium=mid,
                           bid=quote["bid"],ask=quote["ask"],source_time=quote["time"],future_time=future["time"])
                groups[row["underlying"]].append(row)
            except (RuntimeError,ValueError,OverflowError) as exc:
                row["reason"] = str(exc) if isinstance(exc,ValueError) else "报价无法在模型波动率区间内定价"
        summaries, surfaces = {}, {}
        by_product = defaultdict(list)
        for underlying, valid in groups.items():
            summary = smile_summary(valid)
            summaries[underlying] = summary
            if "weighted_gap" in summary:
                minute = max(r["source_time"] for r in valid)[:14]
                calls = {r["strike"] for r in valid if r["side"]=="看涨"}
                pairs = frozenset(r["strike"] for r in valid if r["side"]=="看跌" and r["strike"] in calls)
                summary.update(self.history(underlying,minute,summary["weighted_gap"],pairs))
            for r in valid:
                by_product[(r["product"],r["side"])].append(r)
        query = plan.get("query")
        for (product,side), valid in by_product.items():
            point = (query["strike"],qdate(query["date"])) if query else None
            surfaces.setdefault(product,{})[side] = variance_surface(valid,qdate(day),point)
        spread = None
        if config:=plan.get("spread"):
            pair = {r["symbol"]:r for r in rows if "iv" in r}
            a,b = (pair.get(config[k]) for k in ("first","second"))
            spread = {"reason":"等待两腿同步报价"}
            if a and b:
                times = [stamp({"time":r[k]}) for r in (a,b) for k in ("source_time","future_time")]
                if (max(times)-min(times)).total_seconds()<=BOOK_AGE:
                    days = min(qdate(a["expiry"]),qdate(b["expiry"]))-qdate(day)
                    try:
                        price = kirk_price(a["forward"],b["forward"],a["iv"],b["iv"],config["strike"],days,
                                           plan["rate"],config["correlation"],config["ratio"],qdate(day))
                        spread = dict(price=price,days=days,forward_spread=a["forward"]-config["ratio"]*b["forward"],**config)
                    except (ValueError,RuntimeError):
                        spread = {"reason":"当前价差与换算系数超出 Kirk 模型范围"}
        return dict(session=session,published=now,trading_day=day,mode=plan["mode"],model=plan["model"],
                    rate=plan["rate"],rows=rows,summaries=summaries,surfaces=surfaces,spread=spread)

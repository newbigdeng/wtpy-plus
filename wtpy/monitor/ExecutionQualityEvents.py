"""Execution telemetry uses native event identity and same-process monotonic clocks."""
import math
from pathlib import Path
from .ExecutionQualityFiles import ledger_cache


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def _id(value):
    return str(value or "").strip()


def _aliases(event):
    base = (_id(event.get("channel")), _id(event.get("trading_date")))
    aliases = []
    if _id(event.get("localid")) not in ("", "0"):
        aliases.append((*base, "local", _id(event.get("clock_id")), _id(event["localid"])))
    if _id(event.get("entrust_id")):
        aliases.append((*base, "entrust", _id(event["entrust_id"])))
    if _id(event.get("orderid")):
        aliases.append((*base, "order", _id(event.get("code")), _id(event["orderid"])))
    return aliases


def _elapsed(start, end):
    if not start or not end or not start.get("clock_id") or start.get("clock_id") != end.get("clock_id"):
        return None
    begin, finish = _number(start.get("mono_time_ns")), _number(end.get("mono_time_ns"))
    return (finish-begin)/1e6 if begin is not None and finish is not None and finish >= begin else None


def summarize_events(events, *, limit=200):
    rows, aliases, unmatched, invalid, duplicate_fills = [], {}, [], 0, 0
    submissions = set()
    for event in events:
        if event.get("event") != "submission":
            continue
        keys = _aliases(event)
        quantity = _number(event.get("qty"))
        identity = (*keys[:1], event.get("mono_time_ns"))
        if not keys or quantity is None or quantity <= 0 or not event.get("clock_id"):
            invalid += 1
            continue
        if identity in submissions:
            continue
        submissions.add(identity)
        row = {"submission": event, "events": [], "fills": [], "trade_keys": set(), "total": quantity}
        index = len(rows)
        rows.append(row)
        for key in keys:
            aliases.setdefault(key, set()).add(index)
    # Bind identifiers learned from ack/order updates before processing fills.
    for event in events:
        if event.get("event") in ("submission", "fill"):
            continue
        matches = set()
        for key in _aliases(event):
            matches.update(aliases.get(key, ()))
        if len(matches) == 1:
            index = next(iter(matches))
            for key in _aliases(event):
                aliases.setdefault(key, set()).add(index)
    for event in events:
        if event.get("event") == "submission":
            continue
        if event.get("event") not in ("ack", "order_update", "fill", "reject", "cancel_request", "cancel_ack"):
            invalid += 1
            continue
        matches = set()
        for key in _aliases(event):
            matches.update(aliases.get(key, ()))
        if len(matches) != 1:
            unmatched.append(event)
            continue
        row = rows[next(iter(matches))]
        if event.get("event") == "fill":
            quantity, price = _number(event.get("qty")), _number(event.get("price"))
            if quantity is None or quantity <= 0 or price is None or not _id(event.get("tradeid")):
                invalid += 1
                continue
            key = (_id(event.get("channel")), _id(event.get("trading_date")), _id(event.get("code")), _id(event.get("tradeid")))
            if key in row["trade_keys"]:
                duplicate_fills += 1
                continue
            row["trade_keys"].add(key)
            row["fills"].append(event)
        else:
            row["events"].append(event)
    orders, trades = [], []
    for row in rows:
        start, updates, fills = row["submission"], row["events"], row["fills"]
        received = sum(_number(event["qty"]) for event in fills)
        notified = max([_number(event.get("filled_qty")) or 0 for event in updates] or [0])
        filled = max(received, notified)
        rejected = any(event["event"] == "reject" for event in updates)
        canceled = any(event["event"] == "cancel_ack" or event.get("canceled") is True for event in updates)
        state = "filled" if filled >= row["total"] else "rejected" if rejected else "canceled" if canceled else "open"
        acknowledged = [event for event in updates if event["event"] in ("ack", "order_update")]
        ack_ms = [value for event in acknowledged if (value := _elapsed(start,event)) is not None]
        fill_ms = [value for event in fills if (value := _elapsed(start,event)) is not None]
        cancel_requests = [event for event in updates if event["event"] == "cancel_request"]
        cancel_acks = [event for event in updates if event["event"] == "cancel_ack" or event.get("canceled") is True]
        cancel_ms = []
        for ack in cancel_acks:
            preceding = [(request,value) for request in cancel_requests if (value := _elapsed(request,ack)) is not None]
            if preceding:
                cancel_ms.append(min(value for _,value in preceding))
        quote = start.get("quote") or {}
        baseline = _number(quote.get("ask" if start.get("side") == "buy" else "bid"))
        if start.get("benchmark_kind") != "opposite_touch" or baseline is None or baseline <= 0:
            baseline = None
        sign = 1 if start.get("side") == "buy" else -1 if start.get("side") == "sell" else None
        vwap = sum(_number(event["price"]) * _number(event["qty"]) for event in fills)/received if received else None
        slippage = sign*(vwap-baseline) if sign and vwap is not None and baseline else None
        result = {key: start.get(key) for key in ("channel", "trading_date", "clock_id", "localid", "entrust_id", "code", "side", "price", "wall_time_ns")}
        result.update(orderid=next((event["orderid"] for event in updates if _id(event.get("orderid"))), start.get("orderid")),
                      total=row["total"], filled=filled, matched_volume=received, reconciled=math.isclose(received,filled,abs_tol=1e-8),
                      status=state, vwap=vwap, benchmark_price=baseline, benchmark_quote=quote,
                      arrival_slippage=slippage, submit_to_ack_ms=min(ack_ms) if ack_ms else None,
                      submit_to_first_fill_ms=min(fill_ms) if fill_ms and len(fill_ms)==len(fills) else None,
                      submit_to_last_fill_ms=max(fill_ms) if fill_ms and len(fill_ms)==len(fills) and state=="filled" and math.isclose(received,filled,abs_tol=1e-8) else None,
                      cancel_to_ack_ms=min(cancel_ms) if cancel_ms else None)
        orders.append(result)
        for event in fills:
            price = _number(event["price"])
            trades.append({**{key:event.get(key) for key in ("channel","trading_date","code","orderid","tradeid","qty","price","wall_time_ns")},
                           "benchmark_price":baseline, "arrival_slippage":sign*(price-baseline) if sign and baseline else None,
                           "submit_to_fill_ms":_elapsed(start,event)})
    total = sum(row["total"] for row in orders)
    filled = sum(row["filled"] for row in orders)
    statuses = {name:sum(row["status"]==name for row in orders) for name in ("filled","canceled","rejected","open")}
    orders.sort(key=lambda row:_number(row.get("wall_time_ns")) or 0,reverse=True)
    trades.sort(key=lambda row:_number(row.get("wall_time_ns")) or 0,reverse=True)
    return {"summary":{"submitted_orders":len(orders),"submitted_volume":total,"filled_volume":filled,
                       "volume_fill_rate":filled/total if total else None,
                       "full_order_rate":statuses["filled"]/len(orders) if orders else None,
                       **statuses,"unmatched_events":len(unmatched),"invalid_events":invalid,
                       "duplicate_fills":duplicate_fills},
            "orders":orders[:limit],"trades":trades[:limit],"detail_limit":limit,
            "definitions":{"sample":"仅原生遥测启用后的送单；分母包含未完结、拒单与撤单",
                           "price":"送单前本机最新对手价；买入取卖一，卖出取买一。正值更差",
                           "latency":"同进程单调时钟，送单调用前至适配器收到回报；跨进程留空",
                           "clock":"本地测量包含柜台与网络等待；不代表交易所内部耗时"}}


def event_quality(group, channels, *, limit=200):
    root=Path(group["path"])/"generated/traders"
    events, sources, invalid = [], [], 0
    for channel in channels:
        path=(root/channel/"execution-quality.jsonl").resolve()
        if path.parent.parent != root.resolve():
            raise ValueError("Invalid channel path")
        exists=path.exists()
        sources.append({"channel":channel,"available":exists})
        if exists:
            rows,_,errors=ledger_cache.read(path,"jsonl")
            events.extend(rows);invalid += errors
    result=summarize_events(events,limit=limit)
    result["summary"]["invalid_events"] += invalid
    return {**result,"available":any(row["available"] for row in sources),"sources":sources}

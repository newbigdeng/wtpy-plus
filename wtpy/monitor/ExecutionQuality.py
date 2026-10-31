"""Read native channel ledgers; never submit orders or infer missing clocks."""
import math
from pathlib import Path
from .ExecutionQualityFiles import ledger_cache


def _number(value):
    try:
        value = float(value)
        return value if math.isfinite(value) else None
    except (ValueError, TypeError):
        return None


def _read_csv(path):
    rows, available, _ = ledger_cache.read(path, "csv")
    return rows, available


def _identity(row):
    return tuple(str(row.get(key, "")).strip() for key in
                 ("channel", "code", "localid", "orderid", "action"))


def _clock(row, name):
    value = _number(row.get(name))
    return int(value) if value and value >= 1000000000000 else None


def summarize_execution(orders, trades, *, contracts=(), limit=200):
    """Summarize complete terminal-order samples, independent of UI ledger limits.

    Native orders.csv has terminal notifications only. A filled quantity ratio
    here is conditional on those recorded terminal orders, never all submissions.
    """
    ticks = {row["code"]: _number(row.get("price_tick")) for row in contracts if row.get("code")}
    unique, duplicates, invalid = {}, 0, 0
    for source in orders:
        row = dict(source)
        total, filled, price = (_number(row.get(name)) for name in ("volume", "traded", "price"))
        if total is None or total <= 0 or filled is None or not 0 <= filled <= total or price is None:
            invalid += 1
            continue
        row.update(total=total, filled=filled, price=price)
        key = (_identity(row), str(row.get("date", "")), row.get("inserttime"))
        if key in unique:
            duplicates += 1
        unique[key] = row
    rows = []
    by_identity = {}
    for source in unique.values():
        row = {**source, "fills": [], "insert_time": _clock(source, "inserttime")}
        rows.append(row)
        if str(row.get("orderid") or "").strip():
            by_identity.setdefault(_identity(row), []).append(row)
    seen_trades, unmatched, trade_duplicates = set(), [], 0
    for source in trades:
        trade = dict(source)
        volume, price = _number(trade.get("volume")), _number(trade.get("price"))
        if volume is None or volume <= 0 or price is None or not str(trade.get("tradeid", "")).strip():
            invalid += 1
            continue
        key = (_identity(trade), str(trade.get("date", "")), str(trade["tradeid"]).strip())
        if key in seen_trades:
            trade_duplicates += 1
            continue
        seen_trades.add(key)
        trade.update(volume=volume, price=price, trade_time=_clock(trade, "time"))
        candidates = by_identity.get(_identity(trade), [])
        # OrderDate and TradeDate can differ when an order spans midnight.
        timed = [row for row in candidates if row["insert_time"] and trade["trade_time"]
                 and row["insert_time"] <= trade["trade_time"]]
        if timed:
            latest = max(row["insert_time"] for row in timed)
            candidates = [row for row in timed if row["insert_time"] == latest]
        else:
            candidates = [row for row in candidates if str(row.get("date")) == str(trade.get("date"))]
        if len(candidates) == 1:
            candidates[0]["fills"].append(trade)
        else:
            unmatched.append(trade)
    measurements, order_results = [], []
    for row in rows:
        fills = row.pop("fills")
        quantity = sum(trade["volume"] for trade in fills)
        reconciled = math.isclose(quantity, row["filled"], abs_tol=1e-8)
        vwap = sum(trade["volume"] * trade["price"] for trade in fills) / quantity if quantity else None
        sign = 1 if row.get("action") in ("OL", "CS", "CNS") else -1 if row.get("action") in ("OS", "CL", "CNL") else None
        baseline = row["price"] if row["price"] > 0 else None
        tick = ticks.get(row.get("code"))
        deviation = sign * (vwap - baseline) if sign and vwap is not None and baseline is not None else None
        times = [trade["trade_time"] for trade in fills if trade["trade_time"]]
        first = min(times) if times and len(times) == len(fills) else None
        last = max(times) if first else None
        first_delta = first - row["insert_time"] if first and row["insert_time"] and first >= row["insert_time"] else None
        last_delta = last - row["insert_time"] if last and row["insert_time"] and last >= row["insert_time"] else None
        result = {key: row.get(key) for key in ("channel", "localid", "orderid", "date", "code", "action", "insert_time", "total", "filled", "price")}
        result.update(canceled=str(row.get("canceled", "")).upper() == "TRUE",
                      status="canceled" if str(row.get("canceled", "")).upper() == "TRUE" else "filled" if row["filled"] == row["total"] else "terminal_unclassified",
                      matched_volume=quantity, reconciled=reconciled, vwap=vwap,
                      limit_deviation=deviation, limit_deviation_ticks=deviation / tick if deviation is not None and tick and tick > 0 else None,
                      arrival_slippage=None, submit_to_ack_ms=None, callback_latency_ms=None,
                      exchange_first_fill_ms=first_delta, exchange_last_fill_ms=last_delta,
                      fill_time_precision_ms=1000, first_fill_same_second=first_delta == 0,
                      first_fill_time=first, last_fill_time=last)
        order_results.append(result)
        for trade in fills:
            delta = trade["trade_time"] - row["insert_time"] if trade["trade_time"] and row["insert_time"] and trade["trade_time"] >= row["insert_time"] else None
            value = sign * (trade["price"] - baseline) if sign and baseline else None
            measurements.append({**trade, "limit_price": baseline, "limit_deviation": value,
                                 "limit_deviation_ticks": value / tick if value is not None and tick and tick > 0 else None,
                                 "exchange_fill_ms": delta, "time_precision_ms": 1000,
                                 "arrival_slippage": None, "submit_to_fill_ms": None})
    total = sum(row["total"] for row in rows)
    filled = sum(row["filled"] for row in rows)
    dates = sorted({str(row["date"]) for row in rows if row.get("date")})
    counts = {name: sum(row["status"] == name for row in order_results)
              for name in ("filled", "canceled", "terminal_unclassified")}
    order_results.sort(key=lambda row: row["insert_time"] or 0, reverse=True)
    measurements.sort(key=lambda row: row["trade_time"] or 0, reverse=True)
    return {"summary": {"terminal_orders": len(rows), "sample_start_date": dates[0] if dates else None,
            "sample_end_date": dates[-1] if dates else None, "terminal_order_volume": total,
            "terminal_filled_volume": filled, "terminal_volume_fill_rate": filled / total if total else None,
            "terminal_full_order_rate": counts["filled"] / len(rows) if rows else None,
            **counts, "unique_fills": len(seen_trades), "unmatched_fills": len(unmatched),
            "unreconciled_orders": sum(not row["reconciled"] for row in order_results),
            "duplicate_orders": duplicates, "duplicate_fills": trade_duplicates, "invalid_rows": invalid,
            "open_orders": None, "rejected_orders": None, "all_submission_fill_rate": None},
            "orders": order_results[:limit], "trades": measurements[:limit],
            "unmatched_trades": unmatched[:limit], "detail_limit": limit,
            "definitions": {"sample": "通道原生账簿内全部终态委托；明细只显示最近记录",
                "fill_rate": "终态单已成交量 / 终态单委托量；包含撤单未成交量",
                "full_order_rate": "全部成交的终态单数 / 终态单数",
                "price": "限价偏离=买入(成交价−限价)，卖出(限价−成交价)；正值更差",
                "latency": "柜台成交时间−柜台报单时间；CTP秒精度，0表示同一秒",
                "missing": "账簿缺送单前行情、本地送单/回报时钟、活跃单与完整拒单；相应字段留空"}}


def execution_quality(group, channels, *, contracts=(), limit=200):
    """Read only an already authorized group's configured channel paths."""
    root = Path(group["path"]) / "generated" / "traders"
    orders, trades, sources = [], [], []
    for channel in channels:
        path = (root / channel).resolve()
        if path.parent != root.resolve():
            raise ValueError("Invalid channel path")
        order_rows, order_exists = _read_csv(path / "orders.csv")
        trade_rows, trade_exists = _read_csv(path / "trades.csv")
        orders.extend({**row, "channel": channel} for row in order_rows)
        trades.extend({**row, "channel": channel} for row in trade_rows)
        sources.append({"channel": channel, "orders_available": order_exists, "trades_available": trade_exists})
    return {**summarize_execution(orders, trades, contracts=contracts, limit=limit),
            "sources": sources, "read_only": True}

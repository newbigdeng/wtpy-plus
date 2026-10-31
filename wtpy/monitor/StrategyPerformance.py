"""Read theoretical strategy performance without changing the native ledger."""
import csv
from datetime import datetime, timedelta
import json
import math
from pathlib import Path
from zoneinfo import ZoneInfo

from .MarketSessions import session_status


def _csv(path):
    if not path.is_file():
        return []
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _json(path):
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}


def strategy_funds(group, strategyid):
    """Native settlements take priority; flat boundaries can recover missing days.

    A recovered day needs complete trade fees and closes reconciled to the live
    cumulative fund. Carried positions need a native settlement, never a guessed
    historical mark. Strategy resets retain these native cumulative totals.
    """
    from .WorkbenchApi import load_config
    folder = Path(group["path"])
    output = folder/"generated/outputs"/strategyid
    ledger = _json(folder/"generated/stradata"/(strategyid+".json"))
    fund = ledger.get("fund", {})
    rows = {}
    for row in _csv(output/"funds.csv"):
        day = int(row["date"])
        rows[day] = dict(strategy=strategyid, date=day, closeprofit=float(row["closeprofit"]),
                         dynprofit=float(row["positionprofit"]), dynbalance=float(row["dynbalance"]),
                         fee=float(row.get("fee") or 0), source="native_settlement")
    current_day = int(fund.get("tdate", 0))
    if current_day:
        profit, floating, fee = (float(fund[key]) for key in ("total_profit", "total_dynprofit", "total_fees"))
        rows[current_day] = dict(strategy=strategyid, date=current_day, closeprofit=profit,
                                 dynprofit=floating, fee=fee, dynbalance=profit+floating-fee,
                                 source="native_strategy_fund")

    bases = load_config(folder).get("basefiles", {})
    metadata = {key:_json(folder/name) for key in ("commodity", "session", "holiday")
                if isinstance((name := bases.get(key)), str)}

    def trading_day(code, stamp):
        exchange, product, *_ = code.split(".")
        commodity = metadata.get("commodity", {}).get(exchange, {}).get(product, {})
        contract = {**commodity, "exchange":exchange}
        moment = datetime.strptime(str(stamp), "%Y%m%d%H%M").replace(tzinfo=ZoneInfo("Asia/Shanghai"))
        for clock in (moment, moment-timedelta(microseconds=1)):
            day = session_status(contract, metadata.get("session", {}), metadata.get("holiday", {}), clock)["trading_date"]
            if day:
                return day
        return None

    def previous_trading_day(day):
        if not trades:
            return None
        moment = datetime.strptime(str(day), "%Y%m%d")
        for _ in range(31):
            moment -= timedelta(days=1)
            candidate = trading_day(trades[0]["code"], moment.strftime("%Y%m%d")+"0905")
            if candidate:
                return candidate
        return None

    trades, closes = _csv(output/"trades.csv"), _csv(output/"closes.csv")
    gross = sum(float(row["profit"]) for row in closes)
    fees = sum(float(row["fee"]) for row in trades)
    complete = bool(current_day and trades and
                    math.isclose(gross, float(fund["total_profit"]), abs_tol=1e-6) and
                    math.isclose(fees, float(fund["total_fees"]), abs_tol=1e-6))
    days = {}
    positions = {}
    if complete:
        for trade in sorted(trades, key=lambda row:int(row["time"])):
            day = trading_day(trade["code"], trade["time"])
            if day is None:
                complete = False
                break
            data = days.setdefault(day, {"profit":0, "fee":0, "flat_before":not any(positions.values())})
            key = trade["code"], trade["direct"]
            positions[key] = positions.get(key, 0)+float(trade["qty"])*(1 if trade["action"] == "OPEN" else -1)
            if positions[key] < -1e-8:
                complete = False
                break
            data["fee"] += float(trade["fee"])
            data["flat_after"] = not any(abs(value)>1e-8 for value in positions.values())
        for close in closes:
            day = trading_day(close["code"], close["closetime"])
            if day not in days:
                complete = False
                break
            days[day]["profit"] += float(close["profit"])

    if complete:
        cumulative_profit = cumulative_fee = 0
        for day, data in sorted(days.items()):
            cumulative_profit += data["profit"]
            cumulative_fee += data["fee"]
            if day not in rows and data["flat_after"]:
                rows[day] = dict(strategy=strategyid, date=day, closeprofit=cumulative_profit,
                                 dynprofit=0, fee=cumulative_fee, dynbalance=cumulative_profit-cumulative_fee,
                                 source="reconciled_flat_trade_ledger")
        if current_day not in days and not any(abs(value)>1e-8 for value in positions.values()):
            days[current_day] = dict(profit=0, fee=0, flat_before=True, flat_after=True)

    previous = None
    result = []
    for day, row in sorted(rows.items()):
        data = days.get(day) if complete else None
        if data and data["flat_before"]:
            daily = dict(profit=data["profit"], thisfee=data["fee"],
                         addition=data["profit"]+row["dynprofit"]-data["fee"])
            basis = "reconciled_flat_trade_ledger"
        elif previous is not None and previous["date"] == previous_trading_day(day):
            daily = dict(profit=row["closeprofit"]-previous["closeprofit"],
                         thisfee=row["fee"]-previous["fee"], addition=row["dynbalance"]-previous["dynbalance"])
            basis = "native_settlement_difference"
        else:
            daily = dict(profit=None, thisfee=None, addition=None)
            basis = "missing_start_settlement"
        result.append({**row, **daily, "daily_basis":basis, "performance_kind":"strategy_theoretical"})
        # Only native day-end settlements are reliable carried-position marks.
        previous = row if row["source"] in ("native_settlement", "reconciled_flat_trade_ledger") else None
    return result

"""Account statistics from broker receipts, without inventing missing trading days."""
from bisect import bisect_right
from datetime import datetime
import json
import math
from pathlib import Path
import threading
import time
from urllib.parse import quote

SAMPLE_FIELDS = ("trading_date", "prebalance", "balance", "dynprofit", "deposit",
                 "withdraw", "fee", "closeprofit")
_receipt_lock = threading.Lock()
RESET_BASELINE_WINDOW_MS = 24*60*60*1000


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def _day(value):
    try:
        text = str(int(value))
        datetime.strptime(text, "%Y%m%d")
        return int(text) if len(text) == 8 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _events(events):
    return sorted((event for event in events if _number(event.get("time")) is not None),
                  key=lambda event: event["time"])


def _segment(event=None):
    return "capital-" + str(int(event["time"])) if event else "initial"


def _event_at(events, stamp):
    index = bisect_right([event["time"] for event in events], stamp)
    return events[index-1] if index else None


def account_point(account):
    """Persist only fields actually present in this native account receipt."""
    point = {"time": account["updated_at"], "equity": account["equity"],
             "available": account.get("available"), "margin": account.get("margin")}
    point.update({key: account[key] for key in SAMPLE_FIELDS
                  if _number(account.get(key)) is not None})
    return point


def update_daily_receipts(book, point, events):
    """Keep the final observed receipt per day/phase after curve points expire."""
    day = _day(point.get("trading_date"))
    if day is None:
        return False
    phase = _segment(_event_at(_events(events), point["time"]))
    rows = book.setdefault("daily_receipts", [])
    for index in range(len(rows)-1, -1, -1):
        previous = rows[index]
        if previous.get("trading_date") == day and previous.get("segment_id") == phase:
            if previous["time"] > point["time"]:
                return False
            candidate = {**point, "trading_date": day, "segment_id": phase}
            if previous == candidate:
                return False
            rows[index] = candidate
            return True
    rows.append({**point, "trading_date": day, "segment_id": phase})
    return True


def _read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return {}


def record_account_receipts(groupid, accounts, group_path, history_root, *, now=None):
    """Sidecar-only writer; the core's identity-bound accounts-v3 stays read-only.

    Call with native trade_risk_snapshot accounts, including stale broker receipts.
    A repeated receipt is stored once; freshness/validity flags remain unchanged.
    """
    from .AccountSnapshots import _identities
    identities = _identities(Path(group_path).resolve())
    return _record_receipts(groupid, accounts, group_path, history_root, identities,
                            time.time()*1000 if now is None else now)


def _record_receipts(groupid, accounts, group_path, history_root, identities, now):
    folder = Path(group_path).resolve()
    legacy = Path(history_root)/quote(groupid, safe="")/(quote(str(folder), safe="")+".json")
    target = legacy.with_suffix(".account-receipts-v1.json")
    with _receipt_lock:
        core = _read_json(legacy.with_suffix(".accounts-v3.json"))
        external_events = _read_json(legacy.with_suffix(".capital-events.json"))
        document = _read_json(target) or {"schema":1,"books":{},"active_accounts":{}}
        changed = False
        for account in accounts:
            channel, currency = account["channel"], account["currency"]
            key = channel+":"+currency
            identity_info = identities.get(channel)
            if identity_info is None:
                account.update(history=[],daily_receipts=[],capital_events=[],analytics_identity_valid=False)
                continue
            identity, config_time = identity_info
            book_key = ":".join(quote(str(part),safe="") for part in (channel,currency,*identity))
            core_book = core.get("books",{}).get(book_key,{})
            active = document["active_accounts"].get(key,{})
            if active.get("book") != book_key:
                active = {"book":book_key,"since":config_time}
                document["active_accounts"][key] = active
                changed = True
            book = document["books"].setdefault(book_key,{"history":[],"daily_receipts":[],"capital_events":[]})
            events = list(book["capital_events"])
            for event in core_book.get("capital_events",[])+external_events.get(key,[]):
                bound = event in core_book.get("capital_events",[]) or (_number(event.get("time")) or 0) >= active["since"]
                if bound and event not in events:
                    events.append(event);changed = True
            events = _events(events)
            book["capital_events"] = events
            stamp, equity = _number(account.get("updated_at")), _number(account.get("equity"))
            native = (account.get("timestamp_source") == "柜台查询回执时间" and stamp is not None
                      and active["since"] <= stamp <= now+1000 and equity is not None)
            if native:
                receipt = account_point(account)
                samples = book["history"]
                if not samples or samples[-1]["time"] < stamp:
                    samples.append(receipt);book["history"] = samples[-10000:];changed = True
                elif samples[-1]["time"] == stamp and samples[-1] != receipt:
                    samples[-1] = receipt;changed = True
                changed = update_daily_receipts(book,receipt,events) or changed
            merged = {point["time"]:point for point in core_book.get("history",[])}
            merged.update({point["time"]:point for point in book["history"]})
            series = [merged[stamp] for stamp in sorted(merged)][-10000:]
            reset_at = max((event["time"] for event in events),default=0)
            account.update(history=series,daily_receipts=book["daily_receipts"],capital_events=events,
                           current_history=[point for point in series if point["time"] >= reset_at],
                           history_limit=10000,analytics_identity_valid=native,
                           legacy_balance_samples=len(core.get("legacy_balance",{}).get(key,[])),
                           unbound_history_samples=len(core.get("unbound_accounts",{}).get(key,[])))
        if changed:
            target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            temporary = target.with_suffix(target.suffix+".next")
            temporary.write_text(json.dumps(document,ensure_ascii=False,allow_nan=False),encoding="utf-8")
            temporary.chmod(0o600);temporary.replace(target)
    return accounts


def _daily(point, phase, reset=None, reset_day=None):
    values = {key: _number(point.get(key)) for key in
              ("equity", "prebalance", "deposit", "withdraw", "fee")}
    day = _day(point.get("trading_date"))
    complete = all(values[key] is not None for key in ("equity", "prebalance", "deposit", "withdraw"))
    reason = "" if complete else "柜台回执缺日初资金或出入金字段"
    # A capital reset must not turn an old prebalance into a trading loss.
    if reset and day == reset_day:
        capital = _number(reset.get("requested_capital"))
        aligned = (reset.get("confirmed_at") and capital is not None and values["prebalance"] is not None
                   and math.isclose(values["prebalance"], capital, abs_tol=1e-8))
        if not aligned:
            complete = False
            reason = "重置当日的柜台日初资金尚未对齐新资金"
    pnl = values["equity"]-values["prebalance"]-values["deposit"]+values["withdraw"] if complete else None
    cash = values["deposit"]-values["withdraw"] if values["deposit"] is not None and values["withdraw"] is not None else None
    no_cash = complete and values["deposit"] == 0 and values["withdraw"] == 0
    return_pct = pnl/values["prebalance"]*100 if no_cash and values["prebalance"] > 0 else None
    return_reason = (reason if pnl is None else "存在出入金，缺少现金流时间权重" if not no_cash else
                     "日初资金不大于零" if values["prebalance"] <= 0 else "")
    return {**values, "trading_date": day, "segment_id": phase, "snapshot_at": point["time"],
            "pnl": pnl, "return_pct": return_pct, "net_cash_flow": cash,
            "complete": False, "reason": reason, "return_reason": return_reason,
            "basis": "broker_prebalance", "status": "observed_receipt"}


def _totals(days):
    pnl = sum(day["pnl"] for day in days) if days and all(day["pnl"] is not None for day in days) else None
    returns = [day["return_pct"] for day in days]
    result = (math.prod(1+value/100 for value in returns)-1)*100 if returns and all(value is not None for value in returns) else None
    return pnl, result


def _drawdown(points, baseline, cash_free):
    values = [point.get("pnl") for point in points]
    if not values or any(value is None for value in values):
        return None, None
    peak = 0
    amount = percent = 0
    for value in values:
        peak = max(peak, value)
        amount = max(amount, peak-value)
        if cash_free and baseline is not None and baseline+peak > 0:
            percent = max(percent, (peak-value)/(baseline+peak)*100)
    return amount, percent if cash_free and baseline is not None and baseline > 0 else None


def _curve(points, days, resets, reset_days):
    result = []
    for source in points:
        phase = source["segment_id"]
        day = _day(source.get("trading_date"))
        prior = [row for row in days if row["segment_id"] == phase and day is not None and row["trading_date"] < day]
        receipt = _daily(source, phase, resets.get(phase), reset_days.get(phase)) if day else None
        pnl, return_pct = _totals(prior+[receipt]) if receipt else (None, None)
        result.append({**source, "trading_date": day, "pnl": pnl, "return_pct": return_pct,
                       "pnl_basis": "observed_broker_days", "complete": False})
    return result


def _account(account, start_date, end_date, segment_id, now):
    events = _events([event for event in account.get("capital_events", []) if (_number(event.get("time")) or 0) <= now])
    resets = {_segment(event): event for event in events}
    raw_points = []
    for point in account.get("history", []):
        stamp, equity = _number(point.get("time")), _number(point.get("equity"))
        if stamp is not None and equity is not None:
            raw_points.append({**point, "segment_id": _segment(_event_at(events, stamp))})
    stored_samples = len(raw_points)
    legacy_samples = sum(_day(point.get("trading_date")) is None for point in raw_points)
    known_times = {point["time"]: index for index, point in enumerate(raw_points)}
    for point in account.get("daily_receipts", []):
        stamp, equity = _number(point.get("time")), _number(point.get("equity"))
        if stamp is not None and equity is not None:
            receipt = {**point, "segment_id": _segment(_event_at(events, stamp)), "sample_kind": "daily_receipt"}
            if stamp in known_times:
                # This is the same actual receipt, never an equity-difference estimate.
                raw_points[known_times[stamp]] = {**raw_points[known_times[stamp]], **receipt}
            else:
                known_times[stamp] = len(raw_points)
                raw_points.append(receipt)
    raw_points.sort(key=lambda point: point["time"])
    evidence = list(account.get("daily_receipts", []))+raw_points
    if (account.get("analytics_identity_valid") is not False and account.get("timestamp_source") == "柜台查询回执时间" and
            _number(account.get("updated_at")) is not None and account["updated_at"] <= now+1000 and _number(account.get("equity")) is not None):
        evidence.append(account_point(account))
    reset_days = {phase:_day(event.get("trading_date")) for phase,event in resets.items()
                  if _day(event.get("trading_date")) is not None}
    for point in sorted(evidence, key=lambda point: _number(point.get("time")) or 0):
        stamp = _number(point.get("time"))
        if stamp is None:
            continue
        phase = _segment(_event_at(events, stamp))
        if phase in resets and phase not in reset_days and _day(point.get("trading_date")):
            confirmed = _number(resets[phase].get("confirmed_at")) or resets[phase]["time"]
            # This is only a short baseline-protection window, not a calendar
            # inference. A new receipt days later belongs to its own broker day.
            if 0 <= stamp-confirmed <= RESET_BASELINE_WINDOW_MS:
                reset_days[phase] = _day(point["trading_date"])
    latest = {}
    for point in evidence:
        stamp, day = _number(point.get("time")), _day(point.get("trading_date"))
        if stamp is None or day is None or _number(point.get("equity")) is None:
            continue
        phase = _segment(_event_at(events, stamp))
        key = phase, day
        if key not in latest or latest[key]["time"] <= stamp:
            latest[key] = point
    daily = [_daily(point, phase, resets.get(phase), reset_days.get(phase))
             for (phase, day), point in sorted(latest.items(), key=lambda item: (item[0][1], item[1]["time"]))]
    phase_ids = ["initial"]+list(resets)
    current = phase_ids[-1]
    selected = current if segment_id == "current" else segment_id if segment_id not in (None, "all", "") else None
    def in_range(day):
        return day is not None and (start_date is None or day >= start_date) and (end_date is None or day <= end_date)
    chosen_days = [row for row in daily if in_range(row["trading_date"]) and (selected is None or row["segment_id"] == selected)]
    chosen_points = [point for point in raw_points if (selected is None or point["segment_id"] == selected)
                     and (in_range(_day(point.get("trading_date"))) if start_date or end_date else True)]
    points = _curve(chosen_points, chosen_days, resets, reset_days)
    segments = []
    for index, phase in enumerate(phase_ids):
        phase_days = [row for row in chosen_days if row["segment_id"] == phase]
        phase_points = [point for point in points if point["segment_id"] == phase]
        pnl, return_pct = _totals(phase_days)
        baseline = phase_days[0]["prebalance"] if phase_days else None
        cash_free = bool(phase_days) and all(row["deposit"] == 0 and row["withdraw"] == 0 for row in phase_days)
        drawdown, drawdown_pct = _drawdown(phase_points, baseline, cash_free)
        start = resets[phase]["time"] if phase in resets else min((point["time"] for point in raw_points), default=None)
        end = resets[phase_ids[index+1]]["time"] if index+1 < len(phase_ids) else None
        summary = {"segment_id": phase, "selected_segment_id": selected or current,
                   "start_equity": phase_points[0]["equity"] if phase_points else None,
                   "end_equity": phase_points[-1]["equity"] if phase_points else None,
                   "first_time": phase_points[0]["time"] if phase_points else None,
                   "last_time": phase_points[-1]["time"] if phase_points else None,
                   "pnl": pnl, "return_pct": return_pct, "max_drawdown": drawdown,
                   "max_drawdown_pct": drawdown_pct, "observed_days": len(phase_days),
                   "latest_trading_date": max((row["trading_date"] for row in phase_days), default=None),
                   "complete": False, "basis": "observed_broker_days"}
        segments.append({"id": phase, "start_at": start, "end_at": end, "current": phase == current,
                         "confirmed": bool(resets[phase].get("confirmed_at")) if phase in resets else True,
                         "summary": summary})
    monthly = []
    for phase, month in sorted({(row["segment_id"], str(row["trading_date"])[:4]+"-"+str(row["trading_date"])[4:6]) for row in chosen_days}):
        rows = [row for row in chosen_days if row["segment_id"] == phase and str(row["trading_date"]).startswith(month.replace("-", ""))]
        pnl, return_pct = _totals(rows)
        monthly.append({"month": month, "segment_id": phase, "pnl": pnl, "return_pct": return_pct,
                        "observed_days": len(rows), "pnl_days": sum(row["pnl"] is not None for row in rows),
                        "return_days": sum(row["return_pct"] is not None for row in rows), "complete": False,
                        "first_trading_date": rows[0]["trading_date"], "last_trading_date": rows[-1]["trading_date"],
                        "reason": "仅已记录交易日，未确认完整月份"})
    summary_id = selected or current
    summary = next((row["summary"] for row in segments if row["id"] == summary_id), {})
    key = str(account.get("channel", ""))+":"+str(account.get("currency", ""))
    return {"account_key": key, "channel": account.get("channel"), "currency": account.get("currency"),
            "receipt": {key:account.get(key) for key in
                        ("updated_at","valid","stale","connected","reason","timestamp_source","analytics_identity_valid")},
            "selected_segment_id": selected or current, "latest_trading_date": max((row["trading_date"] for row in daily), default=None),
            "points": points, "daily": chosen_days, "monthly": monthly, "segments": segments, "summary": summary,
            "cash_flows": [{"trading_date": row["trading_date"], "segment_id": row["segment_id"],
                            "deposit": row["deposit"], "withdraw": row["withdraw"], "net": row["net_cash_flow"],
                            "snapshot_at": row["snapshot_at"]} for row in chosen_days
                           if row["deposit"] not in (None, 0) or row["withdraw"] not in (None, 0)],
            "coverage": {"samples": len(points), "stored_samples": stored_samples, "observed_days": len(chosen_days),
                         "legacy_samples_without_trading_date": legacy_samples,
                         "daily_receipts": len(account.get("daily_receipts", [])), "complete": False,
                         "latest_receipt_at": max((point["time"] for point in raw_points),default=None),
                         "history_limit": account.get("history_limit"), "missing_days_filled": False}}


def account_analytics(accounts, *, start_date=None, end_date=None, segment_id=None, now=None):
    """Analyze already identity-bound account_series rows; never sum accounts."""
    if any(value not in (None, "") and _day(value) is None for value in (start_date, end_date)):
        raise ValueError("交易日须为有效的YYYYMMDD日期")
    start_date, end_date = _day(start_date), _day(end_date)
    if start_date and end_date and start_date > end_date:
        raise ValueError("开始交易日不能晚于结束交易日")
    now = time.time()*1000 if now is None else now
    return {"accounts": [_account(account, start_date, end_date, segment_id, now) for account in accounts],
            "read_only": True, "range": {"start_date": start_date, "end_date": end_date},
            "definitions": {"daily_pnl": "柜台动态权益−上日结算资金−入金＋出金；手续费已包含，不重复扣除",
                            "trading_day": "只使用柜台交易日，夜盘归柜台给出的下一交易日",
                            "daily_return": "无出入金且日初资金为正时，日净损益÷柜台日初资金",
                            "period": "日月累计仅含已记录交易日；缺失日留空，未确认为完整期间",
                            "curve": "净损益累加已记录日，收益率复合已记录日；字段缺失则留空",
                            "drawdown": "已采样净损益曲线的最大回撤；出入金期间不计算回撤百分比",
                            "segments": "账号由原账簿隔离；资本重置分段，阶段之间不连接曲线"}}

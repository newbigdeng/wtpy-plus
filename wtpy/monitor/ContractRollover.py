"""Read-only rollover reminders from listed contracts and stored open interest."""
from datetime import datetime, timedelta
import math
from zoneinfo import ZoneInfo

from .MarketSessions import CALENDAR, CN_EXCHANGES

# Historical contract-month habits, not a prediction of the next main contract.
MONTH_PATTERNS = {
    'rb': (1, 5, 10), 'hc': (1, 5, 10),
    'TA': (1, 5, 9), 'MA': (1, 5, 9), 'm': (1, 5, 9), 'p': (1, 5, 9),
    'c': (1, 3, 5, 7, 9, 11),
    **{p: tuple(range(1, 13)) for p in ('cu', 'al', 'zn', 'pb', 'ni', 'sn', 'bc')},
    **{p: tuple(range(1, 13)) for p in ('IF', 'IH', 'IC', 'IM')},
    **{p: (3, 6, 9, 12) for p in ('T', 'TF', 'TS', 'TL')},
}
PATTERN_SOURCE = 'https://www.cfachina.org/servicesupport/analygarden/jrl/fxszh6/202311/P020231109407861865365.pdf'
RB_RULE_SOURCE = 'https://www.shfe.com.cn/regulation/exchangerules/productrules/202512/t20251231_829962.html'


def contract_date(value):
    try:
        return datetime.strptime(str(value), "%Y%m%d").date()
    except (TypeError, ValueError):
        return None


def planned_exit(contract, holidays):
    """The workbench chooses to exit physical futures before delivery month."""
    expiry = contract_date(contract.get("expire_date"))
    if not expiry or contract.get("exchange") not in CN_EXCHANGES:
        return None
    if contract["exchange"] == "CFFEX":
        return expiry.strftime("%Y-%m-%d") if contract.get('product') in ('IF', 'IH', 'IC', 'IM') else None
    day = expiry.replace(day=1) - timedelta(days=1)
    configured = {str(x) for x in holidays.get(contract.get("holiday"), [])}
    bundled = contract.get("holiday") in ("CHINA", "CN")
    while True:
        stamp = int(day.strftime("%Y%m%d"))
        covered = bundled and CALENDAR["coverage_start"] <= stamp <= CALENDAR["coverage_end"]
        if not covered and not any(x.startswith(str(day.year)) for x in configured):
            return None
        closed = str(stamp) in configured or (covered and any(a <= stamp <= b for a, b in CALENDAR["closed_ranges"]))
        if day.weekday() < 5 and not closed:
            return day.strftime("%Y-%m-%d")
        day -= timedelta(days=1)


def rollover_hint(selected, catalog, summaries, holidays, now=None):
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    expiry = contract_date(selected.get("expire_date"))
    peers = {item["code"]: item for item in catalog
             if item["exchange"] == selected["exchange"] and item["product"] == selected["product"]
             and (contract_date(item.get("expire_date")) or now.date()) >= now.date()}
    valid = [row for row in summaries if row.get("code") in peers
             and isinstance(row.get("open_interest"), (int, float))
             and math.isfinite(row["open_interest"]) and row["open_interest"] > 0]
    latest = max((row["date"] for row in valid), default=None)
    rows = [row for row in valid if row["date"] == latest]
    current = next((row for row in rows if row["code"] == selected["code"]), None)
    later = [row for row in rows if expiry and (contract_date(peers[row["code"]].get("expire_date")) or expiry) > expiry]
    candidate = max(later, key=lambda row: (row["open_interest"], row.get("volume") or 0), default=None)
    crossed = bool(candidate and current and candidate["open_interest"] > current["open_interest"])
    deadline = planned_exit(selected, holidays)
    result = {"candidate": candidate, "market_date": latest, "sample_contracts": len(rows),
              "listed_contracts": len(peers), "plan_deadline": deadline,
              "plan_basis": "中金所合约表到期提醒" if selected["exchange"] == "CFFEX" else "工作台保守计划：交割月前完成换月",
              "liquidity_crossed": crossed, "checked_at": int(now.timestamp() * 1000),
              "scope": "同交易日、本机已采集合约，按持仓量比较；未预测未来主力。",
              "recommendation": "远月持仓量超过当前合约时，检查价差与流动性后换月。"}
    if crossed:
        market_day = contract_date(latest).isoformat()
        result["recommendation"] = f"截至{market_day}，远月持仓量已超越。先更新行情，再检查换月。"
    elif not candidate:
        result["recommendation"] = "先订阅同品种远月，观察持仓量何时超越当前合约。"
    if deadline and now.date().isoformat() >= deadline:
        result["recommendation"] = "已到工作台计划截止日，请检查到期与换月安排。"
    return planning_hint(selected, catalog, result, now)


def planning_hint(selected, catalog, observed, now=None):
    """Add an early planning window while keeping observed and habitual months distinct."""
    now = now or datetime.now(ZoneInfo('Asia/Shanghai'))
    expiry = contract_date(selected.get('expire_date'))
    product, exchange = selected.get('product'), selected.get('exchange')
    months = MONTH_PATTERNS.get(product, ())
    peers = [item for item in catalog if item.get('product') == product
             and item.get('exchange') == exchange
             and expiry and (contract_date(item.get('expire_date')) or expiry) > expiry]
    usual = [item for item in peers if contract_date(item.get('expire_date')).month in months]
    successor = min(usual, key=lambda item: item['expire_date'], default=None)
    cash_index = exchange == 'CFFEX' and product in ('IF', 'IH', 'IC', 'IM')
    delivery_month = expiry.replace(day=1) if expiry else None
    window = None
    if delivery_month and not cash_index:
        month_index = delivery_month.year * 12 + delivery_month.month - 3
        start = delivery_month.replace(year=month_index // 12, month=month_index % 12 + 1)
        window = {'start': start.isoformat(), 'end': (delivery_month-timedelta(days=1)).isoformat()}
    result = dict(observed)
    result.update(observation_window=window,
        planned_candidate={k: successor.get(k) for k in ('code','ticker','expire_date')} if successor else None,
        pattern_months=list(months), pattern_source=PATTERN_SOURCE,
        pattern_basis='历史主力月份惯例；接续候选仍需成交与持仓确认',
        delivery_note='到期持仓限制按具体交易所、品种和柜台通知处理。',
        rule_source=None, last_trade_date=expiry.isoformat() if expiry else None,
        plan_basis='保守交易计划，具体持仓期限按柜台通知。')
    if exchange == 'SHFE' and product in ('rb', 'hc'):
        result['delivery_note']='交割月持仓须为30手整倍数；自然人须在最后交易日前第5个交易日清零。柜台可能要求更早。'
        result['rule_source']=RB_RULE_SOURCE if product == 'rb' else None
    elif cash_index:
        result['delivery_note']='股指期货采用现金交割，可持仓进入交割月；换月仍需比较近月流动性。'
    elif exchange == 'CFFEX':
        result['delivery_note']='国债期货持仓与交割资格相关，须核对国债托管账户和柜台要求。'
    if not observed.get('liquidity_crossed'):
        if window and now.date().isoformat() < window['start']:
            result['recommendation']=window['start']+'起观察换月，结合远月成交与持仓迁移决定。'
        elif window:
            result['recommendation']='已进入换月观察期，比较新旧合约持仓、成交和买卖价差。'
        else:
            result['recommendation']='比较当前与接续月份的成交、持仓和买卖价差，再决定换月。'
        deadline=observed.get('plan_deadline')
        if deadline and now.date().isoformat() >= deadline:
            result['recommendation']='已到保守计划完成日，请核对当前持仓与柜台到期安排。'
    return result

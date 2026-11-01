"""Exact-contract exchange quotes for the watch page. No feed subscriptions."""
from datetime import date, datetime, timedelta
import csv
from io import TextIOWrapper
from functools import lru_cache
import json
import math
from time import monotonic
from threading import Lock
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

CN = ZoneInfo("Asia/Shanghai")
PERIODS = {"d":101, "m1":1, "m5":5}
SOURCE = "东方财富"


def get(host, path, **params):
    req = Request(f"https://{host}.eastmoney.com/{path}?"+urlencode(params),
                  headers={"User-Agent":"Mozilla/5.0", "Referer":"https://quote.eastmoney.com/"})
    with urlopen(req, timeout=6) as response:
        return json.load(response)


@lru_cache(maxsize=256)
def security(exchange, raw, option):
    code = raw.replace("-", "").lower()
    rows = get("searchapi","api/suggest/get",input=code,type=14)["QuotationCodeTable"]["Data"] or []
    markets = {"SHFE":("SHFE","SHF"),"CZCE":("CZCE","CZC","ZCE"),"GFEX":("GFEX","GFE")}
    for row in rows:
        venue = str(row.get("JYS","")).upper()
        if (row["Code"].lower()==code and venue.startswith(markets.get(exchange,(exchange,)))
                and (("OP" in venue) == option)):
            market, ticker = row["QuoteID"].split(".",1)
            if market.isdigit() and ticker.lower()==code:
                return row["QuoteID"]
    raise ValueError("公开行情未找到交易所和代码均匹配的合约")


def valid_bar(o,h,l,c,v):
    return all(math.isfinite(n) for n in (o,h,l,c,v)) and 0<l<=min(o,c)<=max(o,c)<=h<1e20 and v>=0


def remote_bars(exchange, raw, period, end, option=True):
    if not option:
        try:
            bars=underlying_history(exchange,raw,period,end)
            if bars:return bars
        except (OSError,ValueError,KeyError):pass
    sec = security(exchange,raw,option)
    result = get("push2his","api/qt/stock/kline/get",secid=sec,klt=PERIODS[period],fqt=0,
                 fields1="f1,f2,f3,f4,f5,f6",fields2="f51,f52,f53,f54,f55,f56,f57,f63",
                 end=end.replace("-",""),lmt=320).get("data")
    if not result or f"{result['market']}.{result['code']}".lower()!=sec.lower():
        raise ValueError("行情源没有返回这个合约")
    bars=[]
    for line in result.get("klines") or []:
        stamp,o,c,h,l,v,amount,*extra=line.split(",")
        values=[float(n) for n in (o,h,l,c,v)]
        dt=datetime.fromisoformat(stamp)
        if dt.date().isoformat()>end or not valid_bar(*values):continue
        bars.append(dict(zip(("open","high","low","close","volume"),values),
                         day=stamp,amount=finite(amount),oi=finite(extra[0]) if extra else None,source=SOURCE))
    return bars


def underlying_history(exchange,raw,period,end):
    # EDB timestamps mark the opening minute. The chart labels the closing minute.
    until=date.fromisoformat(end)
    params=dict(symbol=exchange+"."+raw,period=86400 if period=="d" else 60,
                start_time=str(until-timedelta(days=245 if period=="d" else 7))+" 00:00:00",
                end_time=end+" 23:59:59")
    bars={}
    with urlopen("https://edb.shinnytech.com/md/kline?"+urlencode(params),timeout=8) as r:
        for item in csv.DictReader(TextIOWrapper(r,encoding="utf8")):
            dt=datetime.fromtimestamp(int(item["datetime_nano"])//10**9,CN)
            if period=="d":stamp=dt.date().isoformat()
            else:
                close=dt+timedelta(minutes=(1 if period=="m1" else 5-dt.minute%5))
                stamp=close.strftime("%Y-%m-%d %H:%M")
            o,h,l,c,v=(float(item[k]) for k in ("open","high","low","close","volume"))
            if stamp[:10]>end or not valid_bar(o,h,l,c,v):continue
            b=bars.setdefault(stamp,dict(day=stamp,open=o,high=h,low=l,volume=0,source="信易 EDB"))
            b.update(high=max(h,b["high"]),low=min(l,b["low"]),close=c,oi=finite(item["close_oi"]))
            b["volume"]+=v
    return [bars[t] for t in sorted(bars)[-320:]]


def finite(v, *, price=False):
    try:
        n=float(v)
        if math.isfinite(n) and (0<n if price else 0<=n) and n<1e25:return n
    except (ValueError,TypeError):pass


_quote_lock=Lock()
_quote_next=0.


def public_quote(exchange,raw,end=None):
    # Data Center allows fifteen requests/minute across its public endpoints.
    global _quote_next
    with _quote_lock:
        if monotonic()<_quote_next:raise ValueError("报价源请求间隔内，请稍后刷新")
        _quote_next=monotonic()+6
    params=dict(instruments=raw,markets=exchange)
    if end:params["tradingday"]=end
    with urlopen("http://dict.openctp.cn/prices?"+urlencode(params),timeout=5) as r:
        rows=json.load(r).get("data") or []
    for x in rows:
        if (x.get("ExchangeID"),x.get("InstrumentID"))!=(exchange,raw):continue
        day=x.get("TradingDay");clock=x.get("UpdateTime")
        if end and day!=end:continue
        # Some night snapshots put the *trading date* in UpdateDate. Never
        # stamp that quote onto a future civil minute.
        stamp=None
        if x.get("UpdateDate") and clock and (x["UpdateDate"]!=day or "08:00"<=clock<"18:00"):
            dt=datetime.fromisoformat(x["UpdateDate"]+" "+clock).replace(tzinfo=CN)
            if dt<=datetime.now(CN):stamp=dt.isoformat(timespec="seconds")
        fields={"last":"LastPrice","bid":"BidPrice1","ask":"AskPrice1","prev_close":"PreClosePrice",
                "prev_settle":"PreSettlementPrice","open":"OpenPrice","high":"HighestPrice","low":"LowestPrice",
                "bid_size":"BidVolume1","ask_size":"AskVolume1","oi":"OpenInterest","volume":"Volume","amount":"Turnover"}
        values={k:finite(x.get(f),price=k not in ("bid_size","ask_size","oi","volume","amount")) for k,f in fields.items()}
        return dict(values,source="OpenCTP 延迟快照",time=stamp,source_clock=clock,trading_day=day,
                    observed=datetime.now(CN).isoformat(timespec="seconds"))

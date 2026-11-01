"""Import closed daily bars into source-specific WT archives outside collector storage."""
from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
from threading import Lock
from urllib.parse import urlencode
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request as URLRequest

from fastapi import Body, Request
from wtpy.StrategyRuntime import atomic_json
from .AssetMarkets import asset_root, inside, instrument, OPTION
from .AssetQuotes import read_url
from .HistoryCatalog import native_worker
from .MainHistory import edb
from .MarketHistory import TushareHistory, expected_stamps
from .OptionBars import CN, get, valid_bar, remote_bars
from .OptionExpiryHistory import daily
from .OptionWatch import catalog, provider_future
from .Roles import can_admin

SOURCES = {"stock": {"tushare": "Tushare", "tencent": "腾讯证券", "eastmoney": "东方财富"},
           "option": {"sina": "新浪财经", "tushare":"Tushare","edb": "信易 EDB", "eastmoney": "东方财富"},
           "crypto": {"advanced": "Coinbase Advanced Trade", "exchange": "Coinbase Exchange", "binance": "Binance 现货"}}
def periods(asset, provider):
    if asset == "crypto" or (asset == "option" and provider in ("eastmoney","tushare")):
        return ["d","m1","m5"]
    return ["d","m1"] if asset == "option" and provider == "edb" else ["d"]


_writer = Lock()


def days(start, end, tz):
    if not 0 <= (end-start).days < 90 or end >= datetime.now(tz).date():
        raise ValueError("请选择已结束的日期，每次最多 90 个自然日")


def coinbase_days(provider, symbol, start, end, period="d"):
    instrument("crypto", symbol)
    days(start, end, timezone.utc)
    stop = end + timedelta(days=1)
    # Only this independent service mounts the optional proxy file. The opener
    # is used solely for the two fixed Coinbase hosts and rejects redirects.
    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(self, *args):
            return None
    path = os.environ.get("WT_COINBASE_PROXY_FILE")
    handlers = ProxyHandler({"https":Path(path).read_text().strip()} if path else {})
    def fetch(url):
        request = URLRequest(url,headers={"User-Agent":"WT-MarketMonitor/1.0","Accept":"application/json"})
        with build_opener(handlers, NoRedirect()).open(request,timeout=12) as response:
            return json.load(response)
    step, unit = {"d":(86400,"ONE_DAY"),"m1":(60,"ONE_MINUTE"),"m5":(300,"FIVE_MINUTE")}[period]
    epoch = date(1970,1,1)
    first, last = (start-epoch).days*86400, (stop-epoch).days*86400
    bars=[]
    for cursor in range(first,last,300*step):
        limit=min(last,cursor+300*step)
        if provider == "exchange":
            iso=lambda t:datetime.fromtimestamp(t,timezone.utc).isoformat()
            qs=urlencode(dict(start=iso(cursor),end=iso(limit),granularity=step))
            data=fetch(f"https://api.exchange.coinbase.com/products/{symbol}/candles?"+qs)
            rows=((t,o,h,l,c,v) for t,l,h,o,c,v in data)
        elif provider == "advanced":
            qs=urlencode(dict(start=cursor,end=limit,granularity=unit))
            data=fetch(f"https://api.coinbase.com/api/v3/brokerage/market/products/{symbol}/candles?"+qs)
            rows=((r["start"],*(r[k] for k in ("open","high","low","close","volume"))) for r in data["candles"])
        else:raise ValueError("未知 Coinbase 数据源")
        for t,o,h,l,c,v in rows:
            t=int(t)
            if cursor<=t<limit and t%step==0:
                day=datetime.fromtimestamp(t,timezone.utc).date()
                if period=="d":bars.append([day.isoformat(),o,h,l,c,v])
                else:
                    close=datetime.fromtimestamp(t+step,timezone.utc)
                    bars.append([int(close.strftime("%Y%m%d%H%M")),int(day.strftime("%Y%m%d")),o,h,l,c,v])
    return bars



def crypto_rows(provider,raw,start,end,period):
    if provider!="binance":
        return coinbase_days(provider,raw,start,end,period)
    base,quote=raw.split("-")
    symbol=base+quote
    opener=build_opener(ProxyHandler({}))
    def fetch(endpoint,**params):
        with opener.open("https://data-api.binance.vision/api/v3/"+endpoint+"?"+urlencode(params),timeout=10) as response:
            return json.load(response)
    info=fetch("exchangeInfo",symbol=symbol)["symbols"]
    if not any(r["symbol"]==symbol and (r["baseAsset"],r["quoteAsset"])==(base,quote) for r in info):
        raise ValueError("Binance 未返回所选现货币对")
    interval,step={"m1":("1m",60000),"m5":("5m",300000),"d":("1d",86400000)}[period]
    first=(start-date(1970,1,1)).days*86400000
    stop=(end-date(1970,1,1)).days*86400000+86400000
    bars=[]
    for cursor in range(first,stop,1000*step):
        limit=min(cursor+1000*step,stop)
        for t,o,h,l,c,v,*_ in fetch("klines",symbol=symbol,interval=interval,startTime=cursor,endTime=limit-1,limit=1000):
            if not cursor<=t<limit or t%step:continue
            day=datetime.fromtimestamp(t/1000,timezone.utc).date()
            if period=="d":bars.append([day.isoformat(),o,h,l,c,v])
            else:
                close=datetime.fromtimestamp((t+step)/1000,timezone.utc)
                bars.append([int(close.strftime("%Y%m%d%H%M")),int(day.strftime("%Y%m%d")),o,h,l,c,v])
    return bars


def download(root, asset, provider, ex, raw, start, end, config):
    a, b = (d.strftime("%Y%m%d") for d in (start,end))
    if asset == "crypto":
        return crypto_rows(provider,raw,start,end,"d")
    if asset == "option":
        if provider=="tushare":
            code=option_ts_code(root,ex,raw)
            rs=TushareHistory(config).request("opt_daily",dict(ts_code=code,start_date=a,end_date=b),"ts_code,trade_date,open,high,low,close,vol")
            return [[r["trade_date"],*(r[k] for k in ("open","high","low","close","vol"))] for r in rs if r["ts_code"]==code and all(r[k] is not None for k in ("open","high","low","close","vol"))]
        if provider == "edb":
            return [[d, *(r[k] for k in ("open","high","low","close","volume"))] for d,r in edb(ex+"."+raw,start,end).items()]
        if provider=="eastmoney":
            return [[r["day"],*(r[k] for k in ("open","high","low","close","volume"))] for r in remote_bars(ex,raw,"d",end.isoformat())]
        m = OPTION.fullmatch(raw)
        meta = catalog(root).get(ex+"."+raw,{})
        code = provider_future(ex,m[1]+m[2],meta.get("expiry"))+m[4]+m[5]
        return [[r["d"], r["o"],r["h"],r["l"],r["c"],r["v"]] for r in daily(code, True)]
    if provider == "tushare":
        code = raw+(".SH" if ex=="SSE" else ".SZ")
        rows = TushareHistory(config).request("daily", dict(ts_code=code,start_date=a,end_date=b),
                                              "ts_code,trade_date,open,high,low,close,vol")
        return [[r["trade_date"],r["open"],r["high"],r["low"],r["close"],float(r["vol"])*100]
                for r in rows if r["ts_code"]==code]
    if provider == "tencent":
        symbol = ("sh" if ex=="SSE" else "sz")+raw
        query = urlencode(dict(param=f"{symbol},day,{start},{end},90,"))
        data = json.loads(read_url("https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"+query))
        # Only unadjusted closes match Tushare daily. Tencent volume is in lots.
        return [[d,o,h,l,c,float(v)*100] for d,o,c,h,l,v,*_ in data["data"][symbol].get("day",[])]
    market = 1 if ex=="SSE" else 0
    data = get("push2his","api/qt/stock/kline/get",secid=f"{market}.{raw}",klt=101,fqt=0,
               fields1="f1,f2,f3,f4,f5,f6",fields2="f51,f52,f53,f54,f55,f56",beg=a,end=b).get("data")
    if not data or data.get("code")!=raw or data.get("market")!=market:
        raise ValueError("东方财富未返回所选市场和证券")
    result = []
    for line in data.get("klines") or []:
        d,o,c,h,l,v = line.split(",")[:6]
        result.append([d,o,h,l,c,float(v)*100])
    return result


def normalized(rows, start, end):
    bars = {}
    for day,o,h,l,c,v in rows:
        day = date.fromisoformat(str(day)) if "-" in str(day) else datetime.strptime(str(day),"%Y%m%d").date()
        if not start<=day<=end:
            continue
        values = tuple(float(n) for n in (o,h,l,c,v))
        if not valid_bar(*values):
            continue
        key = int(day.strftime("%Y%m%d"))
        bar = dict(zip(("open","high","low","close","vol"),values),date=key)
        if key in bars and bars[key]!=bar:
            raise ValueError("数据源返回了同一天的冲突价格")
        bars[key]=bar
    if not bars:
        raise ValueError("所选区间没有有效日线，未写入数据")
    return [bars[k] for k in sorted(bars)]


def contract(root, asset, ex, raw):
    path = inside(root,"common","contracts.json")
    contracts = json.loads(path.read_bytes()) if path.is_file() else {}
    if row := contracts.get(ex,{}).get(raw):
        return row
    row = dict(name=raw,code=raw,exchg=ex)
    if asset=="option":
        for path in inside(root,"history","native").glob("*/common/contracts.json"):
            archived=json.loads(inside(root,*path.relative_to(root).parts).read_bytes()).get(ex,{}).get(raw)
            if archived and archived.get("option"):
                return archived
        meta = catalog(root).get(ex+"."+raw)
        if not meta or not meta.get("expiry"):
            raise ValueError("缺少该期权的到期日或标的资料，请先更新合约目录")
        row.update(expiredate=meta["expiry"],product=OPTION.fullmatch(raw)[1]+"_o",
                   option=dict(underlying=meta["underlying"].split(".")[1], strikeprice=meta["strike"],
                               optiontype=49 if OPTION.fullmatch(raw)[4]=="C" else 50, underlyingscale=1))
    return row


def option_ts_code(root,ex,raw):
    from .MarketHistory import tushare_code
    if ex=="CZCE":
        m=OPTION.fullmatch(raw)
        month=provider_future(ex,m[1]+m[2],contract(root,"option",ex,raw)["expiredate"])
        raw=month+m[4]+m[5]
    return tushare_code(dict(exchange=ex,ticker=raw,product=""))


def intraday(root,asset,provider,ex,raw,start,end,period,config=None):
    if (end-start).days>=3:
        raise ValueError("分钟线每次最多 3 天，请分段补数")
    if asset=="crypto":
        rows=crypto_rows(provider,raw,start,end,period)
    else:
        product=contract(root,asset,ex,raw)["product"]
        products=json.loads(inside(root,"common","commodities.json").read_bytes())
        session=json.loads(inside(root,"common","sessions.json").read_bytes())[products[ex][product]["session"]]
        dates={}
        for i in range((end-start).days+1):
            day=start+timedelta(days=i)
            stamps=expected_stamps(day,session,period)
            if stamps is None:raise ValueError("交易日历不覆盖所选日期")
            dates.update((stamp,int(day.strftime("%Y%m%d"))) for stamp in stamps)
        if not dates:raise ValueError("所选交易日没有交易时段")
        if provider=="edb":
            begin=datetime.strptime(str(min(dates))[:8],"%Y%m%d").date()
            series=edb(ex+"."+raw,begin,end,minutes=True)
        elif provider=="tushare":
            code=option_ts_code(root,ex,raw)
            fmt=lambda n:datetime.strptime(str(n),"%Y%m%d%H%M").isoformat(sep=" ")
            rs=TushareHistory(config).request("opt_mins",dict(ts_code=code,freq=period[1:]+"min",start_date=fmt(min(dates)),end_date=fmt(max(dates))),
                                              "ts_code,trade_time,open,high,low,close,vol")
            series={datetime.fromisoformat(r["trade_time"]).strftime("%Y%m%d%H%M"):dict(r,volume=r["vol"])
                    for r in rs if r["ts_code"]==code and all(r[k] is not None for k in ("open","high","low","close","vol"))}
        else:
            series={}
            # Source caps its response at 320 bars. Query each civil day independently.
            for civil in sorted({str(k)[:8] for k in dates}):
                d=datetime.strptime(civil,"%Y%m%d").date().isoformat()
                series.update((r["day"].translate(str.maketrans("","","-: ")),r) for r in remote_bars(ex,raw,period,d))
        rows=[[int(k),dates[int(k)],*(r[f] for f in ("open","high","low","close","volume"))]
              for k,r in series.items() if int(k) in dates]
    result={}
    for stamp,day,o,h,l,c,v in rows:
        values=tuple(float(n) for n in (o,h,l,c,v))
        if valid_bar(*values):
            bar=dict(zip(("open","high","low","close","vol"),values),date=day,time=stamp-199000000000)
            if stamp in result and result[stamp]!=bar:raise ValueError("接口返回了冲突的分钟线")
            result[stamp]=bar
    if not result:raise ValueError("所选时段没有有效分钟线，未写入")
    return [result[t] for t in sorted(result)]


def save(root, asset, provider, ex, raw, bars, period="d"):
    profile = inside(root,"history","native","feed-"+provider)
    with _writer:
        info = contract(root,asset,ex,raw)
        common = inside(profile,"common")
        common.mkdir(parents=True,exist_ok=True)
        base = {}
        for key, name in (("commodity","commodities"),("session","sessions"),("holiday","holidays"),("fee","fees")):
            source = inside(root,"common",name+".json")
            target = inside(common,name+".json")
            if source.is_file():
                atomic_json(target,json.loads(source.read_bytes()))
            elif key=="commodity" and not target.exists():
                atomic_json(target,{})
            if target.exists():
                base[key]="../common/"+target.name
        result = native_worker(dict(action="merge",period=period,storage=str(inside(profile,"storage")),exchange=ex,ticker=raw,
                                    start_date=bars[0]["date"],end_date=bars[-1]["date"],bars=bars,copy_realtime=False))
        path = inside(common,"contracts.json")
        records = json.loads(path.read_bytes()) if path.is_file() else {}
        records.setdefault(ex,{})[raw]=info
        atomic_json(path,records)
        base["contract"]="../common/contracts.json"
        hub = inside(profile,"datahub")
        hub.mkdir(exist_ok=True)
        atomic_json(inside(hub,"catalog.json"),dict(writer=dict(path="../storage"),basefiles=base))
    return dict(received=len(bars),added=result["rows"],first=bars[0]["date"],last=bars[-1]["date"])


def register(app, deploy, authorize):
    config = Path(os.environ.get("WT_HISTORY_PROVIDER_CONFIG") or Path(deploy)/"market-providers.local.json")

    @app.post("/mgr/assets/{asset}/backfill-sources")
    def sources(request:Request,asset:str):
        user,error=authorize(request,asset)
        if error:return error
        return dict(result=0,can_write=can_admin(user),sources=[
            dict(id=k,label=v,periods=periods(asset,k),available=k!="tushare" or TushareHistory(config).configured())
            for k,v in SOURCES.get(asset,{}).items()])

    @app.post("/mgr/assets/{asset}/backfill")
    def backfill(request:Request, asset:str, provider:str=Body(...), symbol:str=Body(...), start:date=Body(...), end:date=Body(...), period:str=Body("d")):
        user,error=authorize(request,asset)
        if error:return error
        if not can_admin(user):return dict(result=-403,message="仅管理员可补充历史数据")
        try:
            if provider not in SOURCES.get(asset,{}):raise ValueError("数据源与资产类别不匹配")
            ex,raw=instrument(asset,symbol)
            if provider=="binance":ex="BINANCE"
            days(start,end,timezone.utc if asset=="crypto" else CN)
            root=asset_root(deploy,asset)
            if period not in periods(asset,provider):raise ValueError("该接口不支持所选周期")
            bars=(normalized(download(root,asset,provider,ex,raw,start,end,config),start,end) if period=="d"
                  else intraday(root,asset,provider,ex,raw,start,end,period,config))
            return dict(result=0,**save(root,asset,provider,ex,raw,bars,period))
        except (OSError,KeyError,TypeError,ValueError) as exc:
            return dict(result=-2,message=str(exc) if isinstance(exc,ValueError) else "行情源未返回可用数据，或历史目录无法读写")

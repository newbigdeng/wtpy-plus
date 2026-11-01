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
from .MarketHistory import TushareHistory
from .OptionBars import CN, get, valid_bar
from .OptionExpiryHistory import daily
from .OptionWatch import catalog, provider_future
from .Roles import can_admin

SOURCES = {"stock": {"tushare": "Tushare", "tencent": "腾讯证券", "eastmoney": "东方财富"},
           "option": {"sina": "新浪财经", "edb": "信易 EDB"},
           "crypto": {"advanced": "Coinbase Advanced Trade", "exchange": "Coinbase Exchange"}}
_writer = Lock()


def days(start, end, tz):
    if not 0 <= (end-start).days < 90 or end >= datetime.now(tz).date():
        raise ValueError("请选择已结束的日期，每次最多 90 个自然日")


def coinbase_days(provider, symbol, start, end):
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
    if provider == "exchange":
        qs = urlencode(dict(start=start.isoformat()+"T00:00:00Z", end=stop.isoformat()+"T00:00:00Z", granularity=86400))
        data = fetch(f"https://api.exchange.coinbase.com/products/{symbol}/candles?"+qs)
        rows = ((t, o, h, l, c, v) for t, l, h, o, c, v in data)
    elif provider == "advanced":
        epoch = date(1970,1,1)
        qs = urlencode(dict(start=(start-epoch).days*86400, end=(stop-epoch).days*86400, granularity="ONE_DAY"))
        data = fetch(f"https://api.coinbase.com/api/v3/brokerage/market/products/{symbol}/candles?"+qs)
        rows = ((r["start"], *(r[k] for k in ("open","high","low","close","volume"))) for r in data["candles"])
    else:
        raise ValueError("未知 Coinbase 数据源")
    return [[datetime.fromtimestamp(int(t), timezone.utc).date().isoformat(), o,h,l,c,v] for t,o,h,l,c,v in rows]


def download(root, asset, provider, ex, raw, start, end, config):
    a, b = (d.strftime("%Y%m%d") for d in (start,end))
    if asset == "crypto":
        return coinbase_days(provider, raw, start, end)
    if asset == "option":
        if provider == "edb":
            return [[d, *(r[k] for k in ("open","high","low","close","volume"))] for d,r in edb(ex+"."+raw,start,end).items()]
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


def save(root, asset, provider, ex, raw, bars):
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
        result = native_worker(dict(action="merge",period="d",storage=str(inside(profile,"storage")),exchange=ex,ticker=raw,
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
            dict(id=k,label=v,available=k!="tushare" or TushareHistory(config).configured())
            for k,v in SOURCES.get(asset,{}).items()])

    @app.post("/mgr/assets/{asset}/backfill")
    def backfill(request:Request, asset:str, provider:str=Body(...), symbol:str=Body(...), start:date=Body(...), end:date=Body(...)):
        user,error=authorize(request,asset)
        if error:return error
        if not can_admin(user):return dict(result=-403,message="仅管理员可补充历史数据")
        try:
            if provider not in SOURCES.get(asset,{}):raise ValueError("数据源与资产类别不匹配")
            ex,raw=instrument(asset,symbol)
            days(start,end,timezone.utc if asset=="crypto" else CN)
            root=asset_root(deploy,asset)
            bars=normalized(download(root,asset,provider,ex,raw,start,end,config),start,end)
            return dict(result=0,**save(root,asset,provider,ex,raw,bars))
        except (OSError,KeyError,TypeError,ValueError) as exc:
            return dict(result=-2,message=str(exc) if isinstance(exc,ValueError) else "行情源未返回可用数据，或历史目录无法读写")

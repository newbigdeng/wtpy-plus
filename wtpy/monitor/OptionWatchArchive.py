"""Per-contract market observations. Native collector files stay read-only."""
from contextlib import closing
from datetime import date, datetime, timedelta
import json
from functools import lru_cache
from threading import Lock
import sqlite3
import os
from pathlib import Path
import time

from .AssetMarkets import inside, instrument, OPTION, FUTURE
from .OptionBars import CN, PERIODS, finite, remote_bars, public_quote, valid_bar
from .OptionWatch import catalog, provider_future, history


def database(root, exchange, raw):
    folder=inside(root,"history","watch",exchange)
    folder.mkdir(parents=True,exist_ok=True)
    db=sqlite3.connect(inside(folder,raw+".sqlite3"),timeout=8)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS observations (
            series TEXT, stamp TEXT, payload TEXT NOT NULL, PRIMARY KEY(series,stamp)
        ) WITHOUT ROWID;
        CREATE TABLE IF NOT EXISTS requests (
            series TEXT, until_day TEXT, checked REAL, message TEXT, PRIMARY KEY(series,until_day)
        ) WITHOUT ROWID;
    """)
    return db


def put(db,series,rows,key="day"):
    with db:
        db.executemany("INSERT INTO observations VALUES(?,?,?) ON CONFLICT(series,stamp) DO UPDATE "
                       "SET payload=excluded.payload WHERE payload<>excluded.payload",
                       ((series,r[key],json.dumps(r,ensure_ascii=False,allow_nan=False)) for r in rows))


def read(db,series,end,n):
    return [json.loads(r[0]) for r in reversed(db.execute(
        "SELECT payload FROM observations WHERE series=? AND stamp<=? ORDER BY stamp DESC LIMIT ?",
        (series,end+"~",n)).fetchall())]


def save_depth(root,exchange,raw,event):
    try:
        stamp=datetime.strptime(event["time"],"%Y%m%d %H:%M:%S.%f").isoformat(sep=" ",timespec="milliseconds")
    except ValueError:return
    with closing(database(root,exchange,raw)) as db:
        put(db,"depth",[dict(event,time=stamp)],"time")


def refresh(db,series,end,ttl,fetch,key="day"):
    check=db.execute("SELECT checked,message FROM requests WHERE series=? AND until_day=?",(series,end)).fetchone()
    if check and time.time()-check[0]<(600 if check[1] else ttl):return check[1]
    try:
        rows=fetch()
        put(db,series,rows,key)
        msg="" if rows else "行情源在此窗口未返回记录"
    except (ValueError,KeyError,OSError,TypeError) as exc:
        msg=str(exc) if type(exc) is ValueError else "公开行情接口暂时不可用"
    with db:
        db.execute("INSERT OR REPLACE INTO requests VALUES(?,?,?,?)",(series,end,time.time(),msg))
    return msg


def future_store(raw):
    path=os.getenv("WT_OPTION_UNDERLYING_STORAGE")
    return Path(path) if path and FUTURE.fullmatch(raw) and Path(path).is_absolute() else None


def native_paths(root,exchange,raw,period):
    folder={"d":"day","m1":"min1","m5":"min5"}[period]
    profiles=[*sorted(inside(root,"history","native").glob("*/storage")),inside(root,"storage")]
    if (reference:=future_store(raw)) is not None:profiles.insert(0,reference)
    return [str(f) for store in profiles for f in
            (store/"his"/folder/exchange/(raw+".dsb"),store/"rt"/folder/exchange/(raw+".dmb"))
            if f.is_file() and f.resolve()==f]


@lru_cache(maxsize=12)
def read_native(action,files,period,end):
    from .HistoryCatalog import native_worker
    return native_worker(dict(action=action,files=[f for f,_ in files],period=period,end=int(end.replace("-","")+"2359")))


def tick_files(store,ex,raw,end):
    from .MarketHistory import open_day
    next_day=date.fromisoformat(end)+timedelta(days=1)
    while open_day(next_day) is False:next_day+=timedelta(days=1)
    ceiling=next_day.strftime("%Y%m%d")
    days=sorted(f for f in (store/"his/ticks"/ex).glob("*/"+raw+".dsb")
                if f.parent.name<=ceiling and f.resolve()==f)
    rt=inside(store,"rt","ticks",ex,raw+".dmb")
    return [*days[-8:],*([rt] if rt.is_file() else [])]


def fingerprints(files):
    return tuple((str(f),Path(f).stat().st_mtime_ns) for f in files)


def native_rows(root,exchange,raw,period,end):
    files=native_paths(root,exchange,raw,period)
    return read_native("option_watch_bars",fingerprints(files),period,end)["bars"] if files else []


def decode_native(q,helper):
    from .HistoryDataWorker import dataset_records
    bars,stamp=dataset_records(q,helper)
    result=[]
    for b in bars[-800:]:
        if not valid_bar(b.open,b.high,b.low,b.close,b.vol):continue
        t=datetime.strptime(str(stamp(b)),"%Y%m%d%H%M")
        result.append(dict(day=t.strftime("%Y-%m-%d" if q["period"]=="d" else "%Y-%m-%d %H:%M"),
                           trading_day=str(b.date),open=b.open,high=b.high,low=b.low,close=b.close,volume=b.vol,
                           amount=finite(b.money),oi=finite(b.hold),source="WT 本地"))
    return dict(bars=result)


def decode_ticks(q,helper):
    from .HistoryDataWorker import dataset_records
    settled={Path(f).parent.name for f in q["files"] if f.endswith(".dsb")}
    def events():
        for f in q["files"]:
            items,_=dataset_records(dict(q,period="tick",files=[f]),helper)
            for tick in items:
                if not f.endswith(".dmb") or str(tick.trading_date) not in settled:yield tick
    bars={}; snapshots={}
    for t in events():
        stamp=datetime.strptime(f"{t.action_date:08}{t.action_time:09}"[:14],"%Y%m%d%H%M%S")
        key=stamp.strftime("%Y-%m-%d %H:%M")
        snapshots[key]=dict(time=stamp.isoformat(sep=" ",timespec="seconds")+f".{t.action_time%1000:03}",
            trading_day=str(t.trading_date),source="WT 采集 Tick",last=finite(t.price,price=True),oi=finite(t.open_interest),
            bid=finite(t.bid_price_0,price=True),ask=finite(t.ask_price_0,price=True),bid_size=finite(t.bid_qty_0),
            ask_size=finite(t.ask_qty_0),volume=finite(t.total_volume),amount=finite(t.total_turnover))
        if not finite(t.price,price=True) or not finite(t.volume,price=True):continue
        if q["period"]=="d":label=datetime.strptime(str(t.trading_date),"%Y%m%d").date().isoformat()
        else:
            step=1 if q["period"]=="m1" else 5
            offset=(step-stamp.minute%step)%step
            if offset==0 and (stamp.second or t.action_time%1000):offset=step
            label=(stamp+timedelta(minutes=offset)).strftime("%Y-%m-%d %H:%M")
        b=bars.setdefault(label,dict(day=label,open=t.price,high=t.price,low=t.price,close=t.price,volume=0.,
                         amount=0.,trading_day=str(t.trading_date),source="WT Tick 合成"))
        b.update(high=max(b["high"],t.price),low=min(b["low"],t.price),close=t.price,oi=finite(t.open_interest))
        b["volume"]+=t.volume;b["amount"]+=finite(t.turn_over) or 0.
    return dict(bars=list(bars.values())[-320:],quotes=list(snapshots.values())[-400:])


def covered(root,exchange,raw,period,end,rows):
    from .MarketHistory import open_day,expected_stamps
    from .MarketSubscriptions import _load
    if not rows:return False
    end_day=date.fromisoformat(end)
    cutoff=datetime.now(CN).strftime("%Y%m%d%H%M")
    clock={r["day"].replace("-","").replace(":","").replace(" ","") for r in rows}
    if period=="d":session={}
    else:
        try:
            item=_load(inside(root,"common","contracts.json"))[exchange][raw]
            prod=_load(inside(root,"common","commodities.json"))[exchange][item["product"]]
            session=_load(inside(root,"common","sessions.json"))[prod["session"]]
        except (OSError,KeyError):return False
    expected=set()
    # Include the next trading day's preceding night session.
    for i in range(-230 if period=="d" else -9,8):
        d=end_day+timedelta(days=i)
        if open_day(d) is None:return False
        expected.update(str(n) for n in expected_stamps(d,session,period)
                        if str(n)<=min(cutoff,end.replace("-","")+"2359") and str(n)[:8]<=end.replace("-",""))
    window=sorted(expected)[-140:]
    return bool(window) and all(t in clock for t in window)


def refresh_interval(end,seconds):
    now=datetime.now(CN)
    if end<now.date().isoformat():return 86400
    if now.weekday()==6 or (now.weekday()==5 and now.hour>=3):
        monday=(now+timedelta(days=7-now.weekday())).replace(hour=8,minute=45,second=0,microsecond=0)
        return max(seconds,(monday-now).total_seconds())
    return seconds


def five_minute(rows):
    groups={}
    for b in rows:
        stamp=datetime.fromisoformat(b["day"])
        end=stamp+timedelta(minutes=(-stamp.minute)%5)
        groups.setdefault(end,[]).append(b)
    for end,group in sorted(groups.items()):
        group.sort(key=lambda b:b["day"])
        if len(group)!=5:continue
        amount=sum(b["amount"] for b in group) if all(b.get("amount") is not None for b in group) else None
        yield dict(day=end.strftime("%Y-%m-%d %H:%M"),open=group[0]["open"],high=max(b["high"] for b in group),
                   low=min(b["low"] for b in group),close=group[-1]["close"],volume=sum(b["volume"] for b in group),
                   oi=group[-1].get("oi"),amount=amount,source=" + ".join(sorted({b["source"] for b in group}))+" · 1分合成")


def bars_for(root,db,exchange,raw,provider,period,end,option=True):
    prefix="" if option else "underlying:"
    series=prefix+period
    local=native_rows(root,exchange,raw,period,end)
    if period=="m5":
        minutes={r["day"]:r for r in read(db,prefix+"m1",end,800)}
        minutes.update((r["day"],r) for r in native_rows(root,exchange,raw,"m1",end))
        aggregated={r["day"]:r for r in five_minute(minutes.values())}
        aggregated.update((r["day"],r) for r in local)
        local=list(aggregated.values())
    stores=[inside(root,"storage")]
    if not option and not local and (reference:=future_store(raw)) is not None:stores.insert(0,reference)
    files=[f for store in stores for f in tick_files(store,exchange,raw,end)] if option or not local else []
    if files:
        ticks=read_native("option_watch_ticks",fingerprints(files),period,end)
        merged={b["day"]:b for b in ticks["bars"] if b["day"][:10]<=end}
        merged.update((b["day"],b) for b in local)
        local=list(merged.values())
        if option:put(db,"depth",ticks["quotes"],"time")
    if not covered(root,exchange,raw,period,end,local):
        def pull():
            try:
                bars=remote_bars(exchange,raw,period,end,option)
                if bars or period!="d" or not option:return bars
            except (OSError,ValueError,TypeError,KeyError):
                if period!="d" or not option:raise
            return [dict(r,source="新浪财经") for r in history(provider,int(time.time()//300)) if r["day"]<=end]
        ttl=refresh_interval(end,300 if period=="d" else 60)
        note=refresh(db,series,end,ttl,pull)
    else:note=""
    # Keep the WT bars in their native store; they always win overlapping clocks.
    merged={b["day"]:b for b in read(db,series,end,320)}
    for b in local:
        if b["source"].endswith("1分合成") and "WT" not in b["source"]:merged.setdefault(b["day"],b)
        else:merged[b["day"]]=b
    return [dict(merged[t]) for t in sorted(merged)[-320:]],note


def metadata(root,exchange,raw):
    symbol=exchange+"."+raw
    meta=dict(catalog(root).get(symbol,{}))
    folders=[inside(root,"common"),*(p.parent for p in inside(root,"history","native").glob("*/common/contracts.json"))]
    from .MarketSubscriptions import _load
    for common in folders:
        path=common/"contracts.json"
        if not path.is_file():continue
        item=_load(path).get(exchange,{}).get(raw)
        if not item:continue
        rules=common/"commodities.json"
        prod=_load(rules).get(exchange,{}).get(item.get("product"),{}) if rules.exists() else {}
        meta.update({k:v for k,v in dict(expiry=item.get("expiredate"),name=item.get("name"),
                     multiplier=prod.get("volscale"),tick=prod.get("pricetick")).items() if v})
        break
    m=OPTION.fullmatch(raw)
    return dict(meta,symbol=symbol,exchange=exchange,side="看涨" if m[4]=="C" else "看跌",
                underlying=exchange+"."+m[1]+m[2],strike=float(m[5]))


def valuations(meta,bars,under):
    from .OptionModels import contract_metrics,qdate
    from statistics import stdev
    from math import log,sqrt
    refs={r["day"]:r for r in under}
    closes=[r["close"] for r in under]
    hv={r["day"]:stdev(log(closes[j]/closes[j-1]) for j in range(i-19,i+1))*sqrt(252)
        for i,r in enumerate(under) if i>=20 and len(r["day"])==10}
    expiry=meta.get("expiry")
    result=[]
    for b in bars:
        stamp=b["day"];ref=refs.get(stamp)
        f=ref["close"] if ref else None
        if not f:continue
        v=dict(day=stamp,forward=f,premium=b["close"],rate=.02,model="美式 BAW",hv20=hv.get(stamp),
               option_source=b["source"],underlying_source=ref["source"])
        v["intrinsic"]=max(0,(f-meta["strike"])*(1 if meta["side"]=="看涨" else -1))
        v["time_value"]=b["close"]-v["intrinsic"]
        if expiry:
            day=date.fromisoformat(stamp[:10]);days=(datetime.strptime(str(expiry),"%Y%m%d").date()-day).days
            v["days"]=days
            try:v.update(contract_metrics(f,meta["strike"],b["close"],days,.02,meta["side"]=="看涨",
                                         "american",qdate(day.strftime("%Y%m%d"))))
            except (RuntimeError,ValueError):v["reason"]="已到期或价格无法反解隐含波动率"
        result.append(v)
    return result


_model_lock=Lock()


def view(root,symbol,period,end=None):
    ex,raw=instrument("option",symbol)
    if period not in PERIODS:raise ValueError("请选择日线、1 分钟或 5 分钟")
    today=datetime.now(CN).date()
    end=date.fromisoformat(end) if end else today
    if end>today:raise ValueError("不能查询未来日期")
    current=end==today
    with closing(database(root,ex,raw)) as db:
        latest=metadata(root,ex,raw)
        saved=read(db,"metadata",today.isoformat(),1)
        terms={**(saved[0] if saved else {}),**{k:v for k,v in latest.items() if v is not None}}
        put(db,"metadata",[dict(terms,observed=today.isoformat())],"observed")
        prior=read(db,"metadata",end.isoformat(),1)
        meta=prior[0] if prior else dict(terms,observed=today.isoformat())
        u=meta["underlying"].split(".")[1]
        month=provider_future(ex,u,meta.get("expiry"))
        m=OPTION.fullmatch(raw)
        provider=month+m[4]+m[5]
        if meta.get("expiry"):end=min(end,datetime.strptime(str(meta["expiry"]),"%Y%m%d").date())
        end=end.isoformat()
        bars,note=bars_for(root,db,ex,raw,provider,period,end)
        under=[]
        if bars:
            under,_=bars_for(root,db,ex,u,month,period,end,False)
        with _model_lock:
            val=valuations(meta,bars[-140:],under)
        put(db,"valuation:"+period,val)
        local_depth=read(db,"depth",end,1)
        depth=local_depth[0] if local_depth else None
        cache_fresh=depth and (datetime.now(CN)-datetime.fromisoformat(depth["time"]).replace(tzinfo=CN)).total_seconds()<60
        if not current and not db.execute(
                "SELECT 1 FROM observations WHERE series='dayquote' AND stamp=?",(end,)).fetchone():
            refresh(db,"dayquote",end,86400,lambda:[q] if (q:=public_quote(ex,raw,end)) else [],"trading_day")
        if current and end==today.isoformat() and not cache_fresh:
            refresh(db,"quote",end,refresh_interval(end,60),lambda: [q] if (q:=public_quote(ex,raw)) else [],"observed")
        quotes=read(db,"quote" if current else "dayquote",end,1)
        if not current and quotes and quotes[0].get("trading_day")!=end:quotes=[]
        if quotes and quotes[0].get("time"):
            q=quotes[0]
            put(db,"depth",[dict(q,time=datetime.fromisoformat(q["time"]).astimezone(CN).replace(tzinfo=None).isoformat(sep=" "))],"time")
        for b in bars[-140:]:
            # Only expose a quote from this candle; no carry across breaks or days.
            clock=datetime.fromisoformat(b["day"])
            if period=="d":clock=clock.replace(hour=15)
            lower=clock-timedelta(minutes=5 if period=="m5" else 1)
            hit=db.execute("SELECT payload FROM observations WHERE series='depth' AND stamp>? AND stamp<=? "
                           "ORDER BY stamp DESC LIMIT 1",(lower.isoformat(sep=" "),clock.isoformat(sep=" ")+".999")).fetchone()
            if hit:b["depth"]=json.loads(hit[0])
        return dict(bars=bars[-140:],valuations=val,meta=meta,quote=depth if depth and (cache_fresh or not quotes or not quotes[0].get("bid")) else (quotes[0] if quotes else None),note=note,
                    sources=sorted({b["source"] for b in bars[-140:]}))

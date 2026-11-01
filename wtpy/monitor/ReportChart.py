"""Read candles and fills from the selected account's saved replay only."""
import csv
import json
import math
import re
from subprocess import TimeoutExpired
from pathlib import Path
from threading import BoundedSemaphore

from fastapi import Body, Request
from .AssetMarkets import asset_root, inside, instrument
from .HistoryCatalog import native_worker
from .ResearchServer import monitor_json, upstream_headers


def read(path, default=None):
    return json.loads(path.read_bytes()) if path.is_file() else default


def field(value):
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", value or ""):
        raise ValueError("任务或账户标识无效")
    return value


def fill(code, stamp, side, offset, price, qty):
    action = {("long", "open"): "开多", ("long", "close"): "平多",
              ("short", "open"): "开空", ("short", "close"): "平空"}.get((side.lower(), offset.lower()))
    return dict(code=code, time=str(stamp), action=action or "未识别", price=float(price), qty=float(qty))


def native(folder, task):
    out = inside(folder, "outputs_bt", task)
    env = read(out/"btenv.json", {})
    meta = read(folder/"market-data.json", {})
    codes = [c for c in dict.fromkeys([meta.get("code") or env.get("code"), *(leg["code"] for leg in meta.get("option_legs", meta.get("legs", [])))]) if c]
    fills = []
    if (out/"trades.csv").is_file():
        with (out/"trades.csv").open(encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                fills.append(fill(r["code"], r["time"], r["direct"], r["action"], r["price"], r["qty"]))
    return folder, codes, fills, env.get("stime") or meta.get("start"), env.get("etime") or meta.get("end"), meta.get("period") or env.get("period")


def ticker(code, asset):
    ex, _, rest = code.partition(".")
    option=re.fullmatch(r"([A-Za-z]+)(\d{4})\.([CP])\.(\d+)",rest)
    if asset == "option" and option:
        product, month, side, strike = option.groups()
        return ex, [product + (month[1:] if ex=="CZCE" else month) + sep + side + sep + strike for sep in ("-", "")]
    if asset in ("futures","option"):
        try:
            _, raw = instrument("futures",code)
            return ex, [raw, rest.replace(".","")]
        except ValueError:
            pass
    return ex, [rest.split(".")[-1]]


def plus(folder, task, owner):
    meta = read(folder/"task.json", {})
    if meta.get("user") != owner or meta.get("id") != task:
        raise ValueError("本账户没有该任务")
    snap = meta.get("market_snapshot") or {}
    codes = list(snap.get("codes") or [meta["code"]])
    trades = read(folder/"report.json", {}).get("trades", [])
    aliases = {code:code for code in codes}
    for code in codes:
        for raw in ticker(code, "futures")[1]:
            if raw in aliases and aliases[raw]!=code:
                aliases[raw]=None
            else:
                aliases[raw]=code
    fills = [fill(aliases.get(r["code"]) or r["code"], f"{int(r['date']):08d}{int(r['time']):09d}",
                  r["direction"], r["offset"], r["price"], r["volume"]) for r in trades]
    return folder, codes, fills, meta.get("start"), meta.get("end"), meta.get("period")


def expiry(root, owner, task, code):
    rec = read(inside(root, "backtests", "expiry-runs", owner, task+".json"))
    if not rec:
        raise ValueError("本账户没有该任务")
    sample = read(inside(root, "history", "expiry",field(rec["plan"]["sample"])+".json"), {})
    codes = [rec["old"],rec["new"]]
    code = code or codes[0]
    if code not in codes:
        raise ValueError("合约不属于本报告")
    by_day = sample.get("contracts",{}).get(code,{}).get("bars",{})
    days={str(row["day"]) for row in rec["daily"]}
    bars=[dict(time=d,**v) for d,v in sorted(by_day.items()) if d in days]
    pos=0; fills=[]
    for r in rec["events"]:
        if r["code"]!=code:continue
        delta=r["qty"]; kind=r["action"]
        if kind in ("开仓","移仓平旧","移仓开新","原生成交"):
            closing=pos*delta<0
            action=("平多" if pos>0 else "平空") if closing else ("开多" if delta>0 else "开空")
            fills.append(dict(time=str(r["day"]),code=code,action=action,price=r["price"],qty=abs(delta)))
        pos+=delta
    return dict(codes=codes,code=code,period="d",bars=bars,fills=fills,days=[],day="",total=len(fills),
                source="跨期样本的合约日线。行权、指派和期货盯市列在清算流水。")


def registered(app, deploy, authorize):
    slots=BoundedSemaphore(1)
    @app.post("/mgr/assets/{asset}/report-chart")
    def chart(request:Request, asset:str, task:str=Body(...), mode:str=Body("native"), strategy:str=Body(""),
              code:str=Body(""), day:str=Body(""), diagnostics:bool=Body(False)):
        user,error=authorize(request,asset,research=True)
        if error:return error
        try:
            field(task);owner=field(user["loginid"])
            root=asset_root(deploy,asset)
            if mode=="expiry" and asset=="option":
                if diagnostics:
                    rec=read(inside(root,"backtests","expiry-runs",owner,task+".json"))
                    if not rec:raise ValueError("本账户没有该清算任务")
                    sample=read(inside(root,"history","expiry",field(rec["plan"]["sample"])+".json"))
                    with slots:return dict(result=0,**native_worker(dict(action="expiry_diagnostics",rec=rec,sample=sample)))
                return dict(result=0,**expiry(root,owner,task,code))
            sources=Path("/report-sources")
            if mode=="main" and asset in ("option","futures"):
                folder=inside(root,"main-runs",owner,task)
                if not (folder/"task.json").exists():raise ValueError("本账户没有此任务")
                if diagnostics:
                    with slots:return dict(result=0,**native_worker(dict(action="run_diagnostics",folder=str(folder),task=task,asset=asset)))
                args=native(folder,task)
            elif mode=="plus" and asset=="futures":
                folder=inside(sources,"plus",task)
                if diagnostics:
                    from .RunDiagnostics import plus_details
                    record=read(folder/"task.json",{})
                    if record.get("user")!=owner or record.get("id")!=task:raise ValueError("本账户没有该任务")
                    with slots:return dict(result=0,**plus_details(folder))
                args=plus(folder,task,owner)
            elif mode in ("native","research"):
                if mode=="research":
                    if asset!="futures":raise ValueError("研究实验不属于此资产")
                    d=monitor_json("http://127.0.0.1:8102","/mgr/research/experiment_detail",{"experiment_id":task},upstream_headers(request))
                    exp=d.get("experiment",{})
                    if d.get("result")!=0 or exp.get("owner")!=owner:raise ValueError("本账户没有该研究实验")
                    strategy,task=field(exp.get("strategy_id")),field(exp.get("native_id"))
                    base=sources/"experiments"
                else:
                    field(strategy)
                    base=sources/"native" if asset=="futures" else root/"backtests"
                    marker=read(inside(base,owner,"marker.json"),{})
                    if task not in marker.get("backtests",{}) or strategy not in marker.get("strategies",{}):
                        raise ValueError("本账户没有该回测记录")
                folder=inside(base,owner,strategy,"backtests",task)
                if not folder.is_dir():raise ValueError("本账户没有此任务的行情目录")
                if diagnostics:
                    with slots:
                        return dict(result=0,**native_worker(dict(action="run_diagnostics",folder=str(folder),task=task,asset=asset)))
                args=native(folder,task)
            else:raise ValueError("报告类型与资产不匹配")
            folder,codes,fills,start,end,period=args
            codes=list(dict.fromkeys(codes+[r["code"] for r in fills]))
            code=code or next((r["code"] for r in fills),next(iter(codes),""))
            if code and code not in codes:raise ValueError("合约不属于本报告")
            fills=[r for r in fills if r["code"]==code]
            if day and not re.fullmatch(r"\d{8}",day):raise ValueError("日期无效")
            with slots:
                view=native_worker(dict(action="report_candles",folder=str(folder),code=code,asset=asset,
                                        start=start,end=end,period=period,mode=mode,day=day,fills=fills))
            return dict(result=0,codes=codes,code=code,total=len(fills),**view)
        except (OSError,ValueError,KeyError,TypeError,TimeoutExpired) as e:
            return {"result":-2,"message":str(e) if isinstance(e,ValueError) and "/" not in str(e) else "无法读取此任务的行情或成交"}


def tick_candles(helper, root, exchange, names, lower, upper):
    """Group the same action clock as the replay, including pre-open ticks."""
    import numpy as np
    bars={}
    if len(lower)!=12 or len(upper)!=12:return bars
    for name in names:
        for path in sorted((root/exchange).glob("*/"+name+".dsb")):
            if not path.is_file():continue
            data=helper.read_dsb_ticks(str(path))
            if data is None:continue
            raw=data.ndarray
            times=raw["action_date"].astype("int64")*10000 + raw["action_time"]//100000
            prices=raw["price"]
            valid=(int(lower)<=times)&(times<=int(upper))&np.isfinite(prices)&(prices>0)&(prices<1e15)
            times,prices=times[valid],prices[valid]
            if not len(times):continue
            starts=np.r_[0,np.flatnonzero(times[1:]!=times[:-1])+1]
            ends=np.r_[starts[1:],len(times)]-1
            for t,o,h,l,c in zip(times[starts],prices[starts],np.maximum.reduceat(prices,starts),
                                   np.minimum.reduceat(prices,starts),prices[ends]):
                key=str(t)
                if key not in bars:bars[key]=dict(time=key,open=float(o),high=float(h),low=float(l),close=float(c))
                else:bars[key].update(high=max(bars[key]["high"],float(h)),low=min(bars[key]["low"],float(l)),close=float(c))
        if bars:break
    return bars


def candles(q, helper):
    from .HistoryDataWorker import read_bars, stamp
    folder=Path(q["folder"]);code=q["code"];fills=q["fills"]
    ex,raw=ticker(code,q["asset"])
    # Never follow paths supplied by the browser or absolute snapshot metadata.
    if any(not re.fullmatch(r"[A-Za-z0-9_-]+",s) for s in (ex,*raw)):
        return dict(bars=[],fills=fills,source="任务未记录可识别的合约",period="",days=[],day="")
    lower,upper=str(q["start"] or ""),str(q["end"] or "")
    def within(t):
        width=min(len(t),len(lower),len(upper))
        return width>=8 and lower[:width]<=t[:width]<=upper[:width]
    def prices(o,h,l,c):
        return all(math.isfinite(p) and 0<p<1e15 for p in (o,h,l,c)) and l<=min(o,c)<=max(o,c)<=h
    roots=[folder/"market-data/his"]
    if q["mode"]=="plus":roots.append(folder/"storage/his")
    periods=list(dict.fromkeys([q["period"],"m1","m5"]))
    bars=tick_candles(helper,roots[0]/"ticks",ex,raw,lower,upper) if q["mode"]=="plus" or q["period"]=="tick" else {}
    tick_clock=bool(bars);period="m1" if tick_clock else "";source=""
    directories={"m1":"min1","m5":"min5","d":"day"}
    for root in roots:
        if bars:break
        for period in [p for p in periods if p in directories]:
            for name in raw:
                f=inside(root,directories[period],ex,name+".dsb")
                if not f.is_file():continue
                for row in read_bars(helper,f,True):
                    t=str(stamp(row,period))
                    if within(t) and prices(row.open,row.high,row.low,row.close):
                        bars[t]=dict(time=t,open=row.open,high=row.high,low=row.low,close=row.close)
                if bars:break
            if bars:break
        if bars:break
    if bars:source="回放 Tick 的分钟线，横轴为分钟开始时间" if tick_clock else "任务保存的 WT 行情，横轴为 K 线结束时间"
    if not bars:source="此任务没有可用的同期 OHLC 行情；不会用成交价补画 K 线。"
    days=sorted({t[:8] for t in bars}|{r["time"][:8] for r in fills}) if period!="d" else []
    day=(q["day"] or (fills[0]["time"][:8] if fills else next(iter(days),""))) if days else ""
    sliced=[bars[t] for t in sorted(bars) if not day or t.startswith(day)]
    visible=[r for r in fills if not day or r["time"].startswith(day)]
    return dict(bars=sliced,fills=visible,days=days,day=day,period=period,source=source,
                tick_clock=tick_clock,bar_clock=q["mode"]!="plus" and q["period"] in directories)

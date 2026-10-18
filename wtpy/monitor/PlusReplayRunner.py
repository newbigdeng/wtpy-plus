"""Plus historical child: WT DataWriter -> live engine -> native trading path."""
from __future__ import annotations
import ctypes
import csv
import datetime as dt
import importlib.util
import inspect
import json
import math
import os
from pathlib import Path
import signal
import sys
import threading
import time
import traceback
from .PlusBacktest import read_json, write_json, dataset_catalog, LIMITATIONS
STOP = threading.Event()
def on_stop(signum, frame): STOP.set()
signal.signal(signal.SIGTERM, on_stop)
signal.signal(signal.SIGINT, on_stop)
def load_ticks(item):
    from wtpy.WtCoreDefs import WTSTickStruct
    if item["kind"] == "synthetic":
        ticks=[]
        for index in range(360):
            tick=WTSTickStruct();tick.exchg=b"SHFE";tick.code=b"rb2701"
            when=dt.datetime(2026,10,12,9)+dt.timedelta(seconds=index)
            tick.action_date=tick.trading_date=20261012
            tick.action_time=int(when.strftime("%H%M%S"))*1000
            tick.price=3300+(index//60)%3
            tick.open=3300;tick.high=max(3300,tick.price);tick.low=3300
            tick.total_volume=index+1;tick.volume=1
            tick.total_turnover=(index+1)*tick.price*10;tick.turn_over=tick.price*10
            tick.open_interest=10000
            tick.bid_price_0=tick.price-1;tick.ask_price_0=tick.price+1
            tick.bid_qty_0=tick.ask_qty_0=2
            tick.upper_limit=4000;tick.lower_limit=2500
            tick.pre_close=tick.pre_settle=3300
            ticks.append(tick)
        return ticks
    from wtpy.wrapper.WtDtHelper import WtDataHelper
    data=WtDataHelper().read_dsb_ticks(item["file"])
    if data is None: raise ValueError("无法读取原生Tick数据")
    records=data.ndarray
    # The installed helper exposes exactly the native Tick struct dtype.
    return [WTSTickStruct.from_buffer_copy(row.tobytes()) for row in records]
class CheckedStrategy:
    def __init__(self, strategy):
        self.strategy=strategy;self.errors=[];self.counts={}
    def name(self):return self.strategy.name()
    def __getattr__(self,name):
        original=getattr(self.strategy,name)
        if not callable(original) or not name.startswith("on_"): return original
        def callback(*args, **kwargs):
            self.counts[name]=self.counts.get(name,0)+1
            try:return original(*args,**kwargs)
            except BaseException as error:
                self.errors.append(name+": "+repr(error));STOP.set()
                return None
        return callback
def build_config(task,item,first,folder):
    base=Path(item["basefiles"])
    basefiles={key:str(base/file) for key,file in (
        ("commodity","commodities.json"),("contract","contracts.json"),
        ("session","sessions.json"),("holiday","holidays.json"),("hot","hots.json"))}
    basefiles["utf-8"] = True
    finance = task["finance"]
    fee = finance["fees"]
    write_json(folder/"fees.json", {fee["key"]: {key: fee[key] for key in ("byvolume", "open", "close", "closetoday", "minfee")}})
    # Freeze effective margin ratios even if server metadata changes later.
    contracts = read_json(base/"contracts.json", {})
    exchange, product, contract = task["code"].split(".")
    contract_info = contracts[exchange][product+contract]
    contract_info["longmarginratio"] = finance["margin"]["long"]
    contract_info["shortmarginratio"] = finance["margin"]["short"]
    write_json(folder/"contracts.json", contracts)
    basefiles["contract"] = str(folder/"contracts.json")
    commodities = read_json(base/"commodities.json", {})
    if product in commodities.get(exchange, {}):
        # WT's commodity override otherwise supersedes the frozen contract rates.
        commodities[exchange][product]["marginrate"] = 0
        write_json(folder/"commodities.json", commodities)
        basefiles["commodity"] = str(folder/"commodities.json")
    empty={"strategy_filters":{},"code_filters":{},"executer_filters":{}}
    write_json(folder/"filters.json",empty)
    write_json(folder/"actpolicy.json",{"default":{"order":[{"action":"close","limit":0},{"action":"open","limit":0}]}})
    clock={"date":int(first.action_date),"time":int(first.action_time),"tradingdate":int(first.trading_date)}
    config={"basefiles":basefiles,
            "env":{"name":task["engine_type"].lower(),"fees":str(folder/"fees.json"),
                   "filters":str(folder/"filters.json"),"product":{"session":"TRADING"},
                   "poolsize":0,"replay":clock},
            "data":{"store":{"module":"WtDataStorage","path":str(folder/"storage")+"/","replay":True}},
            "parsers":[],"traders":[{"active":True,"id":"plus","module":"TraderMocker",
                        "mockerid":1,"replay":True,"replay_date":clock["date"],"replay_time":clock["time"],
                        "replay_tradingdate":clock["tradingdate"],"span":0,"newpx":False,
                        "minqty":1,"maxqty":100,"udp_port":0,"user":"plus","pass":"",
                        "savedata":True,"ignore_selfmatch":False,
                        "capital":task["capital"],"fees":str(folder/"fees.json"),
                        "slippage_ticks":finance["slippage_ticks"]}],
            "executers":[],"bspolicy":str(folder/"actpolicy.json"),"notifier":{"active":False}}
    if finance.get("marginrate") is not None:
        config["traders"][0]["marginrate"] = finance["marginrate"]
    if task["engine_type"]=="CTA":
        config["executers"]=[{"active":True,"id":"plus-local","name":"local","trader":"plus",
                              "scale":1,"poolsize":0,"strict_sync":False,
                              "policy":{"default":{"name":task["execution"]["native"], **task["execution"]["params"]}}}]
    write_json(folder/"config.json",config)
    log={"root":{"level":"info","async":False,"sinks":[{"type":"daily_file_sink","filename":str(folder/"native.log"),"pattern":"%v","truncate":True}]}}
    write_json(folder/"logcfg.json",log)
    return basefiles,log
def run(folder,workspace):
    from wtpy import WtEngine, EngineType, BaseCtaStrategy, BaseHftStrategy
    from wtpy.WtDtEngine import WtDtEngine
    from wtpy.ExtModuleDefs import BaseExtParser
    from wtpy.WtCoreDefs import WTSTickStruct
    folder=Path(folder).resolve();os.chdir(folder)
    task=read_json(folder/"task.json");item=next(x for x in dataset_catalog(workspace) if x["id"]==task["dataset"])
    stream=[x for x in load_ticks(item) if task["start"] <= int(x.action_date)*10000+int(x.action_time)//100000 <=task["end"]]
    if not stream:raise ValueError("起止范围内没有Tick；不创建空结果")
    previous=None
    for tick in stream:
        timestamp=(int(tick.action_date),int(tick.action_time))
        if previous and timestamp<previous:raise ValueError("历史Tick时间倒退")
        if not math.isfinite(tick.price) or tick.price<=0:raise ValueError("历史Tick价格无效")
        exchange, product, contract = task["code"].split(".")
        if bytes(tick.exchg).decode() != exchange or bytes(tick.code).decode() != product+contract:
            raise ValueError("历史Tick合约与数据集登记不一致")
        previous=timestamp
    basefiles,log=build_config(task,item,stream[0],folder)
    task.update(status="running",total=len(stream),updated_at=time.time());write_json(folder/"task.json",task)
    spec=importlib.util.spec_from_file_location("PlusTaskStrategy",folder/"MyStrategy.py")
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    base=BaseCtaStrategy if task["engine_type"]=="CTA" else BaseHftStrategy
    classes=[x for x in vars(module).values() if inspect.isclass(x) and x.__module__==module.__name__ and issubclass(x,base) and x is not base]
    if len(classes)!=1:raise ValueError("源码必须只定义一个与任务类型对应的策略类")
    supplied={"name":task["straid"],"code":task["code"],"period":task["period"],"capital":task["capital"],**task["params"]}
    signature=inspect.signature(classes[0]);params=signature.parameters
    accepts_kwargs=any(x.kind==inspect.Parameter.VAR_KEYWORD for x in params.values())
    extras=set(task["params"])-set(params)
    if extras and not accepts_kwargs:raise ValueError("策略构造函数不支持参数："+",".join(sorted(extras)))
    strategy=CheckedStrategy(classes[0](**{k:v for k,v in supplied.items() if k in params or accepts_kwargs}))
    engine=WtEngine(EngineType.ET_CTA if task["engine_type"]=="CTA" else EngineType.ET_HFT,
                    "logcfg.json",str(folder/"generated"))
    data=None;began=time.monotonic()
    try:
        engine.init("",str(folder/"config.json"))
        engine.commitConfig()
        product = engine.getProductInfo(".".join(task["code"].split(".")[:2]))
        task["volume_scale"] = product.volscale
        engine.add_exetended_parser(BaseExtParser("plus-history"))
        if task["engine_type"]=="CTA":engine.add_cta_strategy(strategy)
        else:engine.add_hft_strategy(strategy,"plus",agent=True)
        data=WtDtEngine()
        data.init_with_config({"basefiles":{**basefiles,"utf-8":True},"allday":True,"parsers":[],
            "writer":{"module":"WtDataStorage","async":False,"groupsize":1000000,
                      "path":str(folder/"storage"),"savelog":False,"disablehis":True,
                      "disabletick":False,"disablemin1":False,"disablemin5":False,"disableday":False}},{})
        data.add_exetended_parser(BaseExtParser("plus-writer"));data.run(True)
        engine.run(True)
        api=engine.__wrapper__.api
        api.plus_replay_flush.argtypes=[ctypes.c_uint32,ctypes.c_uint32];api.plus_replay_flush.restype=ctypes.c_bool
        mock=ctypes.CDLL(str(Path(os.environ["WT_PLUS_NATIVE_DIR"])/"traders/libTraderMocker.so"))
        mock.plus_mocker_step.argtypes=[ctypes.c_uint32,ctypes.POINTER(WTSTickStruct)];mock.plus_mocker_step.restype=ctypes.c_int
        mock.plus_mocker_barrier.argtypes=[ctypes.c_uint32];mock.plus_mocker_barrier.restype=ctypes.c_bool
        for _ in range(8):
            if not mock.plus_mocker_barrier(1):raise RuntimeError("模拟柜台API线程未就绪")
        # Readiness is an actual adapter log/callback observation, never an arbitrary sleep.
        deadline=time.monotonic()+5
        while True:
            if task["engine_type"]=="HFT":
                ready=strategy.counts.get("on_channel_ready",0)>0
            else:
                ready=any("Trading channel ready" in p.read_text(errors="replace") for p in folder.rglob("*.log"))
            if ready:break
            if time.monotonic()>deadline:raise RuntimeError("TraderAdapter未完成登录、持仓和委托成交查询")
            mock.plus_mocker_barrier(1);time.sleep(.01)
        last=None
        for index,tick in enumerate(stream):
            if STOP.is_set() or (folder/"stop.request").exists():break
            if mock.plus_mocker_step(1,ctypes.byref(tick))<0:raise RuntimeError("历史Tick撮合线程失去回执")
            # Synchronous DataWriter updates the isolated live DMB bars before the live engine sees this Tick.
            data.push_quote_from_extended_parser("plus-writer",ctypes.byref(tick),0)
            engine.push_quote_from_extended_parser("plus-history",ctypes.byref(tick),0)
            if not mock.plus_mocker_barrier(1):raise RuntimeError("模拟柜台委托队列未排空")
            if strategy.errors:raise RuntimeError("; ".join(strategy.errors[:3]))
            last=tick
            task.update(processed=index+1,progress=round((index+1)/len(stream)*100,2),updated_at=time.time())
            if index%25==0:write_json(folder/"task.json",task)
        if last is not None and not STOP.is_set():
            when=dt.datetime.strptime(str(last.action_date)+str(last.action_time//1000).zfill(6),"%Y%m%d%H%M%S")
            closing=when.replace(second=0)+dt.timedelta(minutes=1)
            api.plus_replay_flush(int(closing.strftime("%Y%m%d")),int(closing.strftime("%H%M")))
            mock.plus_mocker_barrier(1)
        if strategy.errors:raise RuntimeError("; ".join(strategy.errors[:3]))
        callback=getattr(strategy.strategy,"on_backtest_end",None)
        if callback:
            contexts=engine.__cta_ctxs__ if task["engine_type"]=="CTA" else engine.__hft_ctxs__
            callback(next(iter(contexts.values())))
        task.update(status="stopped" if STOP.is_set() or (folder/"stop.request").exists() else "completed",
                    updated_at=time.time(),elapsed_ms=round((time.monotonic()-began)*1000),callbacks=strategy.counts)
    finally:
        engine.release()
        if data is not None:data.release()
        report=make_report(folder,task,stream[:task.get("processed",0)],strategy.counts)
        write_json(folder/"report.json",report)
        task.update(orders=len(report["orders"]),trades=len(report["trades"]),updated_at=time.time())
        write_json(folder/"task.json",task)
def make_report(folder,task,stream,callbacks):
    events=[]
    path=folder/"replay_events.jsonl"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:events.append(json.loads(line))
            except ValueError:continue
    orders=[dict(x) for x in events if x["type"]=="order"]
    trades=[dict(x) for x in events if x["type"]=="trade"]
    rejections=[dict(x) for x in events if x["type"]=="reject"]
    # The native counter is the sole account ledger. Do not charge fees twice
    # or reconstruct its cash/margin decisions with an independent Python FIFO.
    account=read_json(folder/"replay_account.json", {}) or {}
    funds=[]
    funds_path=folder/"replay_funds.csv"
    if funds_path.is_file():
        with funds_path.open(encoding="utf-8", newline="") as inp:
            for original in csv.DictReader(inp):
                row={key:int(value) if key in ("date", "time", "trading_date") else float(value)
                     for key,value in original.items()}
                if any(isinstance(value,float) and not math.isfinite(value) for value in row.values()):
                    raise ValueError("原生资金账簿包含无效数值")
                row.update(fee=row["commission"], profit=row["realized"], dynprofit=row["unrealized"])
                funds.append(row)
    if task.get("finance") and task.get("status")=="completed" and task.get("processed",0)>0:
        required=("balance","equity","commission","margin","frozen_margin","frozen_commission","available")
        if not funds or any(not isinstance(account.get(key),(int,float)) or not math.isfinite(account[key]) for key in required):
            raise RuntimeError("Plus缺少原生资金账簿；请检查模拟柜台版本，不能把旧版零费用结果标为完成")
    positions=[]
    for original in account.get("positions", []):
        row=dict(original)
        row.update(raw_direction=row["direction"], direction="long" if row["direction"] in (0,48) else "short",
                   avg_price=row.get("average_price"))
        positions.append(row)
    for row in orders+trades:
        row["raw_direction"]=row["direction"];row["raw_offset"]=row["offset"]
        row["direction"]="long" if row["direction"]==48 else "short"
        row["offset"]="open" if row["offset"]==48 else "close"
    return {"orders":orders,"trades":trades,"funds":funds,"positions":positions,"events":events,"rejections":rejections,
            "route":["Historical Tick","WT DataWriter","Live "+task["engine_type"]+" Engine",
                     "CTA Local Executer" if task["engine_type"]=="CTA" else "HFT Context",
                     "TraderAdapter","TraderMocker","Native Returns"],
            "summary":{"processed":task.get("processed",0),"callbacks":callbacks,"actual_orders":len(orders),
                       "actual_trades":len(trades),"errors":len([x for x in events if x["type"]=="reject"]),
                       "capital":task["capital"],"fees_model":"wt_native","liquidity":"shared_l1_next_tick",
                       "finance":task.get("finance"),"execution":task.get("execution"),"account_source":"TraderMocker原生账户",
                       **{key:account.get(key) for key in ("balance","equity","commission","margin","frozen_margin","frozen_commission","available","realized","unrealized")},
                       "slippage_cost":sum(float(x.get("slippage",0))*float(x["volume"])*float(task["volume_scale"]) for x in trades),
                       "rejected_insufficient_funds":sum(x.get("error_code")=="INSUFFICIENT_FUNDS" for x in rejections),
                       "recorded_market_data":task.get("market_source")=="recorded"},
            "limitations":LIMITATIONS}
def main():
    folder=Path(sys.argv[1]).resolve();workspace=Path(sys.argv[2]).resolve()
    try:run(folder,workspace)
    except BaseException as error:
        task=read_json(folder/"task.json",{})
        task.update(status="stopped" if isinstance(error,KeyboardInterrupt) else "failed",
                    error=repr(error),updated_at=time.time())
        write_json(folder/"task.json",task)
        traceback.print_exc()
        raise SystemExit(1)
if __name__=="__main__":main()

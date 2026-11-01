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
from ..ContractCodes import raw_code, contract_info
from .PlusReport import fund_row, save_view
STOP = threading.Event()
def on_stop(signum, frame): STOP.set()
signal.signal(signal.SIGTERM, on_stop)
signal.signal(signal.SIGINT, on_stop)
def final_portfolio_state(engine, strategyid):
    """Retry transient read contention only, after replay and before engine release."""
    from .PortfolioPositions import query_portfolio_state
    transient = {"event_loop_busy", "broker_snapshot_busy_or_resetting",
                 "broker_snapshot_changed", "execution_snapshot_changed"}
    for attempt in range(20):
        state = query_portfolio_state(engine, 'plus', strategyid)
        if state.get('valid') is not False or state.get('reason') not in transient or attempt == 19:
            return state
        time.sleep(.05)

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
    helper=WtDataHelper()
    ticks=[]
    for filename in item.get("files", [item.get("file")]):
        data=helper.read_dsb_ticks(filename)
        if data is None: raise ValueError("无法读取原生Tick数据")
        ticks.extend(WTSTickStruct.from_buffer_copy(row.tobytes()) for row in data.ndarray)
    ticks.sort(key=lambda tick:(int(tick.action_date),int(tick.action_time)))
    return ticks


def portfolio_codes(task):
    from .PortfolioPositions import standard_code
    codes=(task.get('market_snapshot') or {}).get('codes') or [task['code']]
    if (not isinstance(codes,list) or not codes or task['code'] not in codes or len(codes)!=len(set(codes))
            or any(standard_code(code)!=code for code in codes)):
        raise ValueError('Frozen portfolio codes must be unique standard contracts including the anchor')
    return codes


def frozen_finances(task,codes):
    finances=task.get('finance_by_code') or {task['code']:task['finance']}
    if set(finances)!=set(codes):raise ValueError('Each frozen portfolio contract needs fees and margin')
    if len({value['slippage_ticks'] for value in finances.values()})!=1:
        raise ValueError('Native shared account requires one frozen slippage scenario')
    if any(task['finance'][key]!=finances[task['code']][key] for key in ('fees','margin','slippage_ticks')):
        raise ValueError('Anchor finance conflicts with the frozen per-contract scenario')
    return finances


def prepare_stream(task,ticks):
    codes=portfolio_codes(task);frozen_finances(task,codes)
    identities={(code.split('.')[0],raw_code(code)) for code in codes}
    stream=[tick for tick in ticks if task['start']<=int(tick.action_date)*10000+int(tick.action_time)//100000<=task['end']]
    stream.sort(key=lambda tick:(int(tick.action_date),int(tick.action_time),bytes(tick.exchg),bytes(tick.code)))
    if not stream:raise ValueError('起止范围内没有Tick；不创建空结果')
    present=set()
    for tick in stream:
        identity=(bytes(tick.exchg).decode(),bytes(tick.code).decode())
        if identity not in identities:raise ValueError('历史Tick合约与冻结品种不一致')
        if not math.isfinite(tick.price) or tick.price<=0:raise ValueError('历史Tick价格无效')
        dt.datetime.strptime(str(int(tick.action_date))+str(int(tick.action_time)//1000).zfill(6),'%Y%m%d%H%M%S')
        present.add(identity)
    if present!=identities:raise ValueError('Each frozen contract needs Tick coverage in the selected window')
    return codes,stream


def seed_portfolio_history(folder,snapshot,first,codes):
    warmup={}
    for code in codes:
        quote=type(first).from_buffer_copy(first)
        exchange,product,month=code.split('.')
        quote.exchg=exchange.encode();quote.code=raw_code(code).encode()
        warmup[code]=seed_history(folder,snapshot,quote)
    return warmup


def contract_scales(engine,codes):
    result={}
    for code in codes:
        product=engine.getProductInfo('.'.join(code.split('.')[:2]))
        scale=product.volscale if product is not None else None
        if not isinstance(scale,(int,float)) or not math.isfinite(scale) or scale<=0:
            raise ValueError('Frozen WT contract multiplier is unavailable: '+code)
        result[code]=scale
    return result
class CheckedStrategy:
    def __init__(self, strategy, replay_tick_time=None):
        self.strategy=strategy;self.errors=[];self.counts={};self.bar_periods={};self.calculation_days={};self.future_bars=0
        self.future_ticks=0;self.replay_tick_time=replay_tick_time;self._contexts=set()
    def name(self):return self.strategy.name()

    def _abort(self, message):
        if not self.errors:self.errors.append(message)
        STOP.set()

    def _minute_limit(self, context):
        stamp=self.replay_tick_time//100000 if self.replay_tick_time is not None else (
            int(context.stra_get_date())*10000+int(context.stra_get_time()))
        return dt.datetime.strptime(str(stamp),"%Y%m%d%H%M")

    def _bar_close(self, context, code, period, stamp):
        stamp=int(stamp)
        if period.startswith("d"):
            day=dt.datetime.strptime(str(stamp)[:8],"%Y%m%d")
            session=context.stra_get_sessinfo(code)
            if session is None:raise ValueError("Daily replay requires the contract session close")
            offset=int(session.offset)
            if hasattr(session,"getCloseTime"):
                if not session.sections:raise ValueError("Daily replay session has no trading sections")
                close=int(session.getCloseTime(True));minutes=close//100*60+close%100
            else:
                close=int(session.close);minutes=(close//100*60+close%100+offset)%1440
            return day+dt.timedelta(minutes=minutes-offset)
        if stamp<100000000000:stamp+=199000000000
        return dt.datetime.strptime(str(stamp),"%Y%m%d%H%M")

    def _check_bars(self, context, code, period, stamps):
        limit=self._minute_limit(context)
        if any(self._bar_close(context,code,period,stamp)>limit for stamp in stamps):
            self.future_bars+=1
            raise ValueError("Replay exposed a future or unclosed bar: "+str(code)+" "+str(period))

    def _check_ticks(self, context, stamps):
        limit=self.replay_tick_time
        if limit is None:
            limit=int(context.stra_get_date())*1000000000+int(context.stra_get_time())*100000
            seconds=getattr(context,"stra_get_secs",None)
            if seconds is not None:limit+=int(seconds())
        if any(int(stamp)>limit for stamp in stamps):
            self.future_ticks+=1
            raise ValueError("Replay exposed a future tick")

    def _bind_context(self, context):
        if id(context) in self._contexts:return
        self._contexts.add(id(context))
        for name,alias in (("stra_get_bars","get_bars"),("stra_prepare_bars","prepare_bars"),
                           ("stra_get_ticks","get_ticks")):
            original=getattr(context,name,None)
            if original is None:continue
            def checked(*args,_original=original,_name=name,**kwargs):
                if self.errors:raise ValueError("Replay already stopped after a data availability error")
                try:
                    result=_original(*args,**kwargs);data=result
                    code=args[0] if args else kwargs["stdCode"]
                    if _name=="stra_get_ticks":
                        if data is not None:self._check_ticks(context,data.times)
                    else:
                        period=args[1] if len(args)>1 else kwargs["period"]
                        if data is None:
                            data=getattr(context,"__bar_cache__",{}).get(str(code)+"#"+str(period))
                        if data is not None:self._check_bars(context,code,period,data.bartimes)
                    return result
                except BaseException as error:
                    self._abort(_name+": "+repr(error));raise
            setattr(context,name,checked)
            setattr(context,alias,checked)

    def __getattr__(self,name):
        original=getattr(self.strategy,name)
        if not callable(original) or not name.startswith("on_"): return original
        def callback(*args, **kwargs):
            self.counts[name]=self.counts.get(name,0)+1
            if self.errors:return None
            try:
                context=args[0]
                self._bind_context(context)
                if name == "on_bar":
                    context,code,period,bar=args[:4]
                    self.bar_periods[period]=self.bar_periods.get(period,0)+1
                    self._check_bars(context,code,period,[bar["date"] if period.startswith("d") else bar["time"]])
                elif name == "on_tick":
                    tick=args[2]
                    self._check_ticks(context,[int(tick["action_date"])*1000000000+int(tick["action_time"])])
                elif name == "on_calculate":
                    day=str(context.stra_get_date())
                    self.calculation_days[day]=self.calculation_days.get(day,0)+1
                return original(*args,**kwargs)
            except BaseException as error:
                self._abort(name+": "+repr(error))
                return None
        return callback
def seed_history(folder, snapshot, first):
    """Seed only bars already closed before the first replay quote."""
    if not snapshot or not snapshot.get("storage"):
        return {}
    from wtpy.WtCoreDefs import WTSBarStruct
    from wtpy.wrapper.WtDtHelper import WtDataHelper
    helper = WtDataHelper()
    exchange = bytes(first.exchg).decode()
    ticker = bytes(first.code).decode()
    cutoff = int(first.action_date)*10000 + int(first.action_time)//100000
    counts = {}
    for period, directory in (("m1", "min1"), ("m5", "min5"), ("d", "day")):
        relative = Path("his")/directory/exchange/(ticker+".dsb")
        source = Path(snapshot["storage"])/relative
        if not source.is_file():
            continue
        data = helper.read_dsb_bars(str(source))
        if data is None:
            raise ValueError("无法读取历史预热线："+period)
        bars = []
        for row in data.ndarray:
            bar = WTSBarStruct.from_buffer_copy(row.tobytes())
            closed = int(bar.date) < int(first.action_date) if period == "d" else 199000000000+int(bar.time) <= cutoff
            if closed:
                bars.append(bar)
        if bars:
            destination = Path(folder)/"storage"/relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if not helper.store_bars(str(destination), (WTSBarStruct*len(bars))(*bars), len(bars), period):
                raise RuntimeError("无法写入隔离预热线："+period)
        counts[period] = len(bars)
    return counts

def build_config(task,item,first,folder):
    base=Path(item["basefiles"])
    basefiles={key:str(base/file) for key,file in (
        ("commodity","commodities.json"),("contract","contracts.json"),
        ("session","sessions.json"),("holiday","holidays.json"),("hot","hots.json"))}
    if item.get("basefiles_map"):
        basefiles={key:value for key,value in item["basefiles_map"].items() if key != "fee"}
    basefiles["utf-8"] = True
    finances=frozen_finances(task,portfolio_codes(task))
    finance=finances[task['code']]
    fees = {}
    for code,settings in finances.items():
        fee = settings["fees"]
        values={key:fee[key] for key in ('byvolume','open','close','closetoday','minfee')}
        exchange,product,month=code.split('.')
        # WT looks up the exact contract before its commodity fee fallback.
        fees[exchange+'.'+raw_code(code)]=values
        fees.setdefault(fee['key'],values)
    write_json(folder/"fees.json", fees)
    # Freeze effective margin ratios even if server metadata changes later.
    contracts = read_json(Path(basefiles['contract']), {})
    commodities = read_json(Path(basefiles['commodity']), {})
    for code, settings in finances.items():
        exchange, product, contract = code.split(".")
        contract_info = contracts[exchange][raw_code(code)]
        contract_info["longmarginratio"] = settings["margin"]["long"]
        contract_info["shortmarginratio"] = settings["margin"]["short"]
        if product in commodities.get(exchange, {}):
            commodities[exchange][product]["marginrate"] = 0
    (folder/"contracts.json").write_text(json.dumps(contracts,ensure_ascii=True),encoding="ascii")
    (folder/"commodities.json").write_text(json.dumps(commodities,ensure_ascii=True),encoding="ascii")
    basefiles["contract"] = str(folder/"contracts.json")
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
    if len(finances) == 1 and finance.get("marginrate") is not None:
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
    task=read_json(folder/"task.json")
    if task.get("market_snapshot"):
        snapshot=task["market_snapshot"]
        item=dict(kind="native_ticks",source="recorded",files=snapshot["files"],
                  basefiles=str(Path(snapshot["basefiles"]["contract"]).parent),basefiles_map=snapshot["basefiles"])
    else:
        item=next(x for x in dataset_catalog(workspace) if x["id"]==task["dataset"])
    main={row["day"]:row for row in task.get("main_plan",[])}
    if main:
        from .MainPlus import DayStream,raw_signal_tick
        codes=portfolio_codes(task)
        stream=DayStream(task["days_files"])
    else:codes,stream=prepare_stream(task,load_ticks(item))
    warmup=seed_portfolio_history(folder,task.get('market_snapshot'),stream[0],codes)
    task["warmup_bars"] = warmup[task['code']] if len(codes)==1 else warmup
    basefiles,log=build_config(task,item,stream[0],folder)
    task.update(status="running",total=len(stream),updated_at=time.time());write_json(folder/"task.json",task)
    spec=importlib.util.spec_from_file_location("PlusTaskStrategy",folder/"MyStrategy.py")
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
    base=BaseCtaStrategy if task["engine_type"]=="CTA" else BaseHftStrategy
    classes=[x for x in vars(module).values() if inspect.isclass(x) and x.__module__==module.__name__ and issubclass(x,base) and x is not base]
    if len(classes)!=1:raise ValueError("源码必须只定义一个与任务类型对应的策略类")
    supplied={"name":task["straid"],"code":task.get("signal_code",task["code"]),"period":task["period"],"capital":task["capital"],**task["params"]}
    signature=inspect.signature(classes[0]);params=signature.parameters
    accepts_kwargs=any(x.kind==inspect.Parameter.VAR_KEYWORD for x in params.values())
    extras=set(task["params"])-set(params)
    if extras and not accepts_kwargs:raise ValueError("策略构造函数不支持参数："+",".join(sorted(extras)))
    constructor={k:v for k,v in supplied.items() if k in params or accepts_kwargs}
    bound=signature.bind(**constructor);bound.apply_defaults()
    task["constructor_params"]={k:v for k,v in bound.arguments.items() if k != "name"}
    underlying=classes[0](**constructor)
    if main:
        from .MainStrategy import ContinuousStrategy
        underlying=ContinuousStrategy(underlying,task["signal_code"],task["main_plan"],task["period"])
    strategy=CheckedStrategy(underlying,int(stream[0].action_date)*1000000000+int(stream[0].action_time))
    from .PlusDailyReplay import DailyReplay
    daily=DailyReplay(strategy, task.get("signal_code",task["code"]), folder) if task["engine_type"]=="CTA" and task["period"]=="d1" else None
    engine=WtEngine(EngineType.ET_CTA if task["engine_type"]=="CTA" else EngineType.ET_HFT,
                    "logcfg.json",str(folder/"generated"))
    data=None;began=time.monotonic()
    try:
        engine.init("",str(folder/"config.json"))
        engine.commitConfig()
        from .RiskRuntime import attach_risk, replay_account_provider
        attach_risk(engine,task.get("risk"),output=folder/"risk-events.jsonl",account_provider=replay_account_provider(folder/"replay_account.json"))
        from .PortfolioPositions import attach_portfolio_providers
        engine._risk_channels={strategy.name():'plus'}
        attach_portfolio_providers(engine,folder,codes=codes,replay=True,clock=lambda:strategy.replay_tick_time)
        task['volume_scale_by_code']=contract_scales(engine,codes)
        task['volume_scale']=task['volume_scale_by_code'][task['code']]
        if main:
            def released(ctx,code):
                try:
                    positions=engine._portfolio_position_provider(ctx)
                    orders=engine._portfolio_orders_provider(ctx)
                    return not positions.get(code,0) and not orders.get(code,0)
                except ValueError:
                    return False
            underlying.released=released
        engine.add_exetended_parser(BaseExtParser("plus-history"))
        if task["engine_type"]=="CTA":engine.add_cta_strategy(daily or strategy)
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
            if strategy.errors:raise RuntimeError("; ".join(strategy.errors[:3]))
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
            if daily and daily.last is not None and (tick.action_date, tick.action_time//100000)>(daily.last.trading_date,daily.close):
                daily.close_if_due(tick.action_date,tick.action_time//100000)
                if strategy.errors:raise RuntimeError(strategy.errors[0])
            strategy.replay_tick_time=int(tick.action_date)*1000000000+int(tick.action_time)
            if mock.plus_mocker_step(1,ctypes.byref(tick))<0:raise RuntimeError("历史Tick撮合线程失去回执")
            # Synchronous DataWriter updates the isolated live DMB bars before the live engine sees this Tick.
            data.push_quote_from_extended_parser("plus-writer",ctypes.byref(tick),0)
            engine.push_quote_from_extended_parser("plus-history",ctypes.byref(tick),0)
            signal_tick=raw_signal_tick(tick,main,task["signal_code"]) if main else None
            if signal_tick is not None:
                data.push_quote_from_extended_parser("plus-writer",ctypes.byref(signal_tick),0)
                engine.push_quote_from_extended_parser("plus-history",ctypes.byref(signal_tick),0)
            if not mock.plus_mocker_barrier(1):raise RuntimeError("模拟柜台委托队列未排空")
            if strategy.errors:raise RuntimeError("; ".join(strategy.errors[:3]))
            if daily:
                if not main or signal_tick is not None:daily.observe(signal_tick if main else tick)
                if strategy.errors:raise RuntimeError(strategy.errors[0])
            last=tick
            task.update(processed=index+1,progress=round((index+1)/len(stream)*100,2),updated_at=time.time())
            if index%25==0:write_json(folder/"task.json",task)
        if last is not None and not STOP.is_set():
            when=dt.datetime.strptime(str(last.action_date)+str(last.action_time//1000).zfill(6),"%Y%m%d%H%M%S")
            if daily:daily.close_if_due(last.action_date,last.action_time//100000)
            closing=when.replace(second=0)  # Never advance beyond the last observed quote.
            strategy.replay_tick_time=int(last.action_date)*1000000000+int(last.action_time)
            api.plus_replay_flush(int(closing.strftime("%Y%m%d")),int(closing.strftime("%H%M")))
            mock.plus_mocker_barrier(1)
        if strategy.errors:raise RuntimeError("; ".join(strategy.errors[:3]))
        if daily and not STOP.is_set() and not strategy.calculation_days:raise ValueError("区间内没有完整的日线收盘回调")
        callback=getattr(daily or strategy,"on_backtest_end",None)
        if callback:
            contexts=engine.__cta_ctxs__ if task["engine_type"]=="CTA" else engine.__hft_ctxs__
            callback(next(iter(contexts.values())))
        if strategy.errors:raise RuntimeError("; ".join(strategy.errors[:3]))
        task.update(status="stopped" if STOP.is_set() or (folder/"stop.request").exists() else "completed",
                    updated_at=time.time(),elapsed_ms=round((time.monotonic()-began)*1000),callbacks=strategy.counts)
    finally:
        try:write_json(folder/'portfolio-native-final.json',final_portfolio_state(engine,task['straid']))
        finally:engine.release()
        if data is not None:data.release()
        report=make_report(folder,task,(),strategy.counts)
        report["diagnostics"]={"bar_periods":strategy.bar_periods,"calculation_days":strategy.calculation_days,
            "future_bar_callbacks":strategy.future_bars,
            "future_tick_exposures":strategy.future_ticks,
            "message":"策略访问到晚于回放时钟的数据，回放已中止。" if strategy.future_bars or strategy.future_ticks else
                      "没有委托记录；请结合实际策略参数、主周期回调和信号记录检查。" if not report["orders"] else ""}
        write_json(folder/"report.json",report)
        save_view(folder,report)
        task.update(orders=len(report["orders"]),trades=len(report["trades"]),updated_at=time.time())
        write_json(folder/"task.json",task)
def make_report(folder,task,stream,callbacks):
    from .RiskRuntime import read_risk_events
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
                funds.append(fund_row(original))
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
    scales = {};native_codes={}
    frozen_scales=task.get('volume_scale_by_code', {})
    codes=portfolio_codes(task)
    if trades and len(codes)>1 and not frozen_scales:
        raise ValueError('Frozen portfolio contract multipliers are unavailable')
    for code in codes if frozen_scales else []:
        if code not in frozen_scales:raise ValueError('Frozen contract multiplier is unavailable: '+code)
        exchange, product, month = code.split('.')
        ticker=raw_code(code)
        for key in (code, exchange+'.'+ticker):scales[key]=frozen_scales[code]
        native_codes[ticker]=code if ticker not in native_codes else None
    for ticker, code in native_codes.items():
        if code is not None:scales[ticker]=frozen_scales[code]
    slippage_cost = 0
    for trade in trades:
        if scales and trade['code'] not in scales:raise ValueError('Native trade has no frozen contract multiplier')
        scale = scales[trade['code']] if scales else task['volume_scale']
        slippage_cost += float(trade.get('slippage',0))*float(trade['volume'])*float(scale)
    return {"orders":orders,"trades":trades,"funds":funds,"positions":positions,"events":events,"rejections":rejections,
            "route":["Historical Tick","WT DataWriter","Live "+task["engine_type"]+" Engine",
                     "CTA Local Executer" if task["engine_type"]=="CTA" else "HFT Context",
                     "TraderAdapter","TraderMocker","Native Returns"],
            "summary":{"processed":task.get("processed",0),"callbacks":callbacks,"actual_orders":len(orders),
                       "actual_trades":len(trades),"errors":len([x for x in events if x["type"]=="reject"]),
                       "capital":task["capital"],"fees_model":"wt_native","liquidity":"shared_l1_next_tick",
                       "finance":task.get("finance"),"finance_by_code":task.get("finance_by_code"),
                       "execution":task.get("execution"),"account_source":"TraderMocker原生账户",
                       **{key:account.get(key) for key in ("balance","equity","commission","margin","frozen_margin","frozen_commission","available","realized","unrealized")},
                       "slippage_cost":slippage_cost,
                       "rejected_insufficient_funds":sum(x.get("error_code")=="INSUFFICIENT_FUNDS" for x in rejections),
                       "recorded_market_data":task.get("market_source")=="recorded"},
            "risk":{"selection":task.get("risk"),"events":read_risk_events(folder/"risk-events.jsonl")},
            "data_model":task.get("data_model", "recorded_tick"),"limitations":LIMITATIONS}
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

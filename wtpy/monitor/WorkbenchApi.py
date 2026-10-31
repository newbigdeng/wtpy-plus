"""Read-only trading workbench: native market files and existing monitor ledgers."""
import asyncio
from contextlib import asynccontextmanager
import os
import select
import json
import math
import re
import threading
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import quote
from fastapi import Body, Request
import yaml

def finite(value):
    if isinstance(value, dict): return {str(k):finite(v) for k,v in value.items()}
    if isinstance(value, (list,tuple)): return [finite(v) for v in value]
    if hasattr(value, "item"): value=value.item()
    if isinstance(value,float) and not math.isfinite(value): return None
    return value

def dominant_contract(catalog, summaries):
    """Use the latest stored market date, then open interest and volume."""
    indexed={item["code"]:item for item in catalog if item["available"]}
    valid=[row for row in summaries if row.get("code") in indexed
           and isinstance(row.get("open_interest"),(int,float))
           and math.isfinite(row["open_interest"]) and row["open_interest"]>=0]
    if not valid:return next(iter(indexed.values()),catalog[0] if catalog else None)
    latest=max(row["date"] for row in valid)
    selected=max((row for row in valid if row["date"]==latest),
                 key=lambda row:(row["open_interest"],row.get("volume") or 0,row["code"]))
    return indexed[selected["code"]]

def load_config(folder):
    for name in ("config.yaml","config.json"):
        path=folder/name
        if path.is_file():
            text=path.read_text(encoding="utf-8-sig")
            return yaml.safe_load(text) if path.suffix==".yaml" else json.loads(text)
    return {}

def channel_positions(rows, now=None):
    """Expand the native two-sided snapshot without borrowing strategy costs."""
    now=time.time()*1000 if now is None else now
    result=[]
    for row in rows:
        for side in ("long","short"):
            position=row.get(side,{})
            new=float(position.get("newvol",0));previous=float(position.get("prevol",0))
            volume=new+previous
            if volume<=0:continue
            timestamp=position.get("metrics_updated_at")
            fresh=isinstance(timestamp,(int,float)) and -1000<=now-timestamp<=300000
            valid=position.get("metrics_valid") is True and fresh
            def number(key):
                value=position.get(key)
                return value if valid and isinstance(value,(int,float)) and math.isfinite(value) else None
            result.append({"code":row["code"],"channel":row.get("channel"),"long":side=="long",
                "volume":volume,"new_volume":new,"previous_volume":previous,
                "available":float(position.get("newavail",0))+float(position.get("preavail",0)),
                "cost":number("avg_price"),"cost_amount":number("position_cost"),"profit":number("dynprofit"),
                "open_price":number("open_price") if number("open_price") and number("open_price")>0 else None,
                "margin":number("margin"),
                "return_on_margin_pct":number("dynprofit")/number("margin")*100 if number("dynprofit") is not None and number("margin") and number("margin")>0 else None,
                "metrics_updated_at":timestamp,"metrics_valid":valid,
                "metrics_message":"柜台持仓查询回执" if valid else "柜台未提供有效的持仓成本与浮盈",
                "source":"柜台持仓快照"})
    return result

def capital_performance(account, events):
    """Compare native equity with an explicitly confirmed capital reset."""
    resets=[event for event in events if event.get("type")=="simnow_reset" and event.get("confirmed_at")]
    event=max(resets,key=lambda value:value["time"],default={})
    capital=event.get("requested_capital")
    valid=bool(account.get("timestamp_source")=="柜台查询回执时间" and isinstance(capital,(int,float)) and math.isfinite(capital) and capital>0
               and (account.get("updated_at") or 0)>=event.get("time",float("inf")))
    # Cash changes require a new confirmed capital event. Never label deposits
    # as trading gains or silently infer a reset from the equity curve.
    cash_changed=any(account.get(key) not in (None,0) for key in ("deposit","withdraw"))
    valid=valid and not cash_changed
    pnl=account["equity"]-capital if valid and isinstance(account.get("equity"),(int,float)) else None
    return {"valid":pnl is not None,"fresh":bool(account.get("valid")),"snapshot_at":account.get("updated_at"),
            "pnl":pnl,"pnl_pct":pnl/capital*100 if pnl is not None else None,
            "initial_capital":capital,"since":event.get("time"),"currency":account.get("currency"),
            "scope":"current_capital_phase","label":"历史总盈亏",
            "message":"柜台动态权益减最近确认的重置资金，包含手续费与浮盈；重置前的资金阶段单独保留" if pnl is not None else
                      "检测到出入金，须确认新的资金基数" if cash_changed else "等待有效柜台回执和已确认资金基数"}

def theoretical_position_summary(group, running, now=None):
    from .RiskControl import _engine_snapshot
    snapshot=_engine_snapshot(group,running,now=now)
    metric=snapshot["metrics"]["gross_lots"]
    return {"valid":metric["valid"],"gross_lots":metric["value"],"fresh":snapshot["fresh"],
            "snapshot_at":snapshot.get("fund_update_time"),"running":bool(running),
            "message":"引擎账簿的理论持仓，不等于柜台持仓。"+snapshot["reason"]}

def position_owners(group):
    from .StrategyContracts import contract_codes
    folder=Path(group["path"])/"managed-strategies"
    try:
        applied=json.loads((folder/"applied.json").read_text(encoding="utf-8-sig"))
        plan=json.loads((folder/"revisions"/applied["revision"]/"plan.json").read_text(encoding="utf-8-sig"))
        names_path=Path(group["path"])/"generated/strategy-control/names.json"
        names=json.loads(names_path.read_text(encoding="utf-8-sig")) if names_path.is_file() else {}
    except (OSError,ValueError,KeyError):return {}
    result={}
    for item in plan.get("strategies",[]):
        for code in contract_codes(item.get("params",{})):
            result.setdefault((item.get("trader"),code),[]).append({"id":item["id"],"name":names.get(item["id"],item.get("name",item["id"]))})
    return result

def position_market_fields(position, market, owners, now=None):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now=time.time()*1000 if now is None else now
    quote=market.get("quote") or {};stamp=None
    try:
        text=str(int(quote["action_date"]))+str(int(quote["action_time"])).zfill(9)
        stamp=datetime.strptime(text,"%Y%m%d%H%M%S%f").replace(tzinfo=ZoneInfo("Asia/Shanghai")).timestamp()*1000
    except (KeyError,TypeError,ValueError):pass
    state="closed" if market.get("session_open") is False else "unknown" if stamp is None or stamp>now+5000 else "live" if now-stamp<=5000 else "delayed" if now-stamp<=60000 else "stale"
    reference=quote.get('quote_basis')=='closed_bar'
    if reference and state in ('live','delayed'):state='unknown'
    position.update(last_price=quote.get("price"),quote_updated_at=stamp,market_state=state,
                    market_message={"closed":"休市","unknown":"行情未知","live":"行情正常","delayed":"行情延迟","stale":"行情过期"}[state]+(' · 最近闭合K线参考价' if reference else ''),
                    strategy_owners=owners.get((position.get("channel"),position["code"]),[]),
                    ownership_message="已应用计划中负责此合约的策略；柜台持仓本身不带策略归属")
    return position

def trades_with_dates(group, trades, contracts):
    """Use the configured exchange session to place real fills on daily bars."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from .MarketSessions import session_status
    folder=Path(group["path"]).resolve()
    bases=load_config(folder).get("basefiles",{})
    def read(key):
        name=bases.get(key)
        path=folder/name if isinstance(name,str) else None
        return json.loads(path.read_text(encoding="utf-8-sig")) if path and path.is_file() else {}
    sessions,holidays=read("session"),read("holiday")
    catalog={item["code"]:item for item in contracts if item}
    result=[]
    for trade in trades:
        item=dict(trade);item["trading_date"]=None
        contract=catalog.get(item.get("code"))
        stamp=item.get("time")
        if contract and isinstance(stamp,(int,float)) and stamp>1000000000000:
            moment=datetime.fromtimestamp(stamp/1000,ZoneInfo("Asia/Shanghai"))
            item["trading_date"]=session_status(contract,sessions,holidays,moment)["trading_date"]
        result.append(item)
    return result

class WorkbenchFeed:
    def __init__(self, history_root):
        self._lock=threading.RLock()
        self._account_lock=threading.Lock()
        self._cache={}
        self._worker=None
        self._worker_output=b""
        self.history_root=Path(history_root)
    def _read_worker(self, timeout=8):
        deadline=time.monotonic()+timeout
        while b"\n" not in self._worker_output:
            remaining=deadline-time.monotonic()
            if remaining<=0 or not select.select([self._worker.stdout],[],[],remaining)[0]:
                raise TimeoutError("行情文件读取超时")
            chunk=os.read(self._worker.stdout.fileno(),65536)
            if not chunk:
                raise ValueError("行情读取进程异常")
            self._worker_output+=chunk
        line,self._worker_output=self._worker_output.split(b"\n",1)
        return json.loads(line)

    def start(self):
        with self._lock:
            if self._worker is not None and self._worker.poll() is None:
                return
            self.close()
            self._worker=subprocess.Popen(
                [sys.executable,str(Path(__file__).with_name("MarketDataWorker.py")),"--serve"],
                stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
            try:
                if self._read_worker()!={"ready":True}:
                    raise ValueError("行情读取进程初始化失败")
            except Exception:
                self.close()
                raise

    def close(self):
        with self._lock:
            worker=self._worker
            self._worker=None
            self._worker_output=b""
            if worker is None:
                return
            try:
                worker.stdin.close()
            except OSError:
                pass
            try:
                worker.wait(timeout=1)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
            worker.stdout.close()

    def _read_market(self, payload):
        try:
            self.start()
            self._worker.stdin.write((json.dumps(payload)+"\n").encode())
            self._worker.stdin.flush()
            result=self._read_worker()
            if "error" in result:
                raise ValueError(result["error"])
            return result
        except (OSError,ValueError,TimeoutError) as exc:
            self.close()
            raise ValueError("行情读取失败；监控服务保持运行："+str(exc)) from exc

    def market(self, group, code="", period="m1", count=600, include_catalog=True, start_date=None, end_date=None, *, quote_only=False):
        if period not in ("m1","m5","d"): raise ValueError("仅支持 m1、m5、d")
        if not 20<=count<=2000: raise ValueError("K线条数必须为20至2000")
        if quote_only and (start_date or end_date):raise ValueError('历史查询不读取实时Tick')
        from datetime import datetime
        for value in (start_date, end_date):
            if value is not None:
                text = str(value)
                if len(text) != 8:
                    raise ValueError("行情日期须为 YYYYMMDD")
                try: datetime.strptime(text, "%Y%m%d")
                except ValueError: raise ValueError("行情日期无效")
        if start_date is not None:start_date=int(start_date)
        if end_date is not None:end_date=int(end_date)
        if start_date and end_date and start_date > end_date:
            raise ValueError("开始日期不能晚于结束日期")
        folder=Path(group["path"]).resolve()
        cfg=load_config(folder)
        replay=cfg.get("monitor",{}).get("market_mode")=="simnow_replay"
        store=cfg.get("data",{}).get("store",{}).get("path")
        bases=cfg.get("basefiles",{})
        if not store:
            return {"contracts":[],"bars":[],"quote":None,"code":code,"period":period,"market_message":"该组合没有配置行情存储目录","source":"组合 data.store"}
        storage=(folder/store).resolve()
        def base_json(key):
            name=bases.get(key)
            if not isinstance(name,str): return {}
            path=(folder/name).resolve()
            return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}
        contracts=base_json("contract")
        commodities=base_json("commodity")
        available=set()
        for directory in ("rt/min1","rt/min5","rt/ticks","his/min1","his/min5","his/day"):
            base=storage/directory
            if base.is_dir():
                for f in base.glob("*/*"):
                    if f.suffix in (".dmb",".dsb"):
                        identity=(f.parent.name,f.stem)
                        available.add(identity)
        catalog=[]
        for exchange, items in contracts.items():
            for ticker, info in items.items():
                product=info.get("product","")
                standard=exchange+"."+product+"."+ticker[len(product):] if product and ticker.startswith(product) else exchange+"."+ticker
                commodity=commodities.get(exchange,{}).get(product,{})
                catalog.append({"code":standard,"ticker":ticker,"exchange":exchange,"product":product,"name":info.get("name",ticker),"price_tick":commodity.get("pricetick"),"volume_scale":commodity.get("volscale"),"session":commodity.get("session"),"holiday":commodity.get("holiday"),"expire_date":info.get("expiredate"),"long_margin_ratio":info.get("longmarginratio"),"short_margin_ratio":info.get("shortmarginratio"),"available":(exchange,ticker) in available})
        catalog.sort(key=lambda item:(not item["available"],item["code"]))
        if replay:
            for item in catalog:item["name"]+=" · 回放（本地接收时间）"
        if code:
            selected=next((item for item in catalog if code in (item["code"],item["exchange"]+"."+item["ticker"])),None)
        else:
            with self._lock:
                summaries=self._read_market({"operation":"contract_summaries","storage":str(storage),
                    "contracts":[{key:item[key] for key in ("code","exchange","ticker")} for item in catalog if item["available"]]})["summaries"]
            selected=dominant_contract(catalog,summaries)
        if selected is None:
            return {"contracts":catalog if include_catalog else None,"bars":[],"quote":None,"code":code,"period":period,"market_message":"没有可读取的合约；请先让数据中台订阅合约","source":"组合 data.store"}
        exchange,ticker=selected["exchange"],selected["ticker"]
        if not re.fullmatch(r"[A-Za-z0-9_]+",exchange) or not re.fullmatch(r"[A-Za-z0-9_]+",ticker):raise ValueError("合约代码无效")
        directory={"m1":"min1","m5":"min5","d":"day"}[period]
        key=(str(storage),selected["code"],period,count,start_date,end_date,quote_only)
        with self._lock:
            cached=self._cache.get(key)
            if cached and time.monotonic()-cached[0]<(0.2 if quote_only else 2):
                market=dict(cached[1])
            else:
                payload={"storage":str(storage),"exchange":exchange,"ticker":ticker,"directory":directory,"period":period,"count":count,"start_date":start_date,"end_date":end_date,"quote_only":quote_only}
                result=self._read_market(payload)
                ordered,latest=result["bars"],result["quote"]
                market={"bars":ordered,"quote":latest,"code":selected["code"],"period":period,"contract":selected,"market_message":"" if ordered else "该合约尚无该周期K线；没有补造行情","source":"组合 data.store 原生DMB/DSB","partial_last_bar":True,"history_mode":bool(start_date or end_date),"range":result.get("range",{})}
                market['data_warnings']=result.get('data_warnings',[])
                market=finite(market)
                self._cache[key]=(time.monotonic(),market)
                # Bound caches when a browser explores many contracts.
                if len(self._cache)>64:self._cache.pop(next(iter(self._cache)))
            market["contracts"]=catalog if include_catalog else None
            market["fetched_at"]=int(time.time()*1000)
            market["default_selection"]="最新已存行情交易日内，按持仓量选择；仅比较本机已采集合约"
            from .MarketSessions import session_status
            market.update(session_status(selected,base_json("session"),base_json("holiday")))
            if quote_only:return market
            from .ContractRollover import rollover_hint
            peers=[item for item in catalog if item["available"] and item["exchange"]==exchange and item["product"]==selected["product"]]
            try:
                summaries=self._read_market({"operation":"contract_summaries","storage":str(storage),"contracts":peers})["summaries"]
            except ValueError:
                summaries=[]
            market["rollover"]=rollover_hint(selected,catalog,summaries,base_json("holiday"))
            return market
    def account_series(self, groupid, accounts, group_path):
        from .AccountSnapshots import _identities
        folder=Path(group_path).resolve()
        identities=_identities(folder)
        legacy_target=self.history_root/quote(groupid,safe="")/(quote(str(folder),safe="")+".json")
        target=legacy_target.with_suffix(".accounts-v3.json")
        with self._account_lock:
            try:document=json.loads((target if target.exists() else legacy_target).read_text())
            except (FileNotFoundError,json.JSONDecodeError):document={}
            try:capital_events=json.loads(legacy_target.with_suffix(".capital-events.json").read_text())
            except FileNotFoundError:capital_events={}
            migrated=document.get("schema")!=3
            if migrated:
                old_accounts=document.get("accounts",{}) if document.get("schema")==2 else {}
                legacy_balance=document.get("legacy_balance",{}) if document.get("schema")==2 else document
                document={"schema":3,"books":{},"active_accounts":{},"legacy_balance":legacy_balance,
                          "unbound_accounts":old_accounts,"unbound_capital_events":capital_events.copy()}
            books=document["books"]
            changed=migrated
            try:anchors=json.loads((folder/"generated/account-display-snapshots.json").read_text(encoding="utf-8-sig")).get("accounts",{})
            except (FileNotFoundError,json.JSONDecodeError):anchors={}
            for account in accounts:
                key=account["channel"]+":"+account["currency"]
                identity_info=identities.get(account["channel"])
                identity,config_time=identity_info if identity_info else (None,0)
                timestamp=account.get("updated_at")
                equity=account.get("equity")
                series=[];events=[];receipt_since=float("inf")
                if identity is not None:
                    book_key=":".join(quote(str(part),safe="") for part in [account["channel"],account["currency"],*identity])
                    active=document["active_accounts"].get(key)
                    if book_key not in books:
                        book={"identity":identity,"channel":account["channel"],"currency":account["currency"],
                              "history":[],"capital_events":[]}
                        previous=document["unbound_accounts"].get(key,[])
                        anchor=anchors.get(key,{})
                        anchored=bool(previous and anchor.get("identity")==identity and
                                      anchor.get("account",{}).get("updated_at")==previous[-1]["time"])
                        if anchored:
                            book["history"]=document["unbound_accounts"].pop(key)
                            book["capital_events"]=document["unbound_capital_events"].pop(key,[])
                        books[book_key]=book
                        changed=True
                    else:
                        book=books[book_key]
                        anchored=False
                    if not active or active["book"]!=book_key:
                        receipt_since=0 if anchored else config_time
                        if migrated and not anchored:
                            receipt_since=max(receipt_since,max((event.get("time",0)+1 for event in capital_events.get(key,[])),default=0))
                        active={"book":book_key,"since":receipt_since}
                        document["active_accounts"][key]=active;changed=True
                    receipt_since=active["since"]
                    series=book["history"]
                    events=book["capital_events"]
                    for event in capital_events.get(key,[]):
                        if event.get("time",0)>=receipt_since and event not in events:
                            events.append(event);changed=True
                    native=(account.get("timestamp_source")=="柜台查询回执时间" and
                            (account.get("age_seconds") or 0)>=-1 and isinstance(equity,(int,float)) and
                            math.isfinite(equity) and timestamp and timestamp>=receipt_since)
                    if native and (not series or series[-1]["time"]<timestamp):
                        series.append({"time":timestamp,"equity":equity,"available":account.get("available"),"margin":account.get("margin")})
                        book["history"]=series=series[-10000:];changed=True
                account["history"]=series
                account["capital_events"]=events
                current=account if timestamp and timestamp>=receipt_since else dict(account,timestamp_source="当前账号回执未确认")
                account["performance"]=capital_performance(current,events)
                since=max((event["time"] for event in events),default=0)
                account["current_history"]=[point for point in series if point["time"]>=since]
                account["history_before_reset_samples"]=len(series)-len(account["current_history"])
                account["history_limit"]=10000
                account["legacy_balance_samples"]=len(document.get("legacy_balance",{}).get(key,[]))
                account["unbound_history_samples"]=len(document["unbound_accounts"].get(key,[]))
            if changed:
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                temp=target.with_suffix(".next")
                temp.write_text(json.dumps(finite(document)))
                temp.chmod(0o600);temp.replace(target)
        return accounts


def install_workbench_api(server, check_auth):
    feed=WorkbenchFeed(Path.cwd()/"workbench-equity")
    previous_lifespan=server.app.router.lifespan_context
    async def capture_accounts():
        # Preserve the last receipt even when no browser is open.
        from .TradeRiskApi import trade_risk_snapshot
        from .AccountSnapshots import account_snapshots
        import logging
        failures=set()
        while True:
            for group in server.__data_mgr__.get_groups():
                groupid=group["id"]
                try:
                    channels=server.__data_mgr__.get_channels(groupid) or []
                    accounts=(await asyncio.to_thread(trade_risk_snapshot,group,channels,"",running=server._dog.isRunning(groupid)))["accounts"]
                    await asyncio.to_thread(feed.account_series,groupid,accounts,group["path"])
                    await asyncio.to_thread(account_snapshots,group,channels,accounts)
                    failures.discard(groupid)
                except (OSError,ValueError,TypeError,KeyError):
                    if groupid not in failures:
                        logging.getLogger(__name__).warning("账户显示快照保存失败：%s",groupid)
                        failures.add(groupid)
            await asyncio.sleep(20)

    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application) as state:
            # Load the native reader before the first chart request.
            try:
                await asyncio.to_thread(feed.start)
            except (OSError,ValueError,TimeoutError):
                pass
            account_task=asyncio.create_task(capture_accounts())
            try:
                yield state
            finally:
                account_task.cancel()
                try:await account_task
                except asyncio.CancelledError:pass
                await asyncio.to_thread(feed.close)
    server.app.router.lifespan_context=lifespan
    @server.app.post("/mgr/workbench",tags=["交易工作台"])
    async def workbench(request:Request,groupid:str=Body(...,embed=True),code:str=Body("",embed=True),period:str=Body("m1",embed=True),count:int=Body(600,embed=True),include_catalog:bool=Body(True,embed=True),start_date:int=Body(None,embed=True),end_date:int=Body(None,embed=True),token:str=Body(None,embed=True)):
        ok,user=check_auth(request,token,server.__sec_key__)
        if not ok:return user
        products=user.get("products",[])
        if products and groupid not in products:return {"result":-1,"message":"没有该组合的访问权限"}
        manager=server.__data_mgr__
        group=manager.get_group(groupid)
        if not group:return {"result":-1,"message":"组合不存在"}
        try:
            market=await asyncio.to_thread(feed.market,group,code,period,count,include_catalog,start_date,end_date)
            channel_ids=manager.get_channels(groupid) or []
            from .TradeRiskApi import trade_risk_snapshot
            accounts=(await asyncio.to_thread(trade_risk_snapshot,group,channel_ids,"",running=server._dog.isRunning(groupid)))["accounts"]
            if user.get("role") not in ("superman","admin","guest"):accounts=[]
            await asyncio.to_thread(feed.account_series,groupid,accounts,group["path"])
            from .AccountSnapshots import account_snapshots
            display_accounts=await asyncio.to_thread(account_snapshots,group,channel_ids,accounts) if user.get("role") in ("superman","admin","guest") else []
            trades=[];orders=[]
            for channel in channel_ids:
                trades.extend(manager.get_channel_trades(groupid,channel,limit=500))
                orders.extend(manager.get_channel_orders(groupid,channel,limit=500))
            trades=trades_with_dates(group,trades,market.get("contracts") or [market.get("contract")])
            funds=manager.get_group_funds(groupid)
            equity=[{"time":int(row["date"]),"pnl":float(row.get("balance",0))+float(row.get("dynprofit",0))} for row in funds]
            positions=channel_positions(manager.get_channel_positions(groupid,"all"))
            owners=position_owners(group);quotes={market.get("code"):market}
            for position in positions:
                held_code=position["code"]
                if held_code not in quotes:
                    try:quotes[held_code]=await asyncio.to_thread(feed.market,group,held_code,"m1",20,False)
                    except (ValueError,OSError):quotes[held_code]={}
                position_market_fields(position,quotes[held_code],owners)
            theory=await asyncio.to_thread(theoretical_position_summary,group,server._dog.isRunning(groupid))
            return finite({"result":0,"message":"OK","market":market,"running":server._dog.isRunning(groupid),"positions":positions,"theoretical_position_summary":theory,"trades":sorted(trades,key=lambda row:row.get("time",0))[-1000:],"orders":sorted(orders,key=lambda row:row.get("time",0))[-1000:],"portfolio_positions":manager.get_group_positions(groupid),"equity":equity,"equity_kind":"组合净损益，不含默认本金","accounts":accounts,"account_snapshots":display_accounts,"account_history_kind":"仅保存已读取的柜台回执；动态权益=balance+dynprofit，同一回执不重复采样","read_only":True})
        except (ValueError,TypeError,KeyError,OSError) as exc:
            return {"result":-1,"message":"交易工作台读取失败："+str(exc)}

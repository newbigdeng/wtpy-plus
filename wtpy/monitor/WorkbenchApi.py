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

def load_config(folder):
    for name in ("config.yaml","config.json"):
        path=folder/name
        if path.is_file():
            text=path.read_text(encoding="utf-8-sig")
            return yaml.safe_load(text) if path.suffix==".yaml" else json.loads(text)
    return {}

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

    def market(self, group, code="", period="m1", count=600, include_catalog=True, start_date=None, end_date=None):
        if period not in ("m1","m5","d"): raise ValueError("仅支持 m1、m5、d")
        if not 20<=count<=2000: raise ValueError("K线条数必须为20至2000")
        from datetime import datetime
        for value in (start_date, end_date):
            if value is not None:
                text = str(value)
                if len(text) != 8:
                    raise ValueError("行情日期须为 YYYYMMDD")
                try: datetime.strptime(text, "%Y%m%d")
                except ValueError: raise ValueError("行情日期无效")
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
        freshness={}
        for directory in ("rt/min1","rt/min5","his/min1","his/min5","his/day"):
            base=storage/directory
            if base.is_dir():
                for f in base.glob("*/*"):
                    if f.suffix in (".dmb",".dsb"):
                        identity=(f.parent.name,f.stem)
                        available.add(identity)
                        if directory.startswith("rt/"):
                            freshness[identity]=max(freshness.get(identity,0),f.stat().st_mtime_ns)
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
        selected=next((item for item in catalog if code in (item["code"],item["exchange"]+"."+item["ticker"])),None) if code else max((item for item in catalog if item["available"]),key=lambda item:freshness.get((item["exchange"],item["ticker"]),0),default=catalog[0] if catalog else None)
        if selected is None:
            return {"contracts":catalog if include_catalog else None,"bars":[],"quote":None,"code":code,"period":period,"market_message":"没有可读取的合约；请先让数据中台订阅合约","source":"组合 data.store"}
        exchange,ticker=selected["exchange"],selected["ticker"]
        if not re.fullmatch(r"[A-Za-z0-9_]+",exchange) or not re.fullmatch(r"[A-Za-z0-9_]+",ticker):raise ValueError("合约代码无效")
        directory={"m1":"min1","m5":"min5","d":"day"}[period]
        key=(str(storage),selected["code"],period,count,start_date,end_date)
        with self._lock:
            cached=self._cache.get(key)
            if cached and time.monotonic()-cached[0]<2:
                market=dict(cached[1])
            else:
                payload={"storage":str(storage),"exchange":exchange,"ticker":ticker,"directory":directory,"period":period,"count":count,"start_date":start_date,"end_date":end_date}
                result=self._read_market(payload)
                ordered,latest=result["bars"],result["quote"]
                market={"bars":ordered,"quote":latest,"code":selected["code"],"period":period,"contract":selected,"market_message":"" if ordered else "该合约尚无该周期K线；没有补造行情","source":"组合 data.store 原生DMB/DSB","partial_last_bar":True,"history_mode":bool(start_date or end_date),"range":result.get("range",{})}
                market=finite(market)
                self._cache[key]=(time.monotonic(),market)
                # Bound caches when a browser explores many contracts.
                if len(self._cache)>64:self._cache.pop(next(iter(self._cache)))
            market["contracts"]=catalog if include_catalog else None
            market["fetched_at"]=int(time.time()*1000)
            from .MarketSessions import session_status
            market.update(session_status(selected,base_json("session"),base_json("holiday")))
            return market
    def account_series(self, groupid, accounts, group_path):
        target=self.history_root/quote(groupid,safe="")/(quote(str(Path(group_path).resolve()),safe="")+".json")
        with self._account_lock:
            try:document=json.loads(target.read_text())
            except (FileNotFoundError,json.JSONDecodeError):document={}
            # Earlier samples used balance alone. Keep them, but do not label
            # those amounts as dynamic equity without the missing floating PnL.
            migrated=bool(document) and document.get("schema")!=2
            if document.get("schema")!=2:
                document={"schema":2,"accounts":{},"legacy_balance":document}
            stored=document["accounts"]
            changed=migrated
            for account in accounts:
                key=account["channel"]+":"+account["currency"]
                series=stored.setdefault(key,[])
                timestamp=account.get("updated_at")
                equity=account.get("equity")
                if account.get("timestamp_source")=="柜台查询回执时间" and (account.get("age_seconds") or 0)>=-1 and isinstance(equity,(int,float)) and math.isfinite(equity) and timestamp and (not series or series[-1]["time"]<timestamp):
                    series.append({"time":timestamp,"equity":equity,"available":account.get("available"),"margin":account.get("margin")})
                    stored[key]=series[-10000:];changed=True
                account["history"]=stored[key]
                account["history_limit"]=10000
                account["legacy_balance_samples"]=len(document.get("legacy_balance",{}).get(key,[]))
            if changed:
                target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
                temp=target.with_suffix(".next")
                temp.write_text(json.dumps(finite(document)))
                temp.chmod(0o600);temp.replace(target)
        return accounts

def install_workbench_api(server, check_auth):
    feed=WorkbenchFeed(Path.cwd()/"workbench-equity")
    previous_lifespan=server.app.router.lifespan_context
    @asynccontextmanager
    async def lifespan(application):
        async with previous_lifespan(application) as state:
            # Load the native reader before the first chart request.
            try:
                await asyncio.to_thread(feed.start)
            except (OSError,ValueError,TimeoutError):
                pass
            try:
                yield state
            finally:
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
            trades=[];orders=[]
            for channel in channel_ids:
                trades.extend(manager.get_channel_trades(groupid,channel,limit=500))
                orders.extend(manager.get_channel_orders(groupid,channel,limit=500))
            funds=manager.get_group_funds(groupid)
            equity=[{"time":int(row["date"]),"pnl":float(row.get("balance",0))+float(row.get("dynprofit",0))} for row in funds]
            return finite({"result":0,"message":"OK","market":market,"running":server._dog.isRunning(groupid),"positions":manager.get_channel_positions(groupid,"all"),"trades":sorted(trades,key=lambda row:row.get("time",0))[-1000:],"orders":sorted(orders,key=lambda row:row.get("time",0))[-1000:],"portfolio_positions":manager.get_group_positions(groupid),"equity":equity,"equity_kind":"组合净损益，不含默认本金","accounts":accounts,"account_history_kind":"仅保存已读取的柜台回执；动态权益=balance+dynprofit，同一回执不重复采样","read_only":True})
        except (ValueError,TypeError,KeyError,OSError) as exc:
            return {"result":-1,"message":"交易工作台读取失败："+str(exc)}

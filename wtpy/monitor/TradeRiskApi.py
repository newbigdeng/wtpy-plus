"""Read-only broker balances, configured fee templates and native rejections."""
import asyncio
import json
from pathlib import Path
import re
import time

from fastapi import Body, Request

from .TradeFinance import fee_rule, number, read_table
from .WorkbenchApi import load_config
from .Roles import can_read, can_operate, group_allowed
from wtpy.StrategyRuntime import atomic_json

ACCOUNT_MAX_AGE_MS = 300000


def classify_rejection(event):
    message = str(event.get("message", ""))
    if event.get("error_code") == "INSUFFICIENT_FUNDS" or re.search(r"资金不足|欠缺资金|保证金不足|可用资金不足|insufficient\s+(?:available\s+)?(?:funds|margin)|not enough (?:money|funds|margin)", message, re.I):
        return "insufficient_funds"
    return "order_reject" if event.get("type") in ("order_reject", "order_error", "reject") else "trader_error"


def read_native_events(path):
    if not path.is_file():
        return [], False, None
    with path.open("rb") as inp:
        size = path.stat().st_size
        inp.seek(max(0, size-262144))
        if inp.tell():
            inp.readline()
        raw = inp.read()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError:
            return [], True, "风险记录编码无法识别"
    records=[]
    malformed=False
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value=json.loads(line)
            if not isinstance(value, dict):
                raise ValueError()
        except ValueError:
            malformed=True
            continue
        records.append(value)
    return records[-100:], True, "部分风险记录不完整；已展示可读取记录" if malformed else None


def trade_risk_snapshot(group, channels, code="", now=None, running=None):
    now = time.time()*1000 if now is None else now
    folder=Path(group["path"]).resolve()
    accounts=[]; alerts=[]; rejections=[]; sources=[]
    for channel in channels:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(channel)) or channel in (".", ".."):
            continue
        path=folder/"generated/traders"/channel/"rtdata.json"
        try:
            document=json.loads(path.read_text(encoding="utf-8-sig"))
        except FileNotFoundError:
            document={}
        except (OSError, ValueError):
            alerts.append({"severity":"warning","kind":"snapshot_unreadable","channel":channel,"message":"柜台资金快照无法读取"})
            document={}
        try:
            status=json.loads(path.with_name(path.name+".status.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            status={}
        connected = status.get("connected") is True and status.get("ready") is True and running is not False
        funds=document.get("funds", {})
        if isinstance(funds, list):
            values=[(x.get("currency", "UNKNOWN"),x) for x in funds if isinstance(x,dict)]
        elif isinstance(funds, dict):
            values=[(funds.get("currency", "UNKNOWN"),funds)] if "balance" in funds else list(funds.items())
        else:
            values=[]
        for currency, fund in values:
            if not isinstance(fund,dict):
                continue
            item={key:number(fund.get(key)) for key in ("prebalance","balance","available","margin","frozen_margin","frozen_commission","closeprofit","dynprofit")}
            item["fee"]=number(fund.get("commission",fund.get("fee")))
            equity=item["balance"]+item["dynprofit"] if item["balance"] is not None and item["dynprofit"] is not None else None
            timestamp=number(fund.get("updated_at"))
            native_timestamp=timestamp is not None and timestamp>0
            if not native_timestamp:
                timestamp=int(path.stat().st_mtime*1000) if path.exists() else None
            stale=not native_timestamp or timestamp is None or now-timestamp>ACCOUNT_MAX_AGE_MS or timestamp-now>1000
            same_day = str(status.get("trading_date", "")) == str(fund.get("trading_date", "")) and bool(fund.get("trading_date"))
            valid=connected and not stale and same_day
            reason=("柜台断线" if running is False or status.get("connected") is False else
                    "通道连接状态未知" if not status else "通道尚未就绪" if not connected else
                    "柜台交易日尚未更新" if not same_day else "柜台快照过期" if stale else "柜台查询回执有效")
            item.update(channel=channel,currency=str(currency),equity=equity,updated_at=timestamp,
                        trading_date=fund.get("trading_date"),stale=stale,valid=valid,connected=connected,reason=reason,
                        age_seconds=round((now-timestamp)/1000,3) if timestamp else None,
                        timestamp_source="柜台查询回执时间" if native_timestamp else "文件时间（旧快照）",
                        margin_ratio=item["margin"]/equity if valid and item["margin"] is not None and equity is not None and equity>0 else None)
            accounts.append(item)
            def alert(severity, kind, message):
                alerts.append({"severity":severity,"kind":kind,"channel":channel,"currency":str(currency),"message":message})
            if not valid:
                alert("warning","account_unavailable",reason+"；当前资金风险不可计算")
            elif item["available"] is not None and item["available"]<=0:
                alert("error","insufficient_available","该快照可用资金不大于零，新增开仓可能被柜台拒绝")
            elif item["margin_ratio"] is not None and item["margin_ratio"]>=.8:
                alert("warning","high_margin_usage","保证金占权益达到监控关注线80%；这不是柜台强平规则")
        native_events, available, issue=read_native_events(path.with_name(path.name+".risk.jsonl"))
        sources.append({"channel":channel,"risk_events_available":available,"connected":connected,
                        "status_present":bool(status),"trading_date":status.get("trading_date")})
        if issue:
            alerts.append({"severity":"warning","kind":"risk_records_unreadable","channel":channel,"message":issue})
        for event in native_events:
            row={key:event.get(key) for key in ("time","trading_date","type","code","reference","error_code","message","quantity","price")}
            row.update(channel=channel,category=classify_rejection(event))
            # WT error categories and exchange error IDs are different domains.
            broker_code=re.search(r"\((\d+)\)\s*$", str(event.get("message", "")))
            row["broker_error_code"]=int(broker_code.group(1)) if broker_code else None
            rejections.append(row)
    cfg=load_config(folder)
    fees_name=cfg.get("env",{}).get("fees")
    template=fee_rule(read_table(folder/fees_name) if isinstance(fees_name,str) else {}, code, "WT fees.json 配置估算，非柜台费率")
    template["code"]=code
    return {"accounts":accounts,"alerts":alerts,"rejections":sorted(rejections,key=lambda row:row.get("time") or 0)[-100:],
            "fee_template":template,"sources":sources,"read_only":True,"fetched_at":int(now),
            "message":"资金以柜台回执为准；配置费率仅供估算，不执行交易动作"}


def register_trade_risk_routes(app, data_mgr, check_auth, dog=None):
    query_locks={}

    def authorize(request, token, groupid, write=False):
        ok,user=check_auth(request,token)
        if not ok:
            return None,user
        if not (can_operate(user) if write else can_read(user)) or not group_allowed(user,groupid):
            return None,{"result":-403,"message":"没有该组合的操作权限" if write else "没有该组合的查看权限"}
        group=data_mgr.get_group(groupid)
        if not group:
            return None,{"result":-2,"message":"组合不存在"}
        return user,None

    @app.post("/mgr/traderisk", tags=["柜台费用与资金风险"])
    def traderisk(request:Request,groupid:str=Body(...,embed=True),code:str=Body("",embed=True),token:str=Body(None,embed=True)):
        user,error=authorize(request,token,groupid)
        if error:return error
        group=data_mgr.get_group(groupid)
        try:
            snapshot=trade_risk_snapshot(group,data_mgr.get_channels(groupid) or [],code,running=dog.isRunning(groupid) if dog else None)
        except (OSError,ValueError,TypeError) as error:
            return {"result":-2,"message":"柜台风险快照读取失败："+str(error)}
        return {"result":0,**snapshot,"can_query":can_operate(user)}

    @app.post("/mgr/query_trade_funds", tags=["柜台费用与资金风险"])
    async def query_funds(request:Request,groupid:str=Body(...,embed=True),channel:str=Body(...,embed=True),token:str=Body(None,embed=True)):
        user,error=authorize(request,token,groupid,True)
        if error:return error
        group=data_mgr.get_group(groupid)
        if channel not in (data_mgr.get_channels(groupid) or []):
            return {"result":-2,"message":"通道不存在"}
        if dog is not None and not dog.isRunning(groupid):
            return {"result":-2,"message":"请先启动组合；立即读取不会启动进程"}
        folder=Path(group["path"])/"generated/strategy-control"
        status_path=folder/"status.json"
        lock=query_locks.setdefault(groupid,asyncio.Lock())
        if lock.locked():
            return {"result":-3,"message":"已有柜台查询正在等待回执"}
        async with lock:
            try:
                status=json.loads(status_path.read_text(encoding="utf-8-sig"))
                caps=status.get("capabilities",{})
                capable=caps.get("query_fund") if isinstance(caps,dict) else "query_fund" in caps
                if not capable:
                    return {"result":-2,"message":"当前运行程序不支持柜台查询桥，请升级后重启组合"}
                requested=int(time.time()*1000)
                revision=max(requested,int(status.get("query_fund",{}).get("revision",0))+1)
                atomic_json(folder/"query-fund.json",dict(revision=revision,channel=channel,requested_at=requested,expires_at=requested+15000))
                result={"result":-3,"message":"柜台查询回执超时；当前资金仍不可确认","accepted":False,"refreshed":False}
                deadline=time.monotonic()+12
                while time.monotonic()<deadline:
                    await asyncio.sleep(.2)
                    try:
                        status=json.loads(status_path.read_text(encoding="utf-8-sig"))
                    except (OSError,ValueError):
                        continue
                    ack=status.get("query_fund",{})
                    if ack.get("revision")!=revision:continue
                    if not ack.get("accepted"):
                        result.update(message="柜台查询未发出："+str(ack.get("error") or "通道未就绪"))
                        break
                    result["accepted"]=True
                    snap=trade_risk_snapshot(group,[channel],running=dog.isRunning(groupid) if dog else None)
                    fresh=[row for row in snap["accounts"] if row["valid"] and (row["updated_at"] or 0)>=requested]
                    if fresh:
                        result.update(result=0,message="已收到新的柜台资金回执",refreshed=True,accounts=fresh)
                        break
                data_mgr.log_action(user,"query_trade_funds",json.dumps(dict(target=groupid+":"+channel,result=result["result"],accepted=result["accepted"],refreshed=result["refreshed"]),ensure_ascii=False))
                return result
            except (OSError,ValueError,TypeError) as exc:
                return {"result":-3,"message":"柜台查询控制文件不可读："+str(exc)}

"""Persist broker receipts for display only; never make cached funds valid."""
from copy import deepcopy
import json
import math
from pathlib import Path
import threading
import time

from wtpy.StrategyRuntime import atomic_json
from .WorkbenchApi import load_config

_lock=threading.Lock()
FIELDS=("equity","available","margin","fee","prebalance","balance","dynprofit",
        "closeprofit","deposit","withdraw","updated_at","trading_date","currency","channel","timestamp_source")


def _read(path):
    try:return json.loads(path.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError,ValueError):return {}


def _identities(folder):
    import yaml
    cfg=load_config(folder)
    traders=cfg.get("traders",[])
    config=next((folder/name for name in ("config.yaml","config.json") if (folder/name).is_file()),None)
    updated=config.stat().st_mtime*1000 if config else 0
    if isinstance(traders,str):
        path=folder/traders
        traders=yaml.safe_load(path.read_text(encoding="utf-8-sig")).get("traders",[])
        updated=max(updated,path.stat().st_mtime*1000)
    return {row["id"]:([str(row.get(key,"")) for key in ("broker","user")],updated)
            for row in traders if isinstance(row,dict) and row.get("id")}


def _daily(folder,account):
    day=str(account.get("trading_date", ""))
    baseline=_read(folder/"generated/risk-control/baselines"/(day+".json"))
    match=(str(baseline.get("tradingday"))==day and baseline.get("channel")==account["channel"]
           and baseline.get("currency")==account["currency"])
    value=baseline.get("value")
    valid=match and isinstance(value,(int,float)) and math.isfinite(value) and value>0
    resets=[event for event in account.get("capital_events",[]) if event.get("type")=="simnow_reset" and event.get("confirmed_at")]
    latest=max((event["time"] for event in resets),default=0)
    if latest and baseline.get("frozen_at",0)<latest:valid=False
    pnl=account["equity"]-value if valid else None
    return {"valid":valid,"pnl":pnl,"pnl_pct":pnl/value*100 if valid else None,
            "baseline":value if valid else None,"trading_date":account.get("trading_date")}


def account_snapshots(group,channels,accounts,now=None):
    """Capture new native receipts and return explicit historical display rows."""
    now=time.time()*1000 if now is None else now
    folder=Path(group["path"]).resolve()
    identities=_identities(folder)
    target=folder/"generated/account-display-snapshots.json"
    with _lock:
        document=_read(target)
        stored=document.get("accounts",{})
        changed=False
        for account in accounts:
            channel=account["channel"]
            if channel not in channels or channel not in identities:continue
            identity,config_time=identities[channel]
            key=channel+":"+account["currency"]
            previous=stored.get(key,{})
            if previous and previous.get("identity")!=identity:
                stored.pop(key,None);previous={};changed=True
            stamp=account.get("updated_at")
            value=account.get("equity")
            native=(account.get("timestamp_source")=="柜台查询回执时间"
                    and isinstance(stamp,(int,float)) and config_time<=stamp<=now+1000
                    and isinstance(value,(int,float)) and math.isfinite(value))
            if native and stamp>=previous.get("account",{}).get("updated_at",0):
                row={key:account.get(key) for key in FIELDS}
                candidate={"identity":identity,"account":row,"daily":_daily(folder,account)}
                if candidate!=previous:stored[key]=candidate;changed=True
        output=[]
        for value in stored.values():
            row=value.get("account",{})
            channel=row.get("channel")
            if channel not in channels or channel not in identities:continue
            if value.get("identity")!=identities[channel][0]:continue
            result=deepcopy(row)
            result.update(valid=False,historical=True,source="柜台历史快照",
                          daily=deepcopy(value.get("daily",{})))
            output.append(result)
        if changed:
            target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            atomic_json(target,{"schema":1,"accounts":stored})
        return output

"""Run an installed, account-owned C++ CTA factory against frozen option history."""
import csv
import json
import os
import re
import shutil
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import Body, Request
from .AssetMarkets import asset_root, inside
from .Roles import can_admin


def manifest(path):
    return json.loads((path/"manifest.json").read_bytes())


def register(app, deploy, authorize, catalog, get_monitor, lock):
    root = asset_root(deploy, "option")

    def owner_dir(user):
        if not re.fullmatch(r"[\w.@-]+",user["loginid"]) or user["loginid"] in (".",".."):
            raise ValueError("无效账户")
        return inside(root, "factories", user["loginid"])

    @app.post("/mgr/assets/option/factories")
    def factories(request: Request):
        user, error = authorize(request, "option", research=True)
        if error:return error
        rows = [manifest(p.parent) for p in sorted(owner_dir(user).glob("*/manifest.json"))]
        return {"result":0, "can_run":can_admin(user), "factories":[{k:r[k] for k in
                ("id","title","version","start","end","codes","fees")} for r in rows]}

    @app.post("/mgr/assets/option/factory-run")
    def launch(request: Request, factory_id:str=Body(...), version:int=Body(...),
               start:date=Body(...), end:date=Body(...), capital:float=Body(...,gt=0,le=1e12),
               units:int=Body(1,ge=1,le=5)):
        user, error=authorize(request,"option",research=True)
        if error:return error
        if not can_admin(user):return {"result":-403,"message":"仅管理员可运行已安装的策略"}
        try:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}",factory_id):
                raise ValueError("工厂编号无效")
            package=inside(owner_dir(user),factory_id)
            spec=manifest(package)
            if spec["version"]!=version or spec["id"]!=factory_id:
                raise ValueError("工厂版本已变更，请刷新")
            if not date.fromisoformat(spec["start"])<=start<end<=date.fromisoformat(spec["end"]):
                raise ValueError("区间超出此版本的历史样本")
            rows=catalog(root,"option")
            available={r["code"]:r for r in rows if r["profileid"]==spec["profile"] and r["period"]=="d"}
            legs=[available[code] for code in spec["codes"]]
            if spec.get("multiplier") and any(r.get("multiplier")!=spec["multiplier"] for r in legs):
                raise ValueError("合约乘数与已安装的策略不符")
            owner=user["loginid"]; monitor=get_monitor("option")
            with lock:
                stra=monitor.add_strategy(owner, spec["title"]+" · C++ v"+str(version),"cta")["id"]
                try:
                    task=monitor.run_backtest(owner,stra,int(start.strftime("%Y%m%d"))*10000,
                        int(end.strftime("%Y%m%d"))*10000+2359,capital,fill_model="legacy_cta",
                        dataset=legs[0]["id"],catalog=rows,companions=[r["id"] for r in legs[1:]],
                        factory=(package,units))
                except Exception:
                    monitor.del_strategy(owner,stra)
                    raise
            return {"result":0,"task":task}
        except (OSError,KeyError,ValueError) as exc:
            return {"result":-2,"message":str(exc) if isinstance(exc,ValueError) else "工厂或对应的历史合约不可用"}


def freeze(selection, folder, config, task, start, end):
    package,units=selection
    spec=manifest(package)
    for name in ("factory.so","frames.csv","manifest.json"):
        shutil.copyfile(inside(package,name), folder/name)
    cutoff=date.fromisoformat(f"{str(end)[:4]}-{str(end)[4:6]}-{str(end)[6:8]}")-timedelta(days=4)
    params={**spec["params"],"frames":str(folder/"frames.csv"),"exit_date":int(cutoff.strftime("%Y%m%d")),"units":units}
    config["cta"]=dict(name=task,module=str(folder/"factory.so"),model="legacy_cta",
                       strategy=dict(name=spec["id"],id=task,params=params))
    config["replayer"].update(stime=start,etime=end)


def ledger(folder, task):
    spec=manifest(folder);out=folder/"outputs_bt"/task
    def records(name):
        with (out/name).open(encoding="utf-8-sig",newline="") as f:return list(csv.DictReader(f))
    legs={code:dict(code=code,trades=0,buys=0,sells=0,fee=Decimal(0),position=0) for code in spec["codes"]}
    for trade in records("trades.csv"):
        row=legs[trade["code"]]
        buy={("LONG","OPEN"):True,("SHORT","CLOSE"):True,("SHORT","OPEN"):False,("LONG","CLOSE"):False}[trade["direct"],trade["action"]]
        qty=Decimal(trade["qty"])
        if qty<=0 or qty!=int(qty):raise ValueError("原生整手成交数量异常")
        row["trades"]+=1
        row["buys" if buy else "sells"]+=int(qty)
        row["position"]+=int(qty) if buy else -int(qty)
        row["fee"]+=Decimal(trade["fee"])
    funds=records("funds.csv")
    if not funds:raise ValueError("原生回测无日结记录")
    last=funds[-1]
    final={r["code"]:Decimal(r["volume"]) for r in records("positions.csv") if r["date"]==last["date"]}
    if set(final)-set(legs) or any(r["position"]!=final.get(c,0) for c,r in legs.items()):
        raise ValueError("逐笔净仓与原生期末持仓不一致")
    if abs(sum(r["fee"] for r in legs.values())-Decimal(last["fee"]))>Decimal("0.01"):
        raise ValueError("逐笔手续费与原生资金账不一致")
    units=json.loads((folder/"configbt.json").read_bytes())["cta"]["strategy"]["params"]["units"]
    return dict(day=last["date"],factory=spec["id"],title=spec["title"],version=spec["version"],fees=spec["fees"],units=units,
                legs=[dict(r,fee=float(r["fee"])) for r in legs.values()])


def replay(task, capital):
    runtime=Path(os.environ.get("WT_BACKTEST_RUNTIME","/opt/wt/cloud/backtest-runtime.json"))
    if runtime.is_file():os.environ["WTPY_BT_PORTER_LIB"]=json.loads(runtime.read_bytes())["bt_porter_lib"]
    from wtpy import EngineType,WtBtEngine
    from wtpy.apps import WtBtAnalyst
    engine=WtBtEngine(EngineType.ET_CTA,"logcfgbt.json")
    try:
        engine.init("./","configbt.json")
        engine.run_backtest()
        out=Path("outputs_bt")/task
        if json.loads((out/"btenv.json").read_bytes()).get("progress")!=100:
            raise RuntimeError("C++ 策略回放未完成")
        result=ledger(Path.cwd(),task)
        analyst=WtBtAnalyst()
        analyst.add_strategy(task,folder="./outputs_bt/",init_capital=capital,rf=.02,annual_trading_days=240)
        analyst.run_simple()
        (out/"factory-ledger.json").write_text(json.dumps(result,ensure_ascii=False),encoding="utf8")
    finally:
        engine.stop_backtest()
        engine.release_backtest()

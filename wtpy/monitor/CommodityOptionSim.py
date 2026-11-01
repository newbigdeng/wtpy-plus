"""Run a pinned WT strategy against the SimNow test counter in its own workspace."""
import argparse
import importlib.util
import json
import os
import tempfile
from datetime import date
from pathlib import Path

from .AssetMarkets import asset_root, dataset_code, inside, instrument
from .MarketSubscriptions import _load
from .StrategyLibrary import StrategyLibrary


SIM_FRONTS = {
    "tcp://182.254.243.31:30003": ("tcp://182.254.243.31:30013", "session"),
    "tcp://182.254.243.31:40001": ("tcp://182.254.243.31:40011", "simulation"),
}


def simnow_profile(path):
    traders = [t for t in _load(Path(path)).get("traders", []) if t.get("active")]
    if len(traders) != 1:
        raise ValueError("请选择只包含一个活动 SimNow 通道的配置")
    row = traders[0]
    if (row.get("module") != "TraderCTP" or str(row.get("broker")) != "9999"
            or row.get("front") not in SIM_FRONTS):
        raise ValueError("期权模拟仅允许已登记的 SimNow CTP 仿真前置")
    if not all(row.get(k) for k in ("user", "pass", "appid", "authcode")):
        raise ValueError("SimNow 配置缺少账户或认证字段")
    return {key: str(row[key]) for key in ("broker","user","pass","appid","authcode","front")}


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", help="例如 DCE.m2701-C-3000")
    parser.add_argument("strategy", help="期权策略库 ID")
    parser.add_argument("version", type=int)
    parser.add_argument("--also", action="append", default=[], metavar="OPTION", help="同一策略的其他商品期权")
    parser.add_argument("--hedge-underlyings", action="store_true", help="加入这些期权对应的标的期货")
    parser.add_argument("--analysis-session", help="策略读取的同环境分析会话")
    parser.add_argument("--simnow-profile", default=os.getenv("WT_OPTION_SIM_PROFILE"),
                        help="服务器上现有的 SimNow 交易通道文件")
    parser.add_argument("--start", action="store_true", help="启动 SimNow 模拟策略")
    parser.add_argument("--deploy", default=os.environ.get("WT_DATA_DIR", "/data"))
    args = parser.parse_args()
    account = simnow_profile(args.simnow_profile) if args.simnow_profile else None
    tdfront = account["front"] if account else "tcp://182.254.243.31:40001"
    mdfront, mode = SIM_FRONTS[tdfront]
    root = asset_root(args.deploy, "option")
    common = inside(root, "common")
    contracts = _load(inside(root, "common", "contracts.json"))
    options = list(dict.fromkeys([args.symbol,*args.also]))
    selected, codes, hedges = {}, [], []
    for symbol in options:
        exchange, ticker = instrument("option",symbol)
        contract = contracts.get(exchange, {}).get(ticker, {})
        code = dataset_code("option",exchange,ticker,contract)
        if "." in ticker or not code or int(contract.get("expiredate") or 0)<=int(date.today().strftime("%Y%m%d")):
            raise ValueError("须选择已登记、未到期的整数行权价商品期权")
        selected.setdefault(exchange,{})[ticker] = contract
        codes.append(code)
        if args.hedge_underlyings:
            future = contract["option"]["underlying"]
            info = contracts[exchange].get(future)
            if not info or not dataset_code("futures",exchange,future,info):
                raise ValueError("期权工作区的柜台资料缺少标的期货 "+future)
            selected[exchange][future] = info
            month_code = code.split(".")[1]
            hedge = exchange+"."+month_code[:-4]+"."+month_code[-4:]
            if hedge not in hedges:
                hedges.append(hedge)
    params = {"code":codes[0]}
    if args.also:
        params["codes"] = codes
    if args.hedge_underlyings:
        params["underlyings"] = hedges
    if args.analysis_session:
        from .OptionFrames import read_option_frame
        def check_frame():
            return read_option_frame(root,args.analysis_session,mode=mode,symbols=options)
        check_frame()
        params["analysis_session"] = args.analysis_session
    lib = StrategyLibrary(inside(root, "strategies"))
    row = lib.get(args.strategy, args.version, allow_archived=False)
    if row["engine_type"] not in ("CTA","HFT"):
        raise ValueError("模拟柜台支持 CTA 或 HFT 库策略")
    work = inside(root, "run", args.strategy)
    bases = {key:str(inside(root, "common", name + ".json")) for key,name in
             (("commodity","commodities"),("session","sessions"),("holiday","holidays"))}
    fees = inside(root, "common", "fees.json")
    for file in (*bases.values(), fees, inside(root,"common","actpolicy.yaml")):
        if not Path(file).is_file():
            raise ValueError("缺少期权运行资料：" + Path(file).name)
    supported = {p["name"] for p in row["parameters"]}
    if not row["accepts_kwargs"] and not params.keys()<=supported:
        raise ValueError("策略构造函数缺少参数：" + ", ".join(sorted(params.keys()-supported)))
    print(f"SimNow · {', '.join(codes+hedges)} · {row['name']} v{args.version} · {row['engine_type']}", flush=True)
    if not args.start:
        return
    from wtpy import WtEngine, EngineType
    from wtpy.StrategyRuntime import atomic_json
    work.mkdir(parents=True, exist_ok=True)
    os.chdir(work)
    with (work/"runner.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if account is None:
            account = dict(broker="9999", user=os.environ["WT_OPTION_SIM_USER"], **{"pass":os.environ["WT_OPTION_SIM_PASSWORD"]},
                           appid=os.environ["WT_OPTION_SIM_APPID"], authcode=os.environ["WT_OPTION_SIM_AUTHCODE"], front=tdfront)
        trader = dict(**account, id="option_sim", active=True, module="TraderCTP")
        feed = dict(broker=account["broker"], user=account["user"], **{"pass":account["pass"]},
                    id="option_md", active=True, module="ParserCTP", front=mdfront,
                    code=",".join(ex+"."+ticker for ex,items in selected.items() for ticker in items))
        atomic_json(work/"contract.json", selected)
        atomic_json(work/"log.json", {"root":{"level":"info","sinks":[{"type":"console_sink"}]}})
        kind = row["engine_type"]
        config = dict(basefiles={**bases,"contract":str(work/"contract.json"),"utf-8":True},
                      env=dict(name=kind.lower(), mode="product", fees=str(fees), product={"session":"TRADING"}),
                      data={"store":{"path":str(inside(root,"storage"))}}, parsers=[feed], traders=[trader],
                      bspolicy=str(common/"actpolicy.yaml"))
        if kind == "CTA":
            config["routers"] = [{"strategy":row["id"],"executer":"option_exec"}]
            config["executers"] = [dict(id="option_exec",name="local",trader="option_sim",active=True,local=True,scale=1,
                strict_sync=False,policy={"default":{"name":"WtExeFact.WtMinImpactExeUnit","lots":1,"byrate":False,
                                                     "offset":0,"expire":5,"pricemode":1,"span":500}})]
        file = lib.root/row["id"]/"versions"/str(row["version"])/"strategy.py"
        spec = importlib.util.spec_from_file_location("option_strategy", file)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        strategy = getattr(module,row["class_name"])(name=row["id"],**params)
        engine = WtEngine(EngineType.ET_CTA if kind=="CTA" else EngineType.ET_HFT, str(work/"log.json"))
        try:
            with tempfile.NamedTemporaryFile(mode="w",dir=work,suffix=".json",encoding="utf-8") as stream:
                json.dump(config,stream);stream.flush()
                engine.init(str(common),stream.name)
            if kind == "CTA":
                context = engine.add_cta_strategy(strategy)
            else:
                context = engine.add_hft_strategy(strategy,"option_sim")
            if args.analysis_session:
                from .OptionFrames import guard_option_orders
                guard_option_orders(context,kind,check_frame,set(codes+hedges))
            engine.run(False)
        finally:
            engine.release()


if __name__ == "__main__":
    main()

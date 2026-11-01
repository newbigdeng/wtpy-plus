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


def main():
    import fcntl
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", help="例如 DCE.m2701-C-3000")
    parser.add_argument("strategy", help="期权策略库 ID")
    parser.add_argument("version", type=int)
    parser.add_argument("--start", action="store_true", help="启动专用 SimNow 模拟账户")
    parser.add_argument("--deploy", default=os.environ.get("WT_DATA_DIR", "/data"))
    args = parser.parse_args()
    root = asset_root(args.deploy, "option")
    exchange, ticker = instrument("option", args.symbol)
    common = inside(root, "common")
    contracts = _load(inside(root, "common", "contracts.json"))
    contract = contracts.get(exchange, {}).get(ticker, {})
    code = dataset_code("option", exchange, ticker, contract)
    expiry = int(contract.get("expiredate") or 0)
    if "." in ticker:
        raise ValueError("当前 WT 原生交易适配器未支持小数行权价合约")
    if not code or expiry <= int(date.today().strftime("%Y%m%d")):
        raise ValueError("须选择有到期日且未到期的商品期权")
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
    print(f"SimNow · {code} · {row['name']} v{args.version} · {row['engine_type']}", flush=True)
    if not args.start:
        return
    from wtpy import WtEngine, EngineType
    from wtpy.StrategyRuntime import atomic_json
    work.mkdir(parents=True, exist_ok=True)
    os.chdir(work)
    with (work/"runner.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        # Use a dedicated simulated login. Endpoints cannot be redirected to a live broker.
        login = dict(broker="9999", user=os.environ["WT_OPTION_SIM_USER"], **{"pass":os.environ["WT_OPTION_SIM_PASSWORD"]})
        trader = dict(**login, id="option_sim", active=True, module="TraderCTP", front="tcp://182.254.243.31:40001",
                      appid=os.environ["WT_OPTION_SIM_APPID"], authcode=os.environ["WT_OPTION_SIM_AUTHCODE"])
        feed = dict(**login, id="option_md", active=True, module="ParserCTP", front="tcp://182.254.243.31:40011", code=args.symbol)
        atomic_json(work/"contract.json", {exchange:{ticker:contract}})
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
        strategy = getattr(module,row["class_name"])(name=row["id"],code=code)
        engine = WtEngine(EngineType.ET_CTA if kind=="CTA" else EngineType.ET_HFT, str(work/"log.json"))
        try:
            with tempfile.NamedTemporaryFile(mode="w",dir=work,suffix=".json",encoding="utf-8") as stream:
                json.dump(config,stream);stream.flush()
                engine.init(str(common),stream.name)
            if kind == "CTA":
                engine.add_cta_strategy(strategy)
            else:
                engine.add_hft_strategy(strategy,"option_sim")
            engine.run(False)
        finally:
            engine.release()


if __name__ == "__main__":
    main()

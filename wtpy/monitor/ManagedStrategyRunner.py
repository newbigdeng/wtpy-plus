"""Fixed live entry for plans published by the monitor.

User modules are imported only in this child process, never by an HTTP handler.
"""
from copy import deepcopy
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import threading


def native_config(base, plan, root):
    """Use native strategy routing: one execution adapter per CTA instance."""
    config = deepcopy(base)
    kind = plan["engine_type"]
    config.setdefault("env", {})["name"] = kind.lower()
    config["strategies"] = {}
    config["executers"] = []
    config["routers"] = []
    used = {row["trader"] for row in plan["strategies"] if row["enabled"]}
    config["traders"] = [row for row in config.get("traders", []) if row.get("id") in used]
    for trader in config["traders"]:
        trader["savedata"] = True
    for row in plan["strategies"]:
        if not row["enabled"] or kind == "HFT":
            continue
        identifier = "managed-" + row["id"]
        config["executers"].append({"active": True, "id": identifier,
            "name": "local", "trader": row["trader"], "scale": 1,
            "poolsize": 0, "strict_sync": False,
            "policy": {"default": {"name": row["execution"]["native"],
                **row["execution"]["params"]}}})
        config["routers"].append({"strategy": row["id"], "executer": identifier})
    config["notifier"] = {"active": True, "url": "ipc://" + str(root / "monitor.ipc")}
    return config


def register_plan(engine, plan, directory):
    """Register via WtEngine's existing Python context factories."""
    for row in plan["strategies"]:
        if not row["enabled"]:
            continue
        name = "managed_strategy_" + row["id"]
        path = directory / "strategies" / (row["id"] + ".py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        strategy = getattr(module, row["class_name"])(name=row["id"], **row["params"])
        if strategy.name() != row["id"]:
            raise ValueError("策略实例名称必须保持为计划中的ID")
        if plan["engine_type"] == "CTA":
            engine.add_cta_strategy(strategy, slippage=row["slippage"])
        elif plan["engine_type"] == "HFT":
            engine.add_hft_strategy(strategy, row["trader"],
                agent=row["agent"], slippage=row["slippage"])
        else:
            raise ValueError("页面托管交易组合目前支持CTA和HFT")


def main(entry):
    import fcntl
    from wtpy import WtEngine, EngineType
    from wtpy.StrategyRuntime import attach_strategy_control
    from wtpy.monitor.RiskRuntime import attach_risk, live_account_provider

    root = Path(entry).resolve().parent
    os.chdir(root)
    state = root / "managed-strategies"
    with (state / "runner.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        revision = json.loads((state / "applied.json").read_text(encoding="utf-8"))["revision"]
        directory = state / "revisions" / revision
        plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
        if not plan["trading_enabled"]:
            raise RuntimeError("组合尚未启用交易授权；请在策略管理中启用并应用，保存不会启动")
        if not any(row["enabled"] for row in plan["strategies"]):
            raise RuntimeError("组合没有启用的策略")
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        signal.signal(signal.SIGINT, lambda *_: stop.set())
        kind = getattr(EngineType, "ET_" + plan["engine_type"])
        # The native porter prefixes logCfg with its current working directory.
        engine = WtEngine(kind, str((directory / "logcfg.json").relative_to(root)), "generated")
        try:
            engine.init("", str(directory / "config.json"))
            engine.commitConfig()
            attach_strategy_control(engine, root / "generated/strategy-control")
            engine._risk_channels = {row["id"]: row["trader"] for row in plan["strategies"]}
            attach_risk(engine, plan.get("risk"), output=root / "generated/risk-events.jsonl",
                        account_provider=live_account_provider(root, engine.query_trade_account))
            register_plan(engine, plan, directory)
            engine.run()
            stop.wait()
        finally:
            engine.release()

"""Execute a frozen continuous CTA task in a fresh native process."""

import csv
import inspect
import json
import os
from pathlib import Path
import sys
import traceback
from .AssetMarkets import asset_root
from .MainReplay import prepare
from .MainStrategy import ContinuousStrategy
from .PlusReplayRunner import CheckedStrategy
from .PlusBacktest import write_json


def run(folder, task):
    root = asset_root(os.environ.get("WT_DATA_DIR", "/data"), task["asset"])
    signal, plan = prepare(folder, root, task)
    if task.get("model") == "plus_tick":
        from .MainPlus import run_plus

        run_plus(folder, task, signal, plan)
        return
    runtime = Path(
        os.getenv("WT_BACKTEST_RUNTIME", "/opt/wt/cloud/backtest-runtime.json")
    )
    if runtime.is_file():
        os.environ["WTPY_BT_PORTER_LIB"] = json.loads(runtime.read_bytes())[
            "bt_porter_lib"
        ]
    from wtpy import WtBtEngine, EngineType, BaseCtaStrategy
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "wt_main_strategy", folder / "MyStrategy.py"
    )
    source = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(source)
    classes = [
        c
        for c in vars(source).values()
        if inspect.isclass(c)
        and c.__module__ == source.__name__
        and issubclass(c, BaseCtaStrategy)
    ]
    if len(classes) != 1:
        raise ValueError("逐 K 连续回测仅支持单一 WT Python CTA 策略")
    params = dict(
        task.get("params", {}),
        name=task["id"],
        code=signal,
        period=task["period"],
        capital=task["capital"],
    )
    sig = inspect.signature(classes[0])
    if not any(
        p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    ):
        if not {"code", "period"} <= sig.parameters.keys():
            raise ValueError("连续策略构造函数须接收 code 和 period")
        params = {k: v for k, v in params.items() if k in sig.parameters}
    option_tick = task.get("model") == "option_tick"
    if option_tick and (
        not runtime.is_file()
        or not json.loads(runtime.read_bytes()).get("multi_period_replay")
    ):
        raise ValueError("期权逐 Tick 移仓需要多合约原生回放库")
    bridge = ContinuousStrategy(classes[0](**params), signal, plan, task["period"])
    bridge.subscribe_ticks = option_tick
    strategy = CheckedStrategy(bridge)
    base = folder / "market-base"
    files = {
        k: str(base / (f + ".json"))
        for k, f in (
            ("commodity", "commodities"),
            ("session", "sessions"),
            ("contract", "contracts"),
            ("holiday", "holidays"),
            ("hot", "hots"),
        )
    }
    cfg = dict(
        env={"mocker": "cta"},
        replayer=dict(
            mode="bin",
            tick=option_tick,
            dont_simtick_if_notrade=True,
            bar_simulation="close_only",
            fees=str(base / "fees.json"),
            basefiles=files,
            store=dict(path=str(folder / "market-data")),
        ),
    )
    write_json(folder / "configbt.json", cfg)
    write_json(
        folder / "logcfgbt.json",
        {
            "root": {
                "level": "info",
                "sinks": [{"type": "console_sink", "pattern": "%v"}],
            }
        },
    )
    engine = WtBtEngine(EngineType.ET_CTA, "logcfgbt.json")
    try:
        engine.init(str(base), "configbt.json")
        engine.configBacktest(
            task["replay_start"], int(task["end"].replace("-", "") + "2359")
        )
        engine.commitBTConfig()
        engine.set_cta_strategy(
            strategy, fill_model=task.get("fill_model", "causal_touch") if option_tick else "legacy_cta",
            event_delay=task.get("event_delay", 0) if option_tick else 0,
            participation_rate=task.get("participation_rate", 0) if option_tick else 0,
        )
        engine.run_backtest()
        if strategy.errors:
            raise ValueError(strategy.errors[0])
        out = folder / "outputs_bt" / task["id"]
        if not (out / "funds.csv").is_file() or not strategy.counts.get("on_calculate"):
            raise ValueError("原生策略未完成有效计算")
        with (out / "trades.csv").open() as f:
            if any(r["code"] == signal for r in csv.DictReader(f)):
                raise ValueError("信号指数不允许产生真实成交")
        task.update(
            calculations=strategy.counts["on_calculate"],
            future_bars=strategy.future_bars,
            model="期权实录 Tick · " + task.get("fill_model", "causal_touch") if option_tick else "WT CTA 闭合 K 线",
        )
    finally:
        engine.release_backtest()


if __name__ == "__main__":
    folder = Path(sys.argv[1]).resolve()
    os.chdir(folder)
    task = json.loads((folder / "task.json").read_bytes())
    try:
        if task.get("preview"):
            from .MainHistory import product_history, segments

            root = asset_root(os.getenv("WT_DATA_DIR", "/data"), task["asset"])
            _, plan = product_history(root, task["product"], task["start"], task["end"])
            task["segments"] = segments(plan)
        else:
            run(folder, task)
        task["status"] = "completed"
    except Exception as err:
        task.update(status="failed", error=str(err))
        traceback.print_exc()
    write_json(folder / "task.json", task)
    raise SystemExit(task["status"] != "completed")

"""On-demand quotes and asset-local native backtest workspaces."""
import asyncio
from contextlib import aclosing, suppress
from datetime import datetime
from importlib.util import find_spec
import json
import os
from pathlib import Path
import sys
import tempfile
from threading import Lock

from fastapi import Body, Request
from starlette.responses import StreamingResponse

from .AssetMarkets import ASSETS, asset_root, inside, instrument
from .AssetQuotes import http_quote
from .HistoryCatalog import dataset_entries, public_dataset, selection_bounds
from .Roles import can_read, can_admin, can_research_read
from .StrategyLibrary import register_library_routes
from .WtBtMon import WtBtMon


def asset_catalog(root, asset):
    if asset == "futures":
        raise ValueError("请使用期货工作区的数据目录")
    if asset == "crypto":
        from .CryptoArchive import catalog
        return catalog(root)
    profile = {"id": "local", "root": str(root), "datahub_id": asset + "-collector"}
    return dataset_entries(root, profiles_by_group={asset: [profile]}, asset=asset)


async def ctp_quotes(root, exchange, ticker, mode):
    # Each native API owns its flow files. Separate directories also isolate concurrent tabs.
    flows = inside(root, "quotes")
    flows.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=flows) as folder:
        python = os.environ.get("WT_QUOTE_CTP_PYTHON") or sys.executable
        process = await asyncio.create_subprocess_exec(python, "-u",
            str(Path(__file__).with_name("AssetCtpQuote.py")), exchange, ticker, mode, cwd=folder,
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
        try:
            while True:
                try:
                    line = await asyncio.wait_for(process.stdout.readline(), timeout=8)
                except asyncio.TimeoutError:
                    yield {"heartbeat": True}
                    continue
                if not line:
                    yield {"error": "CTP 行情子进程已退出，请检查行情 SDK 与前置配置"}
                    return
                if line.startswith(b"{"):
                    yield json.loads(line)
        finally:
            # The ASGI disconnect cancels the producer. Reap the native API even then.
            process.stdin.close()
            async def reap():
                try:
                    await asyncio.wait_for(process.wait(), 2)
                except asyncio.TimeoutError:
                    with suppress(ProcessLookupError):
                        process.kill()
                    await process.wait()
            import anyio
            with anyio.CancelScope(shield=True):
                await reap()


def register_asset_routes(app, data_mgr, check_auth, deploy):
    monitors, libraries = {}, {}
    lock = Lock()
    for asset in ("stock", "option", "crypto"):
        libraries[asset] = register_library_routes(app, data_mgr, check_auth, deploy, asset=asset)

    def authorize(request, asset, *, research=False):
        ok, user = check_auth(request, None)
        if not ok:
            return None, user
        if (user.get("role") == "guest" or user.get("products") or not can_read(user) or
                (research and not can_research_read(user))):
            return None, {"result": -403, "message": "此操作需要已授权的工作区账户"}
        if asset not in ASSETS:
            return None, {"result": -2, "message": "未知资产类别"}
        return user, None

    @app.post("/mgr/assets/{asset}/quote")
    async def quote(request: Request, asset: str, symbol: str = Body(..., embed=True),
                    mode: str = Body("session", embed=True)):
        _, error = await asyncio.to_thread(authorize, request, asset)
        if error:
            return error
        try:
            exchange, ticker = instrument(asset, symbol)
            if asset == "crypto" and os.environ.get("WT_CRYPTO_BROWSER") == "1":
                return {"result": 0, "client_feed": "coinbase", "product": ticker}
            if asset in ("stock", "crypto"):
                value = await asyncio.to_thread(http_quote, asset, symbol)
                return {"result": 0, "quote": value}
            if mode not in ("session", "simulation"):
                raise ValueError("未知的 CTP 行情环境")
            if not os.environ.get("WT_QUOTE_CTP_PYTHON") and find_spec("openctp_ctp") is None:
                raise ValueError("看盘服务缺少 openctp-ctp 行情 SDK")
        except (ValueError, OSError, KeyError) as exc:
            message = str(exc) if type(exc) is ValueError else "行情源未返回有效快照"
            return {"result": -2, "message": message}

        async def stream():
            stream = ctp_quotes(asset_root(deploy, asset), exchange, ticker, mode)
            yield json.dumps({"status": "已请求交易时段前置，等待行情" if mode=="session" else "已请求 SimNow 仿真前置，等待行情"}) + "\n"
            async with aclosing(stream):
                async for event in stream:
                    yield json.dumps(event, ensure_ascii=True) + "\n"

        return StreamingResponse(stream(), media_type="application/x-ndjson",
                                 headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no", "Content-Encoding": "identity"})

    @app.post("/mgr/assets/option/chain")
    def option_chain(request: Request, underlying: str = Body(..., embed=True)):
        _, error = authorize(request, "option")
        if error:
            return error
        try:
            from .OptionChain import chain
            return {"result": 0, **chain(asset_root(deploy, "option"), underlying)}
        except (OSError, KeyError, ValueError) as exc:
            return {"result": -2, "message": str(exc) if isinstance(exc,ValueError) else "无法读取期权合约表"}

    def get_monitor(asset):
        with lock:
            if asset not in monitors:
                root = asset_root(deploy, asset)
                runs = inside(root, "backtests")
                template = inside(root, "backtests", "template")
                template.mkdir(parents=True, exist_ok=True)
                # Only asset datasets can be passed. Their frozen basefiles replace these at launch.
                defaults = {"configbt.json": {"env": {}, "replayer": {"basefiles": {}},
                            "notifier": {"active": True, "url": "$MQURL$"}},
                            "logcfgbt.json": {"root": {"level": "info", "sinks": [{"type": "console_sink"}]}},
                            "fees.json": {}}
                for name, value in defaults.items():
                    file = inside(root, "backtests", "template", name)
                    if not file.exists():
                        file.write_text(json.dumps(value), encoding="utf-8")
                monitors[asset] = WtBtMon(str(runs))
            return monitors[asset]

    @app.post("/mgr/assets/{asset}/history")
    def history(request: Request, asset: str):
        _, error = authorize(request, asset)
        if error:
            return error
        try:
            rows = asset_catalog(asset_root(deploy, asset), asset)
            return {"result": 0, "datasets": [public_dataset(row) for row in rows]}
        except (OSError, KeyError, ValueError):
            return {"result": -2, "message": "无法读取该资产的数据目录与合约配置"}

    @app.post("/mgr/assets/{asset}/runs")
    def runs(request: Request, asset: str):
        user, error = authorize(request, asset, research=True)
        if error:
            return error
        if asset not in libraries:
            return {"result": -2, "message": "请使用期货回测页面"}
        monitor, owner = get_monitor(asset), user["loginid"]
        tasks = [dict(item, strategy=row["name"]) for row in monitor.get_strategies(owner)
                 for item in (monitor.get_backtests(owner, row["id"]) or [])]
        return {"result": 0, "runs": tasks}

    @app.post("/mgr/assets/{asset}/detail")
    def detail(request: Request, asset: str, strategy: str = Body(...), task: str = Body(...)):
        user, error = authorize(request, asset, research=True)
        if error:
            return error
        if asset not in libraries:
            return {"result": -2, "message": "请使用期货回测页面"}
        monitor, owner = get_monitor(asset), user["loginid"]
        # Resolve both IDs through the owner's inventory before any filesystem lookup.
        rows = monitor.get_strategies(owner)
        if not any(row["id"] == strategy for row in rows) or not any(
                row["id"] == task for row in monitor.get_backtests(owner, strategy) or []):
            return {"result": -2, "message": "本账户没有该任务"}
        result = monitor.get_backtest_detail(owner, strategy, task)
        return {"result": 0, "detail": {key:result[key] for key in ("status","returncode","summary","error","state")}}

    @app.post("/mgr/assets/{asset}/run")
    def run(request: Request, asset: str, library_id: str = Body(...), version: int = Body(...),
            dataset: str = Body(...), start: str = Body(...), end: str = Body(...),
            capital: float = Body(..., gt=0, le=1e12)):
        user, error = authorize(request, asset, research=True)
        if error:
            return error
        if asset not in libraries or not can_admin(user):
            return {"result": -403, "message": "仅管理员可执行此资产的库策略"}
        try:
            begin, finish = [int(datetime.strptime(day, "%Y-%m-%d").strftime("%Y%m%d")) for day in (start, end)]
            catalog = asset_catalog(asset_root(deploy, asset), asset)
            item = next((row for row in catalog if row["id"] == dataset), None)
            if not item:
                raise ValueError("该资产没有所选数据")
            selection_bounds(item, begin, finish)
            if item["period"] == "trades":
                raise ValueError("成交记录无买卖盘口，请选择分钟线回测")
            if "fee" not in item["basefiles_map"] or not all(Path(f).is_file() for f in item["basefiles_map"].values()):
                raise ValueError("请补齐该资产的合约、交易时段、交易日历与费用文件")
            source = libraries[asset].get(library_id, version, allow_archived=False)
            kind = source["engine_type"].lower()
            if kind == "hft" and item["period"] != "tick":
                raise ValueError("HFT 回测需要该合约的 Tick 数据")
            if asset == "option" and "." in item["ticker"]:
                raise ValueError("当前 WT 原生回放未支持小数行权价合约")
            if asset == "option" and (not item.get("expiry") or finish >= item["expiry"]):
                raise ValueError("商品期权回放须在到期日前结束，当前模型未处理行权交割")
            monitor, owner = get_monitor(asset), user["loginid"]
            with lock:
                straid = monitor.add_strategy(owner, source["name"] + " v" + str(version), kind)["id"]
                try:
                    if not monitor.set_strategy_code(owner, straid, source["source"]):
                        raise ValueError("策略副本写入失败")
                    fill = "legacy_cta" if kind == "cta" and item["period"] != "tick" else "causal_touch"
                    task = monitor.run_backtest(owner, straid, begin * 10000, finish * 10000 + 2359,
                                                capital, fill_model=fill, dataset=dataset, catalog=catalog)
                except Exception:
                    monitor.del_strategy(owner, straid)
                    raise
            return {"result": 0, "task": task}
        except (ValueError, OSError) as exc:
            return {"result": -2, "message": str(exc) if isinstance(exc, ValueError) else "回测工作区无法读写"}

    return monitors

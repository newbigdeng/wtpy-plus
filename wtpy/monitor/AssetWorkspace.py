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
import secrets
import shutil

from .OptionDeskFeed import DeskRequest, desk_plan

from fastapi import Body, Request
from starlette.responses import StreamingResponse

from .AssetMarkets import ASSETS, asset_root, inside, instrument
from .AssetQuotes import http_quote
from . import CollectorControl
from .HistoryCatalog import dataset_entries, public_dataset, selection_bounds, native_worker
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
    profiles = [profile]
    if asset == "option":
        archive = inside(root, "history", "native")
        for folder in sorted(archive.glob("*/datahub/catalog.json")):
            source = folder.parents[1]
            profiles.append(dict(id="expiry-"+source.name, root=str(inside(archive, source.name)),
                                 label="历史导入 "+source.name, datahub_id="archive"))
    return dataset_entries(root, profiles_by_group={asset: profiles}, asset=asset)


async def ctp_quotes(root, exchange, ticker, mode):
    # Each native API owns its flow files. Separate directories also isolate concurrent tabs.
    flows = inside(root, "quotes")
    flows.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=flows) as folder:
        async with aclosing(worker_events(folder,"AssetCtpQuote.py",(exchange,ticker,mode))) as events:
            async for event in events:
                yield event


async def worker_events(folder, script, args=(), plan=None):
    python = os.environ.get("WT_QUOTE_CTP_PYTHON") or sys.executable
    process = await asyncio.create_subprocess_exec(python, "-u",
        str(Path(__file__).with_name(script)), *args, cwd=folder, limit=1024*1024,
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    try:
        if plan is not None:
            process.stdin.write(json.dumps(plan).encode()+b"\n")
            await process.stdin.drain()
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=8)
            except asyncio.TimeoutError:
                yield {"heartbeat": True}
                continue
            if not line:
                yield {"error": "行情进程已退出，请检查行情和定价依赖"}
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
    analysis_slots = asyncio.Semaphore(2)
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

    @app.post("/mgr/assets/{asset}/collector")
    def collector(request: Request, asset: str, action: str = Body("status"),
                  symbols: list[str] = Body(default=[], max_length=96), revision: int = Body(0)):
        user, error = authorize(request, asset)
        if error:
            return error
        if asset not in CollectorControl.ASSETS:
            return {"result": -2, "message": "请选择期权或加密货币采集器"}
        root = asset_root(deploy, asset)
        if action == "status":
            return {"result": 0, **CollectorControl.state(root)}
        if not can_admin(user):
            return {"result": -403, "message": "仅管理员可设置长期采集"}
        if action not in ("start", "stop", "save"):
            return {"result": -2, "message": "未知采集操作"}
        try:
            state = CollectorControl.change(root, asset, symbols, action, revision)
            data_mgr.log_action(user, "collector_"+action, asset)
            return {"result": 0, **state}
        except (ValueError, OSError) as exc:
            return {"result": -2, "message": str(exc) if isinstance(exc, ValueError) else "请检查本资产的采集配置"}

    @app.post("/mgr/assets/option/suggestions")
    def suggestions(request: Request, groupid: str = Body(..., embed=True)):
        _, error = authorize(request, "option")
        if error:
            return error
        from .OptionChain import subscription_chains
        from .ResearchServer import monitor_json, upstream_headers
        snapshot = monitor_json(os.getenv("WT_MONITOR_UPSTREAM", "http://127.0.0.1:8100"),
                                "/mgr/market/subscriptions", {"groupid": groupid}, upstream_headers(request))
        if snapshot.get("result") != 0:
            return {"result": -2, "message": snapshot.get("message") or "无法读取该组合的期货订阅"}
        return {"result": 0, "chains": subscription_chains(asset_root(deploy, "option"), snapshot),
                "profile": snapshot["profile_label"]}

    @app.post("/mgr/assets/futures/deferred")
    def deferred(request: Request, groupid: str = Body(...), profileid: str = Body(...),
                 action: str = Body("status"), contracts: list[str] = Body(default=[]), revision: int = Body(0),
                 base: list[str] = Body(default=[])):
        user, error = authorize(request, "futures")
        if error:
            return error
        from . import DeferredSubscriptions
        from .ResearchServer import monitor_json, upstream_headers
        snapshot = monitor_json(os.getenv("WT_MONITOR_UPSTREAM", "http://127.0.0.1:8100"),
                                "/mgr/market/subscriptions", dict(groupid=groupid, profileid=profileid), upstream_headers(request))
        if snapshot.get("result") != 0:
            return {"result": -2, "message": snapshot.get("message") or "无法读取所选中台"}
        folder = inside(asset_root(deploy,"futures"),"deferred",snapshot["datahub_id"])
        configured = snapshot["datahub_id"] in os.getenv("WT_DEFERRED_HUBS","").split(",") and folder.is_dir()
        editable = configured and not snapshot["datahub_running"] and snapshot["current_profile"] != profileid
        if action == "status":
            return {"result": 0, "editable": editable, **(DeferredSubscriptions.state(folder) if configured else {})}
        if not configured or not snapshot["can_manage"] or action not in ("save", "cancel"):
            return {"result": -403, "message": "无权设置此中台的待生效订阅"}
        if action == "save" and not editable:
            return {"result": -2, "message": "只有已停止的非当前环境可以保存待生效订阅，请刷新"}
        if action == "save" and sorted(base) != sorted(snapshot["selected"]):
            return {"result": -2, "message": "原订阅已变更，请刷新并重新预览"}
        try:
            result = DeferredSubscriptions.set_plan(folder,snapshot,action,contracts,revision)
            data_mgr.log_action(user, "deferred_"+action, snapshot["datahub_id"])
            return {"result": 0, **result, "message": "下次启动采用所选订阅" if action=="save" else "已取消待生效订阅"}
        except (OSError, ValueError) as exc:
            return {"result": -2, "message": str(exc) if isinstance(exc,ValueError) else "无法保存中台订阅"}

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

    @app.post("/mgr/assets/option/analysis")
    async def analysis(request: Request, selection: DeskRequest):
        _, error = await asyncio.to_thread(authorize, request, "option")
        if error:
            return error
        root = asset_root(deploy, "option")
        try:
            plan = await asyncio.to_thread(desk_plan,root,selection)
        except (ValueError, OSError, KeyError):
            return {"result":-2,"message":"请检查合约所属期货、到期日和价差参数，最多 96 份期权"}
        async def stream():
            if analysis_slots.locked():
                yield json.dumps({"error":"当前分析会话已满，请关闭不用的分析页"})+"\n"
                return
            async with analysis_slots:
                session = secrets.token_hex(12)
                folder = inside(root,"analysis",session)
                folder.mkdir(mode=0o700,parents=True)
                try:
                    yield json.dumps({"session":session, "subscribed":len(plan["symbols"])})+"\n"
                    async with aclosing(worker_events(folder,"OptionDeskWorker.py",plan=plan)) as events:
                        async for event in events:
                            yield json.dumps(event,ensure_ascii=False,allow_nan=False)+"\n"
                finally:
                    shutil.rmtree(folder)
        return StreamingResponse(stream(),media_type="application/x-ndjson",
                    headers={"Cache-Control":"no-store","X-Accel-Buffering":"no","Content-Encoding":"identity"})

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
            capital: float = Body(..., gt=0, le=1e12), companions: list[str] = Body(default=[],max_length=12),
            fill_model: str | None = Body(None), event_delay: int = Body(0,ge=0,le=1000),
            participation_rate: float = Body(0,ge=0,le=1), fee_per_lot: float | None = Body(None,ge=0,le=1e5)):
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
            if fee_per_lot is not None and asset!="option":
                raise ValueError("按手试算手续费仅用于期权工作区")
            if ("fee" not in item["basefiles_map"] and fee_per_lot is None) or not all(Path(f).is_file() for f in item["basefiles_map"].values()):
                raise ValueError("请补齐该资产的合约、交易时段、交易日历与费用文件")
            source = libraries[asset].get(library_id, version, allow_archived=False)
            kind = source["engine_type"].lower()
            if kind == "hft" and item["period"] != "tick":
                raise ValueError("HFT 回测需要该合约的 Tick 数据")
            if companions:
                selector = Path(os.environ.get("WT_BACKTEST_RUNTIME", "/opt/wt/cloud/backtest-runtime.json"))
                settings = json.loads(selector.read_text(encoding="utf-8")) if selector.is_file() else {}
                if not settings.get("multi_period_replay") or not Path(settings.get("bt_porter_lib","")).is_file():
                    raise ValueError("多合约回放需配置已通过跨周期验证的原生回测库")
            if companions and (asset != "option" or not source["accepts_kwargs"] and
                               "codes" not in {p["name"] for p in source["parameters"]}):
                raise ValueError("多合约期权回放需要接受 codes 参数的策略")
            by_id = {row["id"]:row for row in catalog}
            if asset == "option":
                if item.get("leg_type")!="option":
                    raise ValueError("主数据集须为商品期权")
                if len({dataset,*companions})!=1+len(companions):
                    raise ValueError("请勿重复选择同一条腿")
                shared_days = set(item["dates"])
                underlyings = {by_id[k]["underlying"] for k in (dataset,*companions) if k in by_id and by_id[k].get("leg_type")=="option"}
                for leg_id in (dataset,*companions):
                    leg = by_id.get(leg_id)
                    if not leg or "." in leg["ticker"]:
                        raise ValueError("请选择本期权工作区的整数行权价合约")
                    if leg["leg_type"]=="future" and leg["underlying"] not in underlyings:
                        raise ValueError("期货腿须是已选期权的标的")
                    shared_days.intersection_update(leg["dates"])
                    if not leg.get("expiry") or finish>=leg["expiry"]:
                        raise ValueError("回放须在所有选中期权的到期日前结束")
                if not any(begin<=day<=finish for day in shared_days):
                    raise ValueError("所选日期内没有各腿共同记录的交易日")
            fill = "legacy_cta" if kind == "cta" and item["period"] != "tick" else "causal_touch"
            if fill_model is not None or event_delay or participation_rate:
                if asset!="option" or kind!="cta" or item["period"]!="tick" or fill_model not in ("causal_touch","volume_limited"):
                    raise ValueError("盘口设置只适用于商品期权 CTA Tick 回测")
                fill = fill_model
            monitor, owner = get_monitor(asset), user["loginid"]
            with lock:
                straid = monitor.add_strategy(owner, source["name"] + " v" + str(version), kind)["id"]
                try:
                    if not monitor.set_strategy_code(owner, straid, source["source"]):
                        raise ValueError("策略副本写入失败")
                    task = monitor.run_backtest(owner, straid, begin * 10000, finish * 10000 + 2359,
                                                capital, fill_model=fill, event_delay=event_delay, participation_rate=participation_rate,
                                                dataset=dataset, catalog=catalog, companions=companions, fee_per_lot=fee_per_lot)
                except Exception:
                    monitor.del_strategy(owner, straid)
                    raise
            return {"result": 0, "task": task}
        except (ValueError, OSError) as exc:
            return {"result": -2, "message": str(exc) if isinstance(exc, ValueError) else "回测工作区无法读写"}

    @app.post("/mgr/assets/option/replay-risk")
    def replay_risk(request: Request, strategy: str=Body(...), task: str=Body(...), day: str=Body(...), rate: float=Body(.02,ge=0,le=.5)):
        user,error=authorize(request,"option",research=True)
        if error:return error
        monitor,owner=get_monitor("option"),user["loginid"]
        if not any(s["id"]==strategy for s in monitor.get_strategies(owner)):
            return {"result":-2,"message":"本账户没有该策略"}
        row=next((r for r in monitor.get_backtests(owner,strategy) or [] if r["id"]==task),None)
        legs=(row or {}).get("dataset") or {}
        if not legs.get("option_legs") or row["status"]=="running":
            return {"result":-2,"message":"请选择含期权合约资料的已结束任务"}
        try:
            date=int(datetime.strptime(day,"%Y-%m-%d").strftime("%Y%m%d"))
            folder=Path(monitor.path)/owner/strategy/"backtests"/task
            report=native_worker(dict(action="option_replay_risk",folder=str(folder),task=task,
                                      legs=legs["option_legs"],day=date,rate=rate))
            return {"result":0,**report}
        except (OSError,ValueError,TimeoutError) as exc:
            return {"result":-2,"message":str(exc)}

    from .OptionExpiryRoutes import register as register_expiry_routes
    register_expiry_routes(app, deploy, authorize, get_monitor)
    return monitors

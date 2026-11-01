"""Account-owned continuous runs, separate from collection and broker processes."""

import csv
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from threading import Lock
from uuid import uuid4
from fastapi import Body, Request
from pydantic import BaseModel, Field
from .AssetMarkets import asset_root, inside, instrument
from .MainHistory import product_key, range_dates
from .PlusBacktest import write_json
from .ReportChart import field
from .Roles import can_admin, RESEARCH_ROLES


class Plan(BaseModel):
    product: str = Field(max_length=16)
    option: str = Field("", max_length=42)
    start: str
    end: str
    period: str = "d1"
    strategy: str = ""
    dataset: str = ""
    model: str = "bars"
    fill_model: str = "causal_touch"
    event_delay: int = Field(0, ge=0)
    participation_rate: float = Field(0, ge=0, le=1)
    margin: float = Field(0.12, gt=0, le=1)
    version: int | None = None
    capital: float = Field(100000, gt=0, le=1e12, allow_inf_nan=False)
    fee: float = Field(1, ge=0, le=1e5, allow_inf_nan=False)
    params: dict = Field(default_factory=dict)
    preview: bool = False


def register_main(app, deploy, authorize, libraries):
    children = {}
    lock = Lock()

    def tasks_root(asset, user):
        if asset not in ("futures", "option"):
            raise ValueError("请选择期货或期权")
        return inside(asset_root(deploy, asset), "main-runs", field(user["loginid"]))

    from .PlusBacktest import PlusBacktestManager
    from .Roles import group_allowed

    plus = PlusBacktestManager(
        os.getenv("WT_MAIN_PLUS_ROOT", "/data/plus-tasks"),
        workspace="/workspace",
        max_concurrent=1,
        recover_running=False,
    )

    @app.post("/mgr/assets/futures/tick-plus-run")
    def tick_plus(request: Request, config: dict = Body(...)):
        from .ResearchServer import monitor_auth

        ok, u = monitor_auth(
            os.getenv("WT_MONITOR_UPSTREAM", "http://127.0.0.1:8100"), request
        )
        if not ok:
            return u
        if u.get("role") not in RESEARCH_ROLES:
            return dict(result=-403, message="没有回测权限")
        try:
            owner = field(u["loginid"])
            strategy = field(config["straid"])
            folder = inside(Path("/report-sources/native"), owner)
            meta = json.loads((folder / "marker.json").read_bytes())["strategies"][
                strategy
            ]
            item = next(
                (
                    d
                    for d in plus.capabilities()["datasets"]
                    if d["id"] == config["dataset"]
                ),
                None,
            )
            if not item or (
                item.get("groupid") and not group_allowed(u, item["groupid"])
            ):
                raise ValueError("所选数据不存在或不可读取")
            task = plus.create(
                user=owner,
                straid=strategy,
                strategy_code=inside(folder, strategy, "MyStrategy.py").read_text(
                    encoding="utf-8-sig"
                ),
                engine_type=meta.get("strategy_type", "cta"),
                **{
                    k: config[k]
                    for k in (
                        "code",
                        "dataset",
                        "start",
                        "end",
                        "capital",
                        "period",
                        "params",
                        "finance",
                        "execution",
                        "risk",
                    )
                },
            )
            return dict(result=0, task=task)
        except (KeyError, OSError):
            return dict(result=-2, message="本账户没有所选策略或缺少运行配置")
        except (ValueError, RuntimeError) as e:
            return dict(result=-2, message=str(e))

    @app.post("/mgr/assets/{asset}/main-run")
    def start(request: Request, asset: str, plan: Plan):
        user, error = authorize(request, asset, research=True)
        if error:
            return error
        if not can_admin(user):
            return dict(result=-403, message="仅工作区管理员可以启动历史准备或回测")
        try:
            product_key(plan.product)
            range_dates(plan.start, plan.end)
            if plan.period not in ("d1", "m15"):
                raise ValueError("请选择 15 分钟或日线")
            root = tasks_root(asset, user)
            source = None
            group = None
            if plan.model not in ("bars", "plus_tick", "option_tick"):
                raise ValueError("未知回放方式")
            if plan.model == "option_tick" and (
                asset != "option" or not plan.dataset or plan.preview
            ):
                raise ValueError("请选择期权工作区的真实 Tick 数据集")
            if plan.model == "option_tick":
                from ..wrapper.WtBtWrapper import validate_cta_fill_config
                if plan.fill_model not in ("causal_touch", "volume_limited"):
                    raise ValueError("真实期权 Tick 需要盘口撮合")
                validate_cta_fill_config(plan.fill_model, plan.event_delay, plan.participation_rate)
            if plan.model == "plus_tick":
                if asset != "futures" or plan.preview:
                    raise ValueError("Plus 主力 Tick 适用于期货 CTA")
                from .HistoryCatalog import dataset_entries

                item = next(
                    (
                        r
                        for r in dataset_entries(periods=("tick",))
                        if r["id"] == plan.dataset and group_allowed(user, r["groupid"])
                    ),
                    None,
                )
                if not item or product_key(item["code"]) != product_key(plan.product):
                    raise ValueError("请选择相同品种的真实 Tick 环境")
                group = item["groupid"]
            if not plan.preview:
                field(plan.strategy)
                if asset == "futures":
                    marker = inside(
                        Path("/report-sources/native"),
                        field(user["loginid"]),
                        "marker.json",
                    )
                    meta = json.loads(marker.read_bytes())["strategies"].get(
                        plan.strategy
                    )
                    if (
                        not meta
                        or str(meta.get("strategy_type", "cta")).lower() != "cta"
                    ):
                        raise ValueError("请选择本账户的 Python CTA 策略")
                    source = inside(
                        marker.parent, plan.strategy, "MyStrategy.py"
                    ).read_text(encoding="utf-8-sig")
                else:
                    instrument("option", plan.option)
                    lib = libraries[asset]
                    row = lib.get(plan.strategy, plan.version, allow_archived=False)
                    if row["engine_type"] != "CTA":
                        raise ValueError("逐 K 回测需要 Python CTA 策略")
                    source = row["source"]
            with lock:
                for key, proc in list(children.items()):
                    if proc.poll() is not None:
                        children.pop(key, None)
                if children:
                    raise ValueError("有历史准备或连续回测正在运行，请稍后再试")
                task = dict(
                    plan.model_dump(),
                    id=uuid4().hex,
                    asset=asset,
                    status="running",
                    owner=user["loginid"],
                    groupid=group,
                    created_at=datetime.now().timestamp(),
                )
                folder = inside(root, task["id"])
                folder.mkdir(parents=True, mode=0o700)
                if source:
                    (folder / "MyStrategy.py").write_text(source, encoding="utf8")
                write_json(folder / "task.json", task)
                with (folder / "run.log").open("wb") as output:
                    children[str(folder)] = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "wtpy.monitor.MainRunWorker",
                            str(folder),
                        ],
                        cwd=folder,
                        stdout=output,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
            return dict(result=0, task=task)
        except (OSError, ValueError, KeyError) as exc:
            return dict(
                result=-2,
                message=str(exc)
                if isinstance(exc, ValueError)
                else "任务来源或历史配置不可用",
            )

    def read_task(file):
        with lock:
            row = json.loads(file.read_bytes())
            proc = children.get(str(file.parent))
            if row["status"] == "running" and (proc is None or proc.poll() is not None):
                row = json.loads(file.read_bytes())
                if row["status"] == "running":
                    row.update(status="failed", error="回放进程中断")
                    write_json(file, row)
            return row

    @app.post("/mgr/assets/{asset}/main-list")
    def listing(request: Request, asset: str):
        u, err = authorize(request, asset, research=True)
        if err:
            return err
        root = tasks_root(asset, u)
        records = []
        for f in root.glob("*/task.json"):
            task = read_task(f)
            records.append(task)
        return dict(
            result=0, runs=sorted(records, key=lambda t: t["created_at"], reverse=True)
        )

    @app.post("/mgr/assets/{asset}/main-detail")
    def detail(request: Request, asset: str, task: str = Body(..., embed=True)):
        u, err = authorize(request, asset, research=True)
        if err:
            return err
        folder = inside(tasks_root(asset, u), field(task))
        file = folder / "task.json"
        if not file.exists():
            return dict(result=-2, message="本账户没有此连续任务")
        row = read_task(file)
        out = folder / "outputs_bt" / task

        def ledger(name):
            f = out / name
            if row["status"] != "completed" or not f.exists():
                return []
            with f.open(encoding="utf-8-sig") as stream:
                return list(csv.DictReader(stream))

        from .MainHistory import segments

        fills = [
            dict(
                code=t["code"],
                time=t["time"],
                direction=t["direct"],
                offset=t["action"],
                price=float(t["price"]),
                volume=float(t["qty"]),
                tag=t.get("tag", ""),
                fee=float(t["fee"]),
            )
            for t in ledger("trades.csv")
        ]
        funds = [
            dict(date=t["date"], dynbalance=float(t["dynbalance"]), fee=float(t["fee"]))
            for t in ledger("funds.csv")
        ]
        positions = {}
        for t in fills:
            sign = (1 if t["direction"] == "LONG" else -1) * (
                1 if t["offset"] == "OPEN" else -1
            )
            positions[t["code"]] = positions.get(t["code"], 0) + sign * t["volume"]
        return dict(
            result=0,
            task=row,
            funds=funds,
            trades=fills,
            positions=positions,
            segments=segments(json.loads((folder / "plan.json").read_bytes()))
            if (folder / "plan.json").exists()
            else row.get("segments", []),
        )

    @app.post("/mgr/assets/{asset}/main-delete")
    def delete(request: Request, asset: str, task: str = Body(..., embed=True)):
        import shutil

        u, err = authorize(request, asset, research=True)
        if err:
            return err
        if not can_admin(u):
            return dict(result=-403, message="没有操作权限")
        folder = inside(tasks_root(asset, u), field(task))
        if not (folder / "task.json").is_file():
            return dict(result=-2, message="本账户没有此任务")
        if json.loads((folder / "task.json").read_bytes())["status"] == "running":
            return dict(result=-2, message="任务仍在运行")
        shutil.rmtree(folder)
        return dict(result=0)

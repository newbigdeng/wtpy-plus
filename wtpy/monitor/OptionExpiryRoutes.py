"""Option-only, account-owned historical clearing runs."""

import csv
from datetime import datetime
from pathlib import Path
import json
import re
from uuid import uuid4

from fastapi import Body, Request
from pydantic import BaseModel, Field

from .AssetMarkets import asset_root, inside
from .OptionExpiryReplay import replay


class Declaration(BaseModel):
    day: str
    code: str
    action: str
    qty: int = Field(gt=0)


class Plan(BaseModel):
    sample: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,64}$")
    old: str
    new: str
    lots: int = Field(default=2, ge=-10000, le=10000)
    roll: int = Field(default=1, ge=0, le=10000)
    before: int = Field(default=1, ge=0, le=252)
    capital: float = Field(default=100000, gt=0, le=1e12)
    fee: float = Field(default=1, ge=0, le=1e5)
    exercise_fee: float = Field(default=1, ge=0, le=1e5)
    margin_rate: float = Field(default=0.10, gt=0, le=1)
    declarations: list[Declaration] = Field(default_factory=list, max_length=100)
    strategy: str = ""
    task: str = ""


def native_trades(folder, task, data, code):
    symbol, kind, strike = code.removesuffix(".DCE").lower().split("-")
    name = f"DCE.{symbol}.{kind.upper()}.{strike}"
    out = []
    path = folder / "outputs_bt" / task / "trades.csv"
    with path.open(encoding="utf-8-sig", newline="") as stream:
        for r in csv.DictReader(stream):
            day, clock = r["time"][:8], r["time"][8:12]
            if day not in data["days"] or r["code"] != name:
                raise ValueError("成交不属于所选样本的旧合约或日期")
            if clock >= "2100":
                day = next((d for d in data["days"] if d > day), "")
            if (
                r["direct"] not in ("LONG", "SHORT")
                or r["action"] not in ("OPEN", "CLOSE")
                or not float(r["qty"]).is_integer()
            ):
                raise ValueError("只支持整手、明确开平的 CTA 成交")
            q = int(float(r["qty"]))
            if q <= 0 or float(r["price"]) <= 0 or float(r["fee"]) < 0:
                raise ValueError("原生成交的数量、价格或费用无效")
            close = r["action"] == "CLOSE"
            out.append(
                dict(
                    day=day,
                    code=code,
                    close=close,
                    qty=q * (-1 if r["direct"] == "SHORT" else 1) * (-1 if close else 1),
                    price=r["price"],
                    fee=r["fee"],
                )
            )
    if not out:
        raise ValueError("所选任务没有期权成交")
    return out


def register(app, deploy, authorize, get_monitor):
    root = asset_root(deploy, "option")
    samples = inside(root, "history", "expiry")

    def owner_dir(user):
        name = user["loginid"]
        if not name or name in (".", "..") or "/" in name or "\\" in name:
            raise ValueError("无效的工作区账户")
        return inside(root, "backtests", "expiry-runs", name)

    @app.post("/mgr/assets/option/expiry-catalog")
    def catalog(request: Request):
        user, error = authorize(request, "option", research=True)
        if error:
            return error
        packs = []
        for path in sorted(samples.glob("*.json")):
            data = json.loads(inside(samples, path.name).read_text(encoding="utf8"))
            packs.append(
                dict(
                    id=path.stem,
                    source=data["source"],
                    start=data["days"][0],
                    end=data["days"][-1],
                    contracts=[dict(code=k, expiry=v["expiry"]) for k, v in data["contracts"].items()],
                )
            )
        ids = sorted((p.stem for p in owner_dir(user).glob("*.json")), reverse=True)
        return dict(result=0, samples=packs, runs=ids)

    @app.post("/mgr/assets/option/expiry-run")
    def run(request: Request, plan: Plan):
        user, error = authorize(request, "option", research=True)
        if error:
            return error
        try:
            data = json.loads(inside(samples, plan.sample + ".json").read_text(encoding="utf8"))
            inputs = plan.model_dump(exclude={"sample", "task", "strategy"})
            if plan.task or plan.strategy:
                mon = get_monitor("option")
                owner = user["loginid"]
                if not any(s["id"] == plan.strategy for s in mon.get_strategies(owner)):
                    raise ValueError("未找到当前账户的策略")
                row = next(
                    (r for r in mon.get_backtests(owner, plan.strategy) or [] if r["id"] == plan.task), None
                )
                if (
                    not row
                    or row.get("status") not in ("finished", "completed", "success")
                    or row.get("strategy_type", "").lower() != "cta"
                ):
                    raise ValueError("请选择已完成的本账户期权 CTA 回测")
                inputs["native_fills"] = native_trades(
                    inside(Path(mon.path), owner, plan.strategy, "backtests", plan.task),
                    plan.task,
                    data,
                    plan.old,
                )
            report = replay(data, **inputs)
            record = dict(plan=plan.model_dump(), **report)
            dest = owner_dir(user)
            dest.mkdir(parents=True, exist_ok=True)

            rid = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid4().hex[:12]
            with inside(dest, rid + ".json").open("x", encoding="utf8") as f:
                json.dump(record, f, ensure_ascii=False, allow_nan=False)
            return dict(result=0, id=rid, report=record)
        except (KeyError, ValueError, OSError) as e:
            return dict(
                result=-2, message=str(e) if isinstance(e, ValueError) else "无法读取完整的期权回测数据"
            )

    @app.post("/mgr/assets/option/expiry-delete")
    def delete(request: Request, run: str = Body(..., embed=True)):
        from .Roles import can_admin
        user, error = authorize(request, "option", research=True)
        if error:
            return error
        if not can_admin(user):
            return dict(result=-403, message="仅管理员可删除期权回测")
        if not re.fullmatch(r"\d{8}-\d{6}-[a-f0-9]{12}", run):
            return dict(result=-2, message="任务编号无效")
        path = inside(owner_dir(user), run + ".json")
        try:
            path.rename(path.with_suffix(".deleted"))
        except FileNotFoundError:
            return dict(result=-2, message="本账户没有该任务")
        return dict(result=0)

    @app.post("/mgr/assets/option/expiry-detail")
    def detail(request: Request, run: str = Body(..., embed=True)):
        user, error = authorize(request, "option", research=True)
        if error:
            return error
        if not re.fullmatch(r"\d{8}-\d{6}-[a-f0-9]{12}", run):
            return dict(result=-2, message="任务编号无效")
        path = inside(owner_dir(user), run + ".json")
        if not path.is_file():
            return dict(result=-2, message="本账户没有该任务")
        return dict(result=0, report=json.loads(path.read_text(encoding="utf8")))

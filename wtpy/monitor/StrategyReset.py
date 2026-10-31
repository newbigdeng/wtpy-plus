"""Queue one explicit reset; only the native runner may confirm it completed."""
import json
from pathlib import Path
import re
import time
import uuid

from wtpy.StrategyRuntime import atomic_json
from .StrategyContracts import contract_codes

TERMINAL = frozenset(("completed", "failed"))


def read_json(path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def reset_root(group):
    return Path(group["path"]).resolve()/"generated/strategy-control"


def reset_task(group, strategyid):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", strategyid):
        return None
    return read_json(reset_root(group)/("reset-"+strategyid+".json"))


def reset_eligibility(group, strategyid, plan, status, online):
    task = reset_task(group, strategyid)
    reason = ""
    rows = plan.get("strategies", [])
    selected = next((row for row in rows if row["id"] == strategyid), None)
    observed = next((row for row in status.get("strategies", []) if row["id"] == strategyid), {})
    if not online:
        reason = "组合控制桥未就绪，请先启动并确认柜台连接"
    elif plan.get("engine_type") != "CTA" or not selected or not selected.get("enabled"):
        reason = "仅支持当前已加载的托管CTA策略"
    elif "reset_strategy" not in status.get("capabilities", []) or not observed.get("reset_ready"):
        reason = "当前运行库尚未提供策略重置，请更新后重新启动"
    elif not contract_codes(selected.get("params", {})) or any(
            not re.fullmatch(r"[A-Z][A-Z0-9]*\.[A-Za-z][A-Za-z0-9]*\.\d+", code)
            for code in contract_codes(selected.get("params", {}))):
        reason = "策略未配置可确认归属的具体合约"
    elif any(row["id"] != strategyid and row.get("trader") == selected.get("trader")
             for row in rows):
        reason = "该柜台通道由多个策略共享，不能单独强制平仓"
    elif task and task.get("status") not in TERMINAL:
        reason = "该策略正在重置，请等待任务结果"
    else:
        for channel in (reset_root(group).parent/"traders").glob("*/rtdata.json.status.json"):
            if channel.parent.name != selected.get("trader"):
                continue
            broker = read_json(channel, {})
            if broker.get("connected") is True and broker.get("ready") is True:
                break
        else:
            reason = "柜台尚未连接就绪，不能确认撤单和平仓"
    return {"reset_supported": not reason, "reset_reason": reason,
            "reset_task": task}


def enqueue_reset(group, strategyid, plan, status, actor):
    root = reset_root(group)
    previous = read_json(root/"reset-request.json", {})
    if previous:
        active = reset_task(group, previous["strategyid"])
        if active and active.get("status") not in TERMINAL:
            raise ValueError("组合已有重置任务，请等待结果")
    selected = next(row for row in plan["strategies"] if row["id"] == strategyid)
    identifier = uuid.uuid4().hex
    now = time.time()
    task = {"id": identifier, "strategyid": strategyid, "status": "queued",
            "step": "pausing", "message": "等待策略停止提交新信号", "error": "",
            "created_at": now, "updated_at": now}
    if previous:
        history = root/"reset-history"
        history.mkdir(mode=0o700, exist_ok=True)
        old = reset_task(group, previous["strategyid"])
        if old:
            atomic_json(history/(old["id"]+".json"), old)
    desired = read_json(root/"desired.json", {"states": {}})
    desired["states"][strategyid] = "paused"
    desired.update(schema=1, revision=identifier, requested_at=now)
    atomic_json(root/"desired.json", desired)
    atomic_json(root/("reset-"+strategyid+".json"), task)
    atomic_json(root/"reset-request.json", dict(task,
        trader=selected["trader"], contracts=contract_codes(selected.get("params", {})),
        boot_id=status["boot_id"], actor=actor, expires_at=now+30))
    return task

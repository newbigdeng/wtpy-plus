from .Roles import can_operate
"""Authenticated independent controls for Python decisions and native CTA filters."""
import asyncio
try:
    import fcntl
except ImportError:  # Other monitor features remain importable on Windows.
    fcntl = None
import json
import math
import os
from pathlib import Path
import time
import uuid

from fastapi import Body, Request
from wtpy.StrategyRuntime import atomic_json, SEMANTICS
from .StrategyFilters import filter_snapshot, row_filter_state, set_strategy_filter
from .StrategyContracts import contract_codes

def _read(path, fallback):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else fallback
    except (OSError, ValueError):
        return fallback

def _directory(group):
    return Path(group["path"]).resolve() / "generated" / "strategy-control"

def _snapshot(group, running):
    root = _directory(group)
    status = _read(root / "status.json", {})
    desired = _read(root / "desired.json", {"states": {}})
    if not isinstance(desired.get("states"), dict):
        desired = {"states": {}}
    try:
        alive = bool(running and status.get("alive") and
                     0 <= time.time() - float(status.get("updated_at", 0)) <= 5 and
                     not status.get("error"))
        if alive:
            os.kill(int(status["pid"]), 0)
    except (OSError, ValueError, TypeError, KeyError):
        alive = False
    return status, desired, alive

def _configured(group):
    # Native configuration is read only for identifiers; never return trader
    # settings or credentials in the runtime-management response.
    import yaml
    rows = {}
    root = Path(group["path"])
    for filename in ("config.yaml", "config.json"):
        p = root / filename
        if not p.exists():
            continue
        try:
            obj = yaml.safe_load(p.read_text(encoding="utf-8"))
            for engine, items in obj.get("strategies", {}).items():
                if not isinstance(items, list):
                    continue
                for item in items:
                    if isinstance(item, dict) and item.get("id"):
                        rows[str(item["id"])] = {"id": str(item["id"]),
                            "name": str(item.get("name", item["id"])),
                            "engine": str(engine).upper(), "source": "native",
                            "configured_active": bool(item.get("active", True))}
        except (OSError, ValueError, AttributeError, TypeError, yaml.YAMLError):
            pass
        break
    return rows

def runtime_rows(data_mgr, dog, groupid):
    group = data_mgr.get_group(groupid)
    running = dog.isRunning(groupid)
    status, desired, alive = _snapshot(group, running)
    rows = _configured(group)
    for name in data_mgr.get_strategies(groupid):
        if isinstance(name, str):
            rows.setdefault(name, {"id": name, "name": name,
                "engine": "UNKNOWN", "source": "unmanaged"})
    for item in status.get("strategies", []):
        if isinstance(item, dict) and item.get("id"):
            rows[str(item["id"])] = dict(item)
    observed = set(rows)
    folder = Path(group["path"]) / "managed-strategies"
    draft = _read(folder / "draft.json", {})
    applied = _read(folder / "applied.json", {})
    active = _read(folder / "revisions" / str(applied.get("revision", "")) / "plan.json", {}) if applied.get("revision") else {}
    appconf = dog.getAppConf(groupid) or {}
    managed_entry = bool(applied.get("revision") and "run_managed.py" in appconf.get("param", ""))
    planned = {row["id"]: row for row in draft.get("strategies", [])}
    launched = {row["id"]: row for row in active.get("strategies", [])}
    for sid, item in planned.items():
        rows.setdefault(sid, {"id": sid, "name": item.get("name", sid),
            "engine": draft.get("engine_type", "UNKNOWN"), "source": "plan"})
    filters = filter_snapshot(group)
    for row in rows.values():
        row_filter_state(row, filters, running)
        managed = row.get("source") == "python"
        state = row.get("state", "unknown") if alive and managed else ("stopped" if not running else "unknown")
        target = desired.get("states", {}).get(row["id"], state)
        row.update({"state": state, "control_supported": bool(alive and managed),
            "pending": bool(alive and managed and target != state),
            "desired_state": target, "control_reason": (
                "已接入 Python 决策控制；引擎和交易回报继续运行" if alive and managed else
                "组合进程未运行" if not running else
                "控制桥无新鲜心跳，无法确认状态" if managed else
                "原生 C++ 策略或未接入控制桥的 Python 策略，暂不支持单独暂停")})
        sid = row["id"]
        ledger_path = Path(group["path"]) / "generated/stradata" / (sid + ".json")
        ledger = _read(ledger_path, {})
        try:
            volume = sum(abs(float(item.get("volume", item.get("volumn"))))
                         for item in ledger.get("positions", []))
            position_known = isinstance(ledger.get("positions"), list) and math.isfinite(volume)
        except (TypeError, ValueError, AttributeError):
            volume, position_known = 0, False
        in_plan = sid in planned
        pending = in_plan and (sid not in launched or any(planned[sid].get(key) != launched[sid].get(key)
            for key in ("enabled", "library_id", "library_version", "params", "trader", "execution")))
        row.update({"in_launch_plan": in_plan, "in_applied_plan": sid in launched,
            "new_strategy": sid not in observed, "has_positions": volume > 0,
            "position_volume": volume, "position_known": position_known,
            "plan_pending": pending, "library_id": planned.get(sid, {}).get("library_id", ""),
            "library_version": planned.get(sid, {}).get("library_version")})
        row.update(applied_contracts=contract_codes(launched.get(sid, {}).get("params", {})),
                   planned_contracts=contract_codes(planned.get(sid, {}).get("params", {})),
                   contracts_source="已应用的启动配置" if sid in launched else "尚无已应用合约配置")
    names = _read(_directory(group) / "names.json", {})
    # An applied plan owns the next/current process. Keep orphaned positions
    # visible, but do not present old, flat ledgers as loaded strategies.
    if managed_entry:
        broker_positions = False
        for path in (Path(group["path"]) / "generated/traders").glob("*/rtdata.json"):
            positions = _read(path, {}).get("positions")
            try:
                volume = sum(abs(float(item.get(side, {}).get(key, 0) or 0))
                             for item in positions for side in ("long", "short")
                             for key in ("newvol", "prevol"))
            except (TypeError, ValueError, AttributeError):
                volume = float("nan")
            if not isinstance(positions, list) or not math.isfinite(volume) or volume > 0:
                broker_positions = True
                break
        rows = {sid: row for sid, row in rows.items()
                if sid in planned or sid in launched or row["has_positions"]
                or not row["position_known"] or broker_positions}
    for row in rows.values():
        if isinstance(names.get(row["id"]), str):
            row["name"] = names[row["id"]]
    return {"result": 0, "message": "OK", "strategies": list(rows.values()),
            "process_running": bool(running), "control_online": alive,
            "semantics": SEMANTICS}

def register_strategy_routes(app, data_mgr, dog, check_auth):
    def authorize(request, token, groupid, control=False):
        authenticated, info = check_auth(request, token)
        if not authenticated:
            return None, info
        if not data_mgr.has_group(groupid):
            return None, {"result": -1, "message": "组合不存在"}
        products = info.get("products", "")
        allowed = [value.strip() for value in products.split(",") if value.strip()] if isinstance(products, str) else list(products or [])
        if allowed and groupid not in allowed:
            return None, {"result": -403, "message": "无权访问当前组合"}
        if control and not can_operate(info):
            return None, {"result": -403, "message": "当前角色不能控制策略决策和目标过滤"}
        return info, None

    @app.post("/mgr/qrystra_runtime", tags=["策略运行管理"])
    def query_runtime(request: Request, groupid: str = Body(..., embed=True),
                      token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid)
        if error:
            return error
        result = runtime_rows(data_mgr, dog, groupid)
        result["can_control"] = can_operate(info)
        return result

    @app.post("/mgr/ctrlstra", tags=["策略运行管理"])
    async def control_runtime(request: Request, groupid: str = Body(..., embed=True),
                              strategyid: str = Body(..., embed=True),
                              action: str = Body(..., embed=True),
                              token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid, True)
        if error:
            return error
        if fcntl is None:
            return {"result": -3, "message": "策略控制桥目前仅支持 Linux 容器"}
        if action not in ("pause", "resume"):
            return {"result": -2, "message": "action 必须是 pause 或 resume"}
        rows = runtime_rows(data_mgr, dog, groupid)
        item = next((row for row in rows["strategies"] if row["id"] == strategyid), None)
        if item is None:
            return {"result": -2, "message": "策略不存在"}
        if not item["control_supported"]:
            return {"result": -3, "message": item["control_reason"]}
        root = _directory(data_mgr.get_group(groupid))
        command_id = uuid.uuid4().hex
        with (root / ".command.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            desired = _read(root / "desired.json", {"states": {}})
            if not isinstance(desired.get("states"), dict):
                return {"result": -3, "message": "策略控制状态文件损坏，请先检查"}
            desired.setdefault("states", {})[strategyid] = "paused" if action == "pause" else "running"
            desired.update({"schema": 1, "revision": command_id, "requested_at": time.time()})
            atomic_json(root / "desired.json", desired)
        data_mgr.log_action(info, "strategy_decision_" + action, groupid + "/" + strategyid)
        for _ in range(30):
            status, _, online = _snapshot(data_mgr.get_group(groupid), dog.isRunning(groupid))
            if online and status.get("revision") == command_id:
                return {"result": 0, "message": "决策状态已确认", "pending": False,
                        "command_id": command_id, "semantics": SEMANTICS,
                        "strategies": runtime_rows(data_mgr, dog, groupid)["strategies"]}
            await asyncio.sleep(0.05)
        return {"result": 0, "message": "命令已提交，等待当前回调退出后确认",
                "pending": True, "command_id": command_id, "semantics": SEMANTICS}

    @app.post("/mgr/ctrlstra_filter", tags=["策略运行管理"])
    def control_filter(request: Request, groupid: str = Body(..., embed=True),
                       strategyid: str = Body(..., embed=True),
                       action: str = Body(..., embed=True),
                       token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid, True)
        if error:
            return error
        if action not in ("filter", "unfilter"):
            return {"result": -2, "message": "action 必须是 filter 或 unfilter"}
        rows = runtime_rows(data_mgr, dog, groupid)
        item = next((row for row in rows["strategies"] if row["id"] == strategyid), None)
        if item is None:
            return {"result": -2, "message": "策略不存在"}
        if not item["filter_supported"]:
            return {"result": -3, "message": item["filter_reason"]}
        try:
            changed = set_strategy_filter(data_mgr.get_group(groupid), strategyid, action == "filter")
        except (OSError, ValueError) as exc:
            return {"result": -3, "message": str(exc)}
        data_mgr.log_action(info, "strategy_target_" + action, groupid + "/" + strategyid)
        return {"result": 0, "message": "过滤配置已写入，等待 CTA 调度并核对通道成交",
                "changed": changed, "filter_configured": action == "filter",
                "execution_confirmed": False,
                "strategies": runtime_rows(data_mgr, dog, groupid)["strategies"]}

    @app.post("/mgr/renamestra", tags=["策略运行管理"])
    def rename_strategy(request: Request, groupid: str = Body(..., embed=True),
                        strategyid: str = Body(..., embed=True), name: str = Body(..., embed=True),
                        token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid, True)
        if error:
            return error
        name = name.strip()
        if not name or len(name) > 80 or any(ord(ch) < 32 for ch in name):
            return {"result": -2, "message": "显示名称须为1至80个字符，不含换行"}
        if not any(row["id"] == strategyid for row in runtime_rows(data_mgr, dog, groupid)["strategies"]):
            return {"result": -2, "message": "策略不存在"}
        if fcntl is None:
            return {"result": -3, "message": "在线改名目前仅支持Linux容器"}
        root = _directory(data_mgr.get_group(groupid))
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (root / ".command.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            names = _read(root / "names.json", {})
            names[strategyid] = name
            atomic_json(root / "names.json", names)
        data_mgr.log_action(info, "strategy_display_name", groupid + "/" + strategyid)
        return {"result": 0, "message": "显示名称已修改，策略ID保持不变", "name": name}

"""Stopped-only Python strategy plans and explicit managed-entry publication."""
from contextlib import contextmanager
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

from fastapi import Body, Request
import yaml
from wtpy.StrategyRuntime import atomic_json
from .ChannelProfiles import channel_transition_lock
from .ManagedStrategyRunner import native_config
from .PlusExecution import EXECUTORS, effective_execution
from .Roles import can_admin, group_allowed
from .StrategySource import analyze, identifier
from .StrategyLibrary import StrategyLibrary

ENTRY = "from wtpy.monitor.ManagedStrategyRunner import main\n\nif __name__ == '__main__':\n    main(__file__)\n"
def _read(path, default=None):
    if not path.exists():
        return deepcopy(default)
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("策略计划文件格式错误")
    return value


def _base(group):
    root = Path(group["path"]).resolve()
    path = next((root / name for name in ("config.yaml", "config.json") if (root / name).is_file()), None)
    if path is None:
        raise ValueError("组合缺少config.yaml或config.json")
    value = yaml.safe_load(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError("组合引擎配置无法读取")
    # Inline WT's optional section files, without exposing credentials to the UI.
    for key in ("parsers", "traders", "executers"):
        if isinstance(value.get(key), str):
            section = yaml.safe_load((root / value[key]).read_text(encoding="utf-8-sig"))
            value[key] = section.get(key, [])
    def absolute(item, key=""):
        if isinstance(item, dict):
            return {name: absolute(child, name) for name, child in item.items()}
        if isinstance(item, list):
            return [absolute(child, key) for child in item]
        if isinstance(item, str) and item and not Path(item).is_absolute() and (root / item).exists():
            return str((root / item).resolve())
        return item
    return absolute(value)


def _empty(group):
    return {"schema": 1, "revision": "", "engine_type": str(group.get("gtype", "cta")).upper(),
            "trading_enabled": False, "strategies": []}


def _state(group):
    root = Path(group["path"]).resolve()
    folder = (root / "managed-strategies").resolve()
    if not folder.is_relative_to(root):
        raise ValueError("策略目录不能跨出组合目录")
    return folder


def validate_managed_start(data_mgr, dog, groupid):
    """Give an actionable error before spawning an unconfigured live child."""
    conf = dog.getAppConf(groupid) or {}
    if "run_managed.py" not in conf.get("param", "") or not data_mgr.has_group(groupid):
        return
    folder = _state(data_mgr.get_group(groupid))
    applied = _read(folder / "applied.json", {})
    if not applied.get("revision"):
        raise ValueError("请先在策略管理中导入策略并应用计划，再手动启动")
    plan = _read(folder / "revisions" / applied["revision"] / "plan.json", {})
    if not plan.get("trading_enabled"):
        raise ValueError("组合尚未启用交易授权；请在策略管理中启用并应用，再手动启动")
    if not any(row.get("enabled") for row in plan.get("strategies", [])):
        raise ValueError("已应用的计划中没有启用的策略")


def _normalize(plan, channels, library=None, previous=None):
    if not isinstance(plan, dict) or plan.get("engine_type") not in ("CTA", "HFT"):
        raise ValueError("页面托管交易组合目前支持CTA和HFT；SEL原生回测保持可用")
    rows = plan.get("strategies", [])
    if not isinstance(rows, list) or len(rows) > 100:
        raise ValueError("策略列表格式错误，最多100个实例")
    result = {"schema": 1, "revision": str(plan.get("revision", "")),
              "engine_type": plan["engine_type"], "trading_enabled": plan.get("trading_enabled") is True,
              "strategies": []}
    seen = set()
    previous_rows = {row["id"]: row for row in (previous or {}).get("strategies", [])}
    for item in rows:
        sid = identifier(item.get("id"))
        if sid in seen:
            raise ValueError("策略实例ID不能重复")
        seen.add(sid)
        library_id, version = item.get("library_id"), item.get("library_version")
        old = previous_rows.get(sid, {})
        if library_id:
            if library is None:
                raise ValueError("策略库未配置")
            if isinstance(version, bool) or not isinstance(version, int) or version < 1:
                raise ValueError("请固定策略库版本，再保存组合计划")
            # Clients select a version; source always comes from that immutable version.
            entry = library.get(library_id, version)
            source = entry["source"]
            version = entry["version"]
        elif old and old.get("source") and not old.get("library_id"):
            # Preserve plans created before the library existed, without accepting
            # replacement code through the portfolio endpoint.
            source = old["source"]
            if item.get("source", source) != source:
                raise ValueError("旧计划源码只能先加入策略库，再选择库版本替换")
        else:
            raise ValueError("请先在策略管理页导入Python文件，再从策略库选择策略")
        if item.get("source", source) != source:
            raise ValueError("组合不能上传或替换源码，请在策略库保存版本后选择")
        cls = analyze(source)[0]
        if cls["engine_type"] != plan["engine_type"] or item.get("class_name", cls["name"]) != cls["name"]:
            raise ValueError("策略类与组合引擎类型不一致")
        params = item.get("params", {})
        if not isinstance(params, dict) or "name" in params or any(not isinstance(key, str) or key.startswith("_") for key in params):
            raise ValueError("策略参数须为JSON对象；name由实例ID指定")
        json.dumps(params, allow_nan=False)
        names = {row["name"] for row in cls["parameters"]}
        missing = {row["name"] for row in cls["parameters"] if row["required"]} - set(params) - {"name"}
        if missing:
            raise ValueError("缺少策略参数：" + ",".join(sorted(missing)))
        if set(params) - names and not cls["accepts_kwargs"]:
            raise ValueError("策略不接受参数：" + ",".join(sorted(set(params) - names)))
        trader = item.get("trader", "")
        if trader not in channels:
            raise ValueError("请选择已登记且启用的交易通道")
        slippage = item.get("slippage", 0)
        if isinstance(slippage, bool) or not isinstance(slippage, int) or not 0 <= slippage <= 1000:
            raise ValueError("滑点须为0至1000的整数")
        execution = item.get("execution")
        if plan["engine_type"] != "HFT":
            supplied = deepcopy(execution or {})
            # Native/effective fields returned by a previous read are derived again.
            supplied = {key: supplied[key] for key in ("id", "params", "volume_weights") if key in supplied}
            if isinstance(supplied.get("params"), dict):
                weights = supplied["params"].pop("volume_weights", None)
                supplied["params"].pop("total_secs", None)
                if weights is not None:
                    supplied.setdefault("volume_weights", weights)
            execution = effective_execution("CTA", 900, 1500, supplied)
        elif execution:
            raise ValueError("HFT直接委托，不使用CTA执行器")
        row = {"id": sid, "name": str(item.get("name") or sid)[:80],
            "class_name": cls["name"], "engine_type": cls["engine_type"], "source": source,
            "library_id": library_id or "", "library_version": version,
            "params": params, "trader": trader, "execution": execution,
            "enabled": item.get("enabled", True) is True, "slippage": slippage, "agent": item.get("agent", True) is True}
        result["strategies"].append(row)
    from .RiskLibrary import resolve_selection
    result["risk"] = resolve_selection(plan.get("risk"))
    return result


@contextmanager
def _stopped(dog, groupid, folder):
    import fcntl
    if not dog.has_app(groupid):
        raise ValueError("组合缺少调度启动配置，无法确认进程状态")
    conf = dog.getAppConf(groupid)
    if conf.get("guard") or conf.get("schedule", {}).get("active"):
        raise ValueError("请先关闭进程守护和自动调度，再编辑策略计划")
    running = dog.is_process_running(groupid) if hasattr(dog, "is_process_running") else dog.isRunning(groupid)
    if running:
        raise ValueError("请先停止组合，再保存或应用策略计划")
    folder.mkdir(mode=0o700, exist_ok=True)
    with (folder / "runner.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("组合运行入口仍在使用，请等待退出") from None
        yield


def _protect_positions(data_mgr, groupid, previous, plan, applying):
    current = {item["id"]: item for item in plan["strategies"]}
    before = {item["id"]: item for item in previous.get("strategies", [])}
    changed = {sid for sid, row in before.items() if sid not in current or
               any(row.get(key) != current[sid].get(key) for key in ("enabled", "trader", "execution", "class_name", "source", "params"))}
    changed |= set(current) - set(before)
    if applying:
        known = set(data_mgr.get_strategies(groupid))
        changed |= known - set(current)
        if not previous.get("revision"):
            changed |= known
    for sid in changed:
        path = Path(data_mgr.get_group(groupid)["path"]) / "generated/stradata" / (sid + ".json")
        ledger = _read(path, {})
        positions = ledger.get("positions", [])
        if any(abs(float(row.get("volume", row.get("volumn", 0)) or 0)) > 0 for row in positions):
            raise ValueError("策略%s仍有持仓，请先处理持仓，再改ID、代码、参数或执行通道" % sid)
    if plan["engine_type"] == "HFT" and changed:
        # HFT owns broker positions, not CTA's theoretical strategy ledger.
        channels = {row["trader"] for rows in (before, current) for sid, row in rows.items() if sid in changed}
        has_history = bool(data_mgr.get_strategies(groupid))
        if applying and not previous.get("revision") and has_history:
            channels |= {row["id"] for row in _base(data_mgr.get_group(groupid)).get("traders", [])}
        root = Path(data_mgr.get_group(groupid)["path"])
        for channel in channels:
            folder = root / "generated/traders" / channel
            path = folder / "rtdata.json"
            if not path.exists():
                if folder.exists() or has_history:
                    raise ValueError("HFT通道%s缺少持仓快照，请先查询柜台资金和持仓再停机修改" % channel)
                continue
            snapshot = _read(path, {})
            for position in snapshot.get("positions", []):
                if any(abs(float(position.get(side, {}).get(key, 0) or 0)) > 0
                       for side in ("long", "short") for key in ("newvol", "prevol")):
                    raise ValueError("HFT通道%s仍有持仓，无法归属到单个策略；请先处理持仓再修改" % channel)
            if any(log.exists() and log.stat().st_mtime_ns > path.stat().st_mtime_ns
                   for log in (folder / "orders.csv", folder / "trades.csv")):
                raise ValueError("HFT通道%s在资金持仓快照后还有委托成交；请刷新柜台资金后再停机修改" % channel)


def register_managed_routes(app, data_mgr, dog, check_auth, deploy_dir):
    library = StrategyLibrary.for_deployment(deploy_dir)
    def authorize(request, token, groupid=None, write=False):
        ok, info = check_auth(request, token)
        if not ok:
            return None, info
        if write and not can_admin(info):
            return None, {"result": -403, "message": "仅管理员可修改组合策略"}
        if groupid and (not data_mgr.has_group(groupid) or not group_allowed(info, groupid)):
            return None, {"result": -403, "message": "组合不存在或无权访问"}
        return info, None

    def snapshot(groupid, info):
        group = data_mgr.get_group(groupid)
        folder = _state(group)
        draft = _read(folder / "draft.json", _empty(group))
        applied = _read(folder / "applied.json", {})
        active_plan = _read(folder / "revisions" / applied["revision"] / "plan.json", {}) if applied.get("revision") else {}
        conf = dog.getAppConf(groupid) or {}
        managed = "run_managed.py" in conf.get("param", "") and bool(applied)
        if not can_admin(info):
            draft = deepcopy(draft)
            for row in draft["strategies"]:
                row.pop("source", None)
                row.pop("params", None)
        from .StrategyControl import runtime_rows
        runtime = runtime_rows(data_mgr, dog, groupid)["strategies"]
        planned = {row["id"] for row in draft["strategies"]}
        return {"result": 0, "plan": draft, "existing_strategies": [row for row in runtime if row["id"] not in planned],
            "strategy_status": {row["id"]: row for row in runtime},
            "managed": managed, "mode": "managed" if managed else "legacy",
            "entry_managed": "run_managed.py" in conf.get("param", ""), "has_applied": bool(applied.get("revision")),
            "running": bool(dog.isRunning(groupid)), "can_manage": can_admin(info),
            "applied_revision": applied.get("revision", ""),
            "applied_trading_enabled": bool(active_plan.get("trading_enabled")),
            "pending_changes": draft["revision"] != applied.get("revision", ""),
            "activation_required": not managed, "process_started": False}

    @app.post("/mgr/strategy_catalog", tags=["组合策略配置"])
    def catalog(request: Request, groupid: str = Body(None, embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid)
        if error:
            return error
        groups = [row for row in data_mgr.get_groups() if group_allowed(info, row["id"])]
        channels = []
        if groupid:
            try:
                channels = [{"id": row["id"], "label": row.get("name") or row["id"]} for row in _base(data_mgr.get_group(groupid)).get("traders", []) if row.get("active", True)]
            except (OSError, ValueError, KeyError, yaml.YAMLError):
                pass
        return {"result": 0, "templates": [{"id": row["id"], "name": row["name"], "engine_type": str(row["gtype"]).upper()} for row in groups],
            "channels": channels, "executors": EXECUTORS, "engine_types": ["CTA", "HFT"], "can_manage": can_admin(info)}

    @app.post("/mgr/strategy_plan", tags=["组合策略配置"])
    def query(request: Request, groupid: str = Body(..., embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid)
        if error:
            return error
        try:
            return snapshot(groupid, info)
        except (OSError, ValueError):
            return {"result": -3, "message": "策略计划文件无法读取"}

    @app.post("/mgr/analyze_strategy", tags=["组合策略配置"])
    def inspect_source(request: Request, source: str = Body(..., embed=True), groupid: str = Body(None, embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid, True)
        if error:
            return error
        try:
            return {"result": 0, "classes": analyze(source)}
        except ValueError as exc:
            return {"result": -2, "message": str(exc)}

    @app.post("/mgr/save_strategy_plan", tags=["组合策略配置"])
    def save(request: Request, groupid: str = Body(..., embed=True), plan: dict = Body(..., embed=True), apply: bool = Body(False, embed=True), confirm_takeover: bool = Body(False, embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid, True)
        if error:
            return error
        try:
            with channel_transition_lock:
                group = data_mgr.get_group(groupid)
                folder = _state(group)
                with _stopped(dog, groupid, folder):
                    previous = _read(folder / "draft.json", _empty(group))
                    if plan.get("revision", "") != previous.get("revision", ""):
                        raise ValueError("计划已被其他页面更新，请重新读取")
                    base = _base(group)
                    channels = {row["id"] for row in base.get("traders", []) if row.get("active", True)}
                    normalized = _normalize(plan, channels, library, previous)
                    if normalized["engine_type"] != str(group["gtype"]).upper():
                        raise ValueError("已有组合不能更换引擎类型，请创建独立组合")
                    applied = _read(folder / "applied.json", {})
                    if apply and not applied and not confirm_takeover:
                        raise ValueError("请确认接管启动入口；原run.py保留，下一次手动启动使用页面计划")
                    active_plan = _read(folder / "revisions" / applied["revision"] / "plan.json", {}) if applied.get("revision") else {}
                    _protect_positions(data_mgr, groupid, active_plan, normalized, apply)
                    normalized["revision"] = uuid.uuid4().hex
                    if apply:
                        revision = folder / "revisions" / normalized["revision"]
                        (revision / "strategies").mkdir(mode=0o700, parents=True)
                        for row in normalized["strategies"]:
                            (revision / "strategies" / (row["id"] + ".py")).write_text(row["source"], encoding="utf-8")
                        atomic_json(revision / "plan.json", normalized)
                        atomic_json(revision / "config.json", native_config(base, normalized, Path(group["path"]).resolve()))
                        atomic_json(revision / "logcfg.json", {"root": {"level": "info", "async": True, "sinks": [{"type": "daily_file_sink", "filename": "logs/managed.log", "pattern": "%v"}]}})
                        entry = Path(group["path"]) / "run_managed.py"
                        if entry.exists() and entry.read_text(encoding="utf-8") != ENTRY:
                            raise ValueError("run_managed.py已存在自定义内容，请先移走该文件")
                        entry.write_text(ENTRY, encoding="utf-8")
                        old_app = deepcopy(dog.getAppConf(groupid))
                        new_app = deepcopy(old_app)
                        new_app.update(param="-u " + str(entry.resolve()), folder=str(entry.parent.resolve()), guard=False)
                        new_app["schedule"]["active"] = False
                        for task in new_app["schedule"].get("tasks", []):
                            task["active"] = False
                        if not (folder / "original-app.json").exists():
                            atomic_json(folder / "original-app.json", old_app)
                        old_applied = _read(folder / "applied.json", {})
                        try:
                            atomic_json(folder / "applied.json", {"revision": normalized["revision"]})
                            dog.applyAppConf(new_app, True)
                        except Exception:
                            atomic_json(folder / "applied.json", old_applied)
                            dog.applyAppConf(old_app, True)
                            raise
                    atomic_json(folder / "draft.json", normalized)
            data_mgr.log_action(info, "apply_strategy_plan" if apply else "save_strategy_plan", groupid)
            return {**snapshot(groupid, info), "message": "计划已应用；组合保持停止，请核对后手动启动" if apply else "草稿已保存；启动配置未改变"}
        except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError) as exc:
            return {"result": -3, "message": str(exc) if isinstance(exc, ValueError) else "策略计划保存失败，启动配置未完成变更"}

    @app.post("/mgr/create_strategy_group", tags=["组合策略配置"])
    def create(request: Request, groupid: str = Body(..., embed=True), name: str = Body(..., embed=True), template_groupid: str = Body(..., embed=True), engine_type: str = Body("CTA", embed=True), token: str = Body(None, embed=True)):
        info, error = authorize(request, token, template_groupid, True)
        if error:
            return error
        try:
            identifier(groupid)
            if not group_allowed(info, groupid):
                raise ValueError("当前管理员无权创建这个组合ID")
            if not name.strip() or len(name) > 80 or engine_type not in ("CTA", "HFT"):
                raise ValueError("请填写组合名称；页面托管交易组合目前支持CTA和HFT")
            with channel_transition_lock:
                if data_mgr.has_group(groupid) or dog.has_app(groupid):
                    raise ValueError("组合ID已经存在")
                root = Path(os.environ.get("WT_MANAGED_GROUP_ROOT") or Path(deploy_dir) / "managed").resolve()
                root.mkdir(parents=True, exist_ok=True)
                target = root / groupid
                if target.exists():
                    raise ValueError("组合目录已存在，请更换ID")
                template = data_mgr.get_group(template_groupid)
                config = _base(template)
                config.setdefault("env", {})["name"] = engine_type.lower()
                config["strategies"] = {}
                config["executers"] = []
                config["routers"] = []
                config["notifier"] = {"active": True, "url": "ipc://" + str(target / "monitor.ipc")}
                config["env"]["filters"] = str(target / "filters.json")
                target.mkdir(mode=0o700)
                for section in ("parsers", "traders"):
                    for index, adapter in enumerate(config.get(section, [])):
                        if "CTP" in str(adapter.get("module", "")).upper() or "flowdir" in adapter:
                            flow = target / "flow" / section / str(index)
                            flow.mkdir(parents=True)
                            adapter["flowdir"] = str(flow) + "/"
                group = {"id": groupid, "name": name.strip(), "path": str(target), "info": "页面管理Python策略", "gtype": engine_type.lower(),
                    "datmod": "mannual", "env": template.get("env", "product"), "mqurl": config["notifier"]["url"]}
                appconf = {"id": groupid, "path": sys.executable, "folder": str(target), "param": "-u " + str(target / "run_managed.py"), "type": 1,
                    "span": 3, "guard": False, "redirect": False, "mqurl": group["mqurl"],
                    "schedule": {"active": False, "weekflag": "0000000", "tasks": [{"active": False, "time": 0, "action": 0} for _ in range(6)]}}
                try:
                    atomic_json(target / "config.json", config)
                    atomic_json(target / "filters.json", {"strategy_filters": {}, "code_filters": {}, "executer_filters": {}})
                    (target / "run_managed.py").write_text(ENTRY, encoding="utf-8")
                    folder = _state(group)
                    folder.mkdir(mode=0o700)
                    atomic_json(folder / "draft.json", _empty(group))
                    if not data_mgr.add_group(group):
                        raise ValueError("组合登记失败")
                    dog.applyAppConf(appconf, True)
                except Exception:
                    if dog.has_app(groupid):
                        dog.delApp(groupid)
                    if data_mgr.has_group(groupid):
                        data_mgr.del_group(groupid)
                    shutil.rmtree(target)
                    raise
            data_mgr.log_action(info, "create_managed_group", groupid + "/" + template_groupid)
            return {**snapshot(groupid, info), "groupid": groupid, "message": "组合已创建；请导入策略并应用，尚未启动交易"}
        except (ValueError, OSError, KeyError, TypeError, yaml.YAMLError) as exc:
            return {"result": -3, "message": str(exc) if isinstance(exc, ValueError) else "组合创建失败"}

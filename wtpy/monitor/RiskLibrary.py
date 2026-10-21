"""Versioned declarative risk rules shared by managed trading and research.

Rules contain data, never import paths or Python source. A published task keeps
the resolved versions; editing the library cannot change a running engine.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import re
import time

from fastapi import Body, Request
from wtpy.StrategyRuntime import atomic_json
from .Roles import can_admin, can_read

RULE_TYPES = {
    "max_order_lots": {"label": "单次增仓手数上限", "unit": "手", "max": 1000000},
    "max_position_lots": {"label": "单策略单合约目标上限", "unit": "手", "max": 1000000},
    "min_available": {"label": "新增开仓可用资金下限", "unit": "元", "max": 1e15},
    "day_loss_pct": {"label": "当日权益亏损上限", "unit": "%", "max": 100},
    "drawdown_pct": {"label": "运行期间权益回撤上限", "unit": "%", "max": 100},
}
IDENTIFIER = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}\Z")


def library_root(root=None):
    return Path(root or os.environ.get("WT_RISK_LIBRARY", "/data/risk-library")).resolve()


def _id(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError("风控ID只能使用字母、数字、下划线或短横线")
    return value


def _read(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("风控文件应为JSON对象")
    return value


def validate_rule(value):
    if not isinstance(value, dict):
        raise ValueError("风控规则应为对象")
    identifier = _id(value.get("id"))
    kind = value.get("type")
    if kind not in RULE_TYPES:
        raise ValueError("请选择已注册的风控类型")
    name = str(value.get("name", "")).strip()
    if not name or len(name) > 80:
        raise ValueError("风控名称须为1至80个字符")
    mode = value.get("mode", "pretrade")
    if mode not in ("pretrade", "alert_only"):
        raise ValueError("风控模式须为pretrade或alert_only")
    params = value.get("params", {})
    limit = params.get("limit") if isinstance(params, dict) else None
    if not isinstance(params, dict) or set(params) != {"limit"} or isinstance(limit, bool) or not isinstance(limit, (float, int)) or not math.isfinite(limit) or not 0 < limit <= RULE_TYPES[kind]["max"]:
        raise ValueError("风控limit须为有效正数，且在类型允许范围内")
    return dict(schema=1, id=identifier, name=name, type=kind, mode=mode, params={"limit": limit})


@contextmanager
def _lock(root):
    import fcntl
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (root / ".lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield


def list_rules(root=None):
    root = library_root(root)
    if not root.exists():
        return []
    rows = []
    for path in sorted(root.glob("*/current.json")):
        value = _read(path)
        rows.append(value)
    return rows


def save_rule(value, actor, root=None):
    rule = validate_rule(value)
    root = library_root(root)
    with _lock(root):
        folder = root / rule["id"]
        folder.mkdir(exist_ok=True, mode=0o700)
        current = _read(folder / "current.json") if (folder / "current.json").exists() else {}
        expected = value.get("version", 0)
        if expected != current.get("version", 0):
            raise ValueError("规则已被其他操作更新，请刷新后再保存")
        versions = folder / "versions"
        versions.mkdir(exist_ok=True, mode=0o700)
        last = max((int(p.stem) for p in versions.glob("*.json")), default=0)
        rule.update(version=last + 1, updated_at=time.time(), updated_by=actor)
        atomic_json(versions / (str(rule["version"]) + ".json"), rule)
        atomic_json(folder / "current.json", rule)
    return rule


def delete_rule(identifier, version, root=None):
    """Retire selection entry; immutable task versions remain readable."""
    root = library_root(root)
    with _lock(root):
        path = root / _id(identifier) / "current.json"
        if not path.exists():
            raise ValueError("风控规则不存在或已移出库")
        if _read(path)["version"] != version:
            raise ValueError("规则已更新，请刷新后再删除")
        path.unlink()


def resolve_selection(selection=None, root=None):
    if selection is None or selection == []:
        return {"schema": 1, "rules": []}
    if not isinstance(selection, dict) or set(selection) - {"schema", "rules"}:
        raise ValueError("风控选择须包含rules列表")
    rows = selection.get("rules", [])
    if not isinstance(rows, list) or len(rows) > 20:
        raise ValueError("最多选择20条风控规则")
    resolved, seen = [], set()
    root = library_root(root)
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("风控选择应为ID和版本")
        identifier, version = _id(row.get("id")), row.get("version")
        if identifier in seen or isinstance(version, bool) or not isinstance(version, int) or version <= 0:
            raise ValueError("风控规则不可重复，须指定有效版本")
        path = root / identifier / "versions" / (str(version) + ".json")
        if not path.is_file():
            raise ValueError("风控版本不存在：" + identifier)
        value = _read(path)
        validate_rule(value)
        resolved.append(value)
        seen.add(identifier)
    return {"schema": 1, "rules": deepcopy(resolved)}


def validate_fast_selection(selection, engine_type):
    for rule in selection.get("rules", []):
        if rule["mode"] != "pretrade":
            continue
        if rule["type"] == "min_available":
            raise ValueError("原生快速Mocker没有可用资金账簿；可用资金风控请使用Plus回测")
        if str(engine_type).lower() == "hft" and rule["type"] in ("day_loss_pct", "drawdown_pct"):
            raise ValueError("HFT快速Mocker没有权益接口；资金风控请使用Plus回测")


def register_risk_library_routes(app, data_mgr, check_auth):
    def auth(request, token, write=False):
        ok, user = check_auth(request, token)
        if not ok:
            return None, user
        if not can_read(user) or (write and not can_admin(user)):
            return None, dict(result=-403, message="仅管理员可修改风控库" if write else "无权访问")
        return user, None

    @app.post("/mgr/risk-library/list", tags=["风控库"])
    def listing(request: Request, token: str = Body(None, embed=True)):
        user, error = auth(request, token)
        if error:
            return error
        return dict(result=0, rules=list_rules(), types=RULE_TYPES, can_edit=can_admin(user))

    @app.post("/mgr/risk-library/get", tags=["风控库"])
    def get(request: Request, id: str = Body(..., embed=True), version: int = Body(..., embed=True), token: str = Body(None, embed=True)):
        user, error = auth(request, token)
        if error:
            return error
        try:
            return dict(result=0, rule=resolve_selection({"rules": [dict(id=id, version=version)]})["rules"][0])
        except (OSError, ValueError) as exc:
            return dict(result=-3, message=str(exc))

    @app.post("/mgr/risk-library/save", tags=["风控库"])
    def save(request: Request, rule: dict = Body(..., embed=True), token: str = Body(None, embed=True)):
        user, error = auth(request, token, True)
        if error:
            return error
        try:
            result = save_rule(rule, user["loginid"])
            data_mgr.log_action(user, "risk_library_save", json.dumps(dict(id=result["id"], version=result["version"])))
            return dict(result=0, rule=result, message="已保存新版本；已应用组合及历史任务保持原版本")
        except (OSError, ValueError) as exc:
            return dict(result=-3, message=str(exc))

    @app.post("/mgr/risk-library/delete", tags=["风控库"])
    def delete(request: Request, id: str = Body(..., embed=True), version: int = Body(..., embed=True), token: str = Body(None, embed=True)):
        user, error = auth(request, token, True)
        if error:
            return error
        try:
            delete_rule(id, version)
            data_mgr.log_action(user, "risk_library_delete", json.dumps(dict(id=id, version=version)))
            return dict(result=0, message="已移出风控库；已发布任务保留固定版本")
        except (OSError, ValueError) as exc:
            return dict(result=-3, message=str(exc))

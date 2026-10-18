"""Single-strategy CTA filters, preserving the engine's other filter settings."""
from contextlib import contextmanager
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import tempfile
import time

import yaml

try:
    import fcntl
except ImportError:
    fcntl = None


def filter_path(group):
    """Use the same env.filters setting as WtCtaEngine, never guess a filename."""
    root = Path(group["path"]).resolve()
    for name in ("config.yaml", "config.json"):
        config = root / name
        if not config.is_file():
            continue
        try:
            obj = yaml.safe_load(config.read_text(encoding="utf-8-sig"))
            env = obj.get("env", {})
            if str(env.get("name", "")).lower() != "cta":
                raise ValueError("目前仅支持 CTA 引擎的目标仓位过滤")
            setting = env.get("filters")
            if not isinstance(setting, str) or not setting.strip():
                raise ValueError("组合未配置 env.filters，无法确认引擎读取的过滤文件")
            path = (root / setting).resolve()
            if not path.is_relative_to(root) or path == root:
                raise ValueError("过滤文件须位于本组合目录内，跨目录配置暂不支持在线修改")
            if path.suffix not in (".json", ".yaml", ".yml"):
                raise ValueError("过滤文件须为 JSON 或 YAML")
            if not path.parent.is_dir():
                raise ValueError("过滤文件所在目录不存在")
            return path
        except (OSError, AttributeError, TypeError, yaml.YAMLError) as exc:
            raise ValueError("无法读取组合的引擎过滤配置") from exc
    raise ValueError("未找到组合 config.yaml/config.json，无法确认过滤链路")


def read_filters(path):
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8-sig")
        obj = json.loads(text) if path.suffix == ".json" else yaml.safe_load(text)
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise ValueError("过滤文件无法解析，保留原文件，请先检查配置") from exc
    if not isinstance(obj, dict):
        raise ValueError("过滤文件须为对象，保留原文件，请先检查配置")
    for key in ("strategy_filters", "code_filters", "executer_filters"):
        if key in obj and not isinstance(obj[key], dict):
            raise ValueError(key + " 须为对象，保留原文件，请先检查配置")
    return obj


def filter_snapshot(group):
    try:
        path = filter_path(group)
        obj = read_filters(path)
        return {"supported": fcntl is not None, "entries": obj.get("strategy_filters", {}),
                "reason": "" if fcntl is not None else "在线过滤目前仅支持 Linux 容器"}
    except ValueError as exc:
        return {"supported": False, "entries": {}, "reason": str(exc)}


def row_filter_state(row, snapshot, running):
    entry = snapshot["entries"].get(row["id"])
    enabled = entry is not None and entry is not False
    supported = snapshot["supported"] and str(row.get("engine", "")).upper() == "CTA"
    action = entry.get("action") if isinstance(entry, dict) else None
    target = entry.get("target") if isinstance(entry, dict) else None
    zero = action == "redirect" and isinstance(target, (int, float)) and not isinstance(target, bool) and target == 0
    reason = snapshot["reason"] or ("仅 CTA 策略支持该目标过滤；HFT/UFT 不适用" if not supported else
        "已配置目标归零；等待 CTA 调度及执行器成交，尚未确认退仓" if enabled and zero and running else
        "已配置其他过滤规则，解除前请核对其用途" if enabled and not zero else
        "进程已停止；配置将在下次启动及 CTA 调度后读取" if not running else
        "把本策略对组合的目标贡献归零；不暂停策略回调")
    row.update({"filter_supported": bool(supported), "filter_enabled": bool(enabled),
                "filter_action": action, "filter_target": target,
                "filter_zero_target": bool(enabled and zero), "filter_reason": reason,
                "filter_execution_confirmed": False})


@contextmanager
def _locked(path):
    if fcntl is None:
        raise ValueError("在线过滤目前仅支持 Linux 容器")
    with path.with_name("." + path.name + ".control.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def set_strategy_filter(group, strategyid, enabled):
    path = filter_path(group)
    with _locked(path):
        obj = read_filters(path)
        entries = obj.setdefault("strategy_filters", {})
        wanted = {"action": "redirect", "target": 0}
        if (enabled and entries.get(strategyid) == wanted) or (not enabled and strategyid not in entries):
            return False
        if enabled:
            entries[strategyid] = wanted
        else:
            entries.pop(strategyid, None)
        # WtFilterMgr compares whole seconds and requires a strictly newer mtime.
        # Wait on this worker thread; do not push timestamps into the future.
        previous = int(path.stat().st_mtime) if path.exists() else 0
        deadline = time.monotonic() + 2
        while int(time.time()) <= previous:
            if time.monotonic() >= deadline:
                raise ValueError("过滤文件时间超前，未写入；请先检查系统时间")
            time.sleep(0.02)
        if path.exists():
            backup = path.with_name(path.name + ".before-control-" + datetime.now().strftime("%Y%m%d_%H%M%S_%f") + ".bak")
            shutil.copy2(path, backup)
        payload = json.dumps(obj, ensure_ascii=False, indent=2) + "\n" if path.suffix == ".json" else yaml.safe_dump(obj, allow_unicode=True, sort_keys=False)
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix="." + path.name + ".", delete=False) as file:
                temp = Path(file.name)
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            if path.exists():
                os.chmod(temp, path.stat().st_mode & 0o777)
            # A backward clock adjustment must not silently suppress native reload.
            if int(temp.stat().st_mtime) <= previous:
                raise ValueError("系统时间变化，未写入；请校准后重试")
            os.replace(temp, path)
            temp = None
        finally:
            if temp is not None:
                temp.unlink(missing_ok=True)
    return True

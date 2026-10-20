"""Switch prepared, isolated CTP environments while a group is stopped."""
from copy import deepcopy
import json
from pathlib import Path
import threading

from fastapi import Body, Request
import yaml


channel_transition_lock = threading.RLock()


def load_profiles(manifest_path=None):
    path = Path(manifest_path or Path.cwd() / "channel-profiles.json")
    if not path.is_file():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("groups"), dict):
        raise ValueError("通道配置登记文件格式错误")
    return value["groups"]


def selected_profile(group, profiles):
    current = Path(group["path"]).resolve()
    return next((item for item in profiles
                 if Path(item["root"]).resolve() / "run" == current), None)


def _validate(profile):
    root = Path(profile["root"]).resolve()
    run = root / "run"
    required = [run / name for name in
                ("config.yaml", "config.ctp-observe.yaml", "run.py", "online_observe.py")]
    required += [root / "datahub" / name for name in ("run_datahub.py", "dtcfg.ctp.yaml")]
    if not all(path.is_file() for path in required):
        raise ValueError("通道环境尚未装配完整")
    for filename in ("config.yaml", "config.ctp-observe.yaml"):
        config = yaml.safe_load((run / filename).read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("引擎配置格式错误")
        env = config.get("env", {})
        if not isinstance(env, dict):
            raise ValueError("引擎运行配置必须为对象")
        product = env.get("product")
        if product is not None and not isinstance(product, dict):
            raise ValueError("引擎产品配置必须为对象")
        for key in ("parsers", "traders"):
            target = config.get(key)
            if not isinstance(target, str) or not (run / target).is_file():
                raise ValueError("行情或交易通道配置文件缺失")
            value = yaml.safe_load((run / target).read_text(encoding="utf-8"))
            if not isinstance(value, dict) or not isinstance(value.get(key), list):
                raise ValueError("行情或交易通道配置格式错误")
    if (run / "acceptance.once").exists():
        raise ValueError("目标环境存在一次性交易许可，请先检查并撤除")
    return root


def profile_snapshot(group, profiles, dog):
    current = selected_profile(group, profiles)
    rows = []
    for item in profiles:
        try:
            _validate(item)
            ready, reason = True, "环境已装配；柜台与行情可用性以登录结果为准"
        except (OSError, ValueError, KeyError, yaml.YAMLError):
            ready, reason = False, "环境文件缺失或配置无效"
        rows.append({"id": item["id"], "label": item["label"],
                     "description": item.get("description", ""),
                     "session_hint": item.get("session_hint", ""),
                     "ready": ready, "reason": reason,
                     "current": bool(current and current["id"] == item["id"]),
                     "datahub_running": bool(dog.isRunning(item["datahub_id"]))})
    return {"result": 0, "message": "OK", "profiles": rows,
            "current": current["id"] if current else None,
            "process_running": bool(dog.isRunning(group["id"])),
            "mode": "observe_only", "restart_required": True}


def switch_profile(data_mgr, dog, groupid, profile, profiles):
    """Only change registered paths; never start, stop or arm a trading engine."""
    with channel_transition_lock:
        if dog.isRunning(groupid):
            raise ValueError("请先停止组合，再切换通道环境")
        old_group = deepcopy(data_mgr.get_group(groupid))
        current = selected_profile(old_group, profiles)
        if current and current["id"] == profile["id"]:
            return False
        root = _validate(profile)
        old_app = deepcopy(dog.getAppConf(groupid))
        if not old_app:
            raise ValueError("组合尚未登记启动配置")
        if "run_managed.py" in old_app.get("param", ""):
            raise ValueError("页面管理组合请在策略计划中选择交易通道；切换独立环境请从目标环境模板新建组合")
        if old_app.get("guard") or old_app.get("schedule", {}).get("active"):
            raise ValueError("请先关闭组合的进程守护和自动调度")
        target_run = root / "run"
        group = deepcopy(old_group)
        group.update(path=str(target_run), mqurl="ipc://" + str(target_run / "monitor.ipc"))
        app = deepcopy(old_app)
        app.update(folder=str(target_run), param="-u " + str(target_run / "run.py"),
                   mqurl=group["mqurl"])
        datahub_id = profile["datahub_id"]
        old_hub = deepcopy(dog.getAppConf(datahub_id))
        if old_hub and (Path(old_hub["folder"]).resolve() != root / "datahub" or
                        old_hub["param"] != "-u " + str(root / "datahub/run_datahub.py")):
            raise ValueError("目标数据中台的启动配置与通道环境不一致")
        hub_created = False
        try:
            if not old_hub:
                hub = deepcopy(old_app)
                hub.update(id=datahub_id, folder=str(root / "datahub"),
                           param="-u " + str(root / "datahub/run_datahub.py"),
                           mqurl="", guard=False)
                hub["schedule"]["active"] = False
                for task in hub["schedule"].get("tasks", []):
                    task["active"] = False
                hub_created = True
                dog.applyAppConf(hub, False)
            if not data_mgr.add_group(group):
                raise RuntimeError("组合登记保存失败")
            dog.applyAppConf(app, True)
        except Exception:
            restored = data_mgr.add_group(old_group)
            try:
                dog.applyAppConf(old_app, True)
                if hub_created:
                    dog.delApp(datahub_id)
            except Exception:
                restored = False
            if not restored:
                raise RuntimeError("通道切换失败且恢复失败，请检查组合启动配置") from None
            raise RuntimeError("通道切换失败，原环境配置已恢复") from None
        return True


def register_channel_routes(app, data_mgr, dog, check_auth, manifest_path=None):
    def authorize(request, token, groupid, control=False):
        ok, info = check_auth(request, token)
        if not ok:
            return None, info
        if not data_mgr.has_group(groupid):
            return None, {"result": -1, "message": "组合不存在"}
        products = info.get("products", "")
        allowed = [item.strip() for item in products.split(",") if item.strip()] if isinstance(products, str) else list(products or [])
        if allowed and groupid not in allowed:
            return None, {"result": -403, "message": "无权访问当前组合"}
        if control and info.get("role") not in ("admin", "superman"):
            return None, {"result": -403, "message": "仅管理员可切换通道环境"}
        return info, None

    @app.post("/mgr/qrychannels", tags=["通道环境"])
    def query(request: Request, groupid: str = Body(..., embed=True),
              token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid)
        if error:
            return error
        try:
            profiles = load_profiles(manifest_path).get(groupid, [])
            result = profile_snapshot(data_mgr.get_group(groupid), profiles, dog)
            result["can_switch"] = info.get("role") in ("admin", "superman")
            return result
        except (OSError, ValueError, KeyError):
            return {"result": -3, "message": "通道环境登记文件不可用"}

    @app.post("/mgr/switchchannel", tags=["通道环境"])
    def switch(request: Request, groupid: str = Body(..., embed=True),
               profileid: str = Body(..., embed=True),
               token: str = Body(None, embed=True)):
        info, error = authorize(request, token, groupid, True)
        if error:
            return error
        try:
            profiles = load_profiles(manifest_path).get(groupid, [])
            profile = next((item for item in profiles if item["id"] == profileid), None)
            if profile is None:
                return {"result": -2, "message": "未登记此通道环境"}
            changed = switch_profile(data_mgr, dog, groupid, profile, profiles)
        except (ValueError, RuntimeError) as error:
            return {"result": -3, "message": str(error)}
        except (OSError, KeyError, yaml.YAMLError):
            return {"result": -3, "message": "通道环境配置读取失败"}
        data_mgr.log_action(info, "channel_profile", groupid + "/" + profileid)
        return {"result": 0, "message": "通道环境已选定；启动组合前先启动对应数据中台",
                "changed": changed, "current": profileid, "process_started": False,
                "trading_permit": False, "restart_required": True,
                "datahub_id": profile["datahub_id"]}

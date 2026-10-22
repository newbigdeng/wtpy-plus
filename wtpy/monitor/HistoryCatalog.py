"""Inventory registered WT storage; snapshot input without loading native code in ASGI."""
from __future__ import annotations
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
from fastapi import Body, Request
from .ChannelProfiles import channel_transition_lock, load_profiles
from .MarketSubscriptions import _inside, _load
from .Roles import can_operate, can_read, group_allowed

PERIODS = {"tick":"Tick", "m1":"1 分钟", "m5":"5 分钟", "d":"日线"}
_cache = {}
_lock = threading.RLock()


def manifest_file(workspace, explicit=None):
    if explicit:
        return Path(explicit)
    for path in (Path(os.environ.get("WT_CHANNEL_PROFILES", "/data/channel-profiles.json")),
                 Path(workspace)/"run/channel-profiles.json", Path.cwd()/"channel-profiles.json"):
        if path.is_file():
            return path
    return Path(workspace)/"run/channel-profiles.json"


def native_worker(payload):
    value = subprocess.run([sys.executable, str(Path(__file__).with_name("HistoryDataWorker.py"))],
        input=json.dumps(payload), capture_output=True, text=True, timeout=90)
    try:
        result = json.loads(value.stdout.splitlines()[-1])
    except (ValueError, IndexError):
        raise ValueError("WT 行情读取失败，原数据未修改") from None
    if value.returncode or result.get("error"):
        raise ValueError(result.get("error") or "WT 行情读取失败")
    return result


def _inspect_many(entries, refresh=False):
    pending, signatures = [], {}
    now = time.monotonic()
    with _lock:
        for item in entries:
            signature = tuple((p, Path(p).stat().st_size, Path(p).stat().st_mtime_ns) for p in item["files"])
            signatures[item["id"]] = signature
            old = _cache.get(item["id"])
            # Throttle only changes to the continuously written realtime files.
            # A completed import replaces DSB immediately, even within the TTL.
            history = tuple(row for row in signature if row[0].endswith(".dsb"))
            old_history = tuple(row for row in old[0] if row[0].endswith(".dsb")) if old else None
            has_realtime = any(p.endswith(".dmb") for p in item["files"])
            cached = old and ((old[0] == signature and not has_realtime) or
                              (history == old_history and now-old[1] < 30))
            if cached and not refresh:
                item.update(old[2])
            else:
                pending.append(dict(id=item["id"],period=item["period"],files=item["files"]))
    if pending:
        results = native_worker(dict(action="inspect_many",entries=pending))["entries"]
        with _lock:
            for item in entries:
                if item["id"] in results:
                    result = results[item["id"]]
                    _cache[item["id"]] = (signatures[item["id"]],now,result)
                    item.update(result)
    for item in entries:
        item["ticks"] = item["rows"] if item["period"] == "tick" else 0
    return entries


def dataset_entries(workspace="/workspace", manifest_path=None, refresh=False):
    """IDs are configuration identities, never user paths or arbitrary hashes."""
    entries, contexts, owners = [], [], {}
    for groupid, profiles in load_profiles(manifest_file(workspace, manifest_path)).items():
        for profile in profiles:
            root = Path(profile["root"]).resolve()
            folder = root/"datahub"
            config_path = folder/"dtcfg.ctp.yaml"
            if not config_path.is_file():
                continue
            config = _load(config_path)
            storage = _inside(root, folder, config["writer"]["path"])
            contexts.append((groupid, profile, root, folder, config, storage))
            shared = owners.setdefault(str(storage), dict(groups=set(), collectors=set()))
            shared["groups"].add(groupid)
            shared["collectors"].add(profile["datahub_id"])
    for groupid, profile, root, folder, config, storage in contexts:
        base = {key: str(_inside(root, folder, value)) for key, value in config.get("basefiles", {}).items()
                if key != "utf-8" and isinstance(value, str) and "," not in value}
        contracts = _load(Path(base["contract"]))
        common = Path(base["contract"]).parent
        for key, name in (("hot","hots.json"), ("fee","fees.json")):
            if (common/name).is_file():
                base[key] = str(_inside(root, common, name))
        for exchange, rows in contracts.items():
            for ticker, info in rows.items():
                product = info.get("product", "")
                if not product or not ticker.startswith(product):
                    continue
                code = exchange+"."+product+"."+ticker[len(product):]
                for period, directory in (("tick","ticks"),("m1","min1"),("m5","min5"),("d","day")):
                    files = sorted((storage/"his"/directory/exchange).glob("*/"+ticker+".dsb")) if period == "tick" else [storage/"his"/directory/exchange/(ticker+".dsb")]
                    files = [path for path in files if path.is_file() and not path.is_symlink()
                             and path.resolve().is_relative_to(storage)]
                    realtime = storage/"rt"/directory/exchange/(ticker+".dmb")
                    if realtime.is_file() and not realtime.is_symlink() and realtime.resolve().is_relative_to(storage):
                        files.append(realtime)
                    if not files:
                        continue
                    identifier = "local_"+str(groupid)+"_"+str(profile["id"])+"_"+exchange+"_"+ticker+"_"+period
                    if not re.fullmatch(r"[A-Za-z0-9_-]+", identifier):
                        continue
                    item = dict(id=identifier, groupid=groupid, profileid=profile["id"],
                        collector=profile["datahub_id"], code=code, product=exchange+"."+product,
                        ticker=ticker, exchange=exchange, period=period, source="recorded",
                        kind="native_ticks" if period == "tick" else "native_bars",
                        files=[str(path) for path in files], basefiles=str(common), basefiles_map=base,
                        storage=str(storage), profile_root=str(root),
                        storage_groups=sorted(owners[str(storage)]["groups"]),
                        collectors=sorted(owners[str(storage)]["collectors"]),
                        label=profile.get("label", profile["id"])+" · "+code+" · "+PERIODS[period],
                        bytes=sum(path.stat().st_size for path in files),
                        formats=sorted({path.suffix[1:].upper() for path in files}),
                        model="recorded_tick" if period == "tick" else "closed_bar_only")
                    entries.append(item)
    return _inspect_many(entries, refresh=refresh)


def public_dataset(item):
    hidden = {"files", "basefiles", "basefiles_map", "storage", "profile_root", "collector", "collectors", "storage_groups", "ticker", "exchange", "kind"}
    return {key: value for key, value in item.items() if key not in hidden}


def selection_bounds(item, start, end):
    def convert(value, last):
        value = int(value)
        return value*10000+(2359 if last else 0) if len(str(value)) == 8 else value
    start = convert(start or item["start"], False)
    end = convert(end or item["end"], True)
    if start > end:
        raise ValueError("开始时间不能晚于结束时间")
    if not item.get("rows") or end < item["start"] or start > item["end"]:
        raise ValueError("所选日期没有本地行情，请先采集或导入")
    return start, end


def prepare_native_dataset(workspace, dataset, start, end, taskfolder):
    with channel_transition_lock:
        return _prepare_native_dataset(workspace, dataset, start, end, taskfolder)


def _prepare_native_dataset(workspace, dataset, start, end, taskfolder):
    catalog = dataset_entries(workspace)
    item = next((row for row in catalog if row["id"] == dataset), None)
    if item is None:
        raise ValueError("历史数据集不存在，请刷新历史数据管理")
    start, end = selection_bounds(item, start, end)
    # Validate the requested interval separately from earlier warm-up bars.
    selected = native_worker(dict(action="inspect", files=item["files"], period=item["period"], start=start, end=end))
    if not selected["rows"]:
        raise ValueError("所选时间段没有行情，不创建空回测")
    snapshot = native_worker(dict(action="export", files=item["files"], period=item["period"],
        exchange=item["exchange"], ticker=item["ticker"], start=start if item["period"]=="tick" else 0, end=end,
        destination=str(Path(taskfolder)/"market-data")))
    # Native CTA still needs its main bars when Tick drives fills. Preserve actual
    # earlier bars for indicator warm-up; the replayer controls their visibility.
    for bars in catalog:
        if bars["period"] == "tick" or bars["id"] == item["id"] or bars["code"] != item["code"] or bars["storage"] != item["storage"] or not bars["rows"]:
            continue
        native_worker(dict(action="export", files=bars["files"], period=bars["period"],
            exchange=bars["exchange"], ticker=bars["ticker"], start=0, end=end,
            destination=str(Path(taskfolder)/"market-data")))
    base = Path(taskfolder)/"market-base"
    base.mkdir(exist_ok=True)
    frozen = {}
    for key, filename in item["basefiles_map"].items():
        destination = base/Path(filename).name
        shutil.copyfile(filename, destination)
        frozen[key] = str(destination)
    return dict(snapshot, code=item["code"], period=item["period"], tick_enabled=item["period"]=="tick",
                basefiles=frozen, model=item["model"], dataset=dataset,
                source="recorded", start=start, end=end)


def register_history_catalog_routes(app, data_mgr, dog, check_auth, manifest_path=None, data_dir=None, bt_mon=None, plus_manager=None):
    workspace = os.environ.get("WT_PLUS_WORKSPACE", "/workspace")
    manifest = manifest_file(workspace, manifest_path or Path(data_dir or Path.cwd())/"channel-profiles.json")
    previews = {}

    def authorize(request, token, control=False):
        ok, user = check_auth(request, token)
        if not ok:
            return None, user
        if not (can_operate(user) if control else can_read(user)):
            return None, dict(result=-403, message="游客模式不支持操作" if control else "无查看权限")
        return user, None

    def entries(user, refresh=False):
        return [item for item in dataset_entries(workspace, manifest, refresh=refresh) if group_allowed(user, item["groupid"])]

    def blocked(item, user):
        if not all(group_allowed(user, groupid) for groupid in item["storage_groups"]):
            return "此存储被其他组合共享，无全部组合权限时不能删除"
        if any(dog.isRunning(collector) for collector in item["collectors"]):
            return "数据中台正在采集，请先在调度中心停止"
        if any(dog.isRunning(group["id"]) for group in data_mgr.get_groups()):
            return "有组合正在运行，请先停止组合，避免删除共享行情"
        # Native and Plus readers must finish before mutating their source inventory.
        plus = plus_manager() if callable(plus_manager) else plus_manager
        native = bt_mon() if callable(bt_mon) else bt_mon
        if plus and any(proc.poll() is None for proc in plus.processes.values()):
            return "有 Plus 回测正在运行，请先等待完成"
        if native and getattr(native, "task_map", {}):
            return "有原生回测正在运行，请先等待完成"
        return ""

    @app.post("/mgr/history/catalog", tags=["历史数据"])
    @app.post("/mgr/history/datasets", tags=["历史数据"])
    def catalog(request: Request, refresh: bool = Body(False, embed=True), token: str = Body(None, embed=True)):
        user, error = authorize(request, token)
        if error:
            return error
        result = []
        for item in entries(user, refresh=refresh):
            reason = blocked(item, user)
            result.append(dict(public_dataset(item), delete_reason=reason,
                can_delete=can_operate(user) and not reason and any(Path(p).suffix==".dsb" for p in item["files"])))
        return dict(result=0, entries=result, can_manage=can_operate(user),
                    message="DSB 保存历史；DMB 为采集器实时文件，页面永不删除 DMB")

    @app.post("/mgr/history/delete_preview", tags=["历史数据"])
    def preview(request: Request, id: str = Body(..., embed=True), token: str = Body(None, embed=True)):
        user, error = authorize(request, token, True)
        if error:
            return error
        with channel_transition_lock:
            item = next((row for row in entries(user) if row["id"] == id), None)
            if not item:
                return dict(result=-1, message="数据对象不存在")
            reason = blocked(item, user)
            files = [p for p in item["files"] if Path(p).suffix==".dsb"]
            if reason or not files:
                return dict(result=-1, message=reason or "只有实时 DMB，没有可删除的历史 DSB")
            signature = [(p, Path(p).stat().st_size, Path(p).stat().st_mtime_ns) for p in files]
            confirmation = str(time.time_ns())
            previews[confirmation] = (time.monotonic(), user["loginid"], id, signature)
            return dict(result=0, confirmation=confirmation, dataset=public_dataset(item),
                        files=[dict(name=str(Path(p).relative_to(item["storage"])), bytes=Path(p).stat().st_size) for p in files],
                        message="确认后删除所列历史 DSB；实时 DMB 保留。删除不可恢复")

    @app.post("/mgr/history/delete", tags=["历史数据"])
    def delete(request: Request, id: str = Body(..., embed=True), confirmation: str = Body(..., embed=True), token: str = Body(None, embed=True)):
        user, error = authorize(request, token, True)
        if error:
            return error
        with channel_transition_lock:
            proof = previews.pop(confirmation, None)
            item = next((row for row in entries(user) if row["id"] == id), None)
            if not proof or not item or proof[1:3] != (user["loginid"], id) or time.monotonic()-proof[0] > 120:
                return dict(result=-1, message="删除预览已失效，请重新预览")
            reason = blocked(item, user)
            if reason:
                return dict(result=-1, message=reason)
            current = [(p, Path(p).stat().st_size, Path(p).stat().st_mtime_ns) for p in item["files"] if Path(p).suffix==".dsb"]
            if current != proof[3]:
                return dict(result=-1, message="数据已更新，请重新预览")
            for filename, _, _ in current:
                path = Path(filename).resolve()
                if not path.is_relative_to(Path(item["storage"]).resolve()/"his"):
                    return dict(result=-1, message="只能删除登记存储中的历史 DSB")
            for filename, _, _ in current:
                Path(filename).unlink()
            data_mgr.log_action(user, "history_delete", id)
            return dict(result=0, message="已删除历史 DSB，实时采集文件保留", files=len(current))

    return dataset_entries

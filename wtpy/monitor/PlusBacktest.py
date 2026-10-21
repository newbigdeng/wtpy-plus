"""Isolated historical replay of the real WT engine/trader path.

This manager never imports native libraries in the web process. Historical input
and run directories are server-owned; source is an immutable snapshot per task.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid
from .TradeFinance import dataset_finance, effective_finance
from .PlusExecution import EXECUTORS, effective_execution
from .HistoryCatalog import dataset_entries, prepare_native_dataset, selection_bounds
from .RiskLibrary import resolve_selection

TERMINAL = frozenset(("completed", "stopped", "failed"))
LIMITATIONS = [
    "Plus复用实盘引擎、CTA执行器和TraderAdapter；柜台仅为隔离的TraderMocker，不连接CTP。",
    "仅支持CTA/HFT Python策略；UFT尚无Python实盘桥，不能假装使用HFT替代。",
    "Tick按历史顺序驱动。新委托最早在后续Tick成交，按对手一档价格及可见数量撮合；不模拟真实排队、网络延迟或盘口冲击。",
    "手续费、资金冻结和保证金拒单由TraderMocker原生账户计算；费率来自任务快照，不代表期货公司实际费率。",
    "滑点按不利方向增加指定跳数，限价委托不突破委托价格；不模拟真实排队、盘口冲击、保证金优惠或强平。",
    "结束和停止均保留未成交委托及持仓快照，不自动强制平仓；不会在数据结束后编造行情成交。",
]
def read_json(path, fallback=None):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value
    except (OSError, ValueError):
        return fallback
def write_json(path, data):
    path = Path(path)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    with temporary.open("x", encoding="utf-8") as out:
        os.chmod(temporary, 0o600)
        json.dump(data, out, ensure_ascii=False, indent=2, allow_nan=False)
        out.write("\n")
    os.replace(temporary, path)
def dataset_catalog(workspace):
    workspace = Path(workspace).resolve()
    choices = []
    real = workspace / "validation/replay/storage/real/bin/ticks/SHFE.rb.2701_tick_20261013.dsb"
    if real.is_file():
        choices.append({"id": "rb2701-recorded-20261013", "label": "演示 · 螺纹钢2701实收Tick片段（2026-10-13）",
                        "code": "SHFE.rb.2701", "start": 202610131037, "end": 202610131039,
                        "ticks": 240, "source": "recorded", "kind": "dsb", "file": str(real),
                        "basefiles": str(workspace / "validation/replay/common")})
    common = workspace / "validation/replay/common"
    if common.is_dir():
        choices.append({"id": "rb2701-synthetic-path", "label": "演示 · 螺纹钢2701合成链路数据（非市场数据）",
                        "code": "SHFE.rb.2701", "start": 202610120900, "end": 202610120905,
                        "ticks": 360, "source": "synthetic", "kind": "synthetic", "basefiles": str(common)})
    # Only this server-owned manifest supplies paths. HTTP callers select IDs.
    manifest = workspace / "run/plus-datasets.json"
    if manifest.exists():
        try:
            registered = json.loads(manifest.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ValueError("Plus数据集登记文件无法读取："+str(error)) from error
        if not isinstance(registered, list):
            raise ValueError("plus-datasets.json必须为数据集数组")
        ids = set()
        for original in registered:
            if not isinstance(original, dict):
                raise ValueError("Plus数据集条目必须为对象")
            item = dict(original)
            identifier = item.get("id", "")
            if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", identifier) or identifier in ids:
                raise ValueError("Plus数据集编号无效或重复")
            ids.add(identifier)
            if not isinstance(item.get("label"), str) or not item["label"].strip():
                raise ValueError("Plus数据集缺少名称："+identifier)
            if not re.fullmatch(r"[A-Za-z0-9_]+\.[A-Za-z0-9_]+\.[A-Za-z0-9_]+", str(item.get("code", ""))):
                raise ValueError("Plus数据集合约格式无效："+identifier)
            try:
                item["start"] = int(item["start"]); item["end"] = int(item["end"]); item["ticks"] = int(item["ticks"])
                import datetime
                for boundary in (item["start"], item["end"]):
                    datetime.datetime.strptime(str(boundary), "%Y%m%d%H%M")
                if item["start"] > item["end"] or item["ticks"] <= 0:
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                raise ValueError("Plus数据集时间范围或Tick数量无效："+identifier) from None
            if item.get("kind", "dsb") != "dsb":
                raise ValueError("登记数据集必须使用DSB Tick文件："+identifier)
            item["kind"] = "dsb"
            if item.get("source", "recorded") not in ("recorded", "synthetic"):
                raise ValueError("Plus数据来源必须为recorded或synthetic："+identifier)
            item["source"] = item.get("source", "recorded")
            for key in ("file", "basefiles"):
                value = item.get(key)
                if not isinstance(value, str) or not value:
                    raise ValueError("Plus数据集缺少"+key+"："+identifier)
                path = Path(value)
                path = (path if path.is_absolute() else workspace/path).resolve()
                if not (path.is_file() if key == "file" else path.is_dir()):
                    raise ValueError("Plus数据集路径不存在："+identifier+"/"+key)
                item[key] = str(path)
            needed = ("commodities.json", "contracts.json", "sessions.json", "holidays.json", "hots.json", "fees.json")
            if any(not (Path(item["basefiles"])/name).is_file() for name in needed):
                raise ValueError("Plus数据集基础资料不完整："+identifier)
            choices = [x for x in choices if x["id"] != identifier]
            choices.append(item)
    # Native storage is the source of truth; demo snippets remain explicitly labelled.
    choices = [item for item in dataset_entries(workspace) if item["period"] == "tick" and item["rows"]] + choices
    return choices
class PlusBacktestManager:
    def __init__(self, root, *, workspace="/workspace", max_concurrent=2):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        os.chmod(self.root, 0o700)
        self.workspace = Path(workspace).resolve()
        self.max_concurrent = max(1, min(int(max_concurrent), 4))
        self.lock = threading.RLock()
        self.processes = {}
        self.stopping = set()
        self.closing = False
        for path in self.root.glob("*/task.json"):
            task = read_json(path, {})
            if task and task.get("status") not in TERMINAL:
                task.update(status="failed", error="监控进程重启中断了此任务；未把残留结果视为完成。", updated_at=time.time())
                write_json(path, task)
    def capabilities(self):
        private = {"file", "files", "basefiles", "basefiles_map", "kind", "storage", "profile_root", "collector", "collectors", "storage_groups", "ticker", "exchange"}
        datasets = [{**{k:v for k,v in x.items() if k not in private},
                     "finance": dataset_finance(x)} for x in dataset_catalog(self.workspace)]
        runtime = self.workspace / "artifacts/plus-native/libWtPorter.so"
        return {"engines": ["CTA", "HFT"], "executors": EXECUTORS, "datasets": datasets, "limitations": LIMITATIONS,
                "max_concurrent": self.max_concurrent, "available": runtime.is_file(),
                "route": ["Historical Tick", "WT DataWriter", "Live CTA/HFT Engine",
                          "CTA Local Executer / HFT Context", "TraderAdapter", "TraderMocker", "Native Returns"]}
    def create(self, user, straid, strategy_code, engine_type, code, dataset, start=None, end=None, capital=1000000, period="m1", params=None, finance=None, execution=None, risk=None):
        kind = str(engine_type).upper()
        if kind not in ("CTA", "HFT"):
            raise ValueError("Plus目前支持CTA和HFT；UFT/SEL尚未接入对应实盘桥")
        if not isinstance(strategy_code, str) or not strategy_code.strip() or len(strategy_code.encode("utf-8")) > 1024*1024:
            raise ValueError("策略源码为空或超过1MB")
        compile(strategy_code, "<Plus strategy snapshot>", "exec")
        item = next((x for x in dataset_catalog(self.workspace) if x["id"] == dataset), None)
        if item is None:
            raise ValueError("历史数据集不存在")
        if code != item["code"]:
            raise ValueError("合约必须与所选Tick数据集相同；连续合约须先准备对应映射")
        finance = effective_finance(item, finance)
        start, end = selection_bounds(dict(item, rows=item.get("rows", item.get("ticks", 0))), start, end)
        risk = resolve_selection(risk)
        execution = effective_execution(kind, start, end, execution)
        capital = float(capital)
        if not math.isfinite(capital) or capital <= 0 or capital > 1e12:
            raise ValueError("初始资金必须为有效正数且不超过1万亿")
        if not re.fullmatch(r"m[1-9][0-9]{0,3}", str(period)):
            raise ValueError("Plus当前支持分钟线周期，如m1、m5")
        params = params or {}
        if not isinstance(params, dict) or any(not isinstance(k, str) or k.startswith("_") or k in ("name","code","period","capital") for k in params):
            raise ValueError("构造参数必须为公开参数字典，不能覆盖name/code/period/capital")
        if not self.capabilities()["available"]:
            raise RuntimeError("Plus原生运行库尚未就绪")
        with self.lock:
            if self.closing: raise RuntimeError("监控正在退出")
            if sum(p.poll() is None for p in self.processes.values()) >= self.max_concurrent:
                raise RuntimeError("Plus并发任务已达上限，请等待或停止现有任务")
            taskid = uuid.uuid4().hex
            folder = self.root / taskid;folder.mkdir(mode=0o700)
            market_snapshot = prepare_native_dataset(self.workspace, dataset, start, end, folder) if item["kind"] == "native_ticks" else None
            now = time.time()
            task = {"id": taskid, "user": str(user), "straid": str(straid), "engine_type": kind,
                    "code": code, "dataset": item["id"], "market_source": item["source"], "start": start, "end": end, "capital": capital,
                    "period": period, "params": params, "finance": finance, "execution": execution, "risk": risk,
                    "market_snapshot": market_snapshot, "data_model": "recorded_tick" if item["source"] == "recorded" else "synthetic_demo",
                    "status": "queued", "processed": 0,
                    "total": 0, "progress": 0, "orders": 0, "trades": 0,
                    "created_at": now, "updated_at": now, "error": "", "mode": "plus"}
            (folder / "MyStrategy.py").write_text(strategy_code, encoding="utf-8")
            os.chmod(folder/"MyStrategy.py", 0o600)
            write_json(folder/"task.json", task)
            env = dict(os.environ, PYTHONPATH=str(self.workspace/"src/wtpy"),
                       WT_PLUS_NATIVE_DIR=str(self.workspace/"artifacts/plus-native"),
                       PYTHONUNBUFFERED="1")
            log = (folder/"runner.log").open("wb")
            try:
                proc = subprocess.Popen([sys.executable, "-m", "wtpy.monitor.PlusReplayRunner", str(folder), str(self.workspace)],
                                        cwd=folder, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            finally:
                log.close()
            self.processes[taskid] = proc
            threading.Thread(target=self._watch, args=(taskid, proc), daemon=True, name="plus-reap").start()
            return task
    def _watch(self, taskid, proc):
        result = proc.wait()
        with self.lock:
            path = self.root/taskid/"task.json"
            task = read_json(path, {})
            report=read_json(self.root/taskid/"report.json", {})
            if task and (self.root/taskid/"stop.request").exists() and task.get("status") not in TERMINAL:
                message = "策略未在5秒内退出，已强制停止；仅保留已写出的数据。" if result == -signal.SIGKILL else ""
                task.update(status="stopped", error=message, updated_at=time.time())
                write_json(path, task)
            elif task and (task.get("status") not in TERMINAL or (task.get("status")=="completed" and (result!=0 or not report or task.get("processed",0)<task.get("total",1)))):
                task.update(status="failed", error="Plus子进程未正常完成，退出码"+str(result), updated_at=time.time())
                write_json(path, task)
            if task:
                task["exitcode"]=result;write_json(path, task)
            self.processes.pop(taskid, None)
            self.stopping.discard(taskid)
    def _owned(self, user, taskid):
        if not re.fullmatch(r"[a-f0-9]{32}", str(taskid)): raise ValueError("无效任务编号")
        path = self.root / taskid
        task = read_json(path/"task.json", {})
        if not task or task.get("user") != str(user): raise ValueError("任务不存在或不属于当前用户")
        return path, task
    def list_tasks(self, user, straid=None):
        tasks = [read_json(p, {}) for p in self.root.glob("*/task.json")]
        return sorted([x for x in tasks if x.get("user")==str(user) and (straid is None or x.get("straid")==str(straid))],
                      key=lambda x:x["created_at"], reverse=True)
    def detail(self, user, taskid):
        path, task = self._owned(user, taskid)
        report = read_json(path/"report.json", {}) or {}
        result = {key:report.get(key, []) for key in ("orders","trades","funds","positions","events","rejections")}
        result.update(task=task, route=report.get("route", self.capabilities()["route"]),
                      risk=report.get("risk", {"selection":task.get("risk"), "events":[]}),
                      data_model=report.get("data_model", task.get("data_model")),
                      limitations=report.get("limitations", LIMITATIONS if task.get("finance") else ["旧任务使用零手续费账簿；没有保证金或滑点模型，需重新运行以应用新参数。"]), summary=report.get("summary", {}),
                      log_tail=(path/"runner.log").read_text(encoding="utf-8",errors="replace")[-12000:] if (path/"runner.log").exists() else "")
        return result
    def stop(self, user, taskid):
        with self.lock:
            path, task = self._owned(user, taskid)
            if task["status"] in TERMINAL: return task
            (path/"stop.request").touch(mode=0o600, exist_ok=True)
            proc = self.processes.get(taskid)
            if proc and proc.poll() is None and taskid not in self.stopping:
                self.stopping.add(taskid)
                proc.send_signal(signal.SIGTERM)
                threading.Thread(target=self._finish_stop, args=(proc,), daemon=True, name="plus-stop").start()
            return read_json(path/"task.json", task)
    @staticmethod
    def _finish_stop(proc):
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    def shutdown(self):
        with self.lock:
            self.closing=True
            items=list(self.processes.items())
            for taskid, proc in items:
                (self.root/taskid/"stop.request").touch(mode=0o600,exist_ok=True)
                if proc.poll() is None: proc.send_signal(signal.SIGTERM)
        deadline=time.monotonic()+10
        for taskid,proc in items:
            try:proc.wait(timeout=max(.1,deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL);proc.wait(timeout=3)

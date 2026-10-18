"""Opt-in control bridge for Python live strategies.

Pause is a decision gate, not an engine stop: existing native targets,
conditional orders and working orders may still execute. No order is
cancelled and no position is flattened by this module.
"""
import atexit
try:
    import fcntl
except ImportError:  # Other monitor features remain importable on Windows.
    fcntl = None
import json
import os
from pathlib import Path
import threading
import time
import uuid

DECISIONS = frozenset(("on_tick", "on_bar", "on_calculate",
    "on_calculate_done", "on_order_queue", "on_order_detail", "on_transaction"))
SUBMISSIONS = frozenset(("stra_set_position", "stra_enter_long", "stra_exit_long",
    "stra_enter_short", "stra_exit_short", "stra_buy", "stra_sell"))
SEMANTICS = ("暂停行情、K线和计算决策；生命周期、委托、成交、持仓及通道回报继续，"
             "这些回调的新交易提交也会被拒绝。暂停操作不会自动撤单、平仓或停止引擎；"
             "已提交的委托、条件单及执行器目标仍可能继续执行。"
             "恢复只接收后续事件，不补跑暂停期间信号；重启保留暂停设置。")

def atomic_json(path, payload):
    path = Path(path)
    temp = path.with_name(path.name + "." + str(os.getpid()) + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temp.open("x", encoding="utf-8") as out:
            os.chmod(temp, 0o600)
            json.dump(payload, out, ensure_ascii=False)
            out.flush()
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)

class _ContextView:
    def __init__(self, context, gate):
        self._context, self._gate = context, gate

    def __getattr__(self, name):
        value = getattr(self._context, name)
        if name not in SUBMISSIONS or not callable(value):
            return value
        def submit(*args, **kwargs):
            with self._gate.lock:
                if self._gate.paused:
                    with self._gate.state_lock:
                        self._gate.blocked_submissions += 1
                    return [] if name in ("stra_buy", "stra_sell") else None
                return value(*args, **kwargs)
        return submit

class _Gate:
    def __init__(self, name, engine_type, desired):
        self.name, self.engine_type = name, engine_type
        self.lock = threading.RLock()
        self.state_lock = threading.RLock()
        self.paused = desired == "paused"
        self.applied_at = time.time()
        self.blocked_submissions = 0
        self.views = {}

    def view(self, context):
        identity = id(context)
        if identity not in self.views:
            self.views[identity] = _ContextView(context, self)
        return self.views[identity]

class _ControlledStrategy:
    def __init__(self, strategy, gate):
        self._strategy, self._gate = strategy, gate

    def name(self):
        return self._strategy.name()

    def __getattr__(self, name):
        value = getattr(self._strategy, name)
        if not name.startswith("on_") or not callable(value):
            return value
        def callback(context, *args, **kwargs):
            with self._gate.lock:
                if name in DECISIONS and self._gate.paused:
                    return None
                return value(self._gate.view(context), *args, **kwargs)
        return callback

class StrategyRuntime:
    def __init__(self, directory, query_fund=None):
        if fcntl is None:
            raise RuntimeError('Strategy runtime control currently requires a Linux container')
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self._lock_file = (self.directory / ".runner.lock").open("a")
        try:
            fcntl.flock(self._lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lock_file.close()
            raise
        self.boot_id = uuid.uuid4().hex
        self._gates, self._registry_lock = {}, threading.RLock()
        self._stop = threading.Event()
        self._closed = False
        self._revision = ""
        self._error = None
        self._query_fund = query_fund
        self._query_result = None
        atexit.register(self.close)
        self._thread = threading.Thread(target=self._serve, name="wt-strategy-control", daemon=True)
        self._write_status()
        self._thread.start()

    def _desired(self):
        try:
            obj = json.loads((self.directory / "desired.json").read_text(encoding="utf-8"))
            states = obj.get("states", {})
            if not isinstance(states, dict) or any(v not in ("paused", "running") for v in states.values()):
                raise ValueError("Invalid desired state")
            return obj
        except FileNotFoundError:
            return {"states": {}, "revision": ""}
    
    def wrap(self, strategy, engine_type):
        name = strategy.name()
        with self._registry_lock:
            if name in self._gates:
                raise ValueError("Duplicate controlled strategy name: " + name)
            gate = _Gate(name, engine_type, self._desired()["states"].get(name))
            self._gates[name] = gate
        self._write_status()
        return _ControlledStrategy(strategy, gate)

    def _write_status(self, alive=True):
        # State snapshots must not wait for a long user callback. _serve takes
        # the callback lock before changing state and publishing its revision,
        # so command acknowledgement still follows the in-flight callback.
        with self._registry_lock:
            rows = []
            for gate in self._gates.values():
                with gate.state_lock:
                    rows.append({"id": gate.name, "name": gate.name,
                        "engine": gate.engine_type, "source": "python",
                        "state": "paused" if gate.paused else "running",
                        "applied_at": gate.applied_at,
                        "blocked_submissions": gate.blocked_submissions})
            atomic_json(self.directory / "status.json", {
                "schema": 1, "pid": os.getpid(), "boot_id": self.boot_id,
                "alive": bool(alive and not self._closed), "updated_at": time.time(), "revision": self._revision,
                "error": self._error, "strategies": rows, "semantics": SEMANTICS,
                "capabilities": ["query_fund"] if self._query_fund else [],
                "query_fund": self._query_result})

    def _handle_fund_query(self):
        if not self._query_fund or self._closed:
            return False
        try:
            command = json.loads((self.directory / "query-fund.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False
        revision = command.get("revision")
        if not isinstance(revision, str) or not revision or len(revision) > 80:
            raise ValueError("Invalid fund query revision")
        if self._query_result and self._query_result["revision"] == revision:
            return False
        channel = command.get("channel", "")
        if not isinstance(channel, str):
            raise ValueError("Invalid fund query channel")
        now = time.time() * 1000
        expires = command.get("expires_at", 0)
        if not isinstance(expires, (int, float)) or expires <= now:
            accepted, error = False, "资金查询请求已过期"
        else:
            try:
                accepted = bool(self._query_fund(channel))
                error = "" if accepted else "交易通道尚未就绪，未提交资金查询"
            except (RuntimeError, OSError) as exception:
                accepted, error = False, str(exception)
        self._query_result = {"revision": revision, "accepted": accepted,
                              "error": error, "handled_at": time.time() * 1000}
        return True

    def _serve(self):
        next_heartbeat = 0
        while not self._stop.is_set():
            try:
                desired = self._desired()
                revision = desired.get("revision", "")
                changed = self._handle_fund_query()
                if revision != self._revision:
                    with self._registry_lock:
                        gates = list(self._gates.values())
                    applied = True
                    for gate in gates:
                        target = desired["states"].get(gate.name, "running") == "paused"
                        if gate.paused == target:
                            continue
                        # Never let an in-flight strategy callback block the
                        # bridge heartbeat. Retry its transition on the next poll.
                        if not gate.lock.acquire(blocking=False):
                            applied = False
                            continue
                        try:
                            with gate.state_lock:
                                gate.paused = target
                                gate.applied_at = time.time()
                            changed = True
                        finally:
                            gate.lock.release()
                    if applied:
                        self._revision = revision
                        changed = True
                self._error = None
                if changed or time.monotonic() >= next_heartbeat:
                    self._write_status()
                    next_heartbeat = time.monotonic() + 1
            except (OSError, ValueError, TypeError, KeyError) as error:
                self._error = type(error).__name__
                try:
                    self._write_status()
                except OSError:
                    pass
            self._stop.wait(0.2)

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._stop.set()
        if threading.current_thread() is not self._thread:
            self._thread.join(timeout=2)
        try:
            self._write_status(alive=False)
        finally:
            self._lock_file.close()

def attach_strategy_control(engine, directory):
    """Call before add_*_strategy(). No native engine configuration is changed."""
    if getattr(engine, "_strategy_runtime", None) is not None:
        return engine._strategy_runtime
    control = StrategyRuntime(directory, query_fund=getattr(engine, "query_trade_account", None))
    engine._strategy_runtime = control
    for method, engine_type in (("add_cta_strategy", "CTA"),
                                ("add_hft_strategy", "HFT"),
                                ("add_sel_strategy", "SEL")):
        original = getattr(engine, method)
        def add(strategy, *args, _original=original, _type=engine_type, **kwargs):
            return _original(control.wrap(strategy, _type), *args, **kwargs)
        setattr(engine, method, add)
    original_release = engine.release
    def release(*args, **kwargs):
        # Stop control requests before releasing the native adapter they address.
        control.close()
        return original_release(*args, **kwargs)
    engine.release = release
    return control

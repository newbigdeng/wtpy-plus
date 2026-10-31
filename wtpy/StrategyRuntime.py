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
SUBMISSION_ALIASES = frozenset(name.removeprefix("stra_") for name in SUBMISSIONS)
SEMANTICS = ("暂停行情、K线和计算决策，包含策略自身的止损判断；生命周期、委托、成交、持仓及通道回报继续，"
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
        if name not in SUBMISSIONS|SUBMISSION_ALIASES or not callable(value):
            return value
        def submit(*args, **kwargs):
            with self._gate.lock:
                if self._gate.paused or self._gate.pause_requested:
                    with self._gate.state_lock:
                        self._gate.blocked_submissions += 1
                    return [] if name.removeprefix("stra_") in ("buy", "sell") else None
                return value(*args, **kwargs)
        return submit

class _Gate:
    def __init__(self, name, engine_type, desired):
        self.name, self.engine_type = name, engine_type
        self.lock = threading.RLock()
        self.state_lock = threading.RLock()
        self.paused = desired == "paused"
        self.pause_requested = self.paused
        self.applied_at = time.time()
        self.blocked_submissions = 0
        self.views = {}
        self.context = None
        self.initialized = False
        self.proxy = None
        self.reset_factory = None
        self.rebuild_pending = None
        self.rebuild_result = None

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
                self._gate.context = context
                if name == 'on_init':
                    with self._gate.state_lock:
                        self._gate.initialized = False
                if name in DECISIONS and self._gate.rebuild_pending:
                    identifier = self._gate.rebuild_pending
                    try:
                        with self._gate.state_lock:
                            self._gate.initialized = False
                        replacement = self._gate.reset_factory(self._strategy)
                        self._strategy = replacement
                        replacement.on_init(self._gate.view(context))
                        with self._gate.state_lock:
                            self._gate.initialized = True
                        self._gate.rebuild_result = {"id": identifier, "success": True}
                    except Exception as error:
                        self._gate.rebuild_result = {"id": identifier, "success": False,
                                                     "error": type(error).__name__}
                    self._gate.rebuild_pending = None
                if name in DECISIONS and (self._gate.paused or self._gate.pause_requested):
                    return None
                result = getattr(self._strategy, name)(self._gate.view(context), *args, **kwargs)
                if name == 'on_init':
                    with self._gate.state_lock:
                        self._gate.initialized = True
                return result
        return callback

class StrategyRuntime:
    def __init__(self, directory, query_fund=None, request_reset=None, query_reset=None, wake_reset=None,
                 plan_revision='', configure_reconcile=None, request_reconcile=None, query_reconcile=None,
                 arm_startup_guard=None, release_startup_guard=None, query_startup_guard=None):
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
        self.plan_revision = plan_revision
        self._request_reconcile, self._query_reconcile = request_reconcile, query_reconcile
        self._reconcile_ready = bool(configure_reconcile and plan_revision and
                                     configure_reconcile(self.boot_id, plan_revision))
        self._reconcile_id, self._reconcile_sent, self._reconcile_started = None, False, 0
        self._release_startup_guard, self._query_startup_guard = release_startup_guard, query_startup_guard
        self.startup_guard = {'capable': False, 'active': False, 'nonce': '', 'plan_revision': plan_revision}
        try:
            if self._reconcile_ready and query_startup_guard:
                self.startup_guard = query_startup_guard()
            path = self.directory/'startup-guard.json'
            guard = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
            if guard.get('required'):
                if (guard.get('plan_revision') != plan_revision or not guard.get('nonce') or
                        not self.startup_guard.get('capable') or not arm_startup_guard or
                        not arm_startup_guard(guard['nonce'])):
                    raise RuntimeError('Required native startup guard could not be armed')
                self.startup_guard = query_startup_guard()
                if self.startup_guard.get('active') is not True or self.startup_guard.get('nonce') != guard['nonce']:
                    raise RuntimeError('Native startup guard did not acknowledge the nonce')
        except BaseException:
            self._lock_file.close()
            raise
        self._error = None
        self._query_fund = query_fund
        self._query_result = None
        self._request_reset, self._query_reset = request_reset, query_reset
        self._wake_reset = wake_reset
        self._reset_id = None
        self._reset_sent = False
        self._reset_started = 0
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
        gate.proxy = _ControlledStrategy(strategy, gate)
        return gate.proxy

    def register_reset_factory(self, name, factory):
        with self._registry_lock:
            self._gates[name].reset_factory = factory

    def _handle_reset(self):
        path = self.directory/"reset-request.json"
        try:
            request = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False
        identifier, name = request["id"], request["strategyid"]
        gate = self._gates.get(name)
        if gate is None:
            return False
        target = self.directory/("reset-"+name+".json")
        task = json.loads(target.read_text(encoding="utf-8"))
        if task.get("id") != identifier or task.get("status") in ("completed", "failed"):
            return False
        def update(status, step, message, error="", **extra):
            if status == "failed":
                gate.rebuild_pending = None
            task.update(status=status, step=step, message=message, error=error,
                        updated_at=time.time(), **extra)
            atomic_json(target, task)
            return True
        if request.get("boot_id") != self.boot_id:
            return update("failed", "failed", "进程已变化，策略保持暂停，未自动重试", "请核对柜台和委托后重新发起")
        if not self._request_reset or not self._query_reset or not gate.reset_factory:
            return update("failed", "failed", "当前运行库不支持重置", "策略保持暂停")
        if self._reset_id != identifier:
            self._reset_id, self._reset_sent = identifier, False
            self._reset_started = time.monotonic()
        if self._reset_sent and time.monotonic()-self._reset_started > 180:
            return update("failed", "failed", "等待重置结果超时", "策略保持暂停，请核对柜台与运行日志")
        if not gate.paused:
            return False
        if not gate.lock.acquire(blocking=False):
            return False
        try:
            if not self._reset_sent:
                if time.time() > request["expires_at"]:
                    return update("failed", "failed", "重置请求已过期，未提交原生操作", "策略保持暂停")
                self._reset_sent = True
                if not self._request_reset(name, request["trader"], identifier, request["contracts"]):
                    return update("failed", "failed", "原生运行库拒绝重置请求", "请检查通道和执行器，策略保持暂停")
                return update("running", "cancelling", "已暂停策略，正在等待原生撤单与柜台确认")
            native = self._query_reset(identifier)
            state = native.get("status")
            if state == "completed":
                task.update(native_completed=True, archive_path=native.get("archive_path", ""))
                # Strategy init may touch native bar/subscription caches. Run it
                # from the next engine callback, never this bridge thread.
                result = gate.rebuild_result
                if result and result["id"] == identifier:
                    if not result["success"]:
                        return update("failed", "failed", "柜台已确认空仓，但Python策略初始化失败，保持暂停", result["error"])
                    return update("completed", "completed", "柜台已确认空仓；策略状态已归档并清空，保持暂停")
                gate.rebuild_pending = identifier
                if task.get("step") != "reinitializing":
                    if self._wake_reset and not self._wake_reset(identifier):
                        return update("failed", "failed", "柜台已确认空仓，原生初始化回调未受理", "策略保持暂停")
                    return update("running", "reinitializing", "柜台已确认空仓，等待原生事件线程初始化策略")
                return False
            if state == "failed":
                return update("failed", "failed", native.get("message", "原生重置失败，策略保持暂停"),
                              native.get("error") or "请核对未成交委托与实际持仓",
                              archive_path=native.get("archive_path", ""))
            if state != "running":
                return update("failed", "failed", "无法确认原生重置状态", "策略保持暂停，未自动重试")
            step = native.get("step", "confirming")
            message = native.get("message", "等待柜台回执确认")
            if task.get("step") != step or task.get("message") != message:
                return update("running", step, message)
            return False
        except Exception as error:
            return update("failed", "failed", "重置未完成，策略保持暂停", type(error).__name__)
        finally:
            gate.lock.release()

    def _handle_reconcile(self):
        try:
            request = json.loads((self.directory/'reconcile-request.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            return False
        identifier = request['request_id']
        target = self.directory/'reconcile-result.json'
        if target.exists():
            previous = json.loads(target.read_text(encoding='utf-8'))
            if previous.get('request_id') == identifier and previous.get('status') in ('completed', 'failed'):
                return False
        def fail(reason):
            atomic_json(target, dict(schema=1, status='failed', request_id=identifier, pid=os.getpid(),
                boot_id=self.boot_id, plan_revision=self.plan_revision, query_complete=False,
                positions_complete=False, orders_complete=False, error=reason))
            return True
        if request.get('expected_pid') != os.getpid() or request.get('expected_boot_id') != self.boot_id:
            return fail('Reconciliation request belongs to another process')
        age = time.time()*1000-request['requested_at_ms']
        if not 0 <= age <= 15000:
            return fail('Reconciliation request expired')
        gate = self._gates.get(request['strategyid'])
        if gate is None or gate.engine_type != 'CTA' or gate.context is None or not gate.initialized:
            return fail('A known initialized CTA strategy is required')
        if request.get('purpose') not in ('prepare', 'before_stop', 'rollback'):
            return fail('Unsupported reconciliation purpose')
        if request['purpose'] != 'prepare' and not gate.paused:
            return fail('Python decisions must be paused before terminal reconciliation')
        if not self._reconcile_ready or not self._request_reconcile or not self._query_reconcile:
            return fail('Native reconciliation API is unavailable')
        if identifier != self._reconcile_id:
            self._reconcile_id, self._reconcile_sent = identifier, False
            self._reconcile_started = time.monotonic()
        if time.monotonic()-self._reconcile_started > 12:
            return fail('Native reconciliation timed out')
        if not gate.lock.acquire(blocking=False):
            return False
        try:
            if not self._reconcile_sent:
                payload = dict(request, plan_revision=self.plan_revision)
                if not self._request_reconcile(payload):
                    return fail('Native reconciliation rejected the request')
                self._reconcile_sent = True
                return False
            result = self._query_reconcile(identifier)
            if result.get('status') not in ('completed', 'failed'):
                return False
            if (result.get('request_id') != identifier or result.get('pid') != os.getpid() or
                    result.get('boot_id') != self.boot_id or result.get('plan_revision') != self.plan_revision):
                return fail('Native reconciliation identity does not match')
            if result.get('status') == 'completed':
                if (any(result.get(key) is not True for key in ('query_complete', 'positions_complete', 'orders_complete', 'connected', 'ready')) or
                        type(result.get('event_seq')) is not int or type(result.get('pending_submissions')) is not int or
                        result['event_seq'] < 0 or result['pending_submissions'] < 0 or
                        type(result.get('trading_date')) is not int or result['trading_date'] <= 0 or
                        not isinstance(result.get('positions'), list) or not isinstance(result.get('alive_orders'), list) or
                        type(result.get('execution_idle')) is not bool or
                        not request['requested_at_ms'] <= result.get('completed_at_ms', 0) <= time.time()*1000):
                    return fail('Native reconciliation is incomplete or stale')
            atomic_json(target, result)  # Native complete flags and counts are never synthesized here.
            return True
        except Exception as error:
            return fail(type(error).__name__)
        finally:
            gate.lock.release()

    def _handle_startup_release(self):
        try:
            request = json.loads((self.directory/'release-startup-guard.json').read_text(encoding='utf-8'))
        except FileNotFoundError:
            return False
        target = self.directory/'release-startup-guard-result.json'
        identifier = request['request_id']
        if target.exists() and json.loads(target.read_text()).get('request_id') == identifier:
            return False
        result = dict(schema=1, request_id=identifier, nonce=request.get('nonce'), pid=os.getpid(),
                      boot_id=self.boot_id, plan_revision=self.plan_revision, released=False)
        age = time.time()*1000-request['requested_at_ms']
        valid = (request.get('expected_pid') == os.getpid() and request.get('expected_boot_id') == self.boot_id and
                 request.get('plan_revision') == self.plan_revision and 0 <= age <= 15000 and
                 request.get('nonce') == self.startup_guard.get('nonce') and self.startup_guard.get('active') is True and
                 all(gate.paused for gate in self._gates.values()) and self._release_startup_guard)
        try:
            if valid and self._release_startup_guard(request['nonce']):
                self.startup_guard = self._query_startup_guard()
                result['released'] = self.startup_guard.get('active') is False
        except Exception as error:
            result['error'] = type(error).__name__
        result['completed_at_ms'] = int(time.time()*1000)
        atomic_json(target, result)
        return True # The host, not this runner, retires required:true after checking the ack.

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
                        "reset_ready": bool(gate.reset_factory and gate.context is not None and gate.engine_type == "CTA"),
                        "initialized": gate.initialized,
                        "blocked_submissions": gate.blocked_submissions})
            atomic_json(self.directory / "status.json", {
                "schema": 1, "pid": os.getpid(), "boot_id": self.boot_id,
                "alive": bool(alive and not self._closed), "updated_at": time.time(), "revision": self._revision,
                "plan_revision": self.plan_revision, "reconcile_supported": self._reconcile_ready,
                "startup_guard": self.startup_guard,
                "error": self._error, "strategies": rows, "semantics": SEMANTICS,
                "capabilities": (["query_fund"] if self._query_fund else []) +
                                (["reset_strategy"] if self._request_reset and self._query_reset else []),
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
                        with gate.state_lock:
                            gate.pause_requested = target
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
                changed = self._handle_reset() or changed
                changed = self._handle_reconcile() or changed
                changed = self._handle_startup_release() or changed
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

def attach_strategy_control(engine, directory, plan_revision=''):
    """Call before add_*_strategy(). No native engine configuration is changed."""
    if getattr(engine, "_strategy_runtime", None) is not None:
        return engine._strategy_runtime
    supported = bool(getattr(engine, "supports_strategy_reset", False))
    reconcile = bool(getattr(engine, 'supports_strategy_reconcile', False))
    startup = bool(getattr(engine, 'supports_strategy_startup_guard', False))
    control = StrategyRuntime(directory, query_fund=getattr(engine, "query_trade_account", None),
        request_reset=engine.request_strategy_reset if supported else None,
        query_reset=engine.query_strategy_reset if supported else None,
        wake_reset=engine.wakeup_strategy_reset if supported else None, plan_revision=plan_revision,
        configure_reconcile=engine.configure_strategy_reconcile if reconcile else None,
        request_reconcile=engine.request_strategy_reconcile if reconcile else None,
        query_reconcile=engine.query_strategy_reconcile if reconcile else None,
        arm_startup_guard=engine.arm_strategy_startup_guard if startup else None,
        release_startup_guard=engine.release_strategy_startup_guard if startup else None,
        query_startup_guard=engine.query_strategy_startup_guard if startup else None)
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

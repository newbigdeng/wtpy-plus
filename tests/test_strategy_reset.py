import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient
from wtpy.StrategyRuntime import StrategyRuntime, atomic_json
from wtpy.monitor.StrategyControl import register_strategy_routes
from wtpy.monitor.StrategyReset import enqueue_reset, reset_eligibility, reset_task
from wtpy.monitor.RiskRuntime import RiskRuntime


class Strategy:
    def __init__(self, name="target", attempt=False):
        self.identifier, self.attempt, self.initialized = name, attempt, 0
    def name(self):
        return self.identifier
    def on_init(self, context):
        self.initialized += 1
        if self.attempt:
            context.set_position("SHFE.rb.2701", 99)
    def on_tick(self, context):
        pass


class Context:
    def __init__(self):
        self.submitted = []
    def stra_set_position(self, *args):
        self.submitted.append(args)
    set_position = stra_set_position


class Native:
    def __init__(self):
        self.requests = []
        self.accepted = True
        self.state = {"status": "running", "step": "confirming", "message": "等待真实回执"}
    def request(self, *args):
        self.requests.append(args)
        return self.accepted
    def query(self, identifier):
        return dict(self.state)


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="wt-reset-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root/"generated/strategy-control"
        self.directory.mkdir(parents=True)
        self.group = {"id": "group", "path": str(self.root)}
        self.plan = {"engine_type": "CTA", "strategies": [{"id": "target", "enabled": True,
            "trader": "mock", "params": {"code": "SHFE.rb.2701"}}]}

    def wait(self, condition):
        deadline = time.monotonic()+4
        while time.monotonic() < deadline:
            value = condition()
            if value:
                return value
            time.sleep(.02)
        self.fail("control bridge did not reach the expected state")

    def runtime(self):
        native = Native()
        control = StrategyRuntime(self.directory, request_reset=native.request, query_reset=native.query)
        self.addCleanup(control.close)
        proxy = control.wrap(Strategy(), "CTA")
        context = Context()
        proxy.on_init(context)
        control.register_reset_factory("target", lambda previous: Strategy(attempt=True))
        return control, proxy, context, native

    def enqueue(self, control):
        status = {"boot_id": control.boot_id}
        return enqueue_reset(self.group, "target", self.plan, status, "owner")

    def terminal(self):
        task = reset_task(self.group, "target")
        return task if task and task["status"] in ("completed", "failed") else None

    def test_native_completion_precedes_python_rebuild_and_pause_is_retained(self):
        control, proxy, context, native = self.runtime()
        before = proxy._strategy
        self.enqueue(control)
        self.wait(lambda: native.requests)
        self.assertIs(proxy._strategy, before)
        self.assertEqual(reset_task(self.group, "target")["status"], "running")
        native.state = {"status": "completed", "archive_path": "archive/test"}
        self.wait(lambda: reset_task(self.group, "target").get("step") == "reinitializing")
        self.assertIs(proxy._strategy, before)
        proxy.on_tick(context)
        task = self.wait(self.terminal)
        self.assertEqual(task["status"], "completed")
        self.assertIsNot(proxy._strategy, before)
        self.assertEqual(proxy._strategy.initialized, 1)
        self.assertEqual(context.submitted, [])
        self.assertTrue(proxy._gate.paused)
        self.assertEqual(task["archive_path"], "archive/test")
        time.sleep(.25)
        self.assertEqual(len(native.requests), 1)

    def test_failed_or_partial_native_operation_cannot_clear_python_state(self):
        control, proxy, context, native = self.runtime()
        before = proxy._strategy
        self.enqueue(control)
        self.wait(lambda: native.requests)
        native.state = {"status": "failed", "message": "部分成交", "error": "剩余1手，保持冻结", "archive_path": "archive/partial"}
        task = self.wait(self.terminal)
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["archive_path"], "archive/partial")
        self.assertIn("剩余", task["error"])
        self.assertIs(proxy._strategy, before)
        self.assertTrue(proxy._gate.paused)
        self.assertEqual(context.submitted, [])

    def test_timeout_retains_pause_and_does_not_retry(self):
        control, proxy, context, native = self.runtime()
        self.enqueue(control)
        self.wait(lambda: native.requests)
        control._reset_started -= 181
        task = self.wait(self.terminal)
        self.assertEqual(task["status"], "failed")
        self.assertIn("超时", task["message"])
        self.assertTrue(proxy._gate.paused)
        self.assertEqual(len(native.requests), 1)

    def test_failed_python_rebuild_records_native_archive_and_stays_paused(self):
        control, proxy, context, native = self.runtime()
        def fail(previous):
            raise ValueError("synthetic constructor failure")
        control.register_reset_factory("target", fail)
        self.enqueue(control)
        self.wait(lambda: native.requests)
        native.state = {"status": "completed", "archive_path": "archive/completed-native"}
        self.wait(lambda: reset_task(self.group, "target").get("step") == "reinitializing")
        proxy.on_tick(context)
        task = self.wait(self.terminal)
        self.assertEqual(task["status"], "failed")
        self.assertTrue(task["native_completed"])
        self.assertEqual(task["archive_path"], "archive/completed-native")
        self.assertTrue(proxy._gate.paused)

    def test_expired_request_never_reaches_native(self):
        control, proxy, _, native = self.runtime()
        self.enqueue(control)
        command = json.loads((self.directory/"reset-request.json").read_text())
        command["expires_at"] = 1
        atomic_json(self.directory/"reset-request.json", command)
        task = self.wait(self.terminal)
        self.assertEqual(task["status"], "failed")
        self.assertEqual(native.requests, [])
        self.assertTrue(proxy._gate.paused)

    def test_closed_market_rebuild_uses_explicit_native_event_without_a_quote(self):
        control,proxy,context,native=self.runtime()
        calls=[];workers=[];init_threads=[]
        def factory(previous):
            init_threads.append(threading.current_thread().name)
            return Strategy(attempt=True)
        control.register_reset_factory("target",factory)
        def wake(identifier):
            calls.append(identifier)
            worker=threading.Thread(target=proxy.on_tick,args=(context,),name="fake-native-event")
            workers.append(worker);worker.start();return True
        control._wake_reset=wake
        self.addCleanup(lambda:[worker.join(2) for worker in workers])
        self.enqueue(control);self.wait(lambda:native.requests)
        native.state={"status":"completed","archive_path":"archive/no-quote"}
        task=self.wait(self.terminal)
        self.assertEqual(task["status"],"completed")
        self.assertEqual(calls,[task["id"]])
        self.assertEqual(init_threads,["fake-native-event"])
        self.assertEqual(context.submitted,[])
        self.assertTrue(proxy._gate.paused)

    def test_restart_does_not_replay_an_old_reset(self):
        control, _, _, native = self.runtime()
        control.close()
        self.enqueue(control)
        restarted = StrategyRuntime(self.directory, request_reset=native.request, query_reset=native.query)
        self.addCleanup(restarted.close)
        proxy = restarted.wrap(Strategy(), "CTA")
        proxy.on_init(Context())
        restarted.register_reset_factory("target", lambda previous: Strategy())
        task = self.wait(self.terminal)
        self.assertEqual(task["status"], "failed")
        self.assertEqual(native.requests, [])
        self.assertTrue(proxy._gate.paused)

    def test_inflight_callback_cannot_submit_after_pause_is_requested(self):
        control, proxy, context, native = self.runtime()
        entered, release = threading.Event(), threading.Event()
        def callback(ctx):
            entered.set();release.wait(3)
            ctx.set_position("SHFE.rb.2701", 99)
        proxy._strategy.on_bar = callback
        worker = threading.Thread(target=proxy.on_bar, args=(context,))
        worker.start();self.addCleanup(lambda: worker.join(4))
        self.addCleanup(release.set)
        self.assertTrue(entered.wait(2))
        self.enqueue(control)
        self.wait(lambda: proxy._gate.pause_requested)
        release.set();worker.join(3)
        self.wait(lambda: native.requests)
        self.assertEqual(context.submitted, [])

    def test_risk_rebuild_preserves_account_drawdown_and_day_limits(self):
        runtime = RiskRuntime({}, self.root/"risk.jsonl")
        previous = runtime.wrap(Strategy(), "CTA", "mock")
        previous.state.metrics.update(peak=20000, day_baseline=18000)
        previous.state.pending[1] = {"qty": 1}
        previous.state.targets["SHFE.rb.2701"] = 1
        replacement = runtime.replace_strategy(previous, Strategy())
        self.assertEqual(replacement.state.metrics["peak"], 20000)
        self.assertEqual(replacement.state.metrics["day_baseline"], 18000)
        self.assertEqual(replacement.state.pending, {})
        self.assertEqual(replacement.state.targets, {})

    def api(self):
        plan = self.plan
        state = self.root/"managed-strategies"
        (state/"revisions/current").mkdir(parents=True)
        atomic_json(state/"applied.json", {"revision": "current"})
        atomic_json(state/"draft.json", dict(plan, revision="current"))
        atomic_json(state/"revisions/current/plan.json", plan)
        atomic_json(self.root/"config.json", {"env": {"name": "cta"}})
        atomic_json(self.directory/"status.json", {"alive": True, "pid": os.getpid(), "boot_id": "boot",
            "updated_at": time.time(), "capabilities": ["reset_strategy"], "strategies": [
            {"id": "target", "source": "python", "engine": "CTA", "state": "running", "reset_ready": True}]})
        channel = self.root/"generated/traders/mock"
        channel.mkdir(parents=True)
        atomic_json(channel/"rtdata.json.status.json", {"connected": True, "ready": True})
        group = self.group
        self.signal_invalidations = []
        invalidations = self.signal_invalidations
        class Manager:
            def has_group(self, key):return key == "group"
            def get_group(self, key):return group
            def get_strategies(self, key):return ["target"]
            def log_action(self, *args):pass
            def invalidate_strategy_signals(self, *args):invalidations.append(args)
        class Dog:
            def isRunning(self, key):return True
            def getAppConf(self, key):return {"param": "run_managed.py"}
        app = FastAPI()
        register_strategy_routes(app, Manager(), Dog(), lambda request, token:
            (True, {"role": token or "admin", "loginid": "owner", "products": []}))
        client = TestClient(app)
        self.addCleanup(client.close)
        return client

    def test_api_denies_non_admin_unconfirmed_and_shared_channel(self):
        client = self.api()
        payload = {"groupid": "group", "strategyid": "target", "confirmed": True}
        for role in ("guest", "viewer", "operator"):
            result = client.post("/mgr/reset_strategy", json=dict(payload, token=role)).json()
            self.assertEqual(result["result"], -403)
        self.assertLess(client.post("/mgr/reset_strategy", json=dict(payload, confirmed=False)).json()["result"], 0)
        shared = deepcopy(self.plan)
        shared["strategies"].append(dict(shared["strategies"][0], id="other"))
        atomic_json(self.root/"managed-strategies/revisions/current/plan.json", shared)
        result = client.post("/mgr/reset_strategy", json=payload).json()
        self.assertIn("共享", result["message"])
        self.assertFalse((self.directory/"desired.json").exists())

    def test_api_queues_once_without_reporting_flat_or_completed(self):
        client = self.api()
        payload = {"groupid": "group", "strategyid": "target", "confirmed": True}
        first = client.post("/mgr/reset_strategy", json=payload).json()
        self.assertEqual(first["result"], 0)
        self.assertEqual(first["task"]["status"], "queued")
        second = client.post("/mgr/reset_strategy", json=payload).json()
        self.assertLess(second["result"], 0)
        self.assertEqual(reset_task(self.group, "target")["id"], first["task"]["id"])
        self.assertEqual(json.loads((self.directory/"desired.json").read_text())["states"]["target"], "paused")
        for role in ("guest", "operator"):
            result = client.post("/mgr/reset_strategy_status", json=dict(payload, token=role)).json()
            self.assertEqual(result["result"], -403)

    def test_api_rejects_unready_broker_without_changing_strategy_state(self):
        client = self.api()
        atomic_json(self.root/"generated/traders/mock/rtdata.json.status.json", {"connected": True, "ready": False})
        result = client.post("/mgr/reset_strategy", json={"groupid": "group", "strategyid": "target", "confirmed": True}).json()
        self.assertLess(result["result"], 0)
        self.assertIn("就绪", result["message"])
        self.assertFalse((self.directory/"desired.json").exists())

    def test_native_completion_invalidates_signals_before_python_rebuild_finishes(self):
        client = self.api()
        payload = {"groupid": "group", "strategyid": "target"}
        task = {"id": "reset-cache", "status": "running", "step": "confirming"}
        atomic_json(self.directory/"reset-target.json", task)
        self.assertEqual(client.post("/mgr/reset_strategy_status", json=payload).json()["result"], 0)
        self.assertEqual(self.signal_invalidations, [])
        task.update(native_completed=True, step="reinitializing")
        atomic_json(self.directory/"reset-target.json", task)
        client.post("/mgr/reset_strategy_status", json=payload)
        self.assertEqual(self.signal_invalidations, [("group", "target", "reset-cache")])
        self.signal_invalidations.clear()
        client.post("/mgr/qrystra_runtime", json={"groupid": "group"})
        self.assertEqual(self.signal_invalidations, [("group", "target", "reset-cache")])


if __name__ == "__main__":
    unittest.main()

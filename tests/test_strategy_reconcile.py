"""Fixed runtime IPC with fake native functions; no engine, broker or network."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('reconcile_runtime', Path(__file__).parents[1]/'wtpy/StrategyRuntime.py')
runtime_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime_module)


class ReconcileRuntimeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.control = runtime_module.StrategyRuntime.__new__(runtime_module.StrategyRuntime)
        self.control.directory, self.control.boot_id, self.control.plan_revision = self.root, 'boot', 'plan'
        self.gate = SimpleNamespace(engine_type='CTA', context=object(), initialized=True, paused=True, lock=threading.RLock())
        self.control._gates = {'example': self.gate}
        self.control._reconcile_ready = True
        self.control._reconcile_id, self.control._reconcile_sent, self.control._reconcile_started = None, False, 0
        self.calls = []
        self.control._request_reconcile = lambda payload: self.calls.append(payload) or True
        self.request = dict(request_id='request-1', expected_pid=os.getpid(), expected_boot_id='boot',
                            strategyid='example', trader='fake', contracts=['SHFE.rb.2701'], purpose='before_stop',
                            requested_at_ms=int(time.time()*1000))
        self.native = dict(status='completed', request_id='request-1', pid=os.getpid(), boot_id='boot',
                           plan_revision='plan', query_complete=True, positions_complete=True, orders_complete=True,
                           connected=True, ready=True, event_seq=0, pending_submissions=0, execution_idle=True,
                           trading_date=20261027, positions=[], alive_orders=[], completed_at_ms=int(time.time()*1000))
        self.control._query_reconcile = lambda identifier: dict(self.native)
        self.write('reconcile-request.json', self.request)

    def write(self, name, value):
        (self.root/name).write_text(json.dumps(value), encoding='utf-8')

    def result(self):
        return json.loads((self.root/'reconcile-result.json').read_text())

    def test_complete_empty_native_receipt_is_copied_without_changing_pause(self):
        self.assertFalse(self.control._handle_reconcile())
        self.assertEqual(self.calls[0]['plan_revision'], 'plan')
        self.assertTrue(self.control._handle_reconcile())
        self.assertEqual(self.result(), self.native)
        self.assertTrue(self.gate.paused)
        self.control._handle_reconcile()
        self.assertEqual(len(self.calls), 1)

    def test_prepare_temporarily_queries_without_persistently_pausing(self):
        self.gate.paused = False
        self.request['purpose'] = 'prepare'
        self.write('reconcile-request.json', self.request)
        self.control._handle_reconcile(); self.control._handle_reconcile()
        self.assertTrue(self.result()['query_complete'])
        self.assertFalse(self.gate.paused)

    def test_terminal_query_requires_python_pause(self):
        self.gate.paused = False
        self.control._handle_reconcile()
        self.assertFalse(self.result()['query_complete'])
        self.assertEqual(self.calls, [])

    def test_old_pid_and_boot_are_rejected_before_native_call(self):
        self.request['expected_boot_id'] = 'previous-boot'
        self.write('reconcile-request.json', self.request)
        self.control._handle_reconcile()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.result()['query_complete'])

    def test_missing_api_fails_closed(self):
        self.control._reconcile_ready = False
        self.control._handle_reconcile()
        self.assertFalse(self.result()['positions_complete'])
        self.assertEqual(self.calls, [])

    def test_native_incomplete_and_foreign_identity_are_never_backfilled(self):
        self.control._handle_reconcile()
        self.native.pop('positions_complete')
        self.control._handle_reconcile()
        self.assertEqual(self.result()['status'], 'failed')
        self.assertFalse(self.result()['query_complete'])

    def test_delayed_response_stays_pending_without_inventing_empty_orders(self):
        self.control._handle_reconcile()
        self.native = {'status': 'running'}
        self.assertFalse(self.control._handle_reconcile())
        self.assertFalse((self.root/'reconcile-result.json').exists())

    def test_stale_request_does_not_query(self):
        self.request['requested_at_ms'] -= 20000
        self.write('reconcile-request.json', self.request)
        self.control._handle_reconcile()
        self.assertEqual(self.calls, [])

    def startup_release(self, nonce='guard'):
        self.control.startup_guard = dict(capable=True, active=True, nonce='guard', plan_revision='plan')
        self.control._release_startup_guard = lambda value: self.calls.append(value) or True
        self.control._query_startup_guard = lambda: dict(capable=True, active=False, nonce='guard', plan_revision='plan')
        self.write('release-startup-guard.json', dict(request_id='release-1', nonce=nonce,
            expected_pid=os.getpid(), expected_boot_id='boot', plan_revision='plan', requested_at_ms=int(time.time()*1000)))

    def test_startup_guard_release_requires_nonce_and_preserves_python_pause(self):
        self.startup_release()
        self.control._handle_startup_release()
        result = json.loads((self.root/'release-startup-guard-result.json').read_text())
        self.assertTrue(result['released']); self.assertTrue(self.gate.paused)
        self.assertEqual(self.calls, ['guard'])

    def test_wrong_startup_nonce_cannot_release_native_gate(self):
        self.startup_release(nonce='previous-guard')
        self.control._handle_startup_release()
        self.assertFalse(json.loads((self.root/'release-startup-guard-result.json').read_text())['released'])
        self.assertEqual(self.calls, [])

    def test_startup_release_api_error_retains_pause_and_reports_false(self):
        self.startup_release()
        def fail(nonce):
            raise RuntimeError('fake native release failure')
        self.control._release_startup_guard = fail
        self.control._handle_startup_release()
        result = json.loads((self.root/'release-startup-guard-result.json').read_text())
        self.assertFalse(result['released']); self.assertEqual(result['error'], 'RuntimeError')
        self.assertTrue(self.gate.paused)

    def test_required_startup_guard_rejects_old_library_before_control_thread(self):
        self.write('startup-guard.json', dict(schema=1, nonce='guard', plan_revision='plan', required=True))
        fake_fcntl = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *args: None)
        with patch.object(runtime_module, 'fcntl', fake_fcntl), patch.object(runtime_module.threading, 'Thread') as thread:
            with self.assertRaisesRegex(RuntimeError, 'startup guard could not be armed'):
                runtime_module.StrategyRuntime(self.root, plan_revision='plan')
            thread.assert_not_called()

    def test_startup_guard_is_armed_before_runtime_thread_can_accept_commands(self):
        self.write('startup-guard.json', dict(schema=1, nonce='guard', plan_revision='plan', required=True))
        fake_fcntl = SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *args: None)
        events = []; active = [False]
        def configure(boot, plan):
            events.append('configure'); return True
        def arm(nonce):
            events.append('arm'); active[0] = True; return True
        def query():
            return dict(capable=True, active=active[0], nonce='guard' if active[0] else '', plan_revision='plan')
        with patch.object(runtime_module, 'fcntl', fake_fcntl), patch.object(runtime_module.threading, 'Thread') as thread, \
                patch.object(runtime_module.atexit, 'register'):
            thread.return_value.start.side_effect = lambda: events.append('thread-start')
            control = runtime_module.StrategyRuntime(self.root, plan_revision='plan', configure_reconcile=configure,
                arm_startup_guard=arm, release_startup_guard=lambda nonce: True, query_startup_guard=query)
            self.assertEqual(events, ['configure', 'arm', 'thread-start'])
            self.assertTrue(control.startup_guard['active'])
            control.close()

    def test_initialized_means_on_init_returned_not_merely_context_bound(self):
        started, finished = threading.Event(), threading.Event()
        strategy = SimpleNamespace(on_init=lambda context: (started.set(), finished.wait(2)))
        gate = runtime_module._Gate('example', 'CTA', 'paused')
        proxy = runtime_module._ControlledStrategy(strategy, gate)
        thread = threading.Thread(target=lambda: proxy.on_init(object()))
        thread.start()
        try:
            self.assertTrue(started.wait(1)); self.assertIsNotNone(gate.context)
            self.assertFalse(gate.initialized)
        finally:
            finished.set(); thread.join(2)
        self.assertTrue(gate.initialized)

    def test_failed_initialization_cannot_authorize_reconciliation(self):
        self.gate.initialized = False
        self.control._handle_reconcile()
        self.assertEqual(self.calls, [])
        self.assertFalse(self.result()['query_complete'])


if __name__ == '__main__':
    unittest.main()

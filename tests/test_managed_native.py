"""Exercise managed registration and native routing with the existing replay fixture."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests import test_plus_execution as execution_fixture

CTA, HFT, NATIVE = execution_fixture.CTA, execution_fixture.HFT, execution_fixture.NATIVE


SHIM = '''import json,sys
from pathlib import Path
import wtpy
from wtpy.monitor import PlusReplayRunner as replay
from wtpy.monitor.ManagedStrategyRunner import native_config,register_plan
folder=Path(sys.argv[1])
plan=json.loads((folder.parent.parent/'managed-plan.json').read_text())
original_build=replay.build_config
def build(*args):
    result=original_build(*args)
    config=native_config(json.loads((folder/'config.json').read_text()),plan,folder)
    config['notifier']={'active':False}
    (folder/'config.json').write_text(json.dumps(config))
    (folder/'strategies').mkdir()
    for row in plan['strategies']:
        (folder/'strategies'/(row['id']+'.py')).write_text(row['source'])
    return result
replay.build_config=build
kind=plan['engine_type']
method='add_cta_strategy' if kind=='CTA' else 'add_hft_strategy'
factory=wtpy.WtEngine
def managed_engine(*args,**kwargs):
    engine=factory(*args,**kwargs)
    original_add=getattr(engine,method)
    def register(observed,*args,**kwargs):
        def native_add(strategy,*args,**kwargs):
            observed.strategy=strategy
            return original_add(observed,*args,**kwargs)
        setattr(engine,method,native_add)
        try:
            register_plan(engine,plan,folder)
        finally:
            setattr(engine,method,original_add)
        contexts=engine.__cta_ctxs__ if kind=='CTA' else engine.__hft_ctxs__
        (folder/'managed-registration.json').write_text(json.dumps({
            'ids':[context.__stra_info__.name() for context in contexts.values()]
        }))
    setattr(engine,method,register)
    return engine
wtpy.WtEngine=managed_engine
replay.main()
'''


@unittest.skipUnless(NATIVE and (Path(NATIVE) / 'libWtPorter.so').is_file(),
                     'Set WT_PLUS_NATIVE_DIR to the Plus native directory')
class ManagedNativeTests(unittest.TestCase):
    def setUp(self):
        # Reuse synthetic market, finance and process lifecycle from the Plus tests.
        self.fixture = execution_fixture.NativeExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.shim = self.fixture.workspace / 'managed-replay.py'
        self.shim.write_text(SHIM, encoding='utf-8')

    def replay(self, source, kind, execution=None):
        from wtpy.monitor.PlusExecution import effective_execution
        row = {'id': 'managed_test', 'name': 'Managed test', 'enabled': True,
               'class_name': 'TestStrategy', 'source': source,
               'params': {'code': 'SHFE.rb.2701'}, 'trader': 'plus',
               'agent': True, 'slippage': 0,
               'execution': effective_execution(kind, 900, 905, execution)}
        plan = {'engine_type': kind, 'trading_enabled': True, 'strategies': [row]}
        (self.fixture.workspace / 'managed-plan.json').write_text(json.dumps(plan))
        real_popen = subprocess.Popen

        def child(command, *args, **kwargs):
            if command[1:3] == ['-m', 'wtpy.monitor.PlusReplayRunner']:
                command = [sys.executable, str(self.shim), *command[3:]]
            return real_popen(command, *args, **kwargs)

        with patch('wtpy.monitor.PlusBacktest.subprocess.Popen', side_effect=child):
            result = self.fixture.run_task(source, kind, execution=execution)
        folder = self.fixture.manager.root / result['task']['id']
        registered = json.loads((folder / 'managed-registration.json').read_text())
        self.assertEqual(registered['ids'], ['managed_test'])
        config = json.loads((folder / 'config.json').read_text())
        self.assertTrue(all(row['module'] == 'TraderMocker' for row in config['traders']))
        return result, config

    def test_managed_twap_and_vwap_route_real_orders(self):
        schedules = {}
        for identifier in ('twap', 'vwap'):
            execution = {'id': identifier, 'params': {'begin_time': 900, 'end_time': 905,
                'tail_secs': 5, 'total_times': 5, 'price_mode': 2, 'price_offset': 10}}
            if identifier == 'vwap':
                execution['volume_weights'] = [1, 1, 6, 1, 1]
            result, config = self.replay(CTA, 'CTA', execution)
            self.assertEqual(config['routers'], [{'strategy': 'managed_test',
                                                  'executer': 'managed-managed_test'}])
            self.assertEqual(sum(row['volume'] for row in result['trades']), 10)
            self.assertEqual(result['summary']['commission'], 20)
            schedules[identifier] = [(row['time'], row['volume']) for row in result['trades']]
        self.assertNotEqual(schedules['twap'], schedules['vwap'])

    def test_managed_hft_uses_selected_trader(self):
        result, config = self.replay(HFT, 'HFT')
        self.assertEqual(config['executers'], [])
        self.assertEqual([row['volume'] for row in result['trades']], [2, 1, 2, 1])
        self.assertEqual(result['summary']['commission'], 21)
        self.assertEqual(result['summary']['margin'], 0)


if __name__ == '__main__':
    unittest.main()

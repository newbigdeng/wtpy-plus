"""Terminal snapshot retries retain native evidence and always precede teardown."""
import ast
import importlib
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
package = types.ModuleType('wtpy'); package.__path__ = [str(ROOT / 'wtpy')]
monitor = types.ModuleType('wtpy.monitor'); monitor.__path__ = [str(ROOT / 'wtpy/monitor')]
backtest = types.ModuleType('wtpy.monitor.PlusBacktest')
backtest.read_json = backtest.write_json = backtest.dataset_catalog = Mock()
backtest.LIMITATIONS = []
with patch.dict(sys.modules, {'wtpy': package, 'wtpy.monitor': monitor,
                             'wtpy.monitor.PlusBacktest': backtest}):
    runner = importlib.import_module('wtpy.monitor.PlusReplayRunner')
    positions = importlib.import_module('wtpy.monitor.PortfolioPositions')


class FinalSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.modules = patch.dict(sys.modules, {'wtpy': package, 'wtpy.monitor': monitor,
            'wtpy.monitor.PortfolioPositions': positions})
        self.modules.start(); self.addCleanup(self.modules.stop)
        self.engine = Mock()

    def test_busy_then_complete_returns_the_actual_native_dto(self):
        busy = dict(valid=False, reason='broker_snapshot_busy_or_resetting')
        complete = dict(valid=True, execution_idle=True, actual_positions=[], working_orders={})
        with patch.object(positions, 'query_portfolio_state', side_effect=[busy, complete]) as query, \
                patch.object(runner.time, 'sleep') as sleep:
            self.assertIs(runner.final_portfolio_state(self.engine, 'strategy'), complete)
        self.assertEqual(query.call_count, 2)
        query.assert_called_with(self.engine, 'plus', 'strategy')
        sleep.assert_called_once_with(.05)
        self.engine.release.assert_not_called()

    def test_busy_is_bounded_to_twenty_reads_and_nineteen_waits(self):
        busy = dict(valid=False, reason='broker_snapshot_changed', event_seq=7)
        with patch.object(positions, 'query_portfolio_state', return_value=busy) as query, \
                patch.object(runner.time, 'sleep') as sleep:
            self.assertIs(runner.final_portfolio_state(self.engine, 'strategy'), busy)
        self.assertEqual(query.call_count, 20)
        self.assertEqual(sleep.call_count, 19)
        self.assertAlmostEqual(sum(call.args[0] for call in sleep.call_args_list), .95)
        self.assertNotIn('actual_positions', busy)

    def test_missing_incomplete_and_unfinished_are_returned_without_waiting(self):
        for value in (dict(valid=False, reason='Native portfolio API unavailable'),
                      dict(valid=False, reason='broker_snapshot_incomplete'),
                      dict(valid=False, reason='execution_route_busy_or_unavailable'),
                      dict(valid=True, execution_idle=False, pending_submissions=1,
                           actual_positions=[dict(code='SYNTHETIC.x.2701', long=1, short=0)],
                           working_orders={'SYNTHETIC.x.2701': 1})):
            with self.subTest(value=value), patch.object(positions, 'query_portfolio_state', return_value=value) as query, \
                    patch.object(runner.time, 'sleep') as sleep:
                self.assertIs(runner.final_portfolio_state(self.engine, 'strategy'), value)
                query.assert_called_once()
                sleep.assert_not_called()

    def test_real_finally_queries_before_release_and_releases_on_write_failure(self):
        tree = ast.parse(Path(runner.__file__).read_text(encoding='utf-8'))
        block = next(node for node in ast.walk(tree) if isinstance(node, ast.Try) and len(node.finalbody) >= 1
            and any(isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                    and call.func.id == 'final_portfolio_state' for call in ast.walk(node.finalbody[0])))
        terminal = ast.Module(body=[block.finalbody[0]], type_ignores=[])
        code = compile(ast.fix_missing_locations(terminal), '<real-terminal-finally>', 'exec')
        for failure in (None, 'query', 'write'):
            with self.subTest(failure=failure):
                seen = []
                self.engine.release.side_effect = lambda: seen.append('release')
                dto = dict(valid=False, reason='broker_snapshot_busy_or_resetting')
                def query(engine, strategyid):
                    self.assertEqual(seen, [])
                    seen.append('query')
                    if failure == 'query': raise OSError('synthetic query failure')
                    return dto
                def write(path, value):
                    self.assertIs(value, dto); seen.append('write')
                    if failure == 'write': raise OSError('synthetic write failure')
                namespace = dict(engine=self.engine, task=dict(straid='strategy'), folder=Path('/synthetic'),
                                 final_portfolio_state=query, write_json=write)
                if failure:
                    with self.assertRaises(OSError): exec(code, namespace)
                else:
                    exec(code, namespace)
                self.assertEqual(seen, ['query', 'release'] if failure == 'query' else ['query', 'write', 'release'])


if __name__ == '__main__': unittest.main()

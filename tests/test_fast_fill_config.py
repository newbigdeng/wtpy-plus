import ast
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from wtpy.monitor.WtBtMon import WtBtMon


class FastFillConfigTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='wt-fast-fill-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        template = self.root/'template'
        template.mkdir()
        for name, value in [('configbt.json', {'env':{},'replayer':{'basefiles':{'commodity':'common/commodities.json'}}}),
                            ('logcfgbt.json', {}), ('fees.json', {})]:
            (template/name).write_text(json.dumps(value))
        self.manager = WtBtMon(str(self.root))
        self.strategy = self.manager.add_strategy('test-user', 'Synthetic CTA', 'cta')

    def configured_runner(self, **options):
        with patch('wtpy.monitor.WtBtMon.WtBtTask.run'):
            task = self.manager.run_backtest('test-user', self.strategy['id'], 202610120900,
                                             202610121515, 100000, **options)
        source = self.root/'test-user'/self.strategy['id']/'backtests'/task['id']/'runBT.py'
        tree = ast.parse(source.read_text())
        call = next(node for node in ast.walk(tree) if isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute) and node.func.attr == 'set_cta_strategy')
        kwargs = {item.arg: ast.literal_eval(item.value) for item in call.keywords}
        return task, kwargs

    def test_default_remains_causal_touch(self):
        task, kwargs = self.configured_runner()
        self.assertEqual(kwargs['fill_model'], 'causal_touch')
        self.assertEqual(kwargs['event_delay'], 0)
        self.assertEqual(kwargs['participation_rate'], 0)
        self.assertEqual(task['fill_config']['fill_model'], 'causal_touch')

    def test_selected_models_reach_native_runner_and_saved_task(self):
        for model, delay, participation in [('legacy_cta',0,0), ('causal_touch',2,0), ('volume_limited',2,.25)]:
            with self.subTest(model=model):
                task, kwargs = self.configured_runner(fill_model=model,event_delay=delay,participation_rate=participation)
                for key, value in [('fill_model',model),('event_delay',delay),('participation_rate',participation)]:
                    self.assertEqual(kwargs[key], value)
                    self.assertEqual(task['fill_config'][key], value)

    def test_invalid_combinations_fail_before_creating_tasks(self):
        for options in [{'fill_model':'unknown'}, {'fill_model':'legacy_cta','event_delay':1},
                        {'fill_model':'causal_touch','participation_rate':.2},
                        {'fill_model':'volume_limited','participation_rate':0}, {'event_delay':-1}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.configured_runner(**options)
        self.assertEqual(self.manager.task_map, {})

    def test_hft_rejects_cta_fill_configuration(self):
        self.strategy = self.manager.add_strategy('test-user', 'Synthetic HFT', 'hft')
        with self.assertRaisesRegex(ValueError, '仅适用于CTA'):
            self.configured_runner(fill_model='volume_limited',participation_rate=.2)


if __name__ == '__main__':
    unittest.main()

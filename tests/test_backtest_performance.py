"""Strategy cards use completed owner-bound snapshots, never default zeroes."""
import csv
from pathlib import Path
import tempfile
import unittest

from wtpy.monitor.BacktestPerformance import merge_latest_performance
from wtpy.monitor.PlusBacktest import PlusBacktestManager, write_json
from wtpy.monitor.PlusReport import native_performance, save_view


class BacktestPerformanceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root/'owner'/'strategy'/'MyStrategy.py'
        self.source.parent.mkdir(parents=True)
        self.source.write_text('current source')
        self.manager = PlusBacktestManager(self.root/'_plus', recover_running=False, cache_root=self.root/'cache')
        self.manager.root.mkdir()
        self.rows = [{'id':'strategy','perform':{'total_return':0,'max_falldown':0}}]

    def run_plus(self, identifier='a'*32, owner='owner', created=100, status='completed', source='current source'):
        folder = self.manager.root/identifier
        folder.mkdir()
        write_json(folder/'task.json',dict(id=identifier,user=owner,straid='strategy',status=status,created_at=created))
        (folder/'MyStrategy.py').write_text(source)
        funds = [dict(equity=value,trading_date=20261020) for value in [10000,11000,9000,9768]]
        report = dict(funds=funds,summary=dict(capital=10000,equity=9768,actual_trades=26,account_source='TraderMocker原生账户'))
        write_json(folder/'report.json',report)
        save_view(folder,report)
        return folder

    def cards(self, user='owner'):
        return merge_latest_performance(self.rows,user,research_root=self.root,plus_manager=self.manager)

    def test_unrun_is_unknown_and_does_not_mutate_legacy_perform(self):
        self.assertIsNone(self.cards()[0]['latest_performance'])
        self.assertNotIn('latest_performance',self.rows[0])

    def test_plus_native_result_updates_card_without_annualizing_or_charging_fees_again(self):
        self.run_plus()
        card=self.cards()[0]['latest_performance']
        self.assertEqual(card['mode'],'plus')
        self.assertAlmostEqual(card['metrics']['total_return'],-2.32)
        self.assertAlmostEqual(card['metrics']['max_falldown'],2000/11000*100)
        self.assertIsNone(card['metrics']['annual_return'])
        self.assertTrue(card['matches_saved_code'])

    def test_latest_failed_and_another_owner_cannot_replace_completed_result(self):
        self.run_plus()
        self.run_plus('b'*32,created=200,status='failed')
        self.run_plus('c'*32,owner='another',created=300)
        self.assertEqual(self.cards()[0]['latest_performance']['task_id'],'a'*32)
        self.assertIsNone(self.cards('unrelated')[0]['latest_performance'])

    def test_changed_source_is_explicitly_historical(self):
        self.run_plus(source='old source')
        self.assertFalse(self.cards()[0]['latest_performance']['matches_saved_code'])

    def test_fast_result_must_belong_to_strategy_and_have_actual_summary(self):
        self.run_plus()
        run=self.source.parent/'backtests'/'fast'
        (run/'outputs_bt'/'fast').mkdir(parents=True)
        (run/'MyStrategy.py').write_text('current source')
        write_json(run/'outputs_bt'/'fast'/'summary.json',{'total_return':3,'max_falldown':2})
        marker={'backtests':{'fast':dict(straid='other',status='completed',created_at=200)}}
        write_json(self.root/'owner'/'marker.json',marker)
        self.assertEqual(self.cards()[0]['latest_performance']['mode'],'plus')
        marker['backtests']['fast']['straid']='strategy'
        write_json(self.root/'owner'/'marker.json',marker)
        self.assertEqual(self.cards()[0]['latest_performance']['metrics']['total_return'],3)

    def test_missing_native_account_or_funds_cannot_appear_as_zero_return(self):
        self.assertIsNone(native_performance({'capital':10000,'equity':10000},[{'equity':10000}]))
        self.assertIsNone(native_performance({'capital':10000,'equity':10000,'account_source':'TraderMocker原生账户'},[]))

    def test_existing_view_upgrade_streams_full_csv_not_only_display_page(self):
        folder=self.run_plus()
        view=__import__('json').loads((folder/'report-view.json').read_text())
        view.pop('performance')
        cache=self.manager.cache_root/('a'*32)
        cache.mkdir(parents=True)
        write_json(cache/'report-view.json',view)
        with (folder/'replay_funds.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['date','time','trading_date','equity','commission','realized','unrealized'])
            writer.writeheader()
            for equity in [10000]*201+[5000,9768]:
                writer.writerow(dict(date=20261020,time=90000000,trading_date=20261020,equity=equity,commission=0,realized=0,unrealized=0))
        self.assertEqual(self.cards()[0]['latest_performance']['metrics']['max_falldown'],50)


if __name__=='__main__':unittest.main()

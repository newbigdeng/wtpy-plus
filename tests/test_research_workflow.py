"""Research checks: tied ranks, exact purging, hidden test and recorded decisions."""
import csv
import math
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from tests.test_research_library import module,Store

diagnostics=module('ResearchDiagnostics');factors=module('ResearchFactors');workflow_module=module('ResearchWorkflow')


def prices(count=220):
    return [dict(time=1000+i*100+(i//3)*10,close=100+math.sin(i/5)+i*.01) for i in range(count)]


def candidates():
    return [dict(id='candidate-'+str(i),builtin_id='momentum',lookback=window,name='momentum',factor_id='factor',factor_version=1,builtin_version=1) for i,window in enumerate((3,5,8),1)]


class DiagnosticsTests(unittest.TestCase):
    def test_tied_ranks_match_average_ranks_and_constant_is_missing(self):
        self.assertEqual(diagnostics.ranks([3,1,1,7]),[3,1.5,1.5,4])
        self.assertAlmostEqual(diagnostics.rank_correlation([3,1,1,7],[30,10,10,70]),1)
        self.assertIsNone(diagnostics.rank_correlation([1,1,1],[1,2,3]))

    def test_label_exit_purge_and_future_test_changes_cannot_change_selection(self):
        rows=prices();first=diagnostics.compare(rows,candidates(),5,factors.calculate)
        self.assertNotIn('test',first);self.assertFalse(first['test_revealed'])
        split=first['split']
        for row in first['candidates']:
            self.assertLess(row['train']['last_label_exit'],split['validation_start'])
            self.assertLess(row['validation']['last_label_exit'],split['test_start'])
            self.assertEqual(row['purged_train'],6)
            self.assertEqual(row['purged_validation'],6)
        changed=[dict(row,close=row['close']*3 if row['time']>=split['test_start'] else row['close']) for row in rows]
        second=diagnostics.compare(changed,candidates(),5,factors.calculate)
        self.assertEqual(first,second)

    def test_correlations_use_aligned_training_values_and_flag_duplicates(self):
        pair=[dict(candidates()[0]),dict(candidates()[0],id='duplicate')]
        result=diagnostics.compare(prices(),pair,2,factors.calculate)
        self.assertAlmostEqual(result['correlations'][0]['rank_correlation'],1)
        self.assertEqual(result['candidates'][1]['redundant_with'],pair[0]['id'])

    def test_purge_does_not_cap_at_ten_percent_when_horizon_is_long(self):
        with self.assertRaisesRegex(ValueError,'样本不足'):
            diagnostics.compare(prices(100),candidates(),30,factors.calculate)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.store=Store(self.tmp.name);self.store.lock=threading.RLock()
        self.manager=workflow_module.ResearchWorkflow(SimpleNamespace(workspace=self.store,lock=threading.Lock()))
        self.store._save('project','alice',dict(id='project',title='Test project',version=1))
        self.store._save('factor','alice',dict(id='factor',name='momentum',builtin_id='momentum',builtin_version=1,version=1,lookback=5))
        self.store._save('factor_experiment','alice',dict(id='experiment',project_id='project',factor_id='factor',status='completed',
            dataset='test-data',code='SYNTHETIC.test',period='m1',start=1000,end=999999,horizon=3))
        path=self.store.root/'factor-results';path.mkdir()
        with (path/'experiment-input.csv').open('w',newline='',encoding='utf-8-sig') as stream:
            writer=csv.DictWriter(stream,fieldnames=['time','close']);writer.writeheader();writer.writerows(prices())

    def run_check(self):
        result=self.manager.run(dict(loginid='alice'),dict(project_id='project',input_experiment_id='experiment',factor_ids=['factor'],windows=[3,5,8]))
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            item=self.manager.detail('alice',result['id'])
            if item['workflow']['status'] not in ('comparing','testing'):return item
            time.sleep(.01)
        self.fail('Research subprocess did not complete')

    def test_owner_isolation_and_project_mismatch(self):
        with self.assertRaises(ValueError):self.manager.run(dict(loginid='bob'),dict(project_id='project',input_experiment_id='experiment'))
        self.store._save('project','alice',dict(id='other-project',title='Other',version=1))
        with self.assertRaisesRegex(ValueError,'当前项目'):
            self.manager.run(dict(loginid='alice'),dict(project_id='other-project',input_experiment_id='experiment'))
        self.store._save('project','alice',dict(id='project',title='Cross section',version=1,kind='cross_section'))
        with self.assertRaisesRegex(ValueError,'单合约时序'):
            self.manager.run(dict(loginid='alice'),dict(project_id='project',input_experiment_id='experiment'))

    def test_freeze_compare_lock_reveal_then_record_lifecycle(self):
        detail=self.run_check();item=detail['workflow'];identifier=item['id']
        self.assertEqual(item['status'],'review',item.get('error'))
        self.assertNotIn('test',detail['analysis'])
        self.assertEqual((self.store.root/'factor-results/experiment-input.csv').read_bytes(),self.manager._input_path(item).read_bytes())
        with self.assertRaisesRegex(ValueError,'选定'):self.manager.reveal('alice',identifier)
        selected=detail['analysis']['recommended_candidate_id']
        self.manager.choose('alice',dict(workflow_id=identifier,candidate_id=selected))
        revealed=self.manager.reveal('alice',identifier)
        self.assertTrue(revealed['workflow']['test_revealed']);self.assertIn('test',revealed['analysis'])
        self.assertEqual(revealed,self.manager.reveal('alice',identifier))
        with self.assertRaisesRegex(ValueError,'揭示前'):self.manager.choose('alice',dict(workflow_id=identifier,candidate_id=selected))
        with self.assertRaises(ValueError):self.manager.detail('bob',identifier)
        adopted=self.manager.decide('alice',dict(workflow_id=identifier,lifecycle='accepted',note='Proceed to WT native cost checks'))
        self.assertEqual(adopted['lifecycle'],'accepted')
        retired=self.manager.decide('alice',dict(workflow_id=identifier,lifecycle='retired',note='Later sample does not support the hypothesis'))
        self.assertEqual(len(retired['decisions']),2);self.assertEqual(retired['lifecycle'],'retired')


if __name__=='__main__':unittest.main()

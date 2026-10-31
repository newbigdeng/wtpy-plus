"""Research ownership, immutable inputs and native task/result integration."""
import base64
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from wtpy.monitor.ResearchWorkspace import ResearchWorkspace, template_source
from wtpy.monitor.ResearchWorkspaceRoutes import register_research_routes
from wtpy.monitor.WtBtMon import WtBtMon

DATASET=dict(id='local_test_rb_m1',groupid='g',code='SHFE.rb.2701',period='m1',rows=100,
             start=202610190900,end=202610191500,model='closed_bar_only')


class NativeManager:
    def __init__(self,path):
        self.path=str(path);self.saved={};self.task_map={};self.runs=[];self.complete=False;self.stopped=False;self.interrupted=False
    def add_strategy(self,owner,name,kind):
        identifier='strategy-'+str(len(self.saved));self.saved[identifier]='';return {'id':identifier}
    def set_strategy_code(self,owner,identifier,code):
        self.saved[identifier]=code
    def run_backtest(self,owner,straid,start,end,capital,**options):
        self.runs.append(dict(owner=owner,straid=straid,start=start,end=end,capital=capital,**options))
        return dict(id='native-1',status='running')
    def get_backtest_detail(self,*args):
        status='interrupted' if self.interrupted else 'completed' if self.complete else 'stopped' if self.stopped else 'running'
        return dict(status=status,state={'progress':100 if self.complete else 20},
                    summary={'total_return':1.2,'annual_return':3.4,'max_falldown':.5,'sharpe_ratio':.8,'days':3},logs='native finished',error='')
    def get_bt_trades(self,*args):
        return [dict(code='SHFE.rb.2701',time=202610190902,direction='LONG',offset='OPEN',price=3000,volume=1,fee=2)]
    def get_bt_rounds(self,*args):return []
    def get_bt_signals(self,*args):return [dict(target=1)]
    def get_bt_funds(self,*args):return [dict(date=20261019,closeprofit=0,dynprofit=12,dynbalance=10,fee=2)]
    def stop_backtest(self,*args):self.stopped=True;return True


class ResearchWorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.bt=NativeManager(self.root/'native')
        self.manager=ResearchWorkspace(self.root/'store',self.bt,self.root)
        self.catalog=patch('wtpy.monitor.ResearchWorkspace.dataset_entries',return_value=[DATASET])
        self.catalog.start();self.addCleanup(self.catalog.stop)
        self.user=dict(loginid='alice',role='admin',products=[])
        app=FastAPI()
        def auth(request,token):
            name=request.headers.get('x-user','alice');return True,dict(loginid=name,role='guest' if name=='guest' else 'admin',products=[])
        register_research_routes(app,self.bt,auth,self.manager)
        self.client=TestClient(app);self.addCleanup(self.client.close)

    def project(self):
        return self.manager.save_project('alice',dict(title='Trend <script>',report_id='public-htfc-cta-20171124',
            hypothesis='趋势是否持续？',formula='fast - slow',template_id='dual_ma'))

    def wait_started(self,identifier):
        until=time.monotonic()+2
        while time.monotonic()<until:
            result=self.manager.detail('alice',identifier)
            if result['experiment']['native_id']:return result
            time.sleep(.01)
        self.fail('Native task did not start')

    def test_owner_and_guest_boundaries(self):
        report=self.manager.save_report('alice',dict(title='Private',body='personal body'))
        project=self.project()
        with self.assertRaisesRegex(ValueError,'不属于'):
            self.manager.save_project('bob',dict(id=project['id'],title='overwrite',report_id=report['id']))
        guest=self.client.post('/mgr/research/list',json={},headers={'x-user':'guest'}).json()
        self.assertEqual(len(guest['reports']),1);self.assertFalse(guest['can_edit'])
        self.assertEqual(guest['projects'],[])
        denied=self.client.post('/mgr/research/report_save',json={'title':'No'},headers={'x-user':'guest'}).json()
        self.assertEqual(denied['result'],-403)

    def test_factor_detail_keeps_numeric_status_and_private_analysis(self):
        self.manager._save('factor_experiment','alice',dict(id='factor-check',owner='alice',status='completed'))
        folder=self.manager.root/'factor-results';folder.mkdir()
        (folder/'factor-check.json').write_text(json.dumps(dict(diagnostics=dict(paired_rows=2))))
        value=self.client.post('/mgr/research/factor_detail',json={'experiment_id':'factor-check'}).json()
        self.assertEqual(value['result'],0)
        self.assertEqual(value['analysis']['diagnostics']['paired_rows'],2)
        for user in ('bob','guest'):
            value=self.client.post('/mgr/research/factor_detail',json={'experiment_id':'factor-check'},headers={'x-user':user}).json()
            self.assertLess(value['result'],0)
            self.assertNotIn('analysis',value)

    def test_report_is_passive_and_link_does_not_fetch(self):
        data=base64.b64encode(b'<script>alert(1)</script>').decode()
        report=self.manager.save_report('alice',dict(title='Paper',source_url='https://example.com/paper',attachment={'filename':'../paper.md','content_base64':data}))
        self.assertEqual(report['attachment_filename'],'paper.md')
        response=self.client.get(report['attachment_url'])
        self.assertIn('sandbox',response.headers['content-security-policy'])
        self.assertEqual(response.content,b'<script>alert(1)</script>')
        with self.assertRaises(ValueError):
            self.manager.save_report('alice',dict(title='Bad',source_url='javascript:alert(1)'))
        with self.assertRaises(ValueError):
            self.manager.save_report('alice',dict(title='Bad',attachment={'filename':'x.pdf','content_base64':data}))

    def test_project_versions_and_custom_source(self):
        project=self.project();self.assertEqual(project['version'],1)
        changed=dict(project,title='Changed',code=project['code']+'\n# a deliberate change\n')
        saved=self.manager.save_project('alice',changed)
        self.assertEqual(saved['version'],2);self.assertIsNone(saved['template_id'])
        self.assertIn('deliberate change',saved['code'])
        with self.assertRaisesRegex(ValueError,'仅执行内置'):
            self.manager.run(self.user,dict(project_id=saved['id'],dataset=DATASET['id']))

    def test_native_workflow_freezes_inputs_and_reads_results(self):
        project=self.project()
        item=self.manager.run(self.user,dict(project_id=project['id'],dataset=DATASET['id'],params={'fast':3,'slow':8,'lots':1}))
        self.wait_started(item['id'])
        self.assertEqual(self.bt.runs[0]['fill_model'],'legacy_cta')
        self.assertIn('self.fast, self.slow, self.lots = 3, 8, 1',next(iter(self.bt.saved.values())))
        self.manager.save_project('alice',dict(project,title='Later edit'))
        self.bt.complete=True
        data=self.manager.detail('alice',item['id'])
        self.assertEqual(data['experiment']['status'],'completed')
        self.assertEqual(data['project']['title'],'Trend <script>')
        self.assertEqual(data['metrics']['trade_count'],1)
        self.assertEqual(data['metrics']['fee'],2)
        self.assertEqual(data['experiment']['log_tail'],'native finished')
        page=self.client.get(data['read_url'])
        self.assertEqual(page.status_code,200)
        self.assertIn('Trend &lt;script&gt;',page.text)
        self.assertNotIn('<script>',page.text)
        self.assertIn('sandbox',page.headers['content-security-policy'])

    def test_single_run_cancel_and_permission(self):
        project=self.project()
        item=self.manager.run(self.user,dict(project_id=project['id'],dataset=DATASET['id']))
        self.wait_started(item['id'])
        with self.assertRaisesRegex(RuntimeError,'已有研究'):
            self.manager.run(self.user,dict(project_id=project['id'],dataset=DATASET['id']))
        with self.assertRaises(ValueError):self.manager.cancel('bob',item['id'])
        self.assertEqual(self.manager.cancel('alice',item['id'])['status'],'stopped')
        self.assertTrue(self.bt.stopped)
        partial=self.manager.detail('alice',item['id'])
        self.assertEqual(partial['metrics']['trade_count'],1)
        self.assertEqual(partial['experiment']['error'],'用户取消实验')
        self.assertTrue(partial['read_url'])
        restricted=dict(self.user,products=['other'])
        with self.assertRaisesRegex(ValueError,'访问权限'):
            self.manager.run(restricted,dict(project_id=project['id'],dataset=DATASET['id']))

    def test_restart_retains_input_and_marks_interrupted_prepare(self):
        self.manager._save('experiment','alice',dict(id='pending',owner='alice',status='preparing',native_id=None))
        recovered=ResearchWorkspace(self.root/'store',self.bt,self.root)
        self.assertEqual(recovered._get('experiment','alice','pending')['status'],'interrupted')

    def test_native_restart_and_late_cancel_keep_terminal_results(self):
        project=self.project()
        item=self.manager.run(self.user,dict(project_id=project['id'],dataset=DATASET['id']))
        self.wait_started(item['id']);self.bt.interrupted=True
        recovered=ResearchWorkspace(self.root/'store',self.bt,self.root)
        detail=recovered.detail('alice',item['id'])
        self.assertEqual(detail['experiment']['status'],'interrupted')
        self.assertEqual(detail['project']['id'],project['id'])
        self.assertTrue(detail['read_url'])
        self.bt.interrupted=False;self.bt.complete=True
        another=self.manager.run(self.user,dict(project_id=project['id'],dataset=DATASET['id']))
        self.wait_started(another['id'])
        self.assertEqual(self.manager.cancel('alice',another['id'])['status'],'completed')
        self.assertFalse(self.bt.stopped)

    def test_template_uses_only_closed_native_bar_callbacks(self):
        source=template_source();compile(source,'<template>','exec')
        self.assertIn('def on_calculate',source)
        self.assertNotIn('on_tick',source)
        with self.assertRaises(ValueError):template_source({'fast':20,'slow':5})

    def test_tick_is_not_offered_or_silently_converted_to_bars(self):
        tick=dict(DATASET,id='local_test_rb_tick',period='tick',model='recorded_tick')
        with patch('wtpy.monitor.ResearchWorkspace.dataset_entries',return_value=[DATASET,tick]):
            listing=self.client.post('/mgr/research/list',json={}).json()
            self.assertEqual([row['id'] for row in listing['datasets']],[DATASET['id']])
            project=self.project()
            rejected=self.client.post('/mgr/research/experiment_run',json={
                'project_id':project['id'],'dataset':tick['id']}).json()
        self.assertEqual(rejected['result'],-2)
        self.assertIn('仅支持 K 线',rejected['message'])
        self.assertEqual(self.bt.runs,[])
        self.assertEqual(self.bt.saved,{})
        self.assertEqual(self.manager._rows('experiment','alice'),[])

    def test_status_poll_skips_catalog_and_preserves_owner_and_guest_visibility(self):
        report=self.manager.save_report('alice',dict(title='Private report'))
        project=self.project()
        experiment=self.manager.run(self.user,dict(project_id=project['id'],dataset=DATASET['id']))
        self.wait_started(experiment['id']);self.bt.complete=True
        self.manager.detail('alice',experiment['id'])
        full=self.client.post('/mgr/research/list',json={}).json()
        self.assertTrue(all(key in full for key in ('datasets','templates','lake')))
        with patch('wtpy.monitor.ResearchWorkspace.dataset_entries') as datasets,patch.object(self.manager,'lake_catalog') as lake:
            owner=self.client.post('/mgr/research/list',json={'include_catalog':False}).json()
            guest=self.client.post('/mgr/research/list',json={'include_catalog':False},headers={'x-user':'guest'}).json()
            other=self.client.post('/mgr/research/list',json={'include_catalog':False},headers={'x-user':'bob'}).json()
            datasets.assert_not_called();lake.assert_not_called()
        for value in (owner,guest,other):
            self.assertEqual(value['result'],0)
            self.assertFalse(any(key in value for key in ('datasets','templates','lake')))
        for key in ('reports','projects','experiments','can_edit'):
            self.assertEqual(owner[key],full[key])
        self.assertTrue(owner['can_edit']);self.assertFalse(guest['can_edit'])
        self.assertEqual(guest['projects'],[]);self.assertEqual(guest['experiments'],[])
        self.assertEqual(other['projects'],[]);self.assertEqual(other['experiments'],[])
        self.assertNotIn(report['id'],[row['id'] for row in guest['reports']+other['reports']])

    def test_catalog_is_separate_and_uses_the_callers_group_permissions(self):
        other=dict(DATASET,id='private-history',groupid='private')
        with patch('wtpy.monitor.ResearchWorkspace.dataset_entries',return_value=[DATASET,other]) as datasets:
            value=self.manager.catalog(dict(self.user,products=['g']))
            self.assertEqual([row['id'] for row in value['datasets']],[DATASET['id']])
            options=datasets.call_args.kwargs
            self.assertEqual(options['periods'],('m1','m5','d'))
            self.assertTrue(options['group_filter']('g'))
            self.assertFalse(options['group_filter']('private'))
        guest=self.client.post('/mgr/research/catalog',json={},headers={'x-user':'guest'}).json()
        self.assertEqual(guest['result'],0)
        self.assertEqual(set(guest),{'result','datasets','templates','lake'})

    def test_lake_catalog_is_separate_from_wt_and_hides_paths(self):
        (self.manager.root/'lake-catalog.json').write_text(json.dumps(dict(updated_at=100,
            datasets=[dict(symbol='RB2701.SHF',frequency='1m',rows=120,start='2026-10-19',end='2026-10-23',
                           dataset='futures_minute_bars',path='/private/lake')])))
        value=self.manager.list(self.user)
        self.assertEqual(value['lake']['datasets'][0]['period'],'m1')
        self.assertNotIn('path',value['lake']['datasets'][0])
        self.assertEqual(value['datasets'][0]['id'],DATASET['id'])

    def test_native_runner_uses_selected_frozen_fee_instead_of_template(self):
        root=self.root/'native-fee';(root/'template').mkdir(parents=True)
        baseline=root/'template/fees.json';baseline.write_text('{"SHFE.rb":{"open":999}}')
        (root/'template/configbt.json').write_text(json.dumps({'env':{'mocker':'cta'},
            'replayer':{'basefiles':{},'fees':str(baseline)}}))
        (root/'template/logcfgbt.json').write_text('{}')
        frozen=root/'frozen/fees.json';frozen.parent.mkdir();frozen.write_text('{"SHFE.rb":{"open":2}}')
        native=WtBtMon(str(root));strategy=native.add_strategy('alice','Fee check','cta')
        metadata=dict(code='SHFE.rb.2701',period='m1',tick_enabled=False,storage=str(root/'history'),
                      basefiles={'fee':str(frozen)},model='closed_bar_only')
        with patch('wtpy.monitor.HistoryCatalog.prepare_native_dataset',return_value=metadata),patch('wtpy.monitor.WtBtMon.WtBtTask.run'):
            task=native.run_backtest('alice',strategy['id'],202610190900,202610191500,1000000,
                                     fill_model='legacy_cta',dataset='selected')
        path=root/'alice'/strategy['id']/'backtests'/task['id']/'configbt.json'
        config=json.loads(path.read_text())
        self.assertEqual(config['replayer']['fees'],str(frozen))
        self.assertNotEqual(frozen.read_bytes(),baseline.read_bytes())


if __name__=='__main__':unittest.main()

"""Owner permissions and the stable result=0 / analysis payload contract."""
import csv
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from wtpy.monitor.ResearchWorkspace import ResearchWorkspace
from wtpy.monitor.ResearchWorkspaceRoutes import register_research_routes
from tests.test_research_workflow import prices

DATASET=dict(id='synthetic-m1',groupid='test',code='SYNTHETIC.test',period='m1',rows=220,start=1000,end=999999)


class ResearchWorkflowApiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.bt=SimpleNamespace(path=str(self.root/'native'))
        self.manager=ResearchWorkspace(self.root/'store',self.bt,self.root)
        self.catalog=patch('wtpy.monitor.ResearchWorkspace.dataset_entries',return_value=[DATASET]);self.catalog.start();self.addCleanup(self.catalog.stop)
        app=FastAPI()
        def auth(request,token):
            name=request.headers.get('x-user','alice');return True,dict(loginid=name,role='guest' if name=='guest' else 'researcher',products=[])
        register_research_routes(app,self.bt,auth,self.manager)
        self.client=TestClient(app);self.addCleanup(self.client.close)
        self.project=self.manager.save_project('alice',dict(title='Flow',template_id='dual_ma'))
        self.factor=self.manager.factors.save('alice',dict(name='Mom',builtin_id='momentum',lookback=5))
        self.manager._save('factor_experiment','alice',dict(id='frozen',project_id=self.project['id'],factor_id=self.factor['id'],
            factor_snapshot=self.factor,status='completed',dataset=DATASET['id'],code=DATASET['code'],period='m1',start=1000,end=999999,horizon=3))
        folder=self.manager.root/'factor-results';folder.mkdir()
        with (folder/'frozen-input.csv').open('w',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['time','close']);writer.writeheader();writer.writerows(prices())
        (folder/'frozen.json').write_text(json.dumps(dict(diagnostics=dict(rank_ic=.2))),encoding='utf-8')

    def test_factor_detail_analysis_does_not_override_result_code(self):
        value=self.client.post('/mgr/research/factor_detail',json=dict(experiment_id='frozen')).json()
        self.assertEqual(value['result'],0);self.assertEqual(value['analysis']['diagnostics']['rank_ic'],.2)
        foreign=self.client.post('/mgr/research/factor_detail',json=dict(experiment_id='frozen'),headers={'x-user':'bob'}).json()
        self.assertLess(foreign['result'],0)
        guest=self.client.post('/mgr/research/workflow_run',json={},headers={'x-user':'guest'}).json()
        self.assertEqual(guest['result'],-403)

    def test_api_fixed_input_to_single_reveal_and_rejection(self):
        value=self.client.post('/mgr/research/workflow_run',json=dict(project_id=self.project['id'],input_experiment_id='frozen',
            factor_ids=[self.factor['id']],windows=[3,5,8])).json()
        self.assertEqual(value['result'],0,value)
        identifier=value['workflow']['id'];deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            value=self.client.post('/mgr/research/workflow_detail',json=dict(workflow_id=identifier)).json()
            if value['workflow']['status']!='comparing':break
            time.sleep(.01)
        self.assertEqual(value['workflow']['status'],'review',value)
        self.assertNotIn('test',value['analysis'])
        candidate=value['analysis']['recommended_candidate_id']
        self.assertEqual(self.client.post('/mgr/research/workflow_choose',json=dict(workflow_id=identifier,candidate_id=candidate)).json()['result'],0)
        revealed=self.client.post('/mgr/research/workflow_reveal',json=dict(workflow_id=identifier)).json()
        self.assertEqual(revealed['result'],0);self.assertIn('test',revealed['analysis'])
        self.assertLess(self.client.post('/mgr/research/workflow_choose',json=dict(workflow_id=identifier,candidate_id=candidate)).json()['result'],0)
        rejected=self.client.post('/mgr/research/workflow_decide',json=dict(workflow_id=identifier,lifecycle='rejected',note='Synthetic API verification only')).json()
        self.assertEqual(rejected['result'],0);self.assertEqual(rejected['workflow']['lifecycle'],'rejected')
        foreign=self.client.post('/mgr/research/workflow_detail',json=dict(workflow_id=identifier),headers={'x-user':'bob'}).json()
        self.assertLess(foreign['result'],0)


if __name__=='__main__':unittest.main()

"""Focused numerical, ownership, workflow and shared-budget regressions."""
import json
from contextlib import closing
import math
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
from terminal_analytics import calculate, TOOLS
from terminal_data import DataHub
from terminal_workflows import validate_graph, Workflows
from terminal_research import ResearchDesk, SCHEMA
from terminal_service import Terminal, Records
from research_budget import reserve_call


class TerminalTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        with closing(sqlite3.connect(self.root/'research.sqlite3')) as db:
            db.execute('CREATE TABLE records(kind TEXT,id TEXT,owner TEXT,data TEXT,PRIMARY KEY(kind,id,owner))')
            db.commit()
        self.store=Records(self.root)
        self.config=dict(workspace_root=str(self.root),research_lock=str(self.root/'research.lock'),
            rd_agent=dict(budget_file=str(self.root/'budget.json'),max_calls_per_day=2,base_url='http://127.0.0.1:1',model='fixture',timeout_seconds=3))

    def tearDown(self):self.tmp.cleanup()

    def test_black_price_parity_implied_and_american(self):
        call=calculate('black76',{})['metrics']['权利金']
        self.assertAlmostEqual(call,7.7301493592779,places=6)
        call=calculate('black76',{'forward':110})['metrics']['权利金']
        put=calculate('black76',{'forward':110,'side':'put'})['metrics']['权利金']
        self.assertAlmostEqual(call-put,10*math.exp(-.03),places=9)
        iv=calculate('implied_vol',{'forward':110,'premium':call})['metrics']['隐含波动率']
        self.assertAlmostEqual(iv,.2,places=6)
        euro=calculate('black_scholes',{'side':'put'})['metrics']['权利金']
        american=calculate('american',{'side':'put'})['metrics']['权利金']
        self.assertGreater(american,euro)

    def test_rate_options_payment_time_and_annuity(self):
        params=dict(forward=.04,strike=.03)
        call=calculate('swaption',params)['metrics']['权利金']
        put=calculate('swaption',dict(params,side='put'))['metrics']['权利金']
        self.assertAlmostEqual(call-put,4.5*(.04-.03),places=12)
        cap=calculate('caplet',params)['metrics']['权利金']
        floor=calculate('caplet',dict(params,side='put'))['metrics']['权利金']
        self.assertAlmostEqual(cap-floor,1000000*.5*(.04-.03)*math.exp(-.03*1.5),places=8)

    def test_all_quantlib_models_and_cashflow_constraints(self):
        self.assertEqual(sum(t['group']=='QuantLib' for t in TOOLS),18)
        for tool in TOOLS:
            if tool['id'] not in ('risk','optimize'):
                result=calculate(tool['id'],{})
                json.dumps(result,allow_nan=False)
        self.assertAlmostEqual(calculate('bond',{'yield':.04})['metrics']['价格'],100,places=8)
        self.assertAlmostEqual(calculate('zero_bond',{})['metrics']['现值'],100*math.exp(-.15),places=8)
        self.assertAlmostEqual(calculate('dcf',{'cashflows':'100','discount':.1,'growth':0,'debt':0,'shares':1})['metrics']['企业价值'],1000,places=8)
        with self.assertRaises(ValueError):calculate('dcf',{'growth':.2,'discount':.1})
        with self.assertRaises(ValueError):calculate('black76',{'years':0})

    def test_risk_tail_and_frequency(self):
        p=dict(returns=[-.01,.01]*10,periods=252,confidence=.95,capital=10000,risk_free=0)
        result=calculate('risk',p)['metrics']
        self.assertAlmostEqual(result['累计收益'],.9999**10-1,places=10)
        self.assertAlmostEqual(result['单期 VaR'],.01)
        self.assertAlmostEqual(result['单期 CVaR'],.01)
        self.assertAlmostEqual(result['夏普比率'],0)
        with self.assertRaises(ValueError):calculate('risk',dict(p,returns=[.1]*19))
        with self.assertRaises(ValueError):calculate('risk',dict(p,returns=[float('nan')]*20))

    def test_optimizer_constraints_and_permutation(self):
        a=np.array([[.01,-.01],[-.01,.01]]*20)
        p=dict(matrix=a.tolist(),objective='min_variance',periods=252,max_weight=.6,risk_free=0)
        result=calculate('optimize',p)
        weights=[r['weight'] for r in result['rows']]
        self.assertAlmostEqual(sum(weights),1)
        self.assertAlmostEqual(weights[0],.5,places=5)
        self.assertLess(result['metrics']['年化波动率'],1e-8)
        with self.assertRaises(ValueError):calculate('optimize',dict(p,max_weight=.4))

    def test_cache_stale_retains_provenance(self):
        hub=DataHub(self.root/'cache')
        self.assertEqual(hub.read('btc')['status'],'not_loaded')
        with patch('terminal_data.collect',return_value=dict(series=[],rows=[dict(last=3)],source_url='https://example.org')):
            result=hub.read('btc',True)
        self.assertEqual(result['status'],'ready')
        path=self.root/'cache/btc.json';value=json.loads(path.read_text());value['fetched_at']-=120;path.write_text(json.dumps(value))
        with patch('terminal_data.collect',side_effect=OSError('provider down')):
            result=hub.read('btc',True)
        self.assertTrue(result['stale']);self.assertEqual(result['source_url'],'https://example.org')
        with self.assertRaises(ValueError):hub.read('../../private')

    def graph(self):
        return dict(name='test',nodes=[dict(id=i,type=t,params={}) for i,t in [('a','data'),('b','returns'),('c','risk'),('d','result')]],
                    edges=[dict(source=a,target=b) for a,b in [('a','b'),('b','c'),('c','d')]])

    def test_graph_executes_and_rejects_bypass_cycle_and_other_owner(self):
        graph=self.graph();hub=DataHub(self.root/'cache');flows=Workflows(self.store,hub)
        saved=flows.save('alice',graph)
        series=[dict(time=str(i),value=100+i) for i in range(25)]
        with patch.object(hub,'read',return_value=dict(status='ready',frequency='日度',series=series,source_url='https://example.org')):
            result=flows.run('alice',saved['id'])
        self.assertEqual(result['status'],'completed')
        self.assertFalse(result['outputs']['d']['costs_included'])
        with self.assertRaises(StopIteration):flows.run('bob',saved['id'])
        graph['edges'].append(dict(source='d',target='a'))
        with self.assertRaises(ValueError):validate_graph(graph)
        graph=self.graph();graph['nodes'][2]['type']='safe_apply'
        with self.assertRaises(ValueError):validate_graph(graph)

    def test_guest_cannot_mutate_or_refresh_network(self):
        terminal=Terminal(self.config)
        for action in ('calculate','research_start','workflow_save','workflow_run'):
            with self.assertRaises(PermissionError):terminal.dispatch(action,'alice',{},True)
        with patch('terminal_data.collect',side_effect=AssertionError('guest fetched network')):
            self.assertEqual(terminal.dispatch('data','alice',dict(dataset='btc',refresh=True),True)['status'],'not_loaded')

    def test_bridge_schema_source_scope_and_shared_quota(self):
        observed=[]
        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                observed.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                body=json.dumps(dict(choices=[dict(finish_reason='stop',message=dict(content=json.dumps({key:'fixture' for key in SCHEMA['required']})))]))
                self.send_response(200);self.end_headers();self.wfile.write(body.encode())
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            self.config['rd_agent']['base_url']='http://127.0.0.1:'+str(server.server_port)
            self.store._save('report','alice',dict(id='source1',title='原文',body='公开测试原文'))
            desk=ResearchDesk(self.store,self.config)
            with self.assertRaises(StopIteration):desk.start('bob',dict(role='factor',question='检查这个因子所需的数据以及反例',report_ids=['source1']))
            job=desk.start('alice',dict(role='factor',question='检查这个因子所需的数据以及反例',report_ids=['source1']))
            for _ in range(50):
                result=self.store._get('terminal_research','alice',job['id'])
                if result['status']!='running':break
                time.sleep(.03)
            self.assertEqual(result['status'],'completed')
            self.assertTrue(observed[0]['response_format']['json_schema']['strict'])
            self.assertNotIn('tools',observed[0])
            self.assertIn('source1',observed[0]['messages'][1]['content'])
            reserve_call(self.config['rd_agent']['budget_file'],2)
            with self.assertRaises(RuntimeError):reserve_call(self.config['rd_agent']['budget_file'],2)
            self.assertEqual(desk.budget()['remaining'],0)
        finally:server.shutdown();server.server_close()


if __name__=='__main__':unittest.main(verbosity=2)

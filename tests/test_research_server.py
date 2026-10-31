"""The companion validates existing sessions and never recovers the live manager's tasks."""
import json
from contextlib import closing
import sqlite3
from pathlib import Path
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from unittest.mock import patch
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from wtpy.monitor.ResearchServer import monitor_auth, create_research_app


class ResearchServerTests(unittest.TestCase):
    def test_strategy_performance_denied_upstream_never_reads_local_ledger(self):
        with tempfile.TemporaryDirectory() as directory:
            app=self.account_app(Path(directory))
            with patch('wtpy.monitor.ResearchServer.monitor_json',return_value={'result':-403}), \
                 patch('wtpy.monitor.DataMgr.DataMgr.get_funds') as funds:
                value=TestClient(app).post('/mgr/qryfunds',json={'groupid':'g','strategyid':'example'}).json()
            self.assertEqual(value['result'],-403)
            funds.assert_not_called()

    def test_strategy_performance_uses_authorized_readonly_companion(self):
        with tempfile.TemporaryDirectory() as directory:
            app=self.account_app(Path(directory))
            rows=[{'strategy':'example','date':20261027,'addition':72,'daily_basis':'reconciled_flat_trade_ledger'}]
            with patch('wtpy.monitor.ResearchServer.monitor_json',return_value={'result':0,'funds':[]}), \
                 patch('wtpy.monitor.DataMgr.DataMgr.get_funds',return_value=rows) as funds, \
                 patch('wtpy.monitor.ResearchServer.trade_risk_snapshot') as broker:
                value=TestClient(app).post('/mgr/qryfunds',json={'groupid':'g','strategyid':'example'}).json()
            self.assertEqual(value['funds'],rows)
            self.assertEqual(value['performance_kind'],'strategy_theoretical')
            funds.assert_called_once_with('g','example')
            broker.assert_not_called()

    def test_quote_refresh_bypasses_ledgers_and_the_full_monitor_request(self):
        with tempfile.TemporaryDirectory() as directory:
            app=self.account_app(Path(directory))
            def upstream(_server,path,*_args):
                self.assertEqual(path,'/mgr/session')
                return {'result':0,'userinfo':{'loginid':'alice','role':'viewer','products':['g']}}
            market={'code':'SHFE.rb.2701','quote':{'price':3200},'bars':[]}
            with patch('wtpy.monitor.ResearchServer.monitor_json',side_effect=upstream), \
                 patch('wtpy.monitor.WorkbenchApi.WorkbenchFeed.market',return_value=market) as read_market, \
                 patch('wtpy.monitor.ResearchServer.trade_risk_snapshot') as funds:
                value=TestClient(app).post('/mgr/workbench',json={'groupid':'g','code':'SHFE.rb.2701',
                    'quote_only':True,'count':20}).json()
            self.assertEqual(value,{'result':0,'market':market})
            self.assertTrue(read_market.call_args.kwargs['quote_only'])
            funds.assert_not_called()

    def test_fast_market_paths_enforce_group_visibility_before_reading_files(self):
        with tempfile.TemporaryDirectory() as directory:
            app=self.account_app(Path(directory))
            with patch('wtpy.monitor.ResearchServer.monitor_json',return_value={
                    'result':0,'userinfo':{'loginid':'alice','role':'viewer','products':['allowed']}}), \
                 patch('wtpy.monitor.ResearchServer.sqlite3.connect') as database, \
                 patch('wtpy.monitor.WorkbenchApi.WorkbenchFeed.market') as read_market:
                for mode in ('quote_only','market_only'):
                    value=TestClient(app).post('/mgr/workbench',json={'groupid':'g',mode:True}).json()
                    self.assertEqual(value['result'],-403)
            database.assert_not_called();read_market.assert_not_called()

    def test_strategy_cards_use_only_authorized_rows_and_authenticated_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);app=self.account_app(root)
            def upstream(_server,path,_body,_headers):
                if path=='/mgr/session':return {'result':0,'userinfo':{'loginid':'alice','role':'researcher'}}
                return {'result':0,'strategies':[{'id':'allowed','name':'Synthetic CTA'}]}
            with patch('wtpy.monitor.ResearchServer.monitor_json',side_effect=upstream), \
                 patch('wtpy.monitor.ResearchServer.merge_latest_performance',return_value=[
                    {'id':'allowed','latest_performance':{'metrics':{'total_return':-2.0,'max_falldown':3.0}}}]) as merge:
                response=TestClient(app).post('/bt/qrystras',json={'owner':'bob'})
                value=response.json()
            self.assertEqual(value['result'],0)
            self.assertEqual(value['strategies'][0]['latest_performance']['metrics']['total_return'],-2)
            self.assertEqual(merge.call_args.args,([{'id':'allowed','name':'Synthetic CTA'}],'alice'))
            self.assertEqual(Path(merge.call_args.kwargs['research_root']),root)
            self.assertEqual(response.headers['cache-control'],'no-store')

    def test_strategy_cards_retain_upstream_denial_and_guest_redaction(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);app=self.account_app(root)
            def upstream(_server,path,_body,_headers):
                if path=='/mgr/session':return {'result':0,'userinfo':{'loginid':'guest','role':'guest'}}
                return {'result':-403,'message':'denied'}
            with patch('wtpy.monitor.ResearchServer.monitor_json',side_effect=upstream), \
                 patch('wtpy.monitor.ResearchServer.merge_latest_performance') as merge:
                self.assertEqual(TestClient(app).get('/bt/qrystras').json()['result'],-403)
                merge.assert_not_called()
                upstream_result={'result':0,'strategies':[{'id':'s'}]}
                with patch('wtpy.monitor.ResearchServer.monitor_json',side_effect=lambda _s,path,*_:
                        upstream(_s,path,None,None) if path=='/mgr/session' else upstream_result):
                    merge.return_value=[{'id':'s','params':{'private':1},'latest_performance':{'path':'/private','metrics':{'total_return':1}}}]
                    value=TestClient(app).post('/bt/qrystras',json={}).json()
            self.assertEqual(merge.call_args.args[1],'localadmin')
            self.assertNotIn('params',value['strategies'][0])
            self.assertNotIn('path',value['strategies'][0]['latest_performance'])

    def account_app(self,root,groups=(('g','live'),)):
        template=root/'template';template.mkdir()
        database=root/'monitor.db'
        with closing(sqlite3.connect(database)) as connection:
            connection.execute('CREATE TABLE groups(groupid TEXT,path TEXT)')
            for groupid,folder in groups:
                path=root/folder;path.mkdir()
                connection.execute('INSERT INTO groups VALUES(?,?)',(groupid,str(path)))
            connection.commit()
        class NativeManager:
            def __init__(self,path):pass
            def shutdown(self):pass
        with patch('wtpy.monitor.ResearchServer.WtBtMon',NativeManager):
            return create_research_app(root=root/'store',runs=root/'runs',template=template,
                plus_root=root/'plus',upstream='http://monitor',records_db=database)

    def test_account_finances_keep_existing_role_boundary_before_disk_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            app=self.account_app(Path(directory))
            with patch('wtpy.monitor.ResearchServer.sqlite3.connect') as database, \
                 patch('wtpy.monitor.ResearchServer.trade_risk_snapshot') as funds:
                for role in ('viewer','operator','trader','researcher','risker'):
                    with self.subTest(role=role),patch('wtpy.monitor.ResearchServer.monitor_json',return_value={
                            'result':0,'userinfo':{'loginid':'reader','role':role,'products':['g']}}):
                        self.assertEqual(TestClient(app).post('/mgr/account_equity',json={'groupid':'g'}).json()['result'],-403)
            database.assert_not_called();funds.assert_not_called()

    def test_guest_account_results_and_failures_do_not_expose_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            app=self.account_app(Path(directory))
            with patch('wtpy.monitor.ResearchServer.monitor_json',return_value={
                    'result':0,'userinfo':{'loginid':'guest','role':'guest'}}), \
                 patch('wtpy.monitor.DataMgr.DataMgr.get_channels',return_value=[]), \
                 patch('wtpy.monitor.ResearchServer.trade_risk_snapshot',return_value={'accounts':[]}), \
                 patch('wtpy.monitor.ResearchServer.record_account_receipts',return_value=[]), \
                 patch('wtpy.monitor.ResearchServer.account_analytics',return_value={
                    'accounts':[{'account_key':'a:CNY','path':'/private/accounts','source':'/private/source'}]}):
                result=TestClient(app).post('/mgr/account_equity',json={'groupid':'g'}).json()
                self.assertEqual(result['accounts'],[{'account_key':'a:CNY'}])
                with patch('wtpy.monitor.ResearchServer.sqlite3.connect',side_effect=OSError('/private/accounts')):
                    result=TestClient(app).post('/mgr/account_equity',json={'groupid':'g'}).json()
                self.assertEqual(result['result'],-2)
                self.assertNotIn('/private',result['message'])

    def test_shutdown_waits_for_inflight_local_receipt_capture_before_closing_feed(self):
        with tempfile.TemporaryDirectory() as directory:
            app=self.account_app(Path(directory))
            entered,release,ready,finish=(threading.Event() for _ in range(4))
            def funds(*args,**kwargs):
                entered.set()
                if not release.wait(3):raise RuntimeError('fixture capture was not released')
                return {'accounts':[]}
            def serve():
                with TestClient(app):
                    ready.set();finish.wait(3)
            with patch('wtpy.monitor.DataMgr.DataMgr.get_channels',return_value=[]), \
                 patch('wtpy.monitor.ResearchServer.trade_risk_snapshot',side_effect=funds), \
                 patch('wtpy.monitor.ResearchServer.record_account_receipts',return_value=[]), \
                 patch('wtpy.monitor.WorkbenchApi.WorkbenchFeed.close') as close,ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(serve)
                try:
                    self.assertTrue(ready.wait(2));self.assertTrue(entered.wait(2));finish.set()
                    with self.assertRaises(FutureTimeout):future.result(timeout=.05)
                    close.assert_not_called()
                finally:
                    release.set();finish.set()
                future.result(timeout=3)
                close.assert_called_once()

    def test_one_bad_recorded_group_does_not_stop_other_account_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            app=self.account_app(Path(directory),(('bad','bad'),('good','good')))
            captured=threading.Event()
            def funds(group,*args):
                if group['id']=='bad':raise ValueError('synthetic malformed group')
                captured.set();return {'accounts':[]}
            with patch('wtpy.monitor.DataMgr.DataMgr.get_channels',return_value=[]), \
                 patch('wtpy.monitor.ResearchServer.trade_risk_snapshot',side_effect=funds), \
                 patch('wtpy.monitor.ResearchServer.record_account_receipts',return_value=[]) as record:
                with TestClient(app):self.assertTrue(captured.wait(2))
            self.assertEqual(record.call_args.args[0],'good')

    def test_equity_requires_group_access_before_reading_accounts(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);template=root/'template';template.mkdir()
            class NativeManager:
                def __init__(self,path):pass
                def shutdown(self):pass
            with patch('wtpy.monitor.ResearchServer.WtBtMon',NativeManager):
                app=create_research_app(root=root/'store',runs=root/'runs',template=template,
                    plus_root=root/'plus',upstream='http://monitor',records_db=root/'private.db')
            with patch('wtpy.monitor.ResearchServer.monitor_json',return_value={
                    'result':0,'userinfo':{'loginid':'reader','role':'viewer','products':['allowed']}}), \
                 patch('wtpy.monitor.ResearchServer.sqlite3.connect') as database, \
                 patch('wtpy.monitor.ResearchServer.trade_risk_snapshot') as funds:
                value=TestClient(app).post('/mgr/account_equity',json={'groupid':'other'}).json()
            self.assertEqual(value['result'],-403)
            database.assert_not_called();funds.assert_not_called()

    def test_equity_reads_native_receipts_without_market_or_broker_commands(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);template=root/'template';template.mkdir();run=root/'live';run.mkdir()
            database=root/'monitor.db'
            with closing(sqlite3.connect(database)) as connection:
                connection.execute('CREATE TABLE groups(groupid TEXT,path TEXT)')
                connection.execute('INSERT INTO groups VALUES(?,?)',('g',str(run)))
                connection.commit()
            class NativeManager:
                def __init__(self,path):pass
                def shutdown(self):pass
            with patch('wtpy.monitor.ResearchServer.WtBtMon',NativeManager):
                app=create_research_app(root=root/'store',runs=root/'runs',template=template,
                    plus_root=root/'plus',upstream='http://monitor',records_db=database)
            account={'channel':'a','currency':'CNY','history':[
                {'time':1,'equity':1010,'trading_date':20261026,'prebalance':1000,'deposit':0,'withdraw':0}]}
            with patch('wtpy.monitor.ResearchServer.monitor_json',return_value={
                    'result':0,'userinfo':{'loginid':'owner','role':'admin'}}), \
                 patch('wtpy.monitor.DataMgr.DataMgr.get_channels',return_value=['a']), \
                 patch('wtpy.monitor.ResearchServer.trade_risk_snapshot',return_value={'accounts':[account]}) as funds, \
                 patch('wtpy.monitor.ResearchServer.record_account_receipts',side_effect=lambda _id,rows,*_:rows) as record, \
                 patch('wtpy.monitor.WorkbenchApi.WorkbenchFeed.market') as market:
                response=TestClient(app).post('/mgr/account_equity',json={
                    'groupid':'g','start_date':20261026,'end_date':20261026,'segment_id':'current'})
            value=response.json()
            self.assertEqual(value['result'],0)
            self.assertEqual(value['accounts'][0]['summary']['pnl'],10)
            self.assertEqual(value['accounts'][0]['daily'][0]['trading_date'],20261026)
            self.assertEqual(funds.call_args.args,({'id':'g','path':str(run)},['a']))
            record.assert_called_once();market.assert_not_called()
            self.assertEqual(response.headers['cache-control'],'no-store')

    def test_session_stays_with_the_original_monitor_and_cross_site_is_rejected(self):
        app=FastAPI()
        @app.post('/check')
        def check(request:Request):
            ok,value=monitor_auth('http://monitor',request)
            return {'ok':ok,'value':value}
        client=TestClient(app)
        with patch('wtpy.monitor.ResearchServer.monitor_json',return_value={
            'result':0,'userinfo':{'loginid':'owner','role':'admin'}}) as upstream:
            result=client.post('/check',headers={'cookie':'WtMonSvr_sid=existing','origin':'http://testserver'}).json()
            self.assertTrue(result['ok'])
            self.assertEqual(upstream.call_args.args[3]['cookie'],'WtMonSvr_sid=existing')
            upstream.reset_mock()
            result=client.post('/check',headers={'origin':'https://unrelated.example'}).json()
            self.assertFalse(result['ok'])
            upstream.assert_not_called()

    def test_read_sidecar_keeps_running_task_and_owner_checks(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);template=root/'template';template.mkdir()
            taskid='a'*32;taskdir=root/'plus'/taskid;taskdir.mkdir(parents=True)
            task=dict(id=taskid,user='owner',status='running',finance={},created_at=1)
            (taskdir/'task.json').write_text(json.dumps(task))
            class NativeManager:
                def __init__(self,path):self.path=path
                def shutdown(self):pass
            with patch('wtpy.monitor.ResearchServer.WtBtMon',NativeManager):
                app=create_research_app(root=root/'store',runs=root/'runs',template=template,
                    plus_root=root/'plus',upstream='http://monitor')
            client=TestClient(app)
            with patch('wtpy.monitor.ResearchServer.monitor_json',return_value={
                'result':0,'userinfo':{'loginid':'owner','role':'admin'}}):
                value=client.post('/bt/plus/detail',json={'taskid':taskid}).json()
                self.assertEqual(value['task']['status'],'running')
                self.assertEqual(json.loads((taskdir/'task.json').read_text()),task)
            with patch('wtpy.monitor.ResearchServer.monitor_json',return_value={
                'result':0,'userinfo':{'loginid':'other','role':'researcher'}}):
                self.assertLess(client.post('/bt/plus/detail',json={'taskid':taskid}).json()['result'],0)
            self.assertEqual(client.get('/healthz').json()['watchdog'],False)
            self.assertEqual(client.post('/bt/plus/run',json={}).status_code,404)

    def test_guest_task_list_hides_new_constructor_defaults_and_other_users_stay_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);template=root/'template';template.mkdir()
            taskid='b'*32;taskdir=root/'plus'/taskid;taskdir.mkdir(parents=True)
            task=dict(id=taskid,user='owner',status='completed',created_at=1,
                      straid='s',label='原始参数复核',params={'private':7},constructor_params={'private':7})
            (taskdir/'task.json').write_text(json.dumps(task))
            class NativeManager:
                def __init__(self,path):self.path=path
                def shutdown(self):pass
            with patch('wtpy.monitor.ResearchServer.WtBtMon',NativeManager):
                app=create_research_app(root=root/'store',runs=root/'runs',template=template,
                    plus_root=root/'plus',upstream='http://monitor',owner='owner')
            client=TestClient(app)
            with patch('wtpy.monitor.ResearchServer.monitor_json',return_value={
                'result':0,'userinfo':{'loginid':'guest','role':'guest'}}):
                value=client.post('/bt/plus/list',json={'straid':'s'}).json()
                self.assertEqual(value['tasks'][0]['label'],'原始参数复核')
                self.assertNotIn('params',value['tasks'][0])
                self.assertNotIn('constructor_params',value['tasks'][0])
                value=client.post('/bt/plus/detail',json={'taskid':taskid}).json()
                self.assertNotIn('constructor_params',value['task'])
            with patch('wtpy.monitor.ResearchServer.monitor_json',return_value={
                'result':0,'userinfo':{'loginid':'owner','role':'admin'}}):
                value=client.post('/bt/plus/list',json={'straid':'s'}).json()
                self.assertEqual(value['tasks'][0]['constructor_params'],{'private':7})
            with patch('wtpy.monitor.ResearchServer.monitor_json',return_value={
                'result':0,'userinfo':{'loginid':'other','role':'researcher'}}):
                self.assertEqual(client.post('/bt/plus/list',json={}).json()['tasks'],[])

    def test_read_bridge_does_not_drop_a_position_when_another_channel_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);template=root/'template';template.mkdir();(root/'plus').mkdir()
            run=root/'live/run';(run/'generated/traders/a').mkdir(parents=True)
            (run/'generated/marker.json').write_text(json.dumps({'marks':[],'channels':['a','b']}))
            (run/'config.yaml').write_text('{}')
            (run/'generated/traders/a/rtdata.json').write_text(json.dumps({'positions':[
                {'code':'SHFE.rb.2701','long':{'newvol':1,'newavail':1,'prevol':0,'preavail':0}}]}))
            database=root/'monitor.db'
            connection=sqlite3.connect(database)
            connection.execute('CREATE TABLE groups(id INTEGER,groupid TEXT,path TEXT)')
            connection.execute('INSERT INTO groups VALUES(1,?,?)',('g',str(run)))
            connection.commit();connection.close()
            original=database.read_bytes()
            class NativeManager:
                def __init__(self,path):self.path=path
                def shutdown(self):pass
            with patch('wtpy.monitor.ResearchServer.WtBtMon',NativeManager):
                app=create_research_app(root=root/'store',runs=root/'runs',template=template,
                    plus_root=root/'plus',upstream='http://monitor',records_db=database)
            def upstream(_server,path,_body,_headers):
                if path=='/mgr/session':return {'result':0,'userinfo':{'loginid':'owner','role':'admin'}}
                contract={'code':'SHFE.rb.2701','exchange':'SHFE','product':'rb','expire_date':20270115}
                return {'result':0,'market':{'contract':contract,'contracts':[contract],'code':contract['code'],
                        'quote':{},'session_open':False,'rollover':{}},'positions':[],'accounts':[]}
            with patch('wtpy.monitor.ResearchServer.monitor_json',side_effect=upstream), \
                 patch('wtpy.monitor.WorkbenchApi.WorkbenchFeed.market',side_effect=ValueError('unreadable')):
                result=TestClient(app).post('/mgr/workbench',json={'groupid':'g'}).json()
            self.assertEqual(result['positions'][0]['volume'],1)
            self.assertEqual(result['positions'][0]['channel'],'a')
            self.assertIn('source_warning',result['market'])
            self.assertEqual(database.read_bytes(),original)

            selected={'code':'SHFE.rb.2705','exchange':'SHFE','product':'rb','expire_date':20270517}
            market={'contract':selected,'contracts':[selected],'code':selected['code'],
                    'quote':{},'session_open':False,'rollover':{}}
            payload={'groupid':'g','code':selected['code'],'period':'m5','count':40,
                     'start_date':20261020,'end_date':20261023,'include_catalog':False}
            with patch('wtpy.monitor.ResearchServer.monitor_json',side_effect=upstream), \
                 patch('wtpy.monitor.WorkbenchApi.WorkbenchFeed.market',side_effect=[market,ValueError('damaged')]) as read_market:
                result=TestClient(app).post('/mgr/workbench',json=payload).json()
            self.assertEqual(result['result'],0)
            self.assertEqual(result['market']['code'],selected['code'])
            self.assertIsNone(result['market']['contracts'])
            self.assertEqual(read_market.call_args_list[0].args,
                ({'id':'g','path':str(run)},selected['code'],'m5',40,True,20261020,20261023))
            self.assertEqual(result['positions'][0]['volume'],1)
            self.assertEqual(result['positions'][0]['market_state'],'unknown')

            def denied(_server,path,_body,_headers):
                return upstream(_server,path,_body,_headers) if path=='/mgr/session' else {'result':-403,'message':'denied'}
            with patch('wtpy.monitor.ResearchServer.monitor_json',side_effect=denied), \
                 patch('wtpy.monitor.ResearchServer.sqlite3.connect') as database_read, \
                 patch('wtpy.monitor.WorkbenchApi.WorkbenchFeed.market') as read_market:
                result=TestClient(app).post('/mgr/workbench',json={'groupid':'not-allowed'}).json()
            self.assertEqual(result['result'],-403)
            database_read.assert_not_called()
            read_market.assert_not_called()


if __name__=='__main__':
    unittest.main()
